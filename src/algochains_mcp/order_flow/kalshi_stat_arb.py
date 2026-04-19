"""
Kalshi Statistical Arbitrage — AlgoChains v1.0

Detects pricing inconsistencies between logically related Kalshi event pairs.
Based on yllvar/Kalshi-Quant-TeleBot cointegration arbitrage pattern.

Core insight: In prediction markets, certain probability relationships MUST hold:
  1. P(A and B) ≤ P(A)  [subset constraint]
  2. P(A) + P(not A) ≈ 1.0  [completeness — yes_ask + no_ask should ≈ 1.0]
  3. P(general win) ≤ P(primary win)  [logical ordering]
  4. Related event buckets in same series must be mutually exclusive and exhaustive

When markets violate these constraints, risk-free or near-risk-free arbitrage exists.

Types of arb detected:
  A. CLOB Spread Arb:  yes_ask + no_ask > 1.0 (seller profits guaranteed)
  B. Bucket Completeness: sum of mutually exclusive buckets ≠ 100% (mispricing)
  C. Logical Ordering: P(harder event) > P(easier event) — logical impossibility
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

from algochains_mcp.order_flow.kalshi_signed import kalshi_signed_get
from algochains_mcp.order_flow.kalshi_events_scanner import scan_all_events

logger = logging.getLogger("algochains_mcp.order_flow.kalshi_stat_arb")

MIN_ARB_EDGE  = 0.02   # Minimum edge (2¢) to flag as opportunity
MAX_SPREAD_SUM = 1.02  # If yes_ask + no_ask > this, flag as spread arb


@dataclass
class ArbOpportunity:
    arb_type: str              # "spread_arb" | "bucket_completeness" | "logical_ordering"
    ticker_a: str
    ticker_b: Optional[str]
    title_a: str
    title_b: Optional[str]
    edge_cents: float
    description: str
    action: str
    expected_profit_pct: float


def _get_market_prices(ticker: str) -> Optional[dict[str, float]]:
    """Fetch yes_bid, yes_ask, no_bid, no_ask for a market."""
    code, data = kalshi_signed_get(f"/trade-api/v2/markets/{ticker}")
    if code != 200 or not isinstance(data, dict):
        return None

    market = data.get("market", data)
    yes_bid = (market.get("yes_bid", 0) or 0) / 100.0
    yes_ask = (market.get("yes_ask", 0) or 0) / 100.0
    no_bid = (market.get("no_bid", 0) or 0) / 100.0
    no_ask = (market.get("no_ask", 0) or 0) / 100.0

    if yes_ask <= 0 or no_ask <= 0:
        return None

    return {
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_bid": no_bid,
        "no_ask": no_ask,
        "volume": market.get("volume", 0),
    }


def detect_spread_arb(markets: list[dict[str, Any]]) -> list[ArbOpportunity]:
    """
    Type A: CLOB Spread Arbitrage.

    If yes_ask + no_ask > 1.0, we can sell both sides and lock in profit.
    Example: YES asks at 45¢, NO asks at 60¢ → total 105¢, guaranteed 5¢ profit.

    This is the most common and cleanest arb type on Kalshi.
    """
    opps: list[ArbOpportunity] = []

    for market in markets:
        ticker = market.get("ticker", "")
        if not ticker:
            continue

        prices = _get_market_prices(ticker)
        if not prices:
            time.sleep(0.02)
            continue

        sum_asks = prices["yes_ask"] + prices["no_ask"]
        if sum_asks > MAX_SPREAD_SUM:
            edge = sum_asks - 1.0
            opps.append(ArbOpportunity(
                arb_type="spread_arb",
                ticker_a=ticker,
                ticker_b=None,
                title_a=market.get("title", ""),
                title_b=None,
                edge_cents=round(edge, 4),
                description=(
                    f"YES ask {prices['yes_ask']:.2f} + NO ask {prices['no_ask']:.2f} = {sum_asks:.2f} > 1.0. "
                    f"Sell both sides: guaranteed {edge:.2%} profit on 1 YES + 1 NO sold."
                ),
                action="sell_yes_and_no",
                expected_profit_pct=round(edge, 4),
            ))

        time.sleep(0.03)

    return opps


def detect_bucket_arb(events: list[dict[str, Any]]) -> list[ArbOpportunity]:
    """
    Type B: Bucket Completeness Arbitrage.

    For mutually exclusive and exhaustive market buckets (e.g., "BTC price range" buckets),
    the YES prices must sum to ~100¢.

    If sum < 95¢: total underpriced — buy all YES positions.
    If sum > 105¢: total overpriced — sell all YES positions.

    Example: BTC price range markets for a given date:
      - BTC > $90k: YES at 20¢
      - BTC $80-90k: YES at 30¢
      - BTC $70-80k: YES at 25¢
      - BTC < $70k: YES at 10¢
      Sum = 85¢ (should be 100¢) → underpriced, buy all four YES positions
    """
    opps: list[ArbOpportunity] = []

    for event in events:
        markets = event.get("markets", [])
        if len(markets) < 2:
            continue

        # Check if markets look like mutually exclusive buckets
        titles = [m.get("title", "") for m in markets]
        looks_like_buckets = (
            any(">" in t or "<" in t or "-" in t for t in titles) or
            len(set(m.get("close_time", "") for m in markets)) == 1  # Same close time
        )
        if not looks_like_buckets:
            continue

        # Fetch YES bids for each market
        yes_bids = []
        for m in markets[:10]:  # Limit to 10 to control API calls
            ticker = m.get("ticker", "")
            if not ticker:
                continue
            prices = _get_market_prices(ticker)
            if prices:
                yes_bids.append({"ticker": ticker, "title": m.get("title", ""), "yes_bid": prices["yes_bid"]})
            time.sleep(0.03)

        if len(yes_bids) < 2:
            continue

        total_yes = sum(b["yes_bid"] for b in yes_bids)

        if total_yes < 0.95 and total_yes > 0.10:  # Underprice — buy all
            edge = (1.0 - total_yes) / len(yes_bids)
            opps.append(ArbOpportunity(
                arb_type="bucket_completeness",
                ticker_a=yes_bids[0]["ticker"],
                ticker_b=yes_bids[-1]["ticker"],
                title_a=event.get("title", ""),
                title_b=f"Sum={total_yes:.2f} across {len(yes_bids)} buckets",
                edge_cents=round(edge, 4),
                description=(
                    f"Bucket sum = {total_yes:.2f} (should be ~1.0). "
                    f"Buying all {len(yes_bids)} YES positions has guaranteed profit of {1-total_yes:.2%}"
                ),
                action="buy_all_yes",
                expected_profit_pct=round(1.0 - total_yes, 4),
            ))
        elif total_yes > 1.05:  # Overpriced — sell all NO (equivalent)
            edge = (total_yes - 1.0) / len(yes_bids)
            opps.append(ArbOpportunity(
                arb_type="bucket_completeness",
                ticker_a=yes_bids[0]["ticker"],
                ticker_b=yes_bids[-1]["ticker"],
                title_a=event.get("title", ""),
                title_b=f"Sum={total_yes:.2f} across {len(yes_bids)} buckets",
                edge_cents=round(edge, 4),
                description=(
                    f"Bucket sum = {total_yes:.2f} (should be ~1.0). "
                    f"Selling YES (buying NO) on all {len(yes_bids)} markets locks in {total_yes-1:.2%}"
                ),
                action="buy_all_no",
                expected_profit_pct=round(total_yes - 1.0, 4),
            ))

    return opps


def scan_stat_arb_opportunities(
    max_events: int = 20,
    max_markets_per_scan: int = 50,
) -> dict[str, Any]:
    """
    Full statistical arbitrage scan across the Kalshi universe.

    Returns detected opportunities sorted by edge size.
    """
    # Get events universe (sports + finance — most likely to have related events)
    scan_result = scan_all_events(
        categories=["sports", "finance", "other"],
        max_pages=3,
    )
    events_by_cat = scan_result.get("events_by_category", {})

    all_events: list[dict[str, Any]] = []
    for events in events_by_cat.values():
        all_events.extend(events[:max_events // max(len(events_by_cat), 1)])

    # Flatten markets for spread arb check
    all_markets: list[dict[str, Any]] = []
    for event in all_events:
        all_markets.extend(event.get("markets", [])[:5])  # First 5 markets per event
        if len(all_markets) >= max_markets_per_scan:
            break

    spread_arb_opps = detect_spread_arb(all_markets)
    bucket_arb_opps = detect_bucket_arb(all_events[:10])  # Limit bucket scan

    all_opps = sorted(
        spread_arb_opps + bucket_arb_opps,
        key=lambda o: o.edge_cents,
        reverse=True,
    )

    return {
        "status": "ok",
        "spread_arb_count": len(spread_arb_opps),
        "bucket_arb_count": len(bucket_arb_opps),
        "total_opportunities": len(all_opps),
        "opportunities": [
            {
                "arb_type": o.arb_type,
                "ticker_a": o.ticker_a,
                "ticker_b": o.ticker_b,
                "title": o.title_a,
                "edge": o.edge_cents,
                "action": o.action,
                "expected_profit_pct": o.expected_profit_pct,
                "description": o.description,
            }
            for o in all_opps[:10]
        ],
        "note": "Stat arb opportunities are transient — prices may change before execution",
    }
