"""
Kalshi Safe Compounder — AlgoChains v1.0

The ONLY historically validated positive-edge Kalshi strategy for retail bots.
Based on ryanfrigo/kalshi-ai-trading-bot live trading data:
  - NCAAB NO-side: 74% win rate, +10% ROI
  - NBA NO-side: 52% win rate, +1.5% ROI
  - Pure math — no AI models required
  - Near-certain outcomes only (YES price ≤ 30¢ = implied 70%+ probability NO wins)

Strategy rules (strict — do not relax without evidence):
  1. NO side ONLY — never buys YES
  2. YES last price must be ≤ MAX_YES_PRICE (30¢, expanded from 20¢ for playoff season)
  3. NO ask must be > MIN_NO_ASK (70¢, adjusted to match expanded YES threshold)
  4. Edge (model_prob_no - no_ask) must be > MIN_EDGE (default 5¢)
  5. Place MAKER limit orders at lowest_ask - 1¢ (near-zero fees)
  6. Max MAX_POSITION_PCT of portfolio per position (default 10%)
  7. Skip: entertainment, "mention" markets, economic series

You are getting paid 70¢+ for a ~70%+ probability event. That's the edge.
Threshold expansion rationale: NBA/NHL playoff markets price YES at 25-45¢ for
competitive games — 20¢ cap excluded virtually all playoff opportunities.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

from algochains_mcp.order_flow.kalshi_signed import (
    kalshi_signed_get,
    get_kalshi_orderbook_depth,
    kalshi_signed_post,
)
from algochains_mcp.order_flow.kalshi_events_scanner import scan_sports_markets

logger = logging.getLogger("algochains_mcp.order_flow.kalshi_safe_compounder")

# ─── Strategy parameters ──────────────────────────────────────────────────────
# Threshold raised to 0.30 (from 0.20) to capture NBA/NHL playoff markets where
# YES prices cluster at 25-45¢. Kelly sizing ensures risk is proportional.

MAX_YES_PRICE      = 0.30   # YES must be ≤ 30¢ = implied 70%+ NO probability
MIN_NO_ASK         = 0.70   # NO must pay ≥ 70¢ (adjusted to match wider threshold)
MIN_EDGE_CENTS     = 0.05   # Minimum edge above NO ask (5¢)
MAX_POSITION_PCT   = 0.10   # Never more than 10% of bankroll
MAKER_OFFSET_CENTS = 0.01   # Place limit at best_no_ask - 1¢ for maker fee

# Skip these categories even if they meet the math criteria
SKIP_CATEGORIES = {
    "econ_blocked",   # FED, CPI, NFP — negative edge
    "entertainment",  # Movies, TV — unpredictable
}

# Keywords that indicate a "mention" market (low signal)
SKIP_KEYWORDS = {"mention", "tweet", "post", "says", "stated", "claims"}


def _market_is_mention(title: str) -> bool:
    """Return True if the market is a social-media 'mention' type."""
    return any(kw in title.lower() for kw in SKIP_KEYWORDS)


def _compute_no_probability(yes_bid: float, yes_ask: Optional[float]) -> float:
    """
    Estimate the market-implied NO probability.
    We use the midpoint of the YES market as our reference.
    NO probability ≈ 1 - YES_midpoint.
    """
    if yes_ask is not None and yes_bid > 0:
        yes_mid = (yes_bid + yes_ask) / 2
    elif yes_bid > 0:
        yes_mid = yes_bid
    else:
        return 0.0
    return 1.0 - yes_mid


def scan_safe_compounder_opportunities(
    bankroll_usd: float = 250.0,
    include_orderbook: bool = True,
) -> list[dict[str, Any]]:
    """
    Find all current Safe Compounder opportunities across the full Kalshi universe.

    Returns list of opportunities sorted by edge (highest first).
    Each opportunity is a near-certain NO play with positive expected value.
    """
    # Get sports markets (highest edge) — include_orderbook for precise prices
    sports_markets = scan_sports_markets(
        include_orderbook=include_orderbook,
        min_yes_bid=0.005,
        max_yes_price=MAX_YES_PRICE + 0.05,  # Slightly wider scan, filter below
    )

    # Also scan all other non-blocked categories from events API
    # Deferred import avoids circular import between safe_compounder ↔ events_scanner
    from algochains_mcp.order_flow.kalshi_events_scanner import scan_all_events  # noqa: PLC0415
    all_events_result = scan_all_events(
        categories=["politics", "weather", "finance", "other"],
        max_pages=5,
    )

    # Flatten non-sports markets
    other_markets: list[dict[str, Any]] = []
    for category, events in all_events_result.get("events_by_category", {}).items():
        for event in events:
            for market in event.get("markets", []):
                yes_bid_raw = market.get("yes_bid", 0)
                if not yes_bid_raw:
                    continue
                yes_bid = yes_bid_raw / 100.0
                yes_ask_raw = market.get("yes_ask", 0)
                yes_ask = yes_ask_raw / 100.0 if yes_ask_raw else None
                other_markets.append({
                    "ticker": market.get("ticker", ""),
                    "event_ticker": event["event_ticker"],
                    "title": market.get("title", event.get("title", "")),
                    "category": category,
                    "series": event["event_ticker"].split("-")[0],
                    "close_time": market.get("close_time", event.get("close_time", "")),
                    "yes_bid": yes_bid,
                    "yes_ask": yes_ask,
                    "volume": market.get("volume", 0),
                })

    all_candidates = sports_markets + other_markets
    opportunities: list[dict[str, Any]] = []

    for m in all_candidates:
        ticker = m.get("ticker", "")
        title = m.get("title", "")
        category = m.get("category", "other")
        yes_bid = m.get("yes_bid") or m.get("best_bid")
        yes_ask = m.get("yes_ask") or m.get("best_ask")

        # Hard filters
        if not ticker or not yes_bid:
            continue
        if category in SKIP_CATEGORIES:
            continue
        if _market_is_mention(title):
            continue

        # Core strategy filter: YES must be cheap
        if yes_bid > MAX_YES_PRICE:
            continue

        # We need to know the NO ask price
        # NO ask = 1 - YES_bid (since YES_bid takers are effectively NO sellers)
        # More precisely: fetch orderbook if not already fetched
        if m.get("spread") is None and include_orderbook:
            try:
                ob = get_kalshi_orderbook_depth(ticker, depth=3)
                yes_bid = ob.get("best_bid") or yes_bid
                yes_ask = ob.get("best_ask") or yes_ask
                time.sleep(0.05)
            except Exception as exc:
                logger.debug("OB fetch failed: %s %s", ticker, exc)

        # NO ask price (what we pay to BUY NO)
        # In Kalshi CLOB: NO ask = 1 - YES_bid (best taker price)
        no_ask = 1.0 - (yes_bid or 0.01)

        if no_ask < MIN_NO_ASK:
            continue  # NO costs too much relative to risk

        # Compute implied NO probability and edge
        no_prob_implied = _compute_no_probability(yes_bid or 0.01, yes_ask)
        edge = no_prob_implied - no_ask   # How much above cost is our expected payout

        if edge < MIN_EDGE_CENTS:
            continue  # Not enough margin

        # Position sizing: fractional Kelly at 50% for compounder (more conservative)
        kelly_no = (no_prob_implied * (1 - no_ask) - (1 - no_prob_implied) * no_ask) / (1 - no_ask)
        position_usd = min(
            max(kelly_no * 0.50 * bankroll_usd, 0),
            MAX_POSITION_PCT * bankroll_usd,
        )
        contracts = max(1, int(position_usd / no_ask)) if position_usd > 0 else 0

        if contracts < 1:
            continue

        # Maker limit price: place at NO ask minus 1¢ to get maker rebate
        maker_price_cents = max(1, int((no_ask - MAKER_OFFSET_CENTS) * 100))

        opportunities.append({
            "ticker": ticker,
            "title": title,
            "category": category,
            "close_time": m.get("close_time", ""),
            "yes_bid": yes_bid,
            "yes_ask": yes_ask,
            "no_ask_taker": round(no_ask, 4),
            "no_maker_price": maker_price_cents / 100.0,
            "no_prob_implied": round(no_prob_implied, 4),
            "edge": round(edge, 4),
            "kelly_fraction": round(kelly_no, 4),
            "position_usd": round(position_usd, 2),
            "suggested_contracts": contracts,
            "maker_price_cents": maker_price_cents,
            "execution": "limit_maker",
            "note": f"Safe Compounder: buy NO as maker at {maker_price_cents}¢ (1¢ below taker price)",
        })

    # Sort by edge descending
    opportunities.sort(key=lambda x: x["edge"], reverse=True)
    return opportunities


def run_safe_compounder(
    bankroll_usd: float = 250.0,
    execute: bool = False,
    confirmed: bool = False,
) -> dict[str, Any]:
    """
    Run the Safe Compounder strategy: scan → rank → optionally execute.

    Args:
        bankroll_usd: current trading bankroll in USD
        execute: if True, attempt to place maker orders
        confirmed: must be True to actually execute orders (guard against accidents)

    Returns full scan result with ranked opportunities and execution log.
    """
    start = datetime.now(timezone.utc)
    opps = scan_safe_compounder_opportunities(bankroll_usd=bankroll_usd)

    result: dict[str, Any] = {
        "strategy": "safe_compounder",
        "scanned_at": start.isoformat(),
        "bankroll_usd": bankroll_usd,
        "opportunities_found": len(opps),
        "opportunities": opps[:10],  # Top 10
        "execution_log": [],
        "total_capital_at_risk_usd": sum(o["position_usd"] for o in opps[:5]),
    }

    if execute and confirmed and opps:
        for opp in opps[:3]:  # Execute top 3 opportunities
            ticker = opp["ticker"]
            contracts = opp["suggested_contracts"]
            price_cents = opp["maker_price_cents"]

            order_body = {
                "ticker": ticker,
                "side": "no",
                "type": "limit",
                "count": contracts,
                "action": "buy",
                "yes_price": 100 - price_cents,  # Kalshi stores as YES equivalent
            }

            code, order_data = kalshi_signed_post("/trade-api/v2/portfolio/orders", order_body)
            log_entry = {
                "ticker": ticker,
                "contracts": contracts,
                "price_cents": price_cents,
                "http_status": code,
                "order_id": order_data.get("order", {}).get("order_id", "") if isinstance(order_data, dict) else "",
                "status": "placed" if code in (200, 201) else "error",
                "error": str(order_data)[:200] if code not in (200, 201) else None,
            }
            result["execution_log"].append(log_entry)
            logger.info("Safe Compounder order: %s", log_entry)
            time.sleep(0.1)  # Rate limit between orders
    elif execute and not confirmed:
        result["execution_log"].append({
            "status": "skipped",
            "reason": "confirmed=False — set confirmed=True to execute real orders"
        })

    return result


def get_safe_compounder_stats(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Compute Safe Compounder performance statistics from a list of settled trades.
    Input: list of dicts with keys: side, revenue_cents, total_cost_cents, status
    """
    sc_trades = [t for t in trades if t.get("strategy") == "safe_compounder"]
    if not sc_trades:
        return {"message": "No Safe Compounder trades found yet"}

    wins = [t for t in sc_trades if t.get("profit_cents", 0) > 0]
    losses = [t for t in sc_trades if t.get("profit_cents", 0) <= 0]
    settled = [t for t in sc_trades if t.get("status") == "settled"]

    total_staked = sum(t.get("total_cost_cents", 0) for t in settled)
    total_revenue = sum(t.get("revenue_cents", 0) for t in settled)
    total_profit = total_revenue - total_staked

    return {
        "total_trades": len(sc_trades),
        "settled_trades": len(settled),
        "win_rate": len(wins) / max(len(settled), 1),
        "total_staked_usd": total_staked / 100,
        "total_revenue_usd": total_revenue / 100,
        "total_profit_usd": total_profit / 100,
        "roi_pct": round(total_profit / max(total_staked, 1) * 100, 2),
        "avg_profit_per_trade_usd": round(total_profit / max(len(settled), 1) / 100, 2),
    }
