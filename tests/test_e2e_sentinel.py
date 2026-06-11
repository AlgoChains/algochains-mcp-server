from __future__ import annotations

from algochains_mcp.e2e_sentinel import compact_e2e_sentinel_state


def test_signal_submitted_pending_reconciled_stale_is_resolved() -> None:
    state = compact_e2e_sentinel_state(
        {
            "generated_at": "2026-06-11T12:40:49Z",
            "classification": {
                "state": "signal_submitted_pending",
                "severity": "warning",
                "issue_class": "signal_submitted_pending",
                "incident_id": "1e7d1daa6ea3ac9d",
                "why": (
                    "Latest trade_log row "
                    "fbf37889-e396-4c43-86c3-dded0f10e513 has no fill/exit after 10min"
                ),
                "needs_owner": False,
                "safe_auto_action": "reconcile_stale_signal",
                "skill_routes": ["trading-system-health-audit"],
            },
            "evidence": {
                "broker": {
                    "positions_count": 0,
                    "working_orders_count": 0,
                    "positions_ok": True,
                    "orders_ok": True,
                    "fills_ok": True,
                },
                "process": {"pids": [16479, 16506], "fd_count": None},
                "log": {"last_scan_age_sec": 4.369760513305664},
            },
            "actions": [
                {
                    "action": "reconcile_stale_signal",
                    "action_key": "reconcile_stale_signal:201",
                    "status": "ok",
                    "row_id": "201",
                    "signal_id": "fbf37889-e396-4c43-86c3-dded0f10e513",
                    "reason": (
                        "broker flat, no fill/exit after 600s - "
                        "marked sentinel_reconciled_stale"
                    ),
                }
            ],
        }
    )

    assert state["raw_state"] == "signal_submitted_pending"
    assert state["raw_severity"] == "warning"
    assert state["state"] == "resolved"
    assert state["severity"] == "info"
    assert state["resolved_by_auto_action"] is True
    assert state["resolution"]["row_id"] == "201"
    assert state["broker_flat"] is True
    assert state["positions_count"] == 0
    assert state["working_orders_count"] == 0
    assert state["pids"] == [16479, 16506]
    assert state["fd_count"] is None


def test_signal_submitted_pending_without_reconciliation_stays_warning() -> None:
    state = compact_e2e_sentinel_state(
        {
            "classification": {
                "state": "signal_submitted_pending",
                "severity": "warning",
            },
            "evidence": {
                "broker": {
                    "positions_count": 0,
                    "working_orders_count": 0,
                }
            },
        }
    )

    assert state["state"] == "signal_submitted_pending"
    assert state["severity"] == "warning"
    assert state["resolved_by_auto_action"] is False
    assert state["resolution"] is None
    assert state["broker_flat"] is True


def test_partial_broker_evidence_preserves_quality_flags() -> None:
    state = compact_e2e_sentinel_state(
        {
            "classification": {
                "state": "unknown_cancel_reason",
                "severity": "warning",
            },
            "evidence": {
                "broker": {
                    "positions_count": None,
                    "working_orders_count": None,
                    "positions_ok": True,
                    "orders_ok": False,
                    "fills_ok": False,
                }
            },
        }
    )

    assert state["broker_flat"] is None
    assert state["positions_ok"] is True
    assert state["orders_ok"] is False
    assert state["fills_ok"] is False
    assert state["broker_snapshot_partial"] is True
