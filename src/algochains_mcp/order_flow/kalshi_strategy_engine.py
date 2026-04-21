"""
Kalshi Prediction Market Strategy Engine — AlgoChains v1.0

Strategies:
  1. Macro-edge scanner  — finds FED/CPI/NFP markets where model probability
     diverges from market-implied price by ≥ EDGE_THRESHOLD.
  2. Kelly position sizer — fractional Kelly (25%) on each identified edge.
  3. Market maker scout  — identifies markets with wide spreads suitable for
     passive limit-order market-making.
  4. Cross-platform arb  — compares Kalshi YES prices vs Polymarket (REST v2).

Growth path: $250 → $1000+ over 60-90 days via disciplined positive-EV bets.

All amounts in USD (float). Kalshi API balance is in cents internally; this
module converts automatically.
"""
from __future__ import annotations

import logging
import math
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from algochains_mcp.order_flow.kalshi_signed import (
    kalshi_signed_get,
    kalshi_signed_post,
    get_kalshi_orderbook_depth,
)

logger = logging.getLogger("algochains_mcp.order_flow.kalshi_strategy_engine")

# ─── Constants ────────────────────────────────────────────────────────────────

FRACTIONAL_KELLY = 0.25      # Use 25% of full Kelly to limit ruin risk
MAX_POSITION_PCT = 0.10      # Never risk >10% of bankroll on a single bet
MIN_EDGE = 0.06              # Minimum probability edge to consider (6 cents)
MIN_SPREAD_FOR_MM = 0.12     # Min spread to consider market-making
MAKER_FEE = 0.00             # Kalshi maker fee (0% for limit orders)
TAKER_FEE = 0.07             # Kalshi taker fee (7% of profit)
POLYMARKET_API = "https://gamma-api.polymarket.com"

# ─── CATEGORY DISCIPLINE (from ryanfrigo live-trading data) ──────────────────
# Live trading data across 100+ trades shows these series have STRUCTURAL negative edge.
# Economic releases are already efficiently priced — retail bots cannot compete.
# DO NOT REMOVE THESE BLOCKS without 50+ live trades showing positive ROI.
BLOCKED_SERIES: set[str] = {
    "KXFED",      # FED decisions: 32% WR, -40% ROI — market is efficient
    "KXCPI",      # CPI releases:  25% WR, -65% ROI — already priced by pros
    "KXNFP",      # Non-farm payrolls: structural negative edge
    "KXGDP",      # GDP: same problem — economic data efficiently priced
    "KXUNRATE",   # Unemployment: efficient market, no structural edge
    "KXECON",     # Any other econ series
}

# High-edge series (from live trading validation data)
# Sports NO-side: NCAAB 74% WR +10% ROI, NBA 52% WR +1.5% ROI
HIGH_EDGE_SERIES: set[str] = {
    "KXNCAAB",    # College basketball (March-April peak)
    "KXNBA",      # NBA (Oct-June)
    "KXNFL",      # NFL (Sep-Jan)
    "KXMLB",      # MLB (Apr-Oct)
    "KXNHL",      # NHL (Oct-Jun)
}

# Macro market series tickers — DEPRECATED as primary focus
# Kept for model coverage only; all blocked above
MACRO_SERIES: dict[str, str] = {
    # REMOVED: FED/CPI/NFP — proven negative edge
    # Only include if we develop a validated model for them
}

# Fed funds futures implied probabilities — sourced from CME FedWatch consensus (Apr 2026)
# Current Fed funds rate: 4.25-4.50% (upper bound = 4.50%)
# Market pricing: ~2-4 cuts expected by end 2027 due to tariff-driven slowdown
# Format: { "YYYY-MM-DD": most_likely_upper_bound }  (upper bound after that FOMC meeting)
# Updated: 2026-04-18 — next update required after each FOMC statement
FEDWATCH_ESTIMATES: dict[str, float] = {
    "2026-05-07": 4.50,   # May 2026 — hold near-certain (~95%)
    "2026-06-18": 4.25,   # June 2026 — first cut priced ~55%; expected to 4.25%
    "2026-07-30": 4.25,   # July — hold after first cut
    "2026-09-17": 4.00,   # Sep 2026 — second cut priced ~60%
    "2026-11-05": 4.00,   # Nov — likely hold
    "2026-12-16": 3.75,   # Dec 2026 — third cut priced ~50%
    "2027-01-29": 3.75,
    "2027-03-19": 3.75,
    "2027-04-30": 3.50,   # Apr 2027 — fourth cut priced ~45%
    "2027-06-17": 3.50,
    "2027-09-16": 3.25,
}

# Confidence interval: ±0.25% at each level (one cut uncertainty)
# YES for KXFED-X-T{threshold} = "upper bound > threshold after that meeting"
# Key insight: upper bound 4.50 → one cut → 4.25 (not above 4.25 → NO wins)


# ─── Data structures ──────────────────────────────────────────────────────────

@dataclass
class KalshiEdge:
    ticker: str
    title: str
    close_time: str
    model_prob: float       # Our model's probability of YES
    market_bid: float       # Best YES bid in market
    market_ask: float       # Best YES ask in market (1 - best NO bid)
    edge_yes: float         # model_prob - market_ask  (positive = buy YES)
    edge_no: float          # (1 - model_prob) - (1 - market_bid)  (positive = buy NO)
    spread: float
    best_action: str        # "buy_yes", "buy_no", "market_make", "skip"
    kelly_fraction: float   # Optimal Kelly fraction
    suggested_usd: float    # Suggested dollar amount at fractional Kelly
    confidence: str         # "high" | "medium" | "low"
    notes: str = ""


@dataclass
class KalshiAccountState:
    balance_usd: float
    positions: list[dict[str, Any]] = field(default_factory=list)
    open_orders: list[dict[str, Any]] = field(default_factory=list)
    fetched_at: str = ""


# ─── Account ──────────────────────────────────────────────────────────────────

def get_account_state() -> KalshiAccountState:
    """Fetch live balance, positions, and open orders.

    Raises RuntimeError on network failure or unexpected API response so callers
    can distinguish a genuine $0 balance from a transient read error.
    """
    code_b, data_b = kalshi_signed_get("/trade-api/v2/portfolio/balance")
    if code_b == 0:
        raise RuntimeError(
            f"Kalshi balance API network error (code=0): {str(data_b)[:200]}"
        )
    if not isinstance(data_b, dict):
        raise RuntimeError(
            f"Kalshi balance API unexpected response (code={code_b}): {str(data_b)[:200]}"
        )
    balance_cents = data_b.get("balance", 0)
    balance_usd = balance_cents / 100.0

    code_p, data_p = kalshi_signed_get("/trade-api/v2/portfolio/positions", {"count": "100"})
    positions = []
    if isinstance(data_p, dict):
        positions = data_p.get("market_positions", data_p.get("positions", []))

    code_o, data_o = kalshi_signed_get("/trade-api/v2/portfolio/orders", {"status": "open", "limit": "50"})
    open_orders = []
    if isinstance(data_o, dict):
        open_orders = data_o.get("orders", [])

    return KalshiAccountState(
        balance_usd=balance_usd,
        positions=positions,
        open_orders=open_orders,
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )


# ─── Market scanner ───────────────────────────────────────────────────────────

def scan_macro_markets(limit_per_series: int = 20) -> list[dict[str, Any]]:
    """
    Pull all open markets for macro series (FED, CPI, NFP, etc.).
    Returns markets with orderbook depth fetched.
    """
    results = []
    for series_ticker, description in MACRO_SERIES.items():
        code, data = kalshi_signed_get("/trade-api/v2/markets", {
            "status": "open",
            "series_ticker": series_ticker,
            "limit": str(limit_per_series),
        })
        if not isinstance(data, dict):
            continue
        markets = data.get("markets", [])
        for m in markets:
            ticker = m.get("ticker", "")
            if not ticker:
                continue
            ob = get_kalshi_orderbook_depth(ticker, depth=5)
            results.append({
                "ticker": ticker,
                "series": series_ticker,
                "series_desc": description,
                "title": m.get("title", ""),
                "close_time": m.get("close_time", ""),
                "volume": m.get("volume", 0),
                "open_interest": m.get("open_interest", 0),
                "yes_bid": ob.get("best_bid"),
                "yes_ask": ob.get("best_ask"),
                "spread": ob.get("spread"),
                "yes_bids": ob.get("yes_bids", []),
                "no_bids": ob.get("no_bids", []),
            })
            time.sleep(0.05)  # rate limit safety

    logger.info("scan_macro_markets: found %d markets across %d series", len(results), len(MACRO_SERIES))
    return results


# ─── Probability model ────────────────────────────────────────────────────────

def _fed_model_probability(ticker: str, title: str, close_time: str) -> Optional[float]:
    """
    Estimate probability of YES for a FED rate market using FedWatch estimates.

    For 'KXFED-{DATE}-T{RATE}': YES = rate will be ABOVE threshold after that meeting.
    We use FEDWATCH_ESTIMATES to determine likely rate path.

    Returns float in [0, 1] or None if not parseable.
    """
    import re
    # KXFED ticker format: KXFED-{YY}{MON}-T{threshold}
    # e.g. KXFED-27APR-T4.25 = "above 4.25% after April 2027 meeting"
    # YY = two-digit year, MON = 3-letter month abbreviation
    m = re.match(r"KXFED-(\d{2})([A-Z]{3})-T([\d.]+)", ticker)
    if not m:
        return None

    year_suffix = m.group(1)   # "27" → 2027
    month_str = m.group(2)     # "APR"
    threshold = float(m.group(3))  # 4.25
    year = int("20" + year_suffix)

    month_map = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
                 "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}
    month = month_map.get(month_str, 0)
    if not month:
        return None

    # Find closest FedWatch estimate for this meeting month/year
    target_date = f"{year:04d}-{month:02d}-15"  # mid-month proxy for FOMC meeting
    best_date = None
    best_dist = float("inf")
    for d in FEDWATCH_ESTIMATES:
        dist = abs((datetime.fromisoformat(d) - datetime.fromisoformat(target_date)).days)
        if dist < best_dist:
            best_dist = dist
            best_date = d

    if not best_date or best_dist > 90:
        return None

    # KXFED questions: "Will upper bound be ABOVE threshold after that meeting?"
    # Current upper bound = 4.50%. One cut → 4.25 (NOT above 4.25 → NO)
    # estimated_rate = most likely upper bound after that meeting
    estimated_rate = FEDWATCH_ESTIMATES[best_date]

    # YES probability = P(actual upper bound > threshold)
    # Using a ±0.25% uncertainty model (one cut = 0.25% move)
    diff = estimated_rate - threshold
    if diff >= 0.75:
        return 0.88   # Estimated rate well above threshold — YES very likely
    elif diff >= 0.50:
        return 0.75
    elif diff >= 0.25:
        return 0.60
    elif diff >= 0.05:
        return 0.48   # Borderline — about even
    elif diff >= -0.20:
        return 0.35   # Slightly below threshold estimate → lean NO
    elif diff >= -0.50:
        return 0.22   # One cut below threshold
    elif diff >= -0.75:
        return 0.12   # Two cuts below threshold
    else:
        return 0.06   # Three+ cuts below threshold — NO very likely


def _cpi_model_probability(ticker: str) -> Optional[float]:
    """
    Simple model for CPI markets.
    Current trend: disinflation, monthly CPI around -0.1% to +0.3%.
    Market: KXCPI-{MONTH}-T{threshold}
    """
    import re
    m = re.match(r"KXCPI-\d{2}([A-Z]{3})-T([-\d.]+)", ticker)
    if not m:
        return None
    threshold = float(m.group(2))
    # Monthly CPI currently tracking around -0.1% to 0.1%
    # For YES (CPI above threshold):
    if threshold <= -0.3:
        return 0.15  # Very rare for CPI to be this negative
    elif threshold <= -0.1:
        return 0.30
    elif threshold <= 0.0:
        return 0.45
    elif threshold <= 0.2:
        return 0.60
    elif threshold <= 0.4:
        return 0.75
    else:
        return 0.85


def model_probability(ticker: str, title: str, close_time: str, series: str) -> Optional[float]:
    """Route to appropriate probability model by series."""
    if series == "KXFED":
        return _fed_model_probability(ticker, title, close_time)
    elif series == "KXCPI":
        return _cpi_model_probability(ticker)
    return None  # No model for this series yet


# ─── Kelly sizing ─────────────────────────────────────────────────────────────

def kelly_fraction(model_p: float, contract_price: float) -> float:
    """
    Full Kelly fraction for a binary bet.

    At contract_price we pay `contract_price`, win (1 - contract_price) if right,
    lose `contract_price` if wrong.

    Kelly = (p * win_ratio - (1-p)) / win_ratio
          = (p * (1/contract_price - 1) - (1-p)) / (1/contract_price - 1)
    Simplified: K = (p * (1 - contract_price) - (1-p) * contract_price) / (1 - contract_price)
    """
    if contract_price <= 0.0 or contract_price >= 1.0:
        return 0.0
    win = 1.0 - contract_price   # net gain per $1 wagered (minus fee below)
    loss = contract_price        # net loss per $1 wagered
    # Adjust for taker fee: actual win = win * (1 - TAKER_FEE)
    win_net = win * (1.0 - TAKER_FEE)
    k = (model_p * win_net - (1.0 - model_p) * loss) / win_net
    return max(k, 0.0)


def fractional_kelly_usd(model_p: float, contract_price: float, bankroll_usd: float) -> float:
    """Return dollar amount to bet at fractional Kelly."""
    k = kelly_fraction(model_p, contract_price)
    raw = k * FRACTIONAL_KELLY * bankroll_usd
    # Cap at MAX_POSITION_PCT of bankroll
    capped = min(raw, MAX_POSITION_PCT * bankroll_usd)
    return round(capped, 2)


# ─── Edge detector ────────────────────────────────────────────────────────────

def find_edges(markets: list[dict[str, Any]], bankroll_usd: float) -> list[KalshiEdge]:
    """
    For each market with a model probability, compute edge vs market price.
    Returns list of edges sorted by edge magnitude descending.
    """
    edges: list[KalshiEdge] = []

    for m in markets:
        ticker = m["ticker"]
        series = m["series"]
        title = m.get("title", "")
        close_time = m.get("close_time", "")
        yes_bid = m.get("yes_bid")
        yes_ask = m.get("yes_ask")
        spread = m.get("spread")

        if yes_bid is None or yes_ask is None:
            continue
        if yes_bid <= 0 or yes_ask <= 0 or yes_ask >= 1.0:
            continue

        model_p = model_probability(ticker, title, close_time, series)
        if model_p is None:
            continue

        # Edge for buying YES (model says higher than market ask)
        edge_yes = model_p - yes_ask
        # Edge for buying NO (model says lower than market bid implies)
        edge_no = (1.0 - model_p) - (1.0 - yes_bid)  # = yes_bid - model_p

        best_action = "skip"
        best_edge = 0.0
        kelly_frac = 0.0
        suggested_usd = 0.0
        entry_price = 0.0

        if edge_yes >= MIN_EDGE and edge_yes >= edge_no:
            best_action = "buy_yes"
            best_edge = edge_yes
            entry_price = yes_ask
            kelly_frac = kelly_fraction(model_p, entry_price)
            suggested_usd = fractional_kelly_usd(model_p, entry_price, bankroll_usd)
        elif edge_no >= MIN_EDGE:
            best_action = "buy_no"
            best_edge = edge_no
            entry_price = 1.0 - yes_bid   # NO price = 1 - YES bid
            no_prob = 1.0 - model_p
            kelly_frac = kelly_fraction(no_prob, entry_price)
            suggested_usd = fractional_kelly_usd(no_prob, entry_price, bankroll_usd)
        elif spread is not None and spread >= MIN_SPREAD_FOR_MM:
            best_action = "market_make"
            best_edge = spread / 2
            kelly_frac = 0.0
            suggested_usd = min(25.0, bankroll_usd * 0.05)  # Small MM allocation
        else:
            continue  # No edge

        confidence = "high" if best_edge >= 0.12 else ("medium" if best_edge >= 0.08 else "low")
        notes = f"model_p={model_p:.2%} market={yes_bid:.2f}/{yes_ask:.2f} edge={best_edge:.2%}"

        edges.append(KalshiEdge(
            ticker=ticker,
            title=title,
            close_time=close_time,
            model_prob=model_p,
            market_bid=yes_bid,
            market_ask=yes_ask,
            edge_yes=edge_yes,
            edge_no=edge_no,
            spread=spread or 0.0,
            best_action=best_action,
            kelly_fraction=kelly_frac,
            suggested_usd=suggested_usd,
            confidence=confidence,
            notes=notes,
        ))

    edges.sort(key=lambda e: abs(e.edge_yes if e.best_action == "buy_yes" else e.edge_no), reverse=True)
    return edges


# ─── Polymarket cross-arb scanner ─────────────────────────────────────────────

async def fetch_polymarket_price(condition_keywords: list[str]) -> Optional[float]:
    """
    Search Polymarket gamma API for a matching market and return best YES price.
    Returns float in [0, 1] or None if not found.
    """
    try:
        query = " ".join(condition_keywords[:3])
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{POLYMARKET_API}/markets",
                params={"q": query, "active": "true", "closed": "false", "limit": "5"},
            )
            if resp.status_code != 200:
                return None
            markets = resp.json()
            if not isinstance(markets, list) or not markets:
                return None
            m = markets[0]
            # Polymarket prices are in tokens; midpoint of best bid/ask
            outcomes = m.get("outcomes", [])
            for outcome in outcomes:
                if "yes" in str(outcome.get("outcome", "")).lower():
                    price = outcome.get("price", outcome.get("lastTradePrice"))
                    if price is not None:
                        return float(price)
    except Exception as exc:
        logger.debug("Polymarket fetch failed: %s", exc)
    return None


def cross_arb_opportunity(
    kalshi_ticker: str,
    kalshi_yes_price: float,
    polymarket_yes_price: float,
    threshold: float = 0.04,
) -> Optional[dict[str, Any]]:
    """
    If Kalshi and Polymarket price the same event differently by ≥ threshold,
    return an arbitrage recommendation.

    Strategy: buy cheap side, sell/hold expensive side.
    Note: True simultaneous arb requires both accounts; we flag for manual execution.
    """
    diff = abs(kalshi_yes_price - polymarket_yes_price)
    if diff < threshold:
        return None

    if kalshi_yes_price < polymarket_yes_price:
        cheap_venue = "kalshi"
        expensive_venue = "polymarket"
        cheap_price = kalshi_yes_price
        exp_price = polymarket_yes_price
        action = "buy YES on Kalshi, sell YES on Polymarket"
    else:
        cheap_venue = "polymarket"
        expensive_venue = "kalshi"
        cheap_price = polymarket_yes_price
        exp_price = kalshi_yes_price
        action = "buy YES on Polymarket, sell YES on Kalshi"

    net_edge = diff - 2 * TAKER_FEE  # Both sides pay taker fee
    return {
        "ticker": kalshi_ticker,
        "kalshi_yes": kalshi_yes_price,
        "polymarket_yes": polymarket_yes_price,
        "spread": round(diff, 4),
        "net_edge_after_fees": round(net_edge, 4),
        "action": action,
        "cheap_venue": cheap_venue,
        "expensive_venue": expensive_venue,
        "arb_viable": net_edge > 0,
    }


# ─── Order placement ──────────────────────────────────────────────────────────

def place_kalshi_market_order(
    ticker: str,
    side: str,          # "yes" or "no"
    count: int,         # number of contracts
    max_price_cents: Optional[int] = None,   # for limit orders; None = market
) -> dict[str, Any]:
    """
    Place a Kalshi order.

    Args:
        ticker: market ticker
        side: "yes" or "no"
        count: number of contracts (each pays $1 at expiry)
        max_price_cents: limit price in cents (1-99); None for market order

    Returns dict with order_id or error.
    IMPORTANT: This hits the live API with real money. Always confirm edge first.
    """
    order_body: dict[str, Any] = {
        "ticker": ticker,
        "side": side,
        "type": "market" if max_price_cents is None else "limit",
        "count": count,
        "action": "buy",
    }
    if max_price_cents is not None:
        order_body["max_cost"] = max_price_cents * count  # total max cost in cents

    code, data = kalshi_signed_post("/trade-api/v2/portfolio/orders", order_body)

    if code in (200, 201) and isinstance(data, dict):
        order = data.get("order", data)
        return {
            "status": "placed",
            "order_id": order.get("order_id", order.get("id", "")),
            "ticker": ticker,
            "side": side,
            "count": count,
            "price_cents": max_price_cents,
            "http_status": code,
        }
    return {
        "status": "error",
        "http_status": code,
        "error": str(data)[:500],
        "ticker": ticker,
        "side": side,
        "count": count,
    }


# ─── Main scanner entry point ─────────────────────────────────────────────────

def run_full_scan(require_min_contracts: int = 50) -> dict[str, Any]:
    """
    Full pipeline:
      1. Fetch account state
      2. Scan macro markets
      3. Model probability for each
      4. Find edges with Kelly sizing
      5. Return ranked opportunities

    This is the callable MCP tool entry point.
    """
    account = get_account_state()
    bankroll = account.balance_usd

    if bankroll < 1.0:
        return {
            "status": "insufficient_balance",
            "balance_usd": bankroll,
            "message": "Kalshi balance too low to trade.",
        }

    # P1-9 FIX: MACRO_SERIES is intentionally empty (negative-edge series removed).
    # Call scan_macro_markets only when MACRO_SERIES has entries; skip gracefully otherwise.
    if MACRO_SERIES:
        markets = scan_macro_markets(limit_per_series=50)
    else:
        markets = []
        logger.info(
            "run_full_scan: MACRO_SERIES is empty (all econ series blocked). "
            "Skipping macro scan — use run_safe_compounder or sports scanner for edges."
        )
    edges = find_edges(markets, bankroll)

    # Filter to actionable (non-zero sizing)
    actionable = [e for e in edges if e.suggested_usd >= 1.0]

    output = {
        "status": "ok",
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "balance_usd": bankroll,
        "markets_scanned": len(markets),
        "edges_found": len(edges),
        "actionable_edges": len(actionable),
        "positions_open": len(account.positions),
        "open_orders": len(account.open_orders),
        "opportunities": [],
        "positions": account.positions,
    }

    for e in actionable[:10]:
        contracts = max(1, int(e.suggested_usd / (e.market_ask if e.best_action == "buy_yes" else (1.0 - e.market_bid))))
        output["opportunities"].append({
            "ticker": e.ticker,
            "title": e.title,
            "close_time": e.close_time,
            "action": e.best_action,
            "model_probability": round(e.model_prob, 3),
            "market_bid": e.market_bid,
            "market_ask": e.market_ask,
            "spread": e.spread,
            "edge": round(e.edge_yes if e.best_action == "buy_yes" else e.edge_no, 4),
            "kelly_fraction": round(e.kelly_fraction, 4),
            "suggested_usd": e.suggested_usd,
            "suggested_contracts": contracts,
            "confidence": e.confidence,
            "notes": e.notes,
        })

    if not actionable:
        output["message"] = (
            "No edges found meeting the MIN_EDGE threshold. Markets may be fairly priced "
            "or model coverage is incomplete. Check back after next macro data release."
        )

    return output


# ─── Quick market-maker scan ──────────────────────────────────────────────────

def scan_for_wide_spreads(min_spread: float = MIN_SPREAD_FOR_MM) -> list[dict[str, Any]]:
    """
    Find all open Kalshi markets with spreads ≥ min_spread.
    Wide-spread markets are candidates for passive limit order market-making.
    Returns list of markets sorted by spread descending.
    """
    results = []
    for series_ticker in MACRO_SERIES:
        code, data = kalshi_signed_get("/trade-api/v2/markets", {
            "status": "open",
            "series_ticker": series_ticker,
            "limit": "50",
        })
        if not isinstance(data, dict):
            continue
        for m in data.get("markets", []):
            ticker = m.get("ticker", "")
            if not ticker:
                continue
            ob = get_kalshi_orderbook_depth(ticker, depth=3)
            spread = ob.get("spread")
            if spread is not None and spread >= min_spread:
                results.append({
                    "ticker": ticker,
                    "title": m.get("title", ""),
                    "close_time": m.get("close_time", ""),
                    "yes_bid": ob.get("best_bid"),
                    "yes_ask": ob.get("best_ask"),
                    "spread": spread,
                    "mm_mid": round(((ob.get("best_bid") or 0) + (ob.get("best_ask") or 1)) / 2, 3),
                    "yes_bids": ob.get("yes_bids", []),
                    "no_bids": ob.get("no_bids", []),
                })
            time.sleep(0.05)

    results.sort(key=lambda x: x.get("spread", 0), reverse=True)
    return results


# ─── P&L Summary ─────────────────────────────────────────────────────────────

def get_kalshi_pnl_summary() -> dict[str, Any]:
    """
    Compute P&L from settled trades in portfolio history.
    """
    code, data = kalshi_signed_get("/trade-api/v2/portfolio/settlements", {"limit": "100"})
    if not isinstance(data, dict):
        return {"error": str(data)}

    settlements = data.get("settlements", [])
    total_won = sum(s.get("revenue", 0) for s in settlements if s.get("revenue", 0) > 0)
    total_staked = sum(s.get("market_exposure", 0) for s in settlements)

    account = get_account_state()

    return {
        "balance_usd": account.balance_usd,
        "positions_open": len(account.positions),
        "settled_trades": len(settlements),
        "gross_revenue_cents": total_won,
        "gross_revenue_usd": total_won / 100,
        "total_staked_cents": total_staked,
        "roi_pct": round((total_won / total_staked * 100) if total_staked > 0 else 0, 2),
        "recent_settlements": settlements[:10],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
