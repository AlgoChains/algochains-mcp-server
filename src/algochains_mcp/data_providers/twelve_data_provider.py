"""
Twelve Data provider — real-time + historical across 800+ exchanges.

Covers: Stocks, Forex, Crypto, ETFs, Indices, Commodities, Bonds
Free tier: 800 API credits/day, 8 API calls/min
Paid: $29-$499/mo for more credits and WebSocket
WebSocket: Yes (paid plans)

Install: pip install "algochains-mcp-server[twelvedata]"
Env var: TWELVE_DATA_API_KEY
"""
from __future__ import annotations

import os
from typing import Optional

import httpx

from .base import (
    AssetType, DataProvider, Interval, OHLCV, ProviderInfo, Quote,
)

_INTERVAL_MAP = {
    Interval.M1: "1min",
    Interval.M5: "5min",
    Interval.M15: "15min",
    Interval.M30: "30min",
    Interval.H1: "1h",
    Interval.H4: "4h",
    Interval.D1: "1day",
    Interval.W1: "1week",
    Interval.MO: "1month",
}


class TwelveDataProvider(DataProvider):
    BASE = "https://api.twelvedata.com"

    def __init__(self, api_key: str = ""):
        self.api_key = api_key or os.environ.get("TWELVE_DATA_API_KEY", "")

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            name="Twelve Data",
            description="800+ global exchanges. Real-time + historical OHLCV, technicals, fundamentals. WebSocket streaming on paid plans.",
            asset_types=[AssetType.STOCK, AssetType.ETF, AssetType.FOREX, AssetType.CRYPTO, AssetType.INDEX],
            intervals=[Interval.M1, Interval.M5, Interval.M15, Interval.M30, Interval.H1, Interval.H4, Interval.D1, Interval.W1, Interval.MO],
            requires_api_key=True,
            free_tier=True,
            websocket=True,
            rate_limit="8/min (free), 120/min ($29/mo)",
            docs_url="https://twelvedata.com/docs",
            env_var="TWELVE_DATA_API_KEY",
        )

    async def get_bars(
        self, symbol: str, interval: Interval, limit: int = 100,
        start: Optional[str] = None, end: Optional[str] = None,
    ) -> list[OHLCV]:
        td_interval = _INTERVAL_MAP.get(interval, "1day")
        params: dict = {
            "symbol": symbol, "interval": td_interval,
            "outputsize": limit, "apikey": self.api_key,
        }
        if start:
            params["start_date"] = start
        if end:
            params["end_date"] = end

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{self.BASE}/time_series", params=params)
        data = resp.json()
        bars = []
        for v in data.get("values", []):
            bars.append(OHLCV(
                timestamp=v.get("datetime", ""),
                open=float(v.get("open", 0)),
                high=float(v.get("high", 0)),
                low=float(v.get("low", 0)),
                close=float(v.get("close", 0)),
                volume=float(v.get("volume", 0)),
            ))
        return bars

    async def get_quote(self, symbol: str) -> Quote:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{self.BASE}/quote", params={
                "symbol": symbol, "apikey": self.api_key,
            })
        data = resp.json()
        return Quote(
            symbol=symbol,
            bid=0,
            ask=0,
            last=float(data.get("close", 0)),
            volume=float(data.get("volume", 0)),
            timestamp=data.get("datetime", ""),
            change_pct=float(data.get("percent_change", 0)),
            source="twelvedata",
        )

    async def search_symbols(self, query: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{self.BASE}/symbol_search", params={
                "symbol": query, "outputsize": 20,
            })
        return [
            {"symbol": s.get("symbol"), "name": s.get("instrument_name"), "type": s.get("instrument_type"), "exchange": s.get("exchange")}
            for s in resp.json().get("data", [])
        ]
