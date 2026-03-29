"""
Finnhub data provider — real-time quotes, news, fundamentals.

Covers: Stocks, Forex, Crypto, ETFs, Indices, Economic calendar
Free tier: 60 API calls/min
Paid: $49-$599/mo for premium data
WebSocket: Yes (real-time trades)

Install: pip install "algochains-mcp-server[finnhub]"
Env var: FINNHUB_API_KEY
"""
from __future__ import annotations

import os
from typing import Optional

import httpx

from .base import (
    AssetType, DataProvider, Interval, NewsItem, OHLCV, ProviderInfo, Quote,
)

_RESOLUTION_MAP = {
    Interval.M1: "1",
    Interval.M5: "5",
    Interval.M15: "15",
    Interval.M30: "30",
    Interval.H1: "60",
    Interval.D1: "D",
    Interval.W1: "W",
    Interval.MO: "M",
}


class FinnhubProvider(DataProvider):
    BASE = "https://finnhub.io/api/v1"

    def __init__(self, api_key: str = ""):
        self.api_key = api_key or os.environ.get("FINNHUB_API_KEY", "")

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            name="Finnhub",
            description="Real-time quotes, news sentiment, earnings, insider trading, SEC filings, and economic calendar. Generous free tier (60/min).",
            asset_types=[AssetType.STOCK, AssetType.ETF, AssetType.FOREX, AssetType.CRYPTO, AssetType.INDEX, AssetType.ECONOMIC],
            intervals=[Interval.M1, Interval.M5, Interval.M15, Interval.M30, Interval.H1, Interval.D1, Interval.W1, Interval.MO],
            requires_api_key=True,
            free_tier=True,
            websocket=True,
            rate_limit="60/min (free), 300/min (paid)",
            docs_url="https://finnhub.io/docs/api",
            env_var="FINNHUB_API_KEY",
        )

    async def get_bars(
        self, symbol: str, interval: Interval, limit: int = 100,
        start: Optional[str] = None, end: Optional[str] = None,
    ) -> list[OHLCV]:
        import time as _time
        resolution = _RESOLUTION_MAP.get(interval, "D")
        _to = int(_time.time())
        _from = _to - (limit * 86400 if resolution == "D" else limit * 3600)

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{self.BASE}/stock/candle", params={
                "symbol": symbol, "resolution": resolution,
                "from": _from, "to": _to, "token": self.api_key,
            })
        data = resp.json()
        if data.get("s") != "ok":
            return []

        bars = []
        timestamps = data.get("t", [])
        for i in range(min(len(timestamps), limit)):
            bars.append(OHLCV(
                timestamp=str(timestamps[i]),
                open=data["o"][i],
                high=data["h"][i],
                low=data["l"][i],
                close=data["c"][i],
                volume=data["v"][i],
            ))
        return bars

    async def get_quote(self, symbol: str) -> Quote:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{self.BASE}/quote", params={
                "symbol": symbol, "token": self.api_key,
            })
        data = resp.json()
        return Quote(
            symbol=symbol,
            bid=0,
            ask=0,
            last=data.get("c", 0),
            volume=0,
            timestamp=str(data.get("t", "")),
            change_pct=data.get("dp", 0),
            source="finnhub",
        )

    async def get_news(self, symbol: str, limit: int = 10) -> list[NewsItem]:
        import datetime
        today = datetime.date.today()
        week_ago = today - datetime.timedelta(days=7)
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{self.BASE}/company-news", params={
                "symbol": symbol, "from": str(week_ago), "to": str(today), "token": self.api_key,
            })
        items = []
        for article in resp.json()[:limit]:
            items.append(NewsItem(
                title=article.get("headline", ""),
                url=article.get("url", ""),
                source=article.get("source", ""),
                published=str(article.get("datetime", "")),
                symbols=[symbol],
                sentiment=article.get("sentiment"),
            ))
        return items

    async def search_symbols(self, query: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{self.BASE}/search", params={
                "q": query, "token": self.api_key,
            })
        return [
            {"symbol": r.get("symbol"), "name": r.get("description"), "type": r.get("type")}
            for r in resp.json().get("result", [])[:20]
        ]
