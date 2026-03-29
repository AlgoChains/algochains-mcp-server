"""
Yahoo Finance data provider — free, no API key required.

Covers: Stocks, ETFs, Indices, Crypto, Forex, Mutual Funds
Free tier: Unlimited (rate-limited by Yahoo)
WebSocket: No
Install: pip install "algochains-mcp-server[yahoo]"

Best for: Quick prototyping, free data access, fundamentals
"""
from __future__ import annotations

from typing import Optional

from .base import (
    AssetType, DataProvider, Interval, OHLCV, ProviderInfo, Quote,
)

_YF_INTERVAL_MAP = {
    Interval.M1: "1m",
    Interval.M5: "5m",
    Interval.M15: "15m",
    Interval.M30: "30m",
    Interval.H1: "1h",
    Interval.D1: "1d",
    Interval.W1: "1wk",
    Interval.MO: "1mo",
}


class YahooFinanceProvider(DataProvider):
    """Yahoo Finance via yfinance library (no API key needed)."""

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            name="Yahoo Finance",
            description="Free stock, ETF, crypto, and forex data. No API key required. Great for prototyping and fundamentals. Delayed quotes.",
            asset_types=[AssetType.STOCK, AssetType.ETF, AssetType.CRYPTO, AssetType.FOREX, AssetType.INDEX],
            intervals=[Interval.M1, Interval.M5, Interval.M15, Interval.H1, Interval.D1, Interval.W1, Interval.MO],
            requires_api_key=False,
            free_tier=True,
            websocket=False,
            rate_limit="~2000/hour (unofficial)",
            docs_url="https://github.com/ranaroussi/yfinance",
            env_var="",
        )

    async def get_bars(
        self, symbol: str, interval: Interval, limit: int = 100,
        start: Optional[str] = None, end: Optional[str] = None,
    ) -> list[OHLCV]:
        import yfinance as yf
        yf_interval = _YF_INTERVAL_MAP.get(interval, "1d")
        period = "1y" if interval in (Interval.D1, Interval.W1, Interval.MO) else "5d"
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period, interval=yf_interval)
        bars = []
        for ts, row in df.tail(limit).iterrows():
            bars.append(OHLCV(
                timestamp=str(ts),
                open=float(row.get("Open", 0)),
                high=float(row.get("High", 0)),
                low=float(row.get("Low", 0)),
                close=float(row.get("Close", 0)),
                volume=float(row.get("Volume", 0)),
            ))
        return bars

    async def get_quote(self, symbol: str) -> Quote:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        info = ticker.info
        return Quote(
            symbol=symbol,
            bid=info.get("bid", 0),
            ask=info.get("ask", 0),
            last=info.get("currentPrice", info.get("regularMarketPrice", 0)),
            volume=info.get("volume", info.get("regularMarketVolume", 0)),
            timestamp="",
            change_pct=info.get("regularMarketChangePercent", 0),
            source="yahoo",
        )

    async def get_fundamentals(self, symbol: str) -> dict:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        info = ticker.info
        return {
            "symbol": symbol,
            "name": info.get("longName", ""),
            "sector": info.get("sector", ""),
            "industry": info.get("industry", ""),
            "market_cap": info.get("marketCap", 0),
            "pe_ratio": info.get("trailingPE", 0),
            "forward_pe": info.get("forwardPE", 0),
            "eps": info.get("trailingEps", 0),
            "dividend_yield": info.get("dividendYield", 0),
            "beta": info.get("beta", 0),
            "52w_high": info.get("fiftyTwoWeekHigh", 0),
            "52w_low": info.get("fiftyTwoWeekLow", 0),
            "avg_volume": info.get("averageVolume", 0),
            "revenue": info.get("totalRevenue", 0),
            "profit_margin": info.get("profitMargins", 0),
            "source": "yahoo",
        }

    async def search_symbols(self, query: str) -> list[dict]:
        import yfinance as yf
        # yfinance doesn't have a search, use a simple approach
        try:
            ticker = yf.Ticker(query)
            info = ticker.info
            if info.get("longName"):
                return [{"symbol": query, "name": info["longName"], "type": info.get("quoteType", ""), "exchange": info.get("exchange", "")}]
        except Exception:
            pass
        return []
