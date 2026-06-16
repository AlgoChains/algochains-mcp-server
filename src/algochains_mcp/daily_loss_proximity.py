"""Read-only fleet daily-loss proximity for watchdog / guard surfaces."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .paths import default_control_tower
from .trading_guardrails import MAX_DAILY_LOSS_USD

DEFAULT_ALERT_AT_PCT = 80.0
DEFAULT_BLOCK_SCALPER_AT_PCT = 95.0
SWING_EXEMPT_BOT_IDS = frozenset({"mes", "nq"})


def _is_scalper(strategy_type: str) -> bool:
    return "scalper" in (strategy_type or "").lower()


def _load_env_daily_pnl() -> tuple[float | None, bool]:
    raw = os.environ.get("TODAY_REALIZED_PNL", "").strip()
    if not raw:
        return None, False
    try:
        return float(raw), True
    except ValueError:
        return None, False


def _load_state_daily_pnl(control_tower: Path) -> tuple[float | None, bool]:
    candidates = (
        control_tower / "state" / "daily_loss_proximity.json",
        control_tower / "state" / "fleet_daily_pnl.json",
        control_tower / "state" / "signal_health.json",
    )
    for path in candidates:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        for key in ("daily_pnl_usd", "daily_pnl", "fleet_daily_pnl", "realized_pnl_today"):
            if key in payload:
                try:
                    return float(payload[key]), True
                except (TypeError, ValueError):
                    pass
        if path.name == "signal_health.json":
            total = 0.0
            found = False
            for value in payload.values():
                if not isinstance(value, dict):
                    continue
                for key in ("daily_pnl", "daily_pnl_usd", "realized_pnl_today"):
                    if key in value:
                        try:
                            total += float(value[key])
                            found = True
                        except (TypeError, ValueError):
                            pass
            if found:
                return round(total, 2), True
    return None, False


def _bot_log_path(control_tower: Path, bot_id: str) -> Path:
    names = {
        "mnq": "futures_bot_live.log",
        "cl": "cl_futures_live.log",
        "mes": "mes_swing_live.log",
        "nq": "nq_swing_live.log",
    }
    return control_tower / "logs" / names.get(bot_id, f"{bot_id}.log")


def _bot_metrics_verified(bot_id: str, metrics: Any, control_tower: Path) -> bool:
    source = getattr(metrics, "metrics_source", "")
    if source == "supabase":
        return True
    return _bot_log_path(control_tower, bot_id).exists()


def _format_summary(
    *,
    status: str,
    daily_pnl: float,
    utilization_pct: float,
    daily_loss_limit: float,
) -> str:
    buffer = max(0.0, daily_loss_limit + daily_pnl)
    prefix = {
        "ok": "[OK]",
        "warn": "[WARN]",
        "block_scalpers": "[BLOCK]",
        "halt": "[HALT]",
        "pnl_unverified": "[DEGRADED]",
    }.get(status, "[UNKNOWN]")
    return (
        f"{prefix} Daily P&L ${daily_pnl:.2f} "
        f"({utilization_pct:.0f}% of limit, ${buffer:.0f} buffer)"
    )


def get_daily_loss_proximity(
    *,
    alert_at_pct: float = DEFAULT_ALERT_AT_PCT,
    block_scalper_at_pct: float = DEFAULT_BLOCK_SCALPER_AT_PCT,
    daily_loss_limit_usd: float | None = None,
    control_tower: Path | None = None,
) -> dict[str, Any]:
    """Return fleet daily-loss proximity against the hard-coded limit.

    Alert at ``alert_at_pct`` (default 80%). Block new scalper entries at
    ``block_scalper_at_pct`` (default 95%). Swing bots (MES/NQ) remain exempt
    from the scalper entry block.
    """
    root = control_tower or default_control_tower()
    limit = float(
        daily_loss_limit_usd
        if daily_loss_limit_usd is not None
        else os.environ.get("GUARDRAIL_DAILY_LOSS_MAX", MAX_DAILY_LOSS_USD)
    )

    from .live_bot_intelligence.metrics_parser import parse_all_bots

    all_metrics = parse_all_bots()
    per_bot: list[dict[str, Any]] = []
    verified_sources = 0
    fleet_realized = 0.0

    for bot_id, metrics in all_metrics.items():
        verified = _bot_metrics_verified(bot_id, metrics, root)
        if verified:
            verified_sources += 1
        pnl = float(getattr(metrics, "daily_pnl", 0.0) or 0.0)
        fleet_realized += pnl
        per_bot.append(
            {
                "bot_id": bot_id,
                "symbol": metrics.symbol,
                "strategy_type": metrics.strategy_type,
                "daily_pnl_usd": round(pnl, 2),
                "metrics_source": metrics.metrics_source,
                "pnl_verified": verified,
                "is_scalper": _is_scalper(metrics.strategy_type),
                "swing_entry_block_exempt": bot_id in SWING_EXEMPT_BOT_IDS
                or metrics.strategy_type == "swing",
            }
        )

    env_pnl, env_verified = _load_env_daily_pnl()
    state_pnl, state_verified = _load_state_daily_pnl(root)

    pnl_verified = verified_sources > 0 or env_verified or state_verified
    daily_pnl = round(fleet_realized, 2)
    pnl_source = "bot_metrics"

    if env_verified and env_pnl is not None:
        daily_pnl = round(env_pnl, 2)
        pnl_source = "TODAY_REALIZED_PNL"
        pnl_verified = True
    elif state_verified and state_pnl is not None:
        daily_pnl = round(state_pnl, 2)
        pnl_source = "control_tower_state"
        pnl_verified = True

    loss_usd = max(0.0, -daily_pnl)
    utilization_pct = round((loss_usd / limit) * 100, 1) if limit > 0 else 0.0
    buffer_usd = round(max(0.0, limit - loss_usd), 2)

    if not pnl_verified:
        status = "pnl_unverified"
    elif loss_usd >= limit:
        status = "halt"
    elif utilization_pct >= block_scalper_at_pct:
        status = "block_scalpers"
    elif utilization_pct >= alert_at_pct:
        status = "warn"
    else:
        status = "ok"

    return {
        "status": status,
        "summary_line": _format_summary(
            status=status,
            daily_pnl=daily_pnl,
            utilization_pct=utilization_pct,
            daily_loss_limit=limit,
        ),
        "daily_pnl_usd": daily_pnl,
        "daily_loss_usd": round(loss_usd, 2),
        "daily_loss_limit_usd": limit,
        "loss_utilization_pct": utilization_pct,
        "buffer_usd": buffer_usd,
        "pnl_verified": pnl_verified,
        "pnl_source": pnl_source,
        "alert_at_pct": alert_at_pct,
        "block_scalper_at_pct": block_scalper_at_pct,
        "alert_triggered": utilization_pct >= alert_at_pct and pnl_verified,
        "block_scalper_entries": utilization_pct >= block_scalper_at_pct and pnl_verified,
        "swing_exempt_bot_ids": sorted(SWING_EXEMPT_BOT_IDS),
        "per_bot": per_bot,
        "verified_bot_sources": verified_sources,
        "control_tower": str(root),
    }
