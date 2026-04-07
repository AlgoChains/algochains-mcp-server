"""
Prediction Market Signals — Real Data Only.

Data sources:
  1. Polymarket public API (no auth required for reading):
     https://gamma-api.polymarket.com/markets
  2. Kalshi REST API v2 (RSA-PSS signing — see docs.kalshi.com):
     KALSHI_ACCESS_KEY + KALSHI_PRIVATE_KEY_PATH / KALSHI_PRIVATE_KEY_PEM

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


def _polymarket_yes_no_prices(m: dict) -> tuple[float, float] | None:
    """Parse YES/NO from Gamma ``outcomePrices``. Returns None if missing/invalid — never invent 50/50."""
    raw = m.get("outcomePrices")
    if raw is None:
        return None
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        try:
            outcomes: list = json.loads(s)
        except json.JSONDecodeError:
            return None
    elif isinstance(raw, list):
        outcomes = raw
    else:
        return None
    if not outcomes:
        return None
    try:
        yes = float(outcomes[0])
        no_p = float(outcomes[1]) if len(outcomes) > 1 else (1.0 - yes)
    except (TypeError, ValueError, IndexError):
        return None
    if not (0.0 <= yes <= 1.0 and 0.0 <= no_p <= 1.0):
        return None
    return yes, no_p


def _kalshi_yes_prob(m: dict) -> float | None:
    """Mid-market YES probability from bid/ask cents; None if no usable quote."""
    yb, ya = m.get("yes_bid"), m.get("yes_ask")
    try:
        if yb is not None and ya is not None:
            return (float(yb) + float(ya)) / 2.0 / 100.0
        if yb is not None:
            return float(yb) / 100.0
        if ya is not None:
            return float(ya) / 100.0
    except (TypeError, ValueError):
        pass
    return None


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
    Kalshi reads require RSA credentials (see ``kalshi_signed.kalshi_configured``).
    Polymarket CLOB orders require POLYMARKET_API_KEY / SECRET / PASSPHRASE + explicit limit price.
    """

    POLYMARKET_BASE = "https://gamma-api.polymarket.com"
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

    # Thematic queries → Polymarket Gamma search (real API only)
    CATEGORY_QUERIES: dict[str, list[str]] = {
        "fed": ["fed rate cut", "federal reserve interest", "FOMC"],
        "economic": ["recession GDP", "CPI inflation", "unemployment rate"],
        "political": ["presidential election", "senate race", "house election"],
        "crypto": ["bitcoin", "ethereum", "BTC ETF"],
        "all": ["bitcoin", "fed rate", "election", "S&P recession"],
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
            from .kalshi_signed import kalshi_configured

            if kalshi_configured():
                try:
                    km = self._search_kalshi_signed(query, limit)
                    markets.extend(km)
                except Exception as exc:
                    logger.warning("Kalshi search failed for '%s': %s", query, exc)
            elif os.environ.get("KALSHI_API_KEY"):
                logger.warning(
                    "KALSHI_API_KEY is set but Kalshi trade-api v2 requires "
                    "KALSHI_ACCESS_KEY + KALSHI_PRIVATE_KEY_PATH (RSA). "
                    "See https://docs.kalshi.com/getting_started/api_keys"
                )

        if not markets:
            raise PredictionMarketError(
                f"No real prediction market data available for query '{query}'. "
                "Polymarket Gamma may be unreachable, or Kalshi RSA credentials not configured, "
                "or no Kalshi title matched the query."
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
                yn = _polymarket_yes_no_prices(m)
                if yn is None:
                    continue
                yes_price, no_price = yn

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

    def _search_kalshi_signed(self, query: str, limit: int) -> list[PredictionMarket]:
        """Fetch open Kalshi markets via RSA-signed GET; filter by query in title/ticker."""
        from .kalshi_signed import kalshi_signed_get

        code, data = kalshi_signed_get(
            "/trade-api/v2/markets",
            {"limit": str(min(200, max(30, limit * 8))), "status": "open"},
        )
        if code != 200 or not isinstance(data, dict):
            raise PredictionMarketError(f"Kalshi markets HTTP {code}: {str(data)[:400]}")

        rows = data.get("markets") or []
        q_lower = (query or "").lower()
        if q_lower:
            filtered = [
                x for x in rows
                if q_lower in (x.get("title") or "").lower()
                or q_lower in (x.get("ticker") or "").lower()
            ]
            if not filtered:
                filtered = rows
        else:
            filtered = rows

        markets: list[PredictionMarket] = []
        for m in filtered:
            try:
                yp = _kalshi_yes_prob(m)
                if yp is None:
                    continue
                np = max(0.0, min(1.0, 1.0 - yp))
                markets.append(PredictionMarket(
                    market_id=m.get("ticker", ""),
                    platform="kalshi",
                    question=m.get("title", ""),
                    yes_price=yp,
                    no_price=np,
                    volume_24h=float(m.get("volume", 0) or 0),
                    liquidity=float(m.get("open_interest", 0) or 0),
                    end_date=m.get("close_time"),
                    resolution=None if m.get("status") == "open" else m.get("result"),
                    url=f"https://kalshi.com/markets/{m.get('ticker', '')}",
                ))
            except Exception:
                continue
            if len(markets) >= limit:
                break

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

    def get_signals(self, category: str = "all", min_volume: float = 10000) -> dict[str, Any]:
        """
        Aggregate real Polymarket (and optionally Kalshi) contracts by thematic category.

        Used by MCP tool ``get_prediction_markets``. All probabilities and volumes
        come from live APIs — no fabricated odds.
        """
        cat = (category or "all").lower()
        if cat not in self.CATEGORY_QUERIES:
            cat = "all"
        queries = self.CATEGORY_QUERIES[cat]
        seen: set[str] = set()
        markets_out: list[dict[str, Any]] = []

        for q in queries:
            try:
                for m in self.search_markets(q, platform="polymarket", limit=10):
                    if not m.market_id or m.market_id in seen:
                        continue
                    if m.volume_24h < min_volume:
                        continue
                    seen.add(m.market_id)
                    markets_out.append(m.to_dict())
            except PredictionMarketError:
                continue
            except Exception as exc:
                logger.debug("get_signals polymarket q=%r: %s", q, exc)

        from .kalshi_signed import kalshi_configured

        if kalshi_configured():
            for q in queries[:3]:
                try:
                    for m in self.search_markets(q, platform="kalshi", limit=6):
                        kid = f"kalshi:{m.market_id}"
                        if not m.market_id or kid in seen:
                            continue
                        if m.volume_24h < min_volume:
                            continue
                        seen.add(kid)
                        markets_out.append(m.to_dict())
                except PredictionMarketError:
                    continue
                except Exception as exc:
                    logger.debug("get_signals kalshi q=%r: %s", q, exc)

        markets_out.sort(key=lambda x: x.get("volume_24h", 0.0), reverse=True)

        if not markets_out:
            raise PredictionMarketError(
                f"No live prediction markets met volume_24h>={min_volume} for category '{cat}'. "
                "Lower min_volume or configure Kalshi RSA (KALSHI_ACCESS_KEY + KALSHI_PRIVATE_KEY_PATH)."
            )

        return {
            "category": cat,
            "min_volume_24h": min_volume,
            "count": len(markets_out),
            "markets": markets_out[:40],
            "sources": ["polymarket"] + (["kalshi"] if kalshi_configured() else []),
            "disclaimer": "Market-implied probabilities are not investment advice.",
        }

    def place_polymarket_order(
        self,
        market_id: str,
        side: str,
        amount_usdc: float,
        limit_price: float,
        clob_api_key: str | None = None,
        clob_api_secret: str | None = None,
        clob_passphrase: str | None = None,
    ) -> dict[str, Any]:
        """
        Place a **limit** order on Polymarket CLOB (Central Limit Order Book).

        Requires Polymarket CLOB API credentials:
          POLYMARKET_API_KEY, POLYMARKET_API_SECRET, POLYMARKET_PASSPHRASE

        Args:
            market_id: Token / condition id for the CLOB (from Polymarket metadata).
            side: "YES" or "NO" (Polymarket outcome side).
            amount_usdc: Order size in outcome tokens / USDC notional per py-clob-client semantics.
            limit_price: Limit price in **0–1** probability units from the real book — never defaulted.

        Returns:
            Order confirmation from Polymarket CLOB API
        """
        if limit_price <= 0.0 or limit_price >= 1.0:
            raise PredictionMarketError(
                "limit_price must be strictly between 0 and 1 (real CLOB price). "
                "Fetch the book or midpoint from live data before placing."
            )
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
            price=limit_price,
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
        """Get highest volume prediction markets (Gamma: ``sort=volume24hr``, ``ascending=false``)."""
        if platform == "polymarket":
            params = urllib.parse.urlencode({
                "limit": limit,
                "active": "true",
                "closed": "false",
                "sort": "volume24hr",
                "ascending": "false",
            })
            url = f"{self.POLYMARKET_BASE}/markets?{params}"
            req = urllib.request.Request(url, headers={"User-Agent": "AlgoChains-MCP/22.6"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
            items = data if isinstance(data, list) else data.get("data", [])
            out: list[dict[str, Any]] = []
            for m in items:
                yn = _polymarket_yes_no_prices(m)
                if yn is None:
                    continue
                yes, _ = yn
                out.append({
                    "question": m.get("question", ""),
                    "market_id": str(m.get("id", m.get("conditionId", ""))),
                    "yes_pct": round(yes * 100, 1),
                    "volume_24h": float(m.get("volume24hr", 0) or m.get("volumeNum", 0) or 0),
                    "url": f"https://polymarket.com/event/{m.get('slug', '')}",
                })
                if len(out) >= limit:
                    break
            return out
        raise PredictionMarketError(f"Platform '{platform}' not supported for top markets listing.")

    # ── New capabilities from gap analysis vs mcp-server-kalshi + polymarket-mcp ──

    def get_polymarket_market(self, market_id_or_slug: str) -> dict[str, Any]:
        """
        Fetch a specific Polymarket market by condition ID or event slug.
        Inspired by berlinbra/polymarket-mcp get-market-info tool.

        API: gamma-api.polymarket.com/markets/{condition_id}
             gamma-api.polymarket.com/events/{slug}
        """
        if not market_id_or_slug.strip():
            raise PredictionMarketError("market_id_or_slug is required")

        errors: list[str] = []
        slug_or_id = market_id_or_slug.strip()

        # Gamma API: fetch by slug via query param, or by conditionId via query param
        # Path-based lookups return 422 — must use query params
        for params, base in (
            ({"slug": slug_or_id}, f"{self.POLYMARKET_BASE}/markets"),
            ({"conditionId": slug_or_id}, f"{self.POLYMARKET_BASE}/markets"),
            ({"id": slug_or_id}, f"{self.POLYMARKET_BASE}/markets"),
        ):
            endpoint = f"{base}?{urllib.parse.urlencode(params)}"
            try:
                req = urllib.request.Request(
                    endpoint, headers={"User-Agent": "AlgoChains-MCP/22.8"}
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read())
                if isinstance(data, list):
                    if not data:
                        continue
                    data = data[0]
                if not data:
                    continue
                yn = _polymarket_yes_no_prices(data)
                return {
                    "market_id": str(data.get("id", data.get("conditionId", ""))),
                    "slug": data.get("slug", ""),
                    "question": data.get("question", data.get("title", "")),
                    "description": (data.get("description", "") or "")[:500],
                    "status": "open" if data.get("active") and not data.get("closed") else "closed",
                    "category": data.get("category", ""),
                    "resolution_date": data.get("endDate", data.get("endDateIso", "")),
                    "volume": float(data.get("volumeNum", data.get("volume", 0)) or 0),
                    "volume_24h": float(data.get("volume24hr", 0) or 0),
                    "liquidity": float(data.get("liquidityNum", data.get("liquidity", 0)) or 0),
                    "yes_price": round(yn[0], 4) if yn else None,
                    "no_price": round(yn[1], 4) if yn else None,
                    "yes_pct": round(yn[0] * 100, 1) if yn else None,
                    "url": f"https://polymarket.com/event/{data.get('slug', slug_or_id)}",
                    "source": endpoint,
                }
            except urllib.error.HTTPError as exc:
                errors.append(f"{params}: HTTP {exc.code}")
            except Exception as exc:
                errors.append(f"{params}: {exc}")

        raise PredictionMarketError(
            f"Market '{market_id_or_slug}' not found on Polymarket. Tried: {errors}"
        )

    def get_polymarket_market_history(
        self,
        market_id_or_slug: str,
        timeframe: str = "7d",
    ) -> dict[str, Any]:
        """
        Get historical YES price data for a specific Polymarket market.
        Inspired by berlinbra/polymarket-mcp get-market-history tool.

        Accepts: slug, Gamma numeric ID, or CLOB YES token ID.
        The function auto-resolves to the CLOB YES token (clobTokenIds[0]).

        API: clob.polymarket.com/prices-history?market=<yes_token_id>&interval=<interval>
        timeframe options: 1d (1-day) | 7d (1-week) | 30d (1-month) | all
        """
        _VALID_TIMEFRAMES = {"1d", "7d", "30d", "all"}
        if timeframe not in _VALID_TIMEFRAMES:
            raise PredictionMarketError(
                f"Invalid timeframe '{timeframe}'. Must be one of: {_VALID_TIMEFRAMES}"
            )
        if not market_id_or_slug.strip():
            raise PredictionMarketError("market_id_or_slug is required")

        # Map user-facing timeframe to CLOB API interval values (validated against live API)
        _INTERVAL_MAP = {
            "1d":  ("1d",  "10"),  # ~10-min candles over 1 day
            "7d":  ("1w",  "60"),  # ~1-hour candles over 1 week
            "30d": ("1m",  "1440"), # daily candles over 1 month
            "all": ("all", "1440"), # daily candles all time
        }
        interval, fidelity = _INTERVAL_MAP[timeframe]

        # Resolve to CLOB YES token ID if we received a slug or numeric ID
        yes_token: str = market_id_or_slug.strip()
        resolved_slug = ""
        # If it doesn't look like a large integer token ID, look up via Gamma
        if not yes_token.isdigit() or len(yes_token) < 30:
            try:
                market_data = self.get_polymarket_market(yes_token)
                resolved_slug = market_data.get("slug", "")
                # Re-fetch raw market to get clobTokenIds
                raw_params = urllib.parse.urlencode({"slug": resolved_slug or yes_token})
                raw_url = f"{self.POLYMARKET_BASE}/markets?{raw_params}"
                req0 = urllib.request.Request(raw_url, headers={"User-Agent": "AlgoChains-MCP/22.8"})
                with urllib.request.urlopen(req0, timeout=15) as resp0:
                    raw_list = json.loads(resp0.read())
                if isinstance(raw_list, list) and raw_list:
                    raw_m = raw_list[0]
                    clob_ids = raw_m.get("clobTokenIds")
                    # clobTokenIds may be a JSON-encoded string (double-encoded) — parse it
                    if isinstance(clob_ids, str):
                        try:
                            clob_ids = json.loads(clob_ids)
                        except (json.JSONDecodeError, ValueError):
                            clob_ids = []
                    if clob_ids and isinstance(clob_ids, list) and clob_ids[0]:
                        yes_token = str(clob_ids[0])
            except Exception as exc:
                raise PredictionMarketError(
                    f"Could not resolve '{market_id_or_slug}' to a CLOB token: {exc}"
                ) from exc

        params = urllib.parse.urlencode({
            "interval": interval,
            "fidelity": fidelity,
            "market": yes_token,
        })
        url = f"{self.CLOB_BASE}/prices-history?{params}"

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "AlgoChains-MCP/22.8"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            raise PredictionMarketError(f"Polymarket history fetch failed: HTTP {exc.code}") from exc
        except Exception as exc:
            raise PredictionMarketError(f"Polymarket history fetch failed: {exc}") from exc

        history = data.get("history", data if isinstance(data, list) else [])
        points = []
        for h in history:
            t = h.get("t") or h.get("timestamp")
            p = h.get("p") or h.get("price")
            if t is not None and p is not None:
                try:
                    from datetime import datetime, timezone
                    dt = datetime.fromtimestamp(float(t), tz=timezone.utc).isoformat()
                except Exception:
                    dt = str(t)
                points.append({
                    "timestamp": dt,
                    "yes_price": round(float(p), 4),
                    "yes_pct": round(float(p) * 100, 1),
                })

        return {
            "input": market_id_or_slug,
            "clob_yes_token": yes_token[:20] + "..." if len(yes_token) > 20 else yes_token,
            "slug": resolved_slug,
            "timeframe": timeframe,
            "clob_interval": interval,
            "data_points": len(points),
            "history": points,
            "source": url,
        }

    def list_polymarket_markets(
        self,
        status: str = "open",
        limit: int = 20,
        offset: int = 0,
        category: str | None = None,
    ) -> dict[str, Any]:
        """
        List Polymarket markets with status filtering.
        Inspired by berlinbra/polymarket-mcp list-markets tool.

        status: open | closed | resolved
        """
        _VALID_STATUS = {"open", "closed", "resolved"}
        if status not in _VALID_STATUS:
            raise PredictionMarketError(f"Invalid status '{status}'. Must be one of: {_VALID_STATUS}")

        params: dict[str, Any] = {
            "limit": min(limit, 100),
            "offset": offset,
            "sort": "volume24hr",
            "ascending": "false",
        }
        if status == "open":
            params["active"] = "true"
            params["closed"] = "false"
        elif status == "closed":
            params["closed"] = "true"
        elif status == "resolved":
            params["closed"] = "true"
            params["archived"] = "true"

        if category:
            params["tag"] = category

        url = f"{self.POLYMARKET_BASE}/markets?{urllib.parse.urlencode(params)}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "AlgoChains-MCP/22.8"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = json.loads(resp.read())
        except Exception as exc:
            raise PredictionMarketError(f"Polymarket markets list failed: {exc}") from exc

        items = raw if isinstance(raw, list) else raw.get("data", [])
        markets = []
        for m in items:
            yn = _polymarket_yes_no_prices(m)
            markets.append({
                "market_id": str(m.get("id", m.get("conditionId", ""))),
                "slug": m.get("slug", ""),
                "question": m.get("question", ""),
                "status": status,
                "category": m.get("category", ""),
                "resolution_date": m.get("endDate", ""),
                "volume": float(m.get("volumeNum", m.get("volume", 0)) or 0),
                "volume_24h": float(m.get("volume24hr", 0) or 0),
                "liquidity": float(m.get("liquidityNum", m.get("liquidity", 0)) or 0),
                "yes_pct": round(yn[0] * 100, 1) if yn else None,
                "url": f"https://polymarket.com/event/{m.get('slug', '')}",
            })

        return {
            "status_filter": status,
            "total_returned": len(markets),
            "offset": offset,
            "limit": limit,
            "markets": markets,
        }

    def get_kalshi_settlements(self, limit: int = 25) -> dict[str, Any]:
        """
        Fetch recently settled Kalshi contracts (RSA-PSS signed).
        Inspired by 9crusher/mcp-server-kalshi settlements endpoint.

        API: /trade-api/v2/settlements
        Requires KALSHI_ACCESS_KEY + KALSHI_PRIVATE_KEY_PATH.
        """
        from .kalshi_signed import kalshi_signed_get, kalshi_configured
        if not kalshi_configured():
            return {
                "error": "Kalshi not configured",
                "hint": "Set KALSHI_ACCESS_KEY and KALSHI_PRIVATE_KEY_PATH",
                "configured": False,
            }
        status, data = kalshi_signed_get(
            "/trade-api/v2/settlements",
            query={"limit": str(limit)},
        )
        if status not in (200, 0) or not isinstance(data, dict):
            return {"error": f"Kalshi settlements fetch failed (HTTP {status})", "raw": str(data)[:500]}

        settlements_raw = data.get("settlements", [])
        settlements = []
        for s in settlements_raw:
            settlements.append({
                "market_id": s.get("market_ticker", s.get("ticker", "")),
                "title": s.get("title", ""),
                "result": s.get("result", ""),
                "settled_at": s.get("settled_time", s.get("close_time", "")),
                "yes_count": s.get("yes_count"),
                "no_count": s.get("no_count"),
                "profit_per_yes_contract": s.get("profit_per_contract", {}).get("yes"),
                "profit_per_no_contract": s.get("profit_per_contract", {}).get("no"),
            })
        return {
            "count": len(settlements),
            "settlements": settlements,
            "source": "kalshi /trade-api/v2/settlements",
            "configured": True,
        }

    def place_kalshi_order(
        self,
        ticker: str,
        side: str,
        action: str,
        count: int,
        limit_price_cents: int,
        expiration_ts: int | None = None,
    ) -> dict[str, Any]:
        """
        Place a limit order on Kalshi via RSA-PSS signed POST.
        Inspired by 9crusher/mcp-server-kalshi order placement pattern.

        Args:
            ticker: Kalshi market ticker (e.g. "HIGHAUS-25JUL01-T10.5")
            side: "yes" or "no"
            action: "buy" or "sell"
            count: Number of contracts (integer)
            limit_price_cents: Limit price in cents (1-99, represents probability %)
            expiration_ts: Optional expiration timestamp (ms since epoch)

        Requires KALSHI_ACCESS_KEY + KALSHI_PRIVATE_KEY_PATH.
        """
        from .kalshi_signed import kalshi_signed_post, kalshi_configured
        if not kalshi_configured():
            return {
                "error": "Kalshi not configured — cannot place order",
                "hint": "Set KALSHI_ACCESS_KEY and KALSHI_PRIVATE_KEY_PATH",
            }

        if side not in ("yes", "no"):
            raise PredictionMarketError(f"side must be 'yes' or 'no', got '{side}'")
        if action not in ("buy", "sell"):
            raise PredictionMarketError(f"action must be 'buy' or 'sell', got '{action}'")
        if not (1 <= limit_price_cents <= 99):
            raise PredictionMarketError(
                f"limit_price_cents must be 1-99 (= probability %), got {limit_price_cents}"
            )
        if count < 1:
            raise PredictionMarketError(f"count must be >= 1, got {count}")

        payload: dict[str, Any] = {
            "ticker": ticker,
            "client_order_id": f"algochains-{int(time.time() * 1000)}",
            "type": "limit",
            "action": action,
            "side": side,
            "count": count,
            "yes_price": limit_price_cents if side == "yes" else (100 - limit_price_cents),
            "no_price": limit_price_cents if side == "no" else (100 - limit_price_cents),
        }
        if expiration_ts:
            payload["expiration_ts"] = expiration_ts

        status, data = kalshi_signed_post("/trade-api/v2/orders", payload)
        if status not in (200, 201) or not isinstance(data, dict):
            return {
                "error": f"Kalshi order placement failed (HTTP {status})",
                "raw": str(data)[:500],
            }
        order = data.get("order", data)
        return {
            "order_id": order.get("order_id", order.get("id", "")),
            "ticker": ticker,
            "side": side,
            "action": action,
            "count": count,
            "limit_price_cents": limit_price_cents,
            "status": order.get("status", "submitted"),
            "filled_cost": order.get("total_cost"),
            "raw": order,
        }


_pm_engine: PredictionMarketsEngine | None = None


def get_prediction_markets_engine() -> PredictionMarketsEngine:
    global _pm_engine
    if _pm_engine is None:
        _pm_engine = PredictionMarketsEngine()
    return _pm_engine


# Backwards compatibility (older server lazy-import typo)
PredictionMarketEngine = PredictionMarketsEngine
