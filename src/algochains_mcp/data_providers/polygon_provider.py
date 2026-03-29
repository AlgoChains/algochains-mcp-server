"""
Polygon.io data provider — real-time and historical market data.

Covers: Stocks, Options, Forex, Crypto, Indices
Free tier: 5 API calls/min, delayed data
Paid: $29-$199/mo for real-time, unlimited
WebSocket: Yes (real-time trades, quotes, aggregates)

Install: pip install "algochains-mcp-server[polygon]"
Env var: POLYGON_API_KEY
"""
from __future__ import annotations

import os
from typing import Optional

import httpx

from .base import (
    AssetType, DataProvider, Interval, NewsItem, OHLCV, ProviderInfo, Quote,
)

_INTERVAL_MAP = {
    Interval.M1: ("1", "minute"),
    Interval.M5: ("5", "minute"),
    Interval.M15: ("15", "minute"),
    Interval.M30: ("30", "minute"),
    Interval.H1: ("1", "hour"),
    Interval.H4: ("4", "hour"),
    Interval.D1: ("1", "day"),
    Interval.W1: ("1", "week"),
    Interval.MO: ("1", "month"),
}


class PolygonProvider(DataProvider):
    BASE = "https://api.polygon.io"

    def __init__(self, api_key: str = ""):
        self.api_key = api_key or os.environ.get("POLYGON_API_KEY", "")
        self._client: Optional[httpx.AsyncClient] = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.BASE,
                params={"apiKey": self.api_key},
                timeout=15.0,
            )
        return self._client

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            name="Polygon.io",
            description="Real-time and historical data for stocks, options, forex, and crypto. WebSocket streaming. Used by major fintech companies.",
            asset_types=[AssetType.STOCK, AssetType.ETF, AssetType.OPTION, AssetType.FOREX, AssetType.CRYPTO, AssetType.INDEX],
            intervals=[Interval.M1, Interval.M5, Interval.M15, Interval.H1, Interval.D1, Interval.W1],
            requires_api_key=True,
            free_tier=True,
            websocket=True,
            rate_limit="5/min (free), unlimited (paid)",
            docs_url="https://polygon.io/docs",
            env_var="POLYGON_API_KEY",
        )

    async def get_bars(
        self, symbol: str, interval: Interval, limit: int = 100,
        start: Optional[str] = None, end: Optional[str] = None,
    ) -> list[OHLCV]:
        client = await self._ensure_client()
        mult, span = _INTERVAL_MAP.get(interval, ("1", "day"))
        _start = start or "2024-01-01"
        _end = end or "2025-12-31"
        resp = await client.get(
            f"/v2/aggs/ticker/{symbol}/range/{mult}/{span}/{_start}/{_end}",
            params={"limit": limit, "sort": "desc"},
        )
        data = resp.json()
        bars = []
        for r in data.get("results", []):
            bars.append(OHLCV(
                timestamp=str(r.get("t", "")),
                open=r.get("o", 0),
                high=r.get("h", 0),
                low=r.get("l", 0),
                close=r.get("c", 0),
                volume=r.get("v", 0),
                vwap=r.get("vw"),
                trades=r.get("n"),
            ))
        return bars

    async def get_quote(self, symbol: str) -> Quote:
        client = await self._ensure_client()
        resp = await client.get(f"/v2/last/trade/{symbol}")
        data = resp.json().get("results", {})
        snap_resp = await client.get(f"/v2/snapshot/locale/us/markets/stocks/tickers/{symbol}")
        snap = snap_resp.json().get("ticker", {})
        day = snap.get("day", {})
        return Quote(
            symbol=symbol,
            bid=snap.get("lastQuote", {}).get("p", 0),
            ask=snap.get("lastQuote", {}).get("P", 0),
            last=data.get("p", day.get("c", 0)),
            volume=day.get("v", 0),
            timestamp=str(data.get("t", "")),
            change_pct=snap.get("todaysChangePerc", 0),
            source="polygon",
        )

    async def get_news(self, symbol: str, limit: int = 10) -> list[NewsItem]:
        client = await self._ensure_client()
        resp = await client.get(f"/v2/reference/news", params={"ticker": symbol, "limit": limit})
        items = []
        for article in resp.json().get("results", []):
            items.append(NewsItem(
                title=article.get("title", ""),
                url=article.get("article_url", ""),
                source=article.get("publisher", {}).get("name", ""),
                published=article.get("published_utc", ""),
                symbols=[t.get("ticker", "") for t in article.get("tickers", [])],
            ))
        return items

    async def search_symbols(self, query: str) -> list[dict]:
        client = await self._ensure_client()
        resp = await client.get(f"/v3/reference/tickers", params={"search": query, "limit": 20})
        return [
            {"symbol": t.get("ticker"), "name": t.get("name"), "type": t.get("type"), "exchange": t.get("primary_exchange")}
            for t in resp.json().get("results", [])
        ]
