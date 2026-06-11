"""Compact E2E execution sentinel state for MCP health surfaces."""
from __future__ import annotations

from typing import Any


_STALE_RECONCILIATION_REASON = "sentinel_reconciled_stale"


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _actions(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        if isinstance(value, str) and value.strip():
            return int(value)
    except ValueError:
        return None
    return None


def _bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _compact_action(action: dict[str, Any]) -> dict[str, Any]:
    keys = ("action", "action_key", "status", "row_id", "signal_id", "reason")
    return {key: action.get(key) for key in keys if action.get(key) is not None}


def _is_stale_reconciliation(action: dict[str, Any]) -> bool:
    status = str(action.get("status") or "").lower()
    action_name = str(action.get("action") or "").lower()
    action_key = str(action.get("action_key") or "").lower()
    reason = str(action.get("reason") or "").lower()
    return (
        status == "ok"
        and (
            action_name == "reconcile_stale_signal"
            or action_key.startswith("reconcile_stale_signal:")
        )
        and _STALE_RECONCILIATION_REASON in reason
    )


def _broker_flat_from_evidence(broker: dict[str, Any]) -> bool | None:
    positions_count = _int_or_none(broker.get("positions_count"))
    working_orders_count = _int_or_none(
        broker.get("working_orders_count", broker.get("orders_count"))
    )
    positions_ok = _bool_or_none(broker.get("positions_ok"))
    orders_ok = _bool_or_none(broker.get("orders_ok"))

    if positions_ok is False or orders_ok is False:
        return None
    if positions_count is None or working_orders_count is None:
        return None
    return positions_count == 0 and working_orders_count == 0


def compact_e2e_sentinel_state(raw: dict[str, Any]) -> dict[str, Any]:
    """Return bounded, normalized E2E sentinel state for public MCP health.

    The control-tower sentinel can auto-reconcile a stale submitted signal when the
    broker is flat. Preserve the raw classification, but present that case as
    resolved so health consumers do not keep paging on a DB-only cleanup.
    """
    classification = _mapping(raw.get("classification"))
    evidence = _mapping(raw.get("evidence"))
    broker = _mapping(evidence.get("broker"))
    process = _mapping(evidence.get("process"))
    log = _mapping(evidence.get("log"))
    rate_limits = _mapping(raw.get("rate_limits"))
    actions = _actions(raw.get("actions") if "actions" in raw else raw.get("action"))

    raw_state = classification.get("state")
    raw_severity = classification.get("severity")
    stale_resolution = next(
        (_compact_action(action) for action in actions if _is_stale_reconciliation(action)),
        None,
    )
    resolved_by_auto_action = (
        raw_state == "signal_submitted_pending" and stale_resolution is not None
    )

    positions_count = _int_or_none(broker.get("positions_count"))
    working_orders_count = _int_or_none(
        broker.get("working_orders_count", broker.get("orders_count"))
    )
    positions_ok = _bool_or_none(broker.get("positions_ok"))
    orders_ok = _bool_or_none(broker.get("orders_ok"))
    fills_ok = _bool_or_none(broker.get("fills_ok"))
    broker_snapshot_partial = _bool_or_none(broker.get("broker_snapshot_partial"))
    if broker_snapshot_partial is None:
        broker_snapshot_partial = any(flag is False for flag in (positions_ok, orders_ok, fills_ok))

    state = "resolved" if resolved_by_auto_action else raw_state
    severity = "info" if resolved_by_auto_action else raw_severity

    out: dict[str, Any] = {
        "generated_at": raw.get("generated_at"),
        "state": state,
        "severity": severity,
        "raw_state": raw_state,
        "raw_severity": raw_severity,
        "issue_class": classification.get("issue_class"),
        "incident_id": classification.get("incident_id"),
        "why": classification.get("why"),
        "needs_owner": classification.get("needs_owner"),
        "safe_auto_action": classification.get("safe_auto_action"),
        "skill_routes": classification.get("skill_routes", []),
        "resolved_by_auto_action": resolved_by_auto_action,
        "resolution": stale_resolution,
        "actions": [_compact_action(action) for action in actions[:5]],
        "broker_flat": _broker_flat_from_evidence(broker),
        "positions_count": positions_count,
        "working_orders_count": working_orders_count,
        "positions_ok": positions_ok,
        "orders_ok": orders_ok,
        "fills_ok": fills_ok,
        "broker_snapshot_partial": broker_snapshot_partial,
        "pids": process.get("pids", []),
        "fd_count": process.get("fd_count"),
        "last_scan_age_sec": log.get("last_scan_age_sec"),
        "memory_status": _mapping(raw.get("memory")).get("status"),
        "slack_status": _mapping(raw.get("slack")).get("status"),
        "last_memory_at": rate_limits.get("last_memory_at"),
        "last_slack_at": rate_limits.get("last_slack_at"),
    }
    return out
