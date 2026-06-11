"""Helpers for compact E2E Execution Sentinel health summaries."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


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
