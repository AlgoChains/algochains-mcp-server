"""Quantstats-style performance tearsheet generation for marketplace bots.

Generates professional HTML tearsheets and full metric suites for marketplace
bot listings, inspired by ranaroussi/quantstats (4k+ stars).

Works WITHOUT quantstats installed (pure Python fallback):
  - If quantstats is installed: generates full HTML tearsheet + 30+ metrics
  - If not installed: computes core metrics in pure Python + text summary

Install quantstats (optional but recommended):
    pip install quantstats

Tearsheets are saved to state/tearsheets/bot_<BOT>_<timestamp>.html
"""
from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("algochains_mcp.performance_reports")

_TEARSHEET_DIR = os.environ.get("TEARSHEET_DIR", "state/tearsheets")


# ---------------------------------------------------------------------------
# Pure-Python metric computation (no quantstats dependency)
# ---------------------------------------------------------------------------

def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b and not math.isnan(b) else default


def _annualize_factor(frequency: str) -> float:
    mapping = {
        "daily": 252, "weekly": 52, "monthly": 12,
        "hourly": 252 * 6.5, "minute": 252 * 390,
    }
    return mapping.get(frequency.lower(), 252)


def compute_core_metrics(
    returns: list[float],
    frequency: str = "daily",
    risk_free_rate: float = 0.05,
) -> dict:
    """Compute professional-grade performance metrics from a returns series.

    Args:
        returns:         List of period returns (as decimals, e.g., 0.01 = 1%)
        frequency:       Return frequency ("daily", "weekly", "monthly")
        risk_free_rate:  Annual risk-free rate (default 5% = current US T-bill)

    Returns:
        dict with 20+ institutional metrics
    """
    if not returns or len(returns) < 5:
        return {"error": "insufficient_data", "min_required": 5, "provided": len(returns)}

    n = len(returns)
    ann_factor = _annualize_factor(frequency)

    # Basic stats
    mean_return = sum(returns) / n
    variance = sum((r - mean_return) ** 2 for r in returns) / max(n - 1, 1)
    std_dev = math.sqrt(variance)

    # Annualized metrics
    ann_return = (1 + mean_return) ** ann_factor - 1
    ann_vol = std_dev * math.sqrt(ann_factor)

    # Sharpe Ratio
    rf_per_period = (1 + risk_free_rate) ** (1 / ann_factor) - 1
    excess_returns = [r - rf_per_period for r in returns]
    mean_excess = sum(excess_returns) / n
    excess_std = math.sqrt(sum((r - mean_excess) ** 2 for r in excess_returns) / max(n - 1, 1))
    sharpe = _safe_div(mean_excess, excess_std) * math.sqrt(ann_factor)

    # Sortino Ratio (downside deviation only)
    downside_returns = [min(r - rf_per_period, 0) for r in returns]
    downside_var = sum(r ** 2 for r in downside_returns) / max(n - 1, 1)
    downside_std = math.sqrt(downside_var)
    sortino = _safe_div(mean_excess, downside_std) * math.sqrt(ann_factor)

    # Drawdown analysis
    cumulative = [1.0]
    for r in returns:
        cumulative.append(cumulative[-1] * (1 + r))

    peak = cumulative[0]
    max_dd = 0.0
    dd_series = []
    for val in cumulative:
        peak = max(peak, val)
        dd = (val - peak) / peak
        dd_series.append(dd)
        max_dd = min(max_dd, dd)

    # Calmar Ratio
    calmar = _safe_div(ann_return, abs(max_dd))

    # Win rate
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]
    win_rate = len(wins) / max(n, 1)

    # Average win / loss
    avg_win = sum(wins) / max(len(wins), 1)
    avg_loss = sum(losses) / max(len(losses), 1)
    profit_factor = _safe_div(sum(wins), abs(sum(losses)))

    # Omega ratio (probability weighted gain/loss)
    threshold = rf_per_period
    gains_above = sum(r - threshold for r in returns if r > threshold)
    losses_below = sum(threshold - r for r in returns if r <= threshold)
    omega = _safe_div(gains_above, losses_below)

    # CVaR (Conditional Value at Risk) — expected loss in worst 5%
    sorted_returns = sorted(returns)
    cvar_cutoff = max(1, int(n * 0.05))
    cvar_5pct = sum(sorted_returns[:cvar_cutoff]) / cvar_cutoff

    # Best/worst periods
    best_period = max(returns)
    worst_period = min(returns)

    # Time in drawdown
    in_dd = sum(1 for dd in dd_series if dd < -0.001)
    time_in_drawdown_pct = in_dd / max(len(dd_series), 1) * 100

    return {
        "n_periods": n,
        "frequency": frequency,
        "annualized_return_pct": round(ann_return * 100, 4),
        "annualized_volatility_pct": round(ann_vol * 100, 4),
        "sharpe_ratio": round(sharpe, 4),
        "sortino_ratio": round(sortino, 4),
        "calmar_ratio": round(calmar, 4),
        "omega_ratio": round(omega, 4),
        "max_drawdown_pct": round(max_dd * 100, 4),
        "cvar_5pct": round(cvar_5pct * 100, 4),
        "win_rate_pct": round(win_rate * 100, 2),
        "loss_rate_pct": round((1 - win_rate) * 100, 2),
        "avg_win_pct": round(avg_win * 100, 4),
        "avg_loss_pct": round(avg_loss * 100, 4),
        "profit_factor": round(profit_factor, 4),
        "best_period_pct": round(best_period * 100, 4),
        "worst_period_pct": round(worst_period * 100, 4),
        "time_in_drawdown_pct": round(time_in_drawdown_pct, 2),
        "total_return_pct": round((cumulative[-1] - 1) * 100, 4),
        "risk_free_rate_pct": round(risk_free_rate * 100, 2),
        "computed_at": datetime.now(tz=timezone.utc).isoformat(),
    }


def _try_quantstats(returns: list[float], bot_name: str, output_path: str) -> Optional[str]:
    """Try to use quantstats for HTML tearsheet if installed."""
    try:
        import quantstats as qs
        import pandas as pd

        series = pd.Series(returns)
        series.index = pd.date_range(end=datetime.now(), periods=len(returns), freq="B")

        os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)
        qs.reports.html(
            series,
            output=output_path,
            title=f"AlgoChains — {bot_name} Performance Tearsheet",
            download_filename=os.path.basename(output_path),
        )
        return output_path
    except ImportError:
        return None
    except Exception as e:
        logger.warning("quantstats HTML generation failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_bot_tearsheet(
    bot_name: str,
    returns: list[float],
    frequency: str = "daily",
    risk_free_rate: float = 0.05,
) -> dict:
    """Generate a full performance tearsheet for a marketplace bot.

    Tries quantstats HTML first (if installed), falls back to JSON metrics.

    Args:
        bot_name:   Bot identifier (e.g., "MNQ_scalper", "CL_swing")
        returns:    List of period returns as decimals (e.g., [0.01, -0.005, ...])
        frequency:  Return frequency
        risk_free_rate: Annual risk-free rate

    Returns:
        dict with metrics + optional tearsheet file path
    """
    os.makedirs(_TEARSHEET_DIR, exist_ok=True)

    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    html_path = os.path.join(_TEARSHEET_DIR, f"bot_{bot_name}_{timestamp}.html")
    json_path = os.path.join(_TEARSHEET_DIR, f"bot_{bot_name}_{timestamp}.json")

    # Core metrics (always computed)
    metrics = compute_core_metrics(returns, frequency, risk_free_rate)
    metrics["bot_name"] = bot_name

    # Try HTML tearsheet via quantstats
    html_file = _try_quantstats(returns, bot_name, html_path)

    # Save JSON metrics regardless
    with open(json_path, "w") as f:
        json.dump(metrics, f, indent=2)

    # Marketplace-ready grade
    sharpe = metrics.get("sharpe_ratio", 0)
    max_dd = abs(metrics.get("max_drawdown_pct", 100))
    win_rate = metrics.get("win_rate_pct", 0)

    if sharpe >= 2.0 and max_dd <= 15.0 and win_rate >= 55.0:
        grade = "A — MARKETPLACE ELIGIBLE"
    elif sharpe >= 1.5 and max_dd <= 20.0 and win_rate >= 50.0:
        grade = "B — STRONG CANDIDATE"
    elif sharpe >= 1.0 and max_dd <= 25.0:
        grade = "C — NEEDS IMPROVEMENT"
    else:
        grade = "D — NOT ELIGIBLE"

    return {
        "bot_name": bot_name,
        "marketplace_grade": grade,
        "metrics": metrics,
        "tearsheet_html": html_file,
        "tearsheet_json": json_path,
        "quantstats_available": html_file is not None,
        "install_hint": None if html_file else "Run: pip install quantstats pandas for HTML tearsheet",
    }


def get_bot_metrics_full(
    bot_name: str,
    returns: list[float],
    frequency: str = "daily",
    risk_free_rate: float = 0.05,
) -> dict:
    """Return all computed metrics without generating a file.

    For quick API calls when you need metrics but not the full tearsheet.
    """
    metrics = compute_core_metrics(returns, frequency, risk_free_rate)
    metrics["bot_name"] = bot_name

    # Try to get quantstats extended metrics
    try:
        import quantstats as qs
        import pandas as pd
        series = pd.Series(returns)
        series.index = pd.date_range(end=datetime.now(), periods=len(returns), freq="B")
        qs_metrics = qs.reports.metrics(series, mode="full", display=False)
        if qs_metrics is not None:
            metrics["quantstats_extended"] = qs_metrics.to_dict() if hasattr(qs_metrics, "to_dict") else str(qs_metrics)
    except Exception:
        pass

    return metrics
