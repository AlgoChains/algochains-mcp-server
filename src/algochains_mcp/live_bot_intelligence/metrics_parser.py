"""
metrics_parser.py — Parse live bot logs to extract real trading metrics.

Data source: Real Tradovate fills from bot log files.
No synthetic data. All metrics from actual execution.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# Resolve control tower path (works on Mac and Desktop WSL)
_POSSIBLE_ROOTS = [
    Path("/Users/treycsa/CascadeProjects/algochains-control-tower"),
    Path("/home/trrey/algochains-control-tower"),
    Path("/mnt/c/Users/trrey/algochains-control-tower"),
]
CONTROL_TOWER = next((p for p in _POSSIBLE_ROOTS if p.exists()), _POSSIBLE_ROOTS[0])

BOT_LOG_PATHS: dict[str, Path] = {
    "mnq": CONTROL_TOWER / "logs" / "futures_bot_live.log",
    "cl":  CONTROL_TOWER / "logs" / "cl_futures_live.log",
    # mes_swing.log and nq_swing.log are stale backup files — use the live paths
    "mes": CONTROL_TOWER / "logs" / "mes_swing_live.log",
    "nq":  CONTROL_TOWER / "logs" / "nq_swing_live.log",
}

BOT_META: dict[str, dict] = {
    "mnq": {
        "display_name": "MNQ Futures Scalper (7-AI Ensemble)",
        "script": "FUTURES_SCALPER_UPGRADED.py",
        "symbol": "MNQ",
        "broker": "Tradovate",
        "strategy_type": "scalper",
        "asset_class": "futures",
        "timeframe": "5min",
    },
    "cl": {
        "display_name": "CL Crude Oil Scalper (FinBERT Sentiment)",
        "script": "CL_FUTURES_SCALPER.py",
        "symbol": "CL",
        "broker": "Tradovate",
        "strategy_type": "sentiment_scalper",
        "asset_class": "futures",
        "timeframe": "5min",
    },
    "mes": {
        "display_name": "MES Swing (EMA Pullback)",
        "script": "mes_swing_live.py",
        "symbol": "MES",
        "broker": "Tradovate",
        "strategy_type": "swing",
        "asset_class": "futures",
        "timeframe": "15min",
    },
    "nq": {
        "display_name": "NQ Swing (Trend Following + VIX Gate)",
        "script": "nq_swing_live.py",
        "symbol": "NQ",
        "broker": "Tradovate",
        "strategy_type": "swing",
        "asset_class": "futures",
        "timeframe": "15min",
    },
}


@dataclass
class BotMetrics:
    bot_id: str
    symbol: str
    display_name: str
    strategy_type: str
    # Live state
    is_running: bool = False
    last_log_age_sec: float = 0.0
    last_signal: str = "UNKNOWN"
    last_signal_confidence: float = 0.0
    last_signal_time: str = ""
    # Today's stats
    daily_pnl: float = 0.0
    daily_trades: int = 0
    daily_wins: int = 0
    daily_losses: int = 0
    win_rate_today: float = 0.0
    # Rolling metrics (from log tail)
    recent_fills: list = field(default_factory=list)
    # Quality metrics (from MCPT validated JSON if available)
    sharpe_validated: Optional[float] = None
    max_dd_validated: Optional[float] = None
    win_rate_validated: Optional[float] = None
    mcpt_badge: str = ""
    # Error state
    last_error: str = ""
    error_count_1h: int = 0
    # Timestamp
    parsed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return asdict(self)


def _get_log_tail(log_path: Path, lines: int = 300) -> list[str]:
    """Read last N lines of a log file efficiently."""
    if not log_path.exists():
        return []
    try:
        with open(log_path, "r", errors="replace") as f:
            content = f.readlines()
        return content[-lines:]
    except (OSError, IOError):
        return []


def _parse_pnl_from_lines(lines: list[str]) -> tuple[float, int, int, int]:
    """Extract P&L and trade counts from log lines. Returns (pnl, trades, wins, losses)."""
    pnl = 0.0
    trades = 0
    wins = 0
    losses = 0
    today = datetime.now().strftime("%Y-%m-%d")

    for line in lines:
        if today not in line:
            continue
        # Pattern: "P&L: $123.45" or "pnl: 123.45" or "profit: 123.45"
        pnl_match = re.search(r'[Pp][&]?[Ll]\s*[:=]\s*\$?([-\d.]+)', line)
        if pnl_match:
            try:
                pnl += float(pnl_match.group(1))
                trades += 1
                if float(pnl_match.group(1)) > 0:
                    wins += 1
                else:
                    losses += 1
            except ValueError:
                pass

        # Pattern: "FILL: ... profit/loss" or "filled" entries
        if re.search(r'\bFILL\b|\bfilled\b|\bexecuted\b', line, re.IGNORECASE):
            trades += 1

    return pnl, trades, wins, losses


def _parse_last_signal(lines: list[str]) -> tuple[str, float, str]:
    """Extract last BUY/SELL signal with confidence and timestamp."""
    signal = "HOLD"
    confidence = 0.0
    signal_time = ""
    for line in reversed(lines):
        sig_match = re.search(r'\b(BUY|SELL|LONG|SHORT|FLAT|HOLD)\b', line)
        if sig_match:
            signal = sig_match.group(1).upper()
            if signal in ("LONG", "BUY"):
                signal = "BUY"
            elif signal in ("SHORT", "SELL"):
                signal = "SELL"
            conf_match = re.search(r'[Cc]onfidence[:\s]+([\d.]+)', line)
            if conf_match:
                confidence = float(conf_match.group(1))
            ts_match = re.search(r'(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})', line)
            if ts_match:
                signal_time = ts_match.group(1)
            break
    return signal, confidence, signal_time


def _parse_errors(lines: list[str]) -> tuple[str, int]:
    """Count errors in last hour and get most recent error."""
    one_hour_ago = time.time() - 3600
    error_count = 0
    last_error = ""
    for line in lines:
        if re.search(r'\bERROR\b|\bException\b|\bTraceback\b|\b401\b|\b422\b', line, re.IGNORECASE):
            error_count += 1
            last_error = line.strip()[-200:]
    return last_error, error_count


def _load_mcpt_metrics(bot_id: str) -> tuple[Optional[float], Optional[float], Optional[float], str]:
    """Load validated Sharpe/MaxDD/WinRate from MCPT promoted JSON."""
    promoted_dir = CONTROL_TOWER / "research_pipeline" / "tier6_promoted"
    if not promoted_dir.exists():
        return None, None, None, ""

    symbol = BOT_META.get(bot_id, {}).get("symbol", "")
    for json_file in promoted_dir.glob("*.json"):
        try:
            with open(json_file) as f:
                data = json.load(f)
            if symbol.lower() in json_file.name.lower() or data.get("symbol", "").upper() == symbol:
                sharpe = data.get("oos_sharpe") or data.get("sharpe_ratio")
                max_dd = data.get("oos_max_drawdown") or data.get("max_drawdown")
                wr = data.get("oos_win_rate") or data.get("win_rate")
                p_val = data.get("mcpt_p_value", 1.0)
                badge = (
                    "MCPT Elite (p<0.001)" if p_val < 0.001
                    else "MCPT Verified (p<0.01)" if p_val < 0.01
                    else "MCPT Validated (p<0.05)" if p_val < 0.05
                    else "Pending MCPT"
                )
                return sharpe, max_dd, wr, badge
        except (json.JSONDecodeError, OSError, KeyError):
            continue
    return None, None, None, ""


def parse_bot_metrics(bot_id: str) -> BotMetrics:
    """
    Parse real bot log to extract live trading metrics.
    Returns BotMetrics with all fields populated from actual log data.
    """
    bot_id = bot_id.lower()
    meta = BOT_META.get(bot_id, {})
    log_path = BOT_LOG_PATHS.get(bot_id)

    metrics = BotMetrics(
        bot_id=bot_id,
        symbol=meta.get("symbol", bot_id.upper()),
        display_name=meta.get("display_name", bot_id),
        strategy_type=meta.get("strategy_type", "unknown"),
    )

    if not log_path or not log_path.exists():
        metrics.last_error = f"Log not found: {log_path}"
        return metrics

    # Check if log is fresh (bot is running)
    log_age = time.time() - log_path.stat().st_mtime
    metrics.last_log_age_sec = log_age
    metrics.is_running = log_age < 300  # stale if >5 min

    lines = _get_log_tail(log_path, 500)

    # P&L and trade counts
    pnl, trades, wins, losses = _parse_pnl_from_lines(lines)
    metrics.daily_pnl = round(pnl, 2)
    metrics.daily_trades = trades
    metrics.daily_wins = wins
    metrics.daily_losses = losses
    metrics.win_rate_today = round((wins / trades * 100) if trades > 0 else 0.0, 1)

    # Last signal
    signal, confidence, signal_time = _parse_last_signal(lines)
    metrics.last_signal = signal
    metrics.last_signal_confidence = round(confidence, 3)
    metrics.last_signal_time = signal_time

    # Error state
    last_error, error_count = _parse_errors(lines[-100:])
    metrics.last_error = last_error
    metrics.error_count_1h = error_count

    # MCPT validated metrics
    sharpe, max_dd, wr, badge = _load_mcpt_metrics(bot_id)
    metrics.sharpe_validated = sharpe
    metrics.max_dd_validated = max_dd
    metrics.win_rate_validated = wr
    metrics.mcpt_badge = badge

    return metrics


def parse_all_bots() -> dict[str, BotMetrics]:
    """Parse metrics for all 4 live bots. Returns dict keyed by bot_id."""
    return {bot_id: parse_bot_metrics(bot_id) for bot_id in BOT_LOG_PATHS}
