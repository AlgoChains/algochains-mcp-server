from __future__ import annotations

import asyncio
import json

from algochains_mcp.e2e_sentinel import apply_effective_sentinel_resolution


def _resolved_payload() -> dict:
    return {
        "generated_at": "2026-06-11T12:59:39Z",
        "classification": {
            "state": "warning",
            "severity": "warning",
            "issue_class": "signal_submitted_pending",
            "why": "Latest trade_log row has no fill/exit after 33min",
        },
        "actions": [
            {
                "action": "reconcile_stale_signal",
                "action_key": "reconcile_stale_signal:162",
                "status": "ok",
                "row_id": "162",
                "signal_id": "d302b2ba-1c6b-49ba-bd69-ed60eaabff77",
                "reason": "broker flat, no fill/exit after 600s - marked sentinel_reconciled_stale",
            }
        ],
    }


def test_successful_stale_reconciliation_downgrades_effective_state():
    raw = _resolved_payload()
    summary = {
        "state": raw["classification"]["state"],
        "severity": raw["classification"]["severity"],
        "issue_class": raw["classification"]["issue_class"],
    }

    result = apply_effective_sentinel_resolution(summary, raw)

    assert result["state"] == "resolved"
    assert result["severity"] == "info"
    assert result["resolved"] is True
    assert result["resolved_by"] == "reconcile_stale_signal"
    assert result["raw_state"] == "warning"
    assert result["raw_severity"] == "warning"
    assert result["resolution_action_key"] == "reconcile_stale_signal:162"


def test_failed_reconciliation_preserves_warning_state():
    raw = _resolved_payload()
    raw["actions"][0]["status"] = "error"
    summary = {
        "state": "warning",
        "severity": "warning",
        "issue_class": "signal_submitted_pending",
    }

    result = apply_effective_sentinel_resolution(summary, raw)

    assert result["state"] == "warning"
    assert result["severity"] == "warning"
    assert result.get("resolved") is not True
    assert result["raw_state"] == "warning"
    assert result["raw_severity"] == "warning"


def test_prior_stale_reconciliation_does_not_hide_new_warning():
    raw = _resolved_payload()
    raw["classification"] = {
        "state": "warning",
        "severity": "warning",
        "issue_class": "broker_cancel_failed",
        "why": "Latest cancel request returned an error",
    }
    summary = {
        "state": "warning",
        "severity": "warning",
        "issue_class": "broker_cancel_failed",
        "why": "Latest cancel request returned an error",
    }

    result = apply_effective_sentinel_resolution(summary, raw)

    assert result["state"] == "warning"
    assert result["severity"] == "warning"
    assert result.get("resolved") is not True
    assert result["raw_state"] == "warning"
    assert result["raw_severity"] == "warning"


def test_outcome_summary_uses_raw_outcome_for_http_bridge_status():
    raw = {
        "classification": {
            "outcome": "signal_submitted_pending",
            "severity": "warning",
        },
        "safe_auto_action": {
            "action_key": "reconcile_stale_signal:162",
            "status": "ok",
            "reason": "marked sentinel_reconciled_stale",
        },
    }
    summary = {
        "outcome": "signal_submitted_pending",
        "severity": "warning",
    }

    result = apply_effective_sentinel_resolution(summary, raw, state_key="outcome")

    assert result["outcome"] == "resolved"
    assert result["severity"] == "info"
    assert result["raw_outcome"] == "signal_submitted_pending"
    assert result["raw_severity"] == "warning"


def test_get_bot_health_returns_effective_resolved_sentinel(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "e2e_execution_sentinel.json").write_text(json.dumps(_resolved_payload()))
    monkeypatch.setenv("ALGOCHAINS_CONTROL_TOWER", str(tmp_path))

    import algochains_mcp.server as srv

    result = asyncio.run(srv.call_tool("get_bot_health", {}))
    text = result[0].text if hasattr(result[0], "text") else str(result[0])
    payload = json.loads(text)
    sentinel = payload["e2e_sentinel"]

    assert sentinel["state"] == "resolved"
    assert sentinel["severity"] == "info"
    assert sentinel["raw_state"] == "warning"
    assert sentinel["raw_severity"] == "warning"
    assert sentinel["resolved_by"] == "reconcile_stale_signal"
