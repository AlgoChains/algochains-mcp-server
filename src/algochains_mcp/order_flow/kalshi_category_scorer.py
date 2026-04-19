"""
Kalshi Category Scorer — AlgoChains v1.0

Scores each Kalshi market category 0-100 based on live trade history.
Hard-blocks categories with score < MIN_SCORE_TO_TRADE.

Scoring formula (from ryanfrigo validation):
  ROI          40%   Average return on investment across all trades
  Recent Trend 25%   Direction of last 10 trades (recency-weighted)
  Sample Size  20%   More data = more confidence in the score
  Win Rate     15%   Percentage of winning trades

Allocation tiers:
  80-100 → STRONG: 20% max allocation
  60-79  → GOOD: 10% max allocation
  40-59  → WEAK: 5% max allocation
  20-39  → POOR: 2% max allocation
  0-19   → BLOCKED: 0% — no trades

Hard prior scores (from ryanfrigo live data — 100+ trades):
  NCAAB → 72, NBA → 41, POLITICS → 31, FED → 12, CPI → 8, ECON_MACRO → 10
"""
from __future__ import annotations

import logging
import math
import os
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("algochains_mcp.order_flow.kalshi_category_scorer")

# ─── Score thresholds ─────────────────────────────────────────────────────────
MIN_SCORE_TO_TRADE  = 30    # Hard block below this
MIN_TRADES_FOR_LIVE = 5     # Minimum trades before live score overrides prior

# ─── Prior scores from ryanfrigo live trading data (100+ trades) ─────────────
# These are our starting priors before we have our own data.
# Will be overridden by live Kalshi trade data after MIN_TRADES_FOR_LIVE trades.
PRIOR_SCORES: dict[str, dict[str, float]] = {
    "KXNCAAB":  {"score": 72.3, "win_rate": 0.74, "roi": 0.100, "trades": 50,  "status": "GOOD"},
    "KXNBA":    {"score": 41.2, "win_rate": 0.52, "roi": 0.015, "trades": 28,  "status": "WEAK"},
    "KXNFL":    {"score": 55.0, "win_rate": 0.60, "roi": 0.040, "trades": 10,  "status": "WEAK"},
    "KXMLB":    {"score": 48.0, "win_rate": 0.55, "roi": 0.025, "trades": 8,   "status": "WEAK"},
    "KXNHL":    {"score": 50.0, "win_rate": 0.58, "roi": 0.030, "trades": 6,   "status": "WEAK"},
    "politics": {"score": 31.0, "win_rate": 0.48, "roi": -0.080, "trades": 15, "status": "MARGINAL"},
    "weather":  {"score": 45.0, "win_rate": 0.55, "roi": 0.020, "trades": 5,   "status": "WEAK"},
    "finance":  {"score": 38.0, "win_rate": 0.50, "roi": -0.010, "trades": 8,  "status": "POOR"},
    "other":    {"score": 35.0, "win_rate": 0.50, "roi": 0.000, "trades": 0,   "status": "POOR"},
    # Hard blocks — negative ROI from live data
    "KXFED":    {"score": 12.1, "win_rate": 0.32, "roi": -0.400, "trades": 25, "status": "BLOCKED"},
    "KXCPI":    {"score": 8.4,  "win_rate": 0.25, "roi": -0.650, "trades": 20, "status": "BLOCKED"},
    "KXNFP":    {"score": 10.5, "win_rate": 0.30, "roi": -0.550, "trades": 40, "status": "BLOCKED"},
    "KXGDP":    {"score": 9.0,  "win_rate": 0.28, "roi": -0.500, "trades": 10, "status": "BLOCKED"},
    "KXUNRATE": {"score": 10.0, "win_rate": 0.30, "roi": -0.450, "trades": 8,  "status": "BLOCKED"},
}


def compute_score(
    win_rate: float,
    roi: float,
    n_trades: int,
    recent_pnl: list[float],
) -> float:
    """
    Compute a 0-100 category score.

    Args:
        win_rate: fraction of winning trades (0.0-1.0)
        roi: average ROI per trade (-1.0 to +1.0)
        n_trades: total settled trades for this category
        recent_pnl: list of last 10 trade P&Ls (positive = win, negative = loss)

    Returns float in [0, 100].
    """
    # ROI component (40%): normalize to 0-100
    roi_clamped = max(-1.0, min(1.0, roi))
    roi_score = (roi_clamped + 1.0) / 2.0 * 100  # map [-1,1] → [0,100]

    # Win rate component (15%): scale to 0-100
    wr_score = win_rate * 100

    # Sample size component (20%): logarithmic saturation
    # 0 trades → 0; 5 trades → 50; 20 trades → 75; 50+ trades → 95
    if n_trades >= 1:
        sample_score = min(95.0, math.log(n_trades + 1) / math.log(51) * 95)
    else:
        sample_score = 0.0

    # Recent trend component (25%): weighted average of last 10 trades
    if recent_pnl:
        weights = [i + 1 for i in range(len(recent_pnl))]  # more recent = higher weight
        weighted_pnl = sum(p * w for p, w in zip(recent_pnl, weights)) / sum(weights)
        # Normalize: a +10% recent trade → 75 score; -10% → 25 score
        trend_score = max(0, min(100, (weighted_pnl / 0.20 + 1) * 50))
    else:
        trend_score = 50.0  # neutral prior if no recent data

    score = (
        roi_score * 0.40
        + wr_score * 0.15
        + sample_score * 0.20
        + trend_score * 0.25
    )
    return round(score, 1)


def score_to_status(score: float) -> str:
    if score >= 80:
        return "STRONG"
    elif score >= 60:
        return "GOOD"
    elif score >= 40:
        return "WEAK"
    elif score >= 30:
        return "MARGINAL"
    elif score >= 20:
        return "POOR"
    else:
        return "BLOCKED"


def score_to_max_allocation(score: float) -> float:
    """Return max portfolio allocation fraction for this score."""
    if score >= 80:
        return 0.20  # 20%
    elif score >= 60:
        return 0.10  # 10%
    elif score >= 40:
        return 0.05  # 5%
    elif score >= MIN_SCORE_TO_TRADE:
        return 0.02  # 2%
    else:
        return 0.00  # BLOCKED


def get_all_category_scores(
    live_trades: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """
    Return current category scores for all tracked Kalshi categories.

    If live_trades are provided (from Supabase kalshi_trades table),
    live scores are computed from real data and merged with priors.
    Otherwise, prior scores from ryanfrigo data are returned.

    Args:
        live_trades: list of dicts from Supabase kalshi_trades (optional)

    Returns list of category score dicts sorted by score descending.
    """
    # Start with priors
    scores: dict[str, dict[str, Any]] = {}
    for series, prior in PRIOR_SCORES.items():
        scores[series] = {
            "series": series,
            "score": prior["score"],
            "win_rate": prior["win_rate"],
            "roi": prior["roi"],
            "trades": prior["trades"],
            "status": prior["status"],
            "max_allocation_pct": score_to_max_allocation(prior["score"]) * 100,
            "tradeable": prior["score"] >= MIN_SCORE_TO_TRADE,
            "data_source": "prior",
        }

    # Override with live data if available
    if live_trades:
        from collections import defaultdict
        cat_trades: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for trade in live_trades:
            series = trade.get("series") or trade.get("strategy", "other")
            cat_trades[series].append(trade)

        for series, trades in cat_trades.items():
            settled = [t for t in trades if t.get("status") == "settled"]
            if len(settled) < MIN_TRADES_FOR_LIVE:
                continue  # Not enough data to override prior

            wins = [t for t in settled if (t.get("profit_cents", 0) or 0) > 0]
            total_staked = sum(t.get("total_cost_cents", 1) for t in settled) or 1
            total_profit = sum(t.get("profit_cents", 0) for t in settled)

            win_rate = len(wins) / len(settled)
            roi = total_profit / total_staked
            recent_pnl = [t.get("profit_cents", 0) / max(t.get("total_cost_cents", 1), 1)
                          for t in settled[-10:]]

            live_score = compute_score(win_rate, roi, len(settled), recent_pnl)
            status = score_to_status(live_score)

            scores[series] = {
                "series": series,
                "score": live_score,
                "win_rate": round(win_rate, 3),
                "roi": round(roi, 3),
                "trades": len(settled),
                "status": status,
                "max_allocation_pct": score_to_max_allocation(live_score) * 100,
                "tradeable": live_score >= MIN_SCORE_TO_TRADE,
                "data_source": "live",
            }

    result = sorted(scores.values(), key=lambda x: x["score"], reverse=True)
    return result


def is_category_tradeable(series_prefix: str, live_trades: Optional[list] = None) -> bool:
    """Quick check: is this category currently tradeable?"""
    all_scores = get_all_category_scores(live_trades)
    for entry in all_scores:
        if entry["series"] == series_prefix:
            return entry["tradeable"]
    # Unknown category — use conservative default
    return False


def get_max_allocation_for_series(series_prefix: str) -> float:
    """Return max portfolio allocation fraction for a series prefix."""
    for series, prior in PRIOR_SCORES.items():
        if series == series_prefix:
            return score_to_max_allocation(prior["score"])
    return 0.02  # Unknown category → conservative


def format_scores_table(scores: list[dict[str, Any]]) -> str:
    """Format category scores as a text table for Slack/CLI output."""
    header = f"{'Category':<20} {'Score':>6} {'WR':>6} {'ROI':>8} {'Trades':>7} {'Alloc':>6} {'Status':<12}\n"
    sep = "-" * 78 + "\n"
    rows = ""
    for s in scores:
        rows += (
            f"{s['series']:<20} {s['score']:>6.1f} {s['win_rate']:>6.1%} "
            f"{s['roi']:>8.1%} {s['trades']:>7} {s['max_allocation_pct']:>5.0f}% "
            f"{s['status']:<12}\n"
        )
    return header + sep + rows
