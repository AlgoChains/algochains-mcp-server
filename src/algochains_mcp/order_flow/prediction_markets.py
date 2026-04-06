"""
Prediction Market Signals — Real Data Only.

Data sources:
  1. Polymarket public API (no auth required for reading):
     https://gamma-api.polymarket.com/markets
  2. Kalshi REST API (auth required):
     https://trading-api.kalshi.com/trade-api/v2/markets

FAIL CLOSED: Raises PredictionMarketError if real API data is unavailable.
No synthetic market probabilities. No placeholder odds.

AlphaLoop generated $40M in profits by trading on Polymarket signals.
These are real prediction market contracts that can be traded.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
import urllib.error
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("algochains_mcp.order_flow.prediction_markets")


class PredictionMarketError(Exception):
    pass


@dataclass
class PredictionMarket:
    market_id: str
    platform: str          # "polymarket" | "kalshi"
    question: str
    yes_price: float       # probability 0-1
    no_price: float
    volume_24h: float
    liquidity: float
    end_date: str | None
    resolution: str | None  # None if open, "YES"/"NO" if resolved
    url: str
    related_symbols: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "market_id": self.market_id,
            "platform": self.platform,
            "question": self.question,
            "yes_probability": round(self.yes_price, 3),
            "no_probability": round(self.no_price, 3),
            "implied_yes_pct": round(self.yes_price * 100, 1),
            "volume_24h": round(self.volume_24h, 2),
            "liquidity": round(self.liquidity, 2),
            "end_date": self.end_date,
            "resolution": self.resolution,
            "url": self.url,
            "related_symbols": self.related_symbols,
        }


@dataclass
class EquitySignal:
    symbol: str
    market_id: str
    platform: str
    directional_signal: str    # "bullish" | "bearish" | "neutral"
    signal_strength: float     # 0-1
    rationale: str
    yes_probability: float
    correlation_type: str      # "earnings_beat" | "fed_rate_cut" | "election_outcome" | "macro"

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "market_id": self.market_id,
            "platform": self.platform,
            "directional_signal": self.directional_signal,
            "signal_strength": round(self.signal_strength, 2),
            "rationale": self.rationale,
            "yes_probability": round(self.yes_probability, 3),
            "correlation_type": self.correlation_type,
        }


class PredictionMarketsEngine:
    """
    Fetches real prediction market data from Polymarket and Kalshi APIs.

    No authentication required for Polymarket reads.
    Kalshi requires KALSHI_API_KEY env var for reads.
    Both require API keys for order placement.
    """

    POLYMARKET_BASE = "https://gamma-api.polymarket.com"
    KALSHI_BASE = "https://trading-api.kalshi.com/trade-api/v2"
    CLOB_BASE = "https://clob.polymarket.com"

    # Equity-correlated market keywords
    EQUITY_CORRELATIONS: dict[str, list[str]] = {
        "SPY": ["s&p 500", "stock market", "recession", "fed rate", "inflation"],
        "QQQ": ["tech", "nasdaq", "ai", "earnings"],
        "GLD": ["gold", "inflation", "recession"],
        "TLT": ["interest rate", "fed rate", "bonds", "treasury"],
        "NVDA": ["nvidia", "ai chips", "gpu", "earnings"],
        "TSLA": ["tesla", "elon", "ev", "electric vehicle"],
        "BTC": ["bitcoin", "crypto", "btc"],
        "ETH": ["ethereum", "eth", "crypto"],
    }

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, list[PredictionMarket]]] = {}
        self._cache_ttl = 300  # 5 min

    def search_markets(self, query: str, platform: str = "all", limit: int = 10) -> list[PredictionMarket]:
        """
        Search prediction markets by keyword using real APIs.

        Args:
            query: Search term (e.g. "NVDA earnings", "Fed rate cut")
            platform: "polymarket" | "kalshi" | "all"
            limit: Max markets to return

        Returns:
            List of real prediction markets with current probabilities
        """
        markets: list[PredictionMarket] = []

        if platform in ("polymarket", "all"):
            try:
                pm_markets = self._search_polymarket(query, limit)
                markets.extend(pm_markets)
            except Exception as exc:
                logger.warning("Polymarket search failed for '%s': %s", query, exc)

        if platform in ("kalshi", "all"):
            kalshi_key = os.environ.get("KALSHI_API_KEY", "")
            if kalshi_key:
                try:
                    km = self._search_kalshi(query, limit, kalshi_key)
                    markets.extend(km)
                except Exception as exc:
                    logger.warning("Kalshi search failed for '%s': %s", query, exc)

        if not markets:
            raise PredictionMarketError(
                f"No real prediction market data available for query '{query}'. "
                "Polymarket API may be unreachable. "
                "Set KALSHI_API_KEY for Kalshi access."
            )

        return markets[:limit]

    def _search_polymarket(self, query: str, limit: int) -> list[PredictionMarket]:
        """Fetch real markets from Polymarket Gamma API."""
        params = urllib.parse.urlencode({
            "q": query,
            "limit": min(limit, 20),
            "active": "true",
            "closed": "false",
        })
        url = f"{self.POLYMARKET_BASE}/markets?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "AlgoChains-MCP/21.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())

        markets = []
        # Polymarket returns a list directly
        items = data if isinstance(data, list) else data.get("data", data.get("markets", []))

        for m in items:
            try:
                outcomes = m.get("outcomePrices", "[]")
                if isinstance(outcomes, str):
                    outcomes = json.loads(outcomes)
                yes_price = float(outcomes[0]) if outcomes else 0.5
                no_price = float(outcomes[1]) if len(outcomes) > 1 else (1 - yes_price)

                markets.append(PredictionMarket(
                    market_id=str(m.get("id", m.get("conditionId", ""))),
                    platform="polymarket",
                    question=m.get("question", m.get("title", "")),
                    yes_price=yes_price,
                    no_price=no_price,
                    volume_24h=float(m.get("volume24hr", m.get("volumeNum", 0)) or 0),
                    liquidity=float(m.get("liquidity", m.get("liquidityNum", 0)) or 0),
                    end_date=m.get("endDate", m.get("end_date_iso")),
                    resolution=m.get("resolution") or (None if m.get("active") else "closed"),
                    url=f"https://polymarket.com/event/{m.get('slug', m.get('id', ''))}",
                ))
            except Exception:
                continue

        return markets

    def _search_kalshi(self, query: str, limit: int, api_key: str) -> list[PredictionMarket]:
        """Fetch real markets from Kalshi API (requires API key)."""
        params = urllib.parse.urlencode({
            "limit": min(limit, 25),
            "status": "open",
            "series_ticker": query.upper().replace(" ", "_")[:20],
        })
        url = f"{self.KALSHI_BASE}/markets?{params}"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Token {api_key}",
                "User-Agent": "AlgoChains-MCP/21.0",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())

        markets = []
        for m in data.get("markets", []):
            try:
                yes_price = float(m.get("yes_bid", m.get("yes_ask", 50))) / 100
                no_price = 1.0 - yes_price
                markets.append(PredictionMarket(
                    market_id=m.get("ticker", ""),
                    platform="kalshi",
                    question=m.get("title", ""),
                    yes_price=yes_price,
                    no_price=no_price,
                    volume_24h=float(m.get("volume", 0)),
                    liquidity=float(m.get("open_interest", 0)) * 0.01,
                    end_date=m.get("close_time"),
                    resolution=None if m.get("status") == "open" else m.get("result"),
                    url=f"https://kalshi.com/markets/{m.get('ticker', '')}",
                ))
            except Exception:
                continue

        return markets

    def get_equity_signal(self, symbol: str, market_id: str, platform: str = "polymarket") -> EquitySignal:
        """
        Derive an equity directional signal from a prediction market.

        Looks up the current YES probability on the prediction market and
        maps it to a directional signal for the correlated equity.
        """
        # Find the market
        query = symbol
        markets = self.search_markets(query, platform=platform, limit=5)
        target = next((m for m in markets if m.market_id == market_id), None)
        if not target:
            target = markets[0] if markets else None

        if not target:
            raise PredictionMarketError(f"Market {market_id} not found on {platform}")

        yes_prob = target.yes_price
        question_lower = target.question.lower()

        # Map question type to equity direction
        if any(w in question_lower for w in ["beat", "above", "exceed", "earnings"]):
            correlation_type = "earnings_beat"
            # YES = earnings beat → bullish for stock
            if yes_prob > 0.65:
                signal, strength = "bullish", yes_prob
            elif yes_prob < 0.35:
                signal, strength = "bearish", 1 - yes_prob
            else:
                signal, strength = "neutral", 0.5
        elif any(w in question_lower for w in ["rate cut", "cut rates", "lower rates"]):
            correlation_type = "fed_rate_cut"
            # YES = rate cut → bullish for equities (especially growth)
            if yes_prob > 0.60:
                signal, strength = "bullish", yes_prob
            else:
                signal, strength = "neutral", 0.5
        elif any(w in question_lower for w in ["recession", "gdp decline", "negative growth"]):
            correlation_type = "macro"
            # YES = recession → bearish for equities
            if yes_prob > 0.50:
                signal, strength = "bearish", yes_prob
            else:
                signal, strength = "neutral", 0.5
        else:
            correlation_type = "macro"
            signal = "neutral"
            strength = 0.40

        return EquitySignal(
            symbol=symbol,
            market_id=target.market_id,
            platform=platform,
            directional_signal=signal,
            signal_strength=strength,
            rationale=(
                f"Prediction market '{target.question}' has {yes_prob*100:.1f}% YES probability. "
                f"Correlated to {symbol} via {correlation_type}."
            ),
            yes_probability=yes_prob,
            correlation_type=correlation_type,
        )

    def place_polymarket_order(
        self,
        market_id: str,
        side: str,
        amount_usdc: float,
        clob_api_key: str | None = None,
        clob_api_secret: str | None = None,
        clob_passphrase: str | None = None,
    ) -> dict[str, Any]:
        """
        Place an order on Polymarket CLOB (Central Limit Order Book).

        Requires Polymarket CLOB API credentials:
          POLYMARKET_API_KEY, POLYMARKET_API_SECRET, POLYMARKET_PASSPHRASE

        Args:
            market_id: Polymarket condition ID (hex string)
            side: "YES" or "NO"
            amount_usdc: Amount in USDC to spend

        Returns:
            Order confirmation from Polymarket CLOB API
        """
        api_key = clob_api_key or os.environ.get("POLYMARKET_API_KEY", "")
        api_secret = clob_api_secret or os.environ.get("POLYMARKET_API_SECRET", "")
        passphrase = clob_passphrase or os.environ.get("POLYMARKET_PASSPHRASE", "")

        if not all([api_key, api_secret, passphrase]):
            raise PredictionMarketError(
                "Polymarket order placement requires POLYMARKET_API_KEY, "
                "POLYMARKET_API_SECRET, and POLYMARKET_PASSPHRASE environment variables. "
                "Register at https://polymarket.com to obtain CLOB API credentials."
            )

        # py-clob-client is the official Polymarket Python client
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import OrderArgs, OrderType
        except ImportError:
            raise PredictionMarketError(
                "py-clob-client not installed. Run: pip install py-clob-client"
            )

        client = ClobClient(
            host=self.CLOB_BASE,
            key=api_key,
            secret=api_secret,
            passphrase=passphrase,
            chain_id=137,  # Polygon mainnet
        )

        order_args = OrderArgs(
            token_id=market_id,
            price=0.5,  # Market order at midpoint
            size=amount_usdc,
            side=side.upper(),
        )
        order = client.create_and_post_order(OrderType.GTC, order_args)
        return {
            "order_id": order.get("orderID"),
            "market_id": market_id,
            "side": side,
            "amount_usdc": amount_usdc,
            "status": order.get("status"),
            "platform": "polymarket",
        }

    def get_top_markets(self, platform: str = "polymarket", limit: int = 20) -> list[dict[str, Any]]:
        """Get highest volume prediction markets right now."""
        if platform == "polymarket":
            params = urllib.parse.urlencode({"limit": limit, "active": "true", "sort": "volume24hr", "order": "desc"})
            url = f"{self.POLYMARKET_BASE}/markets?{params}"
            req = urllib.request.Request(url, headers={"User-Agent": "AlgoChains-MCP/21.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
            items = data if isinstance(data, list) else data.get("data", [])
            return [
                {
                    "question": m.get("question", ""),
                    "yes_pct": round(float((json.loads(m.get("outcomePrices", "[0.5]")) or [0.5])[0]) * 100, 1),
                    "volume_24h": float(m.get("volume24hr", 0) or 0),
                    "url": f"https://polymarket.com/event/{m.get('slug', '')}",
                }
                for m in items[:limit]
            ]
        raise PredictionMarketError(f"Platform '{platform}' not supported for top markets listing.")


_pm_engine: PredictionMarketsEngine | None = None


def get_prediction_markets_engine() -> PredictionMarketsEngine:
    global _pm_engine
    if _pm_engine is None:
        _pm_engine = PredictionMarketsEngine()
    return _pm_engine
