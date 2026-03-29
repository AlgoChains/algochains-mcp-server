"""
Base interface for all data providers.

Every data provider implements this ABC so the MCP server can
swap providers transparently. Users configure which provider(s)
they want via environment variables.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger("algochains_mcp.data_providers")


class AssetType(str, Enum):
    STOCK = "stock"
    ETF = "etf"
    OPTION = "option"
    FUTURE = "future"
    FOREX = "forex"
    CRYPTO = "crypto"
    INDEX = "index"
    ECONOMIC = "economic"


class Interval(str, Enum):
    TICK = "tick"
    M1 = "1min"
    M5 = "5min"
    M15 = "15min"
    M30 = "30min"
    H1 = "1hour"
    H4 = "4hour"
    D1 = "1day"
    W1 = "1week"
    MO = "1month"


@dataclass
class OHLCV:
    """A single candlestick bar."""
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: Optional[float] = None
    trades: Optional[int] = None

    def to_dict(self) -> dict:
        d = {
            "timestamp": self.timestamp,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }
        if self.vwap is not None:
            d["vwap"] = self.vwap
        if self.trades is not None:
            d["trades"] = self.trades
        return d


@dataclass
class Quote:
    """A real-time quote snapshot."""
    symbol: str
    bid: float
    ask: float
    last: float
    volume: float
    timestamp: str
    change_pct: float = 0.0
    source: str = ""

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "bid": self.bid,
            "ask": self.ask,
            "last": self.last,
            "volume": self.volume,
            "timestamp": self.timestamp,
            "change_pct": self.change_pct,
            "source": self.source,
        }


@dataclass
class NewsItem:
    """A financial news article."""
    title: str
    url: str
    source: str
    published: str
    symbols: list[str] = field(default_factory=list)
    sentiment: Optional[float] = None  # -1 to 1

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "source": self.source,
            "published": self.published,
            "symbols": self.symbols,
            "sentiment": self.sentiment,
        }


@dataclass
class ProviderInfo:
    """Metadata about a data provider."""
    name: str
    description: str
    asset_types: list[AssetType]
    intervals: list[Interval]
    requires_api_key: bool
    free_tier: bool
    websocket: bool
    rate_limit: str  # e.g. "5/min", "unlimited"
    docs_url: str
    env_var: str  # Environment variable name for API key

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "asset_types": [a.value for a in self.asset_types],
            "intervals": [i.value for i in self.intervals],
            "requires_api_key": self.requires_api_key,
            "free_tier": self.free_tier,
            "websocket": self.websocket,
            "rate_limit": self.rate_limit,
            "docs_url": self.docs_url,
            "env_var": self.env_var,
        }


class DataProvider(ABC):
    """Abstract base class for all data providers."""

    @abstractmethod
    def info(self) -> ProviderInfo:
        """Return provider metadata."""
        ...

    @abstractmethod
    async def get_bars(
        self,
        symbol: str,
        interval: Interval,
        limit: int = 100,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> list[OHLCV]:
        """Fetch OHLCV bars."""
        ...

    @abstractmethod
    async def get_quote(self, symbol: str) -> Quote:
        """Fetch a real-time quote."""
        ...

    async def get_news(self, symbol: str, limit: int = 10) -> list[NewsItem]:
        """Fetch news articles (optional — not all providers support this)."""
        return []

    async def search_symbols(self, query: str) -> list[dict]:
        """Search for symbols matching a query."""
        return []

    async def get_fundamentals(self, symbol: str) -> dict:
        """Fetch fundamental data (optional)."""
        return {}

    async def health_check(self) -> bool:
        """Check if the provider is reachable and authenticated."""
        try:
            await self.get_quote("AAPL")
            return True
        except Exception:
            return False
