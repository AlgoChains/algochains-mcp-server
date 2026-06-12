"""Utilities for summarizing E2E execution sentinel state.

The control tower owns the full sentinel classifier and evidence collection.
This module only creates a compact, non-sensitive summary for MCP and HTTP
health surfaces.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "ok"}:
            return True
        if normalized in {"false", "0", "no", "error", "failed"}:
            return False
    return None


def _first_bool(source: Mapping[str, Any], *keys: str) -> bool | None:
    for key in keys:
        if key in source:
            parsed = _coerce_bool(source.get(key))
            if parsed is not None:
                return parsed
    return None


def _count_from(source: Mapping[str, Any], count_key: str, payload_key: str) -> int | None:
    raw_count = source.get(count_key)
    if isinstance(raw_count, bool):
        return None
    if isinstance(raw_count, int):
        return raw_count
    if isinstance(raw_count, float) and raw_count.is_integer():
        return int(raw_count)
    payload = source.get(payload_key)
    if isinstance(payload, list):
        return len(payload)
    return None


def _explicit_none(source: Mapping[str, Any], *keys: str) -> bool:
    return any(key in source and source.get(key) is None for key in keys)


def summarize_e2e_sentinel_state(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    """Build a compact Sentinel summary without exposing raw broker payloads.

    Broker state can be partial: positions, working orders, or fills may be
    unavailable independently. Missing counts must stay "unknown" instead of
    being interpreted as non-flat exposure.
    """
    state = _as_mapping(raw)
    classification = _as_mapping(state.get("classification"))
    evidence = _as_mapping(state.get("evidence"))
    broker = _as_mapping(evidence.get("broker"))
    process = _as_mapping(evidence.get("process"))
    log = _as_mapping(evidence.get("log"))
    rate_limits = _as_mapping(state.get("rate_limits"))

    issue_class = (
        classification.get("issue_class")
        or classification.get("reason")
        or classification.get("outcome")
    )
    why = (
        classification.get("why")
        or classification.get("description")
        or classification.get("reason")
    )

    positions_count = _count_from(broker, "positions_count", "positions")
    working_orders_count = _count_from(broker, "working_orders_count", "working_orders")
    if working_orders_count is None:
        working_orders_count = _count_from(broker, "orders_count", "orders")

    positions_ok = _first_bool(broker, "positions_ok")
    orders_ok = _first_bool(broker, "orders_ok", "working_orders_ok")
    fills_ok = _first_bool(broker, "fills_ok")

    broker_snapshot_partial = any(flag is False for flag in (positions_ok, orders_ok, fills_ok))
    broker_snapshot_partial = broker_snapshot_partial or _explicit_none(
        broker,
        "positions",
        "working_orders",
        "orders",
        "fills",
    )

    broker_flat: bool | None = None
    positions_available = positions_ok is not False and positions_count is not None
    orders_available = orders_ok is not False and working_orders_count is not None
    if positions_available and orders_available:
        broker_flat = positions_count == 0 and working_orders_count == 0

    return {
        "generated_at": state.get("generated_at"),
        "ts": state.get("last_check") or state.get("generated_at"),
        "state": classification.get("state") or classification.get("outcome"),
        "outcome": classification.get("outcome") or classification.get("state"),
        "severity": classification.get("severity"),
        "issue_class": issue_class,
        "reason": classification.get("reason") or issue_class,
        "description": classification.get("description") or why,
        "why": why,
        "incident_id": classification.get("incident_id"),
        "needs_owner": classification.get("needs_owner"),
        "safe_auto_action": classification.get("safe_auto_action"),
        "skill_routes": classification.get("skill_routes", []),
        "broker_flat": broker_flat,
        "broker_snapshot_partial": broker_snapshot_partial,
        "positions_ok": positions_ok,
        "orders_ok": orders_ok,
        "fills_ok": fills_ok,
        "positions_count": positions_count,
        "working_orders_count": working_orders_count,
        "pids": process.get("pids", []),
        "fd_count": process.get("fd_count"),
        "last_scan_age_sec": log.get("last_scan_age_sec"),
        "memory_status": _as_mapping(state.get("memory")).get("status"),
        "slack_status": _as_mapping(state.get("slack")).get("status"),
        "last_memory_at": rate_limits.get("last_memory_at"),
        "last_slack_at": rate_limits.get("last_slack_at"),
    }


_SUCCESS_STATUSES = {"ok", "success", "succeeded", "completed", "resolved"}


def _iter_action_candidates(value: Any) -> Iterable[Mapping[str, Any]]:
    """Yield action dicts from the known sentinel action payload shapes."""
    if isinstance(value, Mapping):
        yield value
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, Mapping):
                yield item


def iter_sentinel_actions(raw_state: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    """Yield safe auto-action records from a raw sentinel state payload."""
    classification = raw_state.get("classification")
    scopes = [raw_state]
    if isinstance(classification, Mapping):
        scopes.append(classification)

    for scope in scopes:
        for key in (
            "action",
            "actions",
            "auto_action",
            "auto_actions",
            "safe_auto_action",
            "safe_auto_actions",
            "actions_taken",
        ):
            yield from _iter_action_candidates(scope.get(key))


def find_successful_stale_reconciliation(
    raw_state: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    """Return the successful stale-signal reconciliation action, if present."""
    for action in iter_sentinel_actions(raw_state):
        action_name = str(action.get("action") or "")
        action_key = str(action.get("action_key") or "")
        reason = str(action.get("reason") or action.get("exit_reason") or "")
        status = str(action.get("status") or "").lower()

        is_reconcile_action = (
            action_name == "reconcile_stale_signal"
            or action_key.startswith("reconcile_stale_signal:")
        )
        is_stale_cleanup = "sentinel_reconciled_stale" in reason
        if status in _SUCCESS_STATUSES and (is_reconcile_action or is_stale_cleanup):
            return action
    return None


def apply_effective_sentinel_resolution(
    summary: Mapping[str, Any],
    raw_state: Mapping[str, Any],
    *,
    state_key: str = "state",
) -> dict[str, Any]:
    """Apply safe-action resolution to a sentinel summary without losing raw state.

    The E2E Sentinel may classify a stale submitted signal as warning and then
    immediately reconcile it because the broker is flat with no orders. Health
    consumers should see that as effectively resolved, while raw classifier fields
    stay available for audit/debugging.
    """
    out = dict(summary)
    if state_key in out:
        raw_key = "raw_outcome" if state_key == "outcome" else "raw_state"
        out.setdefault(raw_key, out.get(state_key))
    if "severity" in out:
        out.setdefault("raw_severity", out.get("severity"))

    action = find_successful_stale_reconciliation(raw_state)
    if action is None:
        return out

    out[state_key] = "resolved"
    out["severity"] = "info"
    out["resolved"] = True
    out["resolved_by"] = "reconcile_stale_signal"
    out["resolution_action_key"] = action.get("action_key")
    out["resolution_reason"] = action.get("reason") or "successful stale signal reconciliation"
    return out
