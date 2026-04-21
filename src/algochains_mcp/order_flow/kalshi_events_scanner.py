"""
Kalshi Events API Scanner — AlgoChains v1.0

CRITICAL: The /markets endpoint only returns KXMVE parlay tickers.
ALL tradeable individual markets are under the /events endpoint.
This module scans the full tradeable universe correctly.

Source: ryanfrigo/kalshi-ai-trading-bot — ingestion pipeline discovery
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

from algochains_mcp.order_flow.kalshi_signed import (
    kalshi_signed_get,
    get_kalshi_orderbook_depth,
)

logger = logging.getLogger("algochains_mcp.order_flow.kalshi_events_scanner")

# Category → Kalshi event_ticker prefix mapping.
# Broadened to capture the full tradeable universe. Add new prefixes here as
# Kalshi launches new series — the scanner uses startswith() matching so exact
# ticker formats do not need to be known in advance.
CATEGORY_PREFIXES: dict[str, list[str]] = {
    "sports": [
        "KXNCAAB", "KXNBA", "KXNFL", "KXMLB", "KXNHL", "KXNASCAR",
        "KXSOCCER", "KXMLS", "KXPGA", "KXGOLF", "KXTENNIS", "KXATP",
        "KXWTA", "KXMMA", "KXUFC", "KXBOXING", "KXNCAAF", "KXCFB",
        "KXOLYMPIC", "KXNWSL", "KXWNBA", "KXFORMULA", "KXCRICKET",
    ],
    "politics": [
        "KXPRES", "KXSENA", "KXHOUSE", "KXGOV", "KXLEGAL",
        "KXELECT", "KXPOLL", "KXAPPROVAL", "KXCONGRESS", "KXWORLD",
        "KXSUPREME", "KXPOTUS", "KXVP",
    ],
    "weather": ["KXHURR", "KXSN", "KXTEMP", "KXRAIN", "KXWILD", "KXSTORM"],
    "finance": [
        "KXBTC", "KXETH", "KXSPY", "KXINX", "KXNASD", "KXOIL",
        "KXGOLD", "KXSILVER", "KXDXY", "KXVIX", "KXNQ", "KXDOW",
        "KXRUS", "KXCRYPTO", "KXSOL", "KXBNB", "KXDOGE",
    ],
    "culture": [
        "KXOSCARS", "KXEMMY", "KXGRAMMY", "KXAWARD", "KXPOP",
        "KXMOVIE", "KXBOX", "KXCHARTS", "KXMUSIC",
    ],
    "tech": ["KXAI", "KXTECH", "KXAPPLE", "KXGOOG", "KXAMZN", "KXMETA", "KXNVDA"],
    # DO NOT TRADE — economic series have structural negative edge (see BLOCKED_SERIES in
    # kalshi_strategy_engine.py for per-series win-rate data). KXECON catches any
    # catch-all economic series not enumerated above.
    "econ_blocked": ["KXFED", "KXCPI", "KXNFP", "KXGDP", "KXUNRATE", "KXECON"],
}

SPORTS_CATEGORIES = set(CATEGORY_PREFIXES["sports"])

# P0-B FIX: also import and merge the authoritative BLOCKED_SERIES from the strategy
# engine so both modules enforce the same block list. Fallback to empty set on import
# error to prevent scanner startup failures.
try:
    from algochains_mcp.order_flow.kalshi_strategy_engine import BLOCKED_SERIES as _ENGINE_BLOCKED
except Exception:  # pragma: no cover
    _ENGINE_BLOCKED = set()  # type: ignore[assignment]

BLOCKED_PREFIXES: set[str] = set(CATEGORY_PREFIXES["econ_blocked"]) | _ENGINE_BLOCKED


def _classify_series(event_ticker: str) -> str:
    """Classify an event ticker into a high-level category."""
    t = event_ticker.upper()
    for category, prefixes in CATEGORY_PREFIXES.items():
        for prefix in prefixes:
            if t.startswith(prefix):
                return category
    return "other"


def scan_all_events(
    limit_per_page: int = 100,
    max_pages: int = 20,
    min_markets_per_event: int = 1,
    categories: Optional[list[str]] = None,
) -> dict[str, Any]:
    """
    Scan ALL open Kalshi events via the Events API (correct endpoint for full universe).

    Returns:
        - events_by_category: dict mapping category → list of event dicts
        - total_events: total event count
        - total_markets: total individual market count
        - fetched_at: ISO timestamp
    """
    all_events: list[dict[str, Any]] = []
    cursor = None
    pages_fetched = 0
    auth_error: Optional[str] = None

    while pages_fetched < max_pages:
        params: dict[str, str] = {
            "status": "open",
            "limit": str(limit_per_page),
            "with_nested_markets": "true",
        }
        if cursor:
            params["cursor"] = cursor

        code, data = kalshi_signed_get("/trade-api/v2/events", params)
        # P1-5 FIX: non-dict response signals an auth/network error — surface it
        # instead of silently breaking with an empty universe.
        if not isinstance(data, dict):
            auth_error = f"non-dict response (code={code}): {str(data)[:200]}"
            logger.error("Events API auth/network error — aborting scan: %s", auth_error)
            break

        batch = data.get("events", [])
        if not batch:
            break

        all_events.extend(batch)
        cursor = data.get("cursor")
        pages_fetched += 1

        if not cursor:
            break
        time.sleep(0.05)  # Rate-limit safety

    # Classify and organize
    events_by_category: dict[str, list[dict[str, Any]]] = {}
    total_markets = 0

    for event in all_events:
        ticker = event.get("event_ticker", "")
        category = _classify_series(ticker)

        # Skip blocked economic series
        is_blocked = any(ticker.upper().startswith(p) for p in BLOCKED_PREFIXES)
        if is_blocked:
            continue

        # Filter by requested categories
        if categories and category not in categories:
            continue

        markets = event.get("markets", [])
        if len(markets) < min_markets_per_event:
            continue

        total_markets += len(markets)

        if category not in events_by_category:
            events_by_category[category] = []

        events_by_category[category].append({
            "event_ticker": ticker,
            "title": event.get("title", ""),
            "category": category,
            "close_time": event.get("close_time", ""),
            "markets": markets,
            "market_count": len(markets),
        })

    # P1-5 FIX: if an auth/network error occurred, surface it so callers know
    # this is not an "empty market" situation.
    if auth_error and not all_events:
        return {
            "status": "error",
            "error": auth_error,
            "pages_fetched": pages_fetched,
            "total_events": 0,
            "total_events_active": 0,
            "total_markets": 0,
            "events_by_category": {},
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    return {
        "status": "ok",
        "pages_fetched": pages_fetched,
        "total_events": len(all_events),
        "total_events_active": sum(len(v) for v in events_by_category.values()),
        "total_markets": total_markets,
        "events_by_category": events_by_category,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def scan_sports_markets(
    include_orderbook: bool = False,
    min_yes_bid: float = 0.01,
    max_yes_price: float = 0.85,
) -> list[dict[str, Any]]:
    """
    Scan all open sports markets — the highest-edge category on Kalshi.

    For NO-side strategy: focus on markets where YES price ≤ 20¢
    (near-certain NO outcome).

    Args:
        include_orderbook: if True, fetches orderbook depth for each market
        min_yes_bid: minimum YES bid to consider (filters empty books)
        max_yes_price: maximum YES ask price to consider (filters markets where YES is expensive)

    Returns list of market dicts sorted by NO opportunity score.
    """
    result = scan_all_events(categories=["sports"])
    sports_events = result.get("events_by_category", {}).get("sports", [])

    markets_out: list[dict[str, Any]] = []
    for event in sports_events:
        for market in event.get("markets", []):
            ticker = market.get("ticker", "")
            yes_bid = market.get("yes_bid", 0) / 100.0 if market.get("yes_bid") else None
            yes_ask = market.get("yes_ask", 0) / 100.0 if market.get("yes_ask") else None

            if yes_bid is None:
                continue

            if yes_bid < min_yes_bid:
                continue

            entry: dict[str, Any] = {
                "ticker": ticker,
                "event_ticker": event["event_ticker"],
                "title": market.get("title", event.get("title", "")),
                "category": "sports",
                "series": event["event_ticker"].split("-")[0] if "-" in event["event_ticker"] else event["event_ticker"],
                "close_time": market.get("close_time", event.get("close_time", "")),
                "yes_bid": yes_bid,
                "yes_ask": yes_ask,
                "volume": market.get("volume", 0),
                "open_interest": market.get("open_interest", 0),
            }

            if include_orderbook and ticker:
                try:
                    ob = get_kalshi_orderbook_depth(ticker, depth=3)
                    entry["best_bid"] = ob.get("best_bid")
                    entry["best_ask"] = ob.get("best_ask")
                    entry["spread"] = ob.get("spread")
                    time.sleep(0.05)
                except Exception as exc:
                    logger.debug("Orderbook fetch failed for %s: %s", ticker, exc)

            markets_out.append(entry)

    # Sort by ascending YES price (cheapest YES = best NO opportunity)
    markets_out.sort(key=lambda m: m.get("yes_bid", 1.0))
    return markets_out


def scan_full_universe_summary() -> dict[str, Any]:
    """
    Quick summary scan of the full Kalshi universe.
    Returns category counts, total markets, and top categories by market count.
    Does not fetch orderbook data.
    """
    result = scan_all_events(max_pages=10)
    by_cat = result.get("events_by_category", {})

    summary: list[dict[str, Any]] = []
    for category, events in by_cat.items():
        total_m = sum(e["market_count"] for e in events)
        summary.append({
            "category": category,
            "event_count": len(events),
            "market_count": total_m,
        })

    summary.sort(key=lambda x: x["market_count"], reverse=True)

    return {
        "status": "ok",
        "categories": summary,
        "total_non_blocked_events": result["total_events_active"],
        "total_non_blocked_markets": result["total_markets"],
        "fetched_at": result["fetched_at"],
        "note": "FED/CPI/NFP/GDP series are excluded (proven negative edge)"
    }
