"""Read-only daily loss proximity guard status for MCP health surfaces."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import default_control_tower
from .trading_guardrails import MAX_DAILY_LOSS_USD

STATE_CANDIDATES = (
    Path("state") / "daily_loss_proximity_state.json",
    Path("state") / "daily_loss_proximity_guard_state.json",
    Path("state") / "daily_loss_guard_state.json",
)

ALERT_THRESHOLD_PCT = 80.0
BLOCK_SCALPER_THRESHOLD_PCT = 95.0
MNQ_SWING_EXEMPT = True

_PNL_KEYS = (
    "daily_pnl",
    "daily_pnl_usd",
    "realized_pnl_today",
    "today_realized_pnl",
    "daily_realized_pnl",
)

_VERIFIED_SOURCES = frozenset({"broker", "tradovate", "fills", "cash_balance", "rithmic"})


def _first_existing(root: Path, candidates: tuple[Path, ...]) -> Path | None:
    for relative in candidates:
        path = root / relative
        if path.exists():
            return path
    return None


def _read_state(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _state_pnl_verified(container: dict[str, Any]) -> bool:
    """Fail closed: only trust state P&L when verification is explicitly true."""
    if "pnl_verified" in container:
        return bool(container.get("pnl_verified"))
    if "source_verified" in container:
        return bool(container.get("source_verified"))
    if "verified" in container:
        return bool(container.get("verified"))
    source = str(container.get("source") or container.get("pnl_source") or "").strip().lower()
    if source in _VERIFIED_SOURCES:
        return True
    return False


def _resolve_daily_pnl(
    state: dict[str, Any],
) -> tuple[float | None, str, bool]:
    """Return (daily_pnl, source, verified)."""
    env_val = os.environ.get("TODAY_REALIZED_PNL", "").strip()
    if env_val:
        pnl = _coerce_float(env_val)
        if pnl is not None:
            return pnl, "env:TODAY_REALIZED_PNL", True

    for key in _PNL_KEYS:
        if key in state:
            pnl = _coerce_float(state.get(key))
            if pnl is not None:
                return pnl, f"state:{key}", _state_pnl_verified(state)

    nested = state.get("daily")
    if isinstance(nested, dict):
        for key in _PNL_KEYS:
            if key in nested:
                pnl = _coerce_float(nested.get(key))
                if pnl is not None:
                    return pnl, f"state:daily.{key}", _state_pnl_verified(nested)

    return None, "unknown", False


def _reconcile_bot_daily_pnl(
    root: Path,
) -> tuple[float | None, str, bool, dict[str, Any]]:
    """Aggregate today's P&L from live bot metrics when state/env is missing."""
    try:
        from .live_bot_intelligence.metrics_parser import parse_all_bots
    except Exception:
        return None, "unknown", False, {}

    try:
        bots = parse_all_bots()
    except Exception:
        return None, "unknown", False, {}

    if not bots:
        return None, "unknown", False, {}

    total_pnl = 0.0
    fresh_bots: list[str] = []
    per_bot: dict[str, Any] = {}
    for bot_id, metrics in bots.items():
        per_bot[bot_id] = {
            "daily_pnl_usd": metrics.daily_pnl,
            "daily_trades": metrics.daily_trades,
            "is_running": metrics.is_running,
            "last_log_age_sec": metrics.last_log_age_sec,
        }
        total_pnl += float(metrics.daily_pnl or 0.0)
        if metrics.is_running:
            fresh_bots.append(bot_id)

    details = {"bot_breakdown": per_bot, "fresh_bots": fresh_bots}
    if not fresh_bots:
        return None, "bots:stale", False, details

    return round(total_pnl, 2), "bots:aggregate", True, details


def _resolve_limit_usd(state: dict[str, Any]) -> float:
    for key in ("daily_loss_limit_usd", "limit_usd", "max_daily_loss_usd"):
        value = _coerce_float(state.get(key))
        if value is not None and value > 0:
            return value
    env_limit = os.environ.get("GUARDRAIL_DAILY_LOSS_MAX", "").strip()
    if env_limit:
        value = _coerce_float(env_limit)
        if value is not None and value > 0:
            return value
    return float(MAX_DAILY_LOSS_USD)


def _classify(utilization_pct: float, *, verified: bool) -> str:
    if not verified:
        return "DEGRADED"
    if utilization_pct >= BLOCK_SCALPER_THRESHOLD_PCT:
        return "BLOCK"
    if utilization_pct >= ALERT_THRESHOLD_PCT:
        return "WARN"
    return "OK"


def _summary_line(status: str, daily_pnl: float, utilization_pct: float, buffer_usd: float) -> str:
    prefix = {
        "OK": "[OK]",
        "WARN": "[WARN]",
        "BLOCK": "[BLOCK]",
        "DEGRADED": "[DEGRADED]",
    }.get(status, "[UNKNOWN]")
    return (
        f"{prefix} Daily P&L ${daily_pnl:.2f} "
        f"({utilization_pct:.0f}% of limit, ${buffer_usd:.0f} buffer)"
    )


def _build_payload(
    *,
    status: str,
    summary: str,
    daily_pnl: float | None,
    limit_usd: float,
    pnl_source: str,
    pnl_verified: bool,
    root: Path,
    state_path: Path | None,
    state: dict[str, Any],
    reconciliation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    loss_usd = round(max(0.0, -daily_pnl), 2) if daily_pnl is not None else None
    utilization_pct = (
        round((loss_usd / limit_usd) * 100, 1)
        if daily_pnl is not None and limit_usd > 0
        else None
    )
    buffer_usd = (
        round(max(0.0, limit_usd - loss_usd), 2)
        if loss_usd is not None
        else None
    )
    payload: dict[str, Any] = {
        "status": status,
        "summary": summary,
        "formatted_line": summary,
        "daily_pnl_usd": round(daily_pnl, 2) if daily_pnl is not None else None,
        "daily_loss_limit_usd": limit_usd,
        "loss_usd": loss_usd,
        "utilization_pct": utilization_pct,
        "buffer_usd": buffer_usd,
        "alert_threshold_pct": ALERT_THRESHOLD_PCT,
        "block_scalper_threshold_pct": BLOCK_SCALPER_THRESHOLD_PCT,
        "mnq_swing_exempt": MNQ_SWING_EXEMPT,
        "pnl_source": pnl_source,
        "pnl_verified": pnl_verified,
        "control_tower": str(root),
        "state_path": str(state_path) if state_path else None,
        "state_exists": state_path is not None,
        "policy": (
            "Alert at 80% of daily loss limit; block new scalper entries at 95%; "
            "MNQ swing exempt"
        ),
        "checked_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    if daily_pnl is not None and pnl_verified:
        payload["alert_at_loss_usd"] = round(limit_usd * ALERT_THRESHOLD_PCT / 100, 2)
        payload["block_scalper_at_loss_usd"] = round(
            limit_usd * BLOCK_SCALPER_THRESHOLD_PCT / 100, 2
        )
        payload["block_new_scalper_entries"] = status == "BLOCK"
    if state:
        payload["state"] = state
    if reconciliation:
        payload["reconciliation"] = reconciliation
    if status == "DEGRADED" and daily_pnl is None:
        payload["action"] = (
            "Set TODAY_REALIZED_PNL, write state/daily_loss_proximity_state.json with "
            "pnl_verified=true, or ensure live bot logs are fresh before trusting OK status."
        )
    return payload


def get_daily_loss_proximity(
    *,
    control_tower: Path | None = None,
) -> dict[str, Any]:
    """Return daily loss proximity guard evidence for watchdog triage."""
    root = control_tower or default_control_tower()
    state_path = _first_existing(root, STATE_CANDIDATES)
    state = _read_state(state_path)

    daily_pnl, pnl_source, pnl_verified = _resolve_daily_pnl(state)
    limit_usd = _resolve_limit_usd(state)
    reconciliation: dict[str, Any] | None = None

    if daily_pnl is None or not pnl_verified:
        bot_pnl, bot_source, bot_verified, bot_details = _reconcile_bot_daily_pnl(root)
        if bot_pnl is not None and bot_verified:
            daily_pnl = bot_pnl
            pnl_source = bot_source
            pnl_verified = True
            reconciliation = bot_details

    if daily_pnl is None:
        summary = "[DEGRADED] Daily P&L unavailable — proximity guard unverified"
        return _build_payload(
            status="DEGRADED",
            summary=summary,
            daily_pnl=None,
            limit_usd=limit_usd,
            pnl_source=pnl_source,
            pnl_verified=False,
            root=root,
            state_path=state_path,
            state=state,
            reconciliation=reconciliation,
        )

    loss_usd = max(0.0, -daily_pnl)
    utilization_pct = round((loss_usd / limit_usd) * 100, 1) if limit_usd > 0 else 0.0
    status = _classify(utilization_pct, verified=pnl_verified)
    buffer_usd = round(max(0.0, limit_usd - loss_usd), 2)
    summary = _summary_line(status, daily_pnl, utilization_pct, buffer_usd)

    return _build_payload(
        status=status,
        summary=summary,
        daily_pnl=daily_pnl,
        limit_usd=limit_usd,
        pnl_source=pnl_source,
        pnl_verified=pnl_verified,
        root=root,
        state_path=state_path,
        state=state,
        reconciliation=reconciliation,
    )
