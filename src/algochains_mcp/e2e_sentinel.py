"""Utilities for summarizing E2E execution sentinel state.

The control tower owns the full sentinel classifier and evidence collection.
This module only creates a compact, non-sensitive summary for MCP and HTTP
health surfaces.
"""
from __future__ import annotations

from collections.abc import Mapping
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
