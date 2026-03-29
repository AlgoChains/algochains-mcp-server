"""
Alpha Vantage data provider — free tier with fundamentals and technicals.

Covers: Stocks, Forex, Crypto, Economic indicators, Technicals
Free tier: 25 API calls/day (with key)
Paid: $49.99/mo (75 calls/min), $249.99/mo (600 calls/min)
WebSocket: No

Install: pip install "algochains-mcp-server[alphavantage]"
Env var: ALPHA_VANTAGE_API_KEY
"""
from __future__ import annotations

import os
from typing import Optional

import httpx

from .base import (
    AssetType, DataProvider, Interval, OHLCV, ProviderInfo, Quote,
)

_FUNCTION_MAP = {
    Interval.M1: "TIME_SERIES_INTRADAY",
    Interval.M5: "TIME_SERIES_INTRADAY",
    Interval.M15: "TIME_SERIES_INTRADAY",
    Interval.M30: "TIME_SERIES_INTRADAY",
    Interval.H1: "TIME_SERIES_INTRADAY",
    Interval.D1: "TIME_SERIES_DAILY",
    Interval.W1: "TIME_SERIES_WEEKLY",
    Interval.MO: "TIME_SERIES_MONTHLY",
}

_INTRADAY_INTERVALS = {
    Interval.M1: "1min",
    Interval.M5: "5min",
    Interval.M15: "15min",
    Interval.M30: "30min",
    Interval.H1: "60min",
}


class AlphaVantageProvider(DataProvider):
    BASE = "https://www.alphavantage.co/query"

    def __init__(self, api_key: str = ""):
        self.api_key = api_key or os.environ.get("ALPHA_VANTAGE_API_KEY", "")

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            name="Alpha Vantage",
            description="Free fundamentals, technicals, and OHLCV data. 110+ technical indicators built in. Great free tier for learning.",
            asset_types=[AssetType.STOCK, AssetType.ETF, AssetType.FOREX, AssetType.CRYPTO, AssetType.ECONOMIC],
            intervals=[Interval.M1, Interval.M5, Interval.M15, Interval.M30, Interval.H1, Interval.D1, Interval.W1, Interval.MO],
            requires_api_key=True,
            free_tier=True,
            websocket=False,
            rate_limit="25/day (free), 75/min ($49.99/mo)",
            docs_url="https://www.alphavantage.co/documentation/",
            env_var="ALPHA_VANTAGE_API_KEY",
        )

    async def get_bars(
        self, symbol: str, interval: Interval, limit: int = 100,
        start: Optional[str] = None, end: Optional[str] = None,
    ) -> list[OHLCV]:
        function = _FUNCTION_MAP.get(interval, "TIME_SERIES_DAILY")
        params: dict = {"function": function, "symbol": symbol, "apikey": self.api_key, "outputsize": "compact"}
        if function == "TIME_SERIES_INTRADAY":
            params["interval"] = _INTRADAY_INTERVALS.get(interval, "5min")

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(self.BASE, params=params)
        data = resp.json()

        # Alpha Vantage uses different keys for different functions
        ts_key = next((k for k in data if "Time Series" in k), None)
        if not ts_key:
            return []

        bars = []
        for ts, vals in list(data[ts_key].items())[:limit]:
            bars.append(OHLCV(
                timestamp=ts,
                open=float(vals.get("1. open", 0)),
                high=float(vals.get("2. high", 0)),
                low=float(vals.get("3. low", 0)),
                close=float(vals.get("4. close", 0)),
                volume=float(vals.get("5. volume", 0)),
            ))
        return bars

    async def get_quote(self, symbol: str) -> Quote:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(self.BASE, params={
                "function": "GLOBAL_QUOTE", "symbol": symbol, "apikey": self.api_key,
            })
        gq = resp.json().get("Global Quote", {})
        return Quote(
            symbol=symbol,
            bid=0,
            ask=0,
            last=float(gq.get("05. price", 0)),
            volume=float(gq.get("06. volume", 0)),
            timestamp=gq.get("07. latest trading day", ""),
            change_pct=float(gq.get("10. change percent", "0").rstrip("%")),
            source="alphavantage",
        )

    async def get_fundamentals(self, symbol: str) -> dict:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(self.BASE, params={
                "function": "OVERVIEW", "symbol": symbol, "apikey": self.api_key,
            })
        data = resp.json()
        return {
            "symbol": symbol,
            "name": data.get("Name", ""),
            "sector": data.get("Sector", ""),
            "industry": data.get("Industry", ""),
            "market_cap": int(data.get("MarketCapitalization", 0)),
            "pe_ratio": float(data.get("PERatio", 0) or 0),
            "eps": float(data.get("EPS", 0) or 0),
            "dividend_yield": float(data.get("DividendYield", 0) or 0),
            "beta": float(data.get("Beta", 0) or 0),
            "52w_high": float(data.get("52WeekHigh", 0) or 0),
            "52w_low": float(data.get("52WeekLow", 0) or 0),
            "revenue": int(data.get("RevenueTTM", 0) or 0),
            "profit_margin": float(data.get("ProfitMargin", 0) or 0),
            "source": "alphavantage",
        }

    async def search_symbols(self, query: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(self.BASE, params={
                "function": "SYMBOL_SEARCH", "keywords": query, "apikey": self.api_key,
            })
        return [
            {"symbol": m.get("1. symbol"), "name": m.get("2. name"), "type": m.get("3. type"), "region": m.get("4. region")}
            for m in resp.json().get("bestMatches", [])
        ]
