from __future__ import annotations

from algochains_mcp.e2e_sentinel import summarize_e2e_sentinel_state


def test_partial_broker_snapshot_preserves_quality_flags() -> None:
    raw = {
        "generated_at": "2026-06-11T01:10:50Z",
        "classification": {
            "state": "warning",
            "severity": "WARNING",
            "issue_class": "unknown_cancel_reason",
            "incident_id": "6601f901f68f6315",
            "why": "Broker snapshot partial; working orders or fills unavailable.",
            "needs_owner": False,
            "safe_auto_action": "none",
            "skill_routes": ["trading-system-health-audit"],
        },
        "evidence": {
            "broker": {
                "positions": None,
                "orders": None,
                "orders_ok": False,
                "fills_ok": False,
            },
            "process": {"pids": [16479, 16506], "fd_count": None},
            "log": {"last_scan_age_sec": 2.6348750591278076},
        },
    }

    summary = summarize_e2e_sentinel_state(raw)

    assert summary["issue_class"] == "unknown_cancel_reason"
    assert summary["why"] == "Broker snapshot partial; working orders or fills unavailable."
    assert summary["orders_ok"] is False
    assert summary["fills_ok"] is False
    assert summary["broker_snapshot_partial"] is True
    assert summary["positions_count"] is None
    assert summary["working_orders_count"] is None
    assert summary["broker_flat"] is None
    assert summary["pids"] == [16479, 16506]


def test_complete_flat_snapshot_reports_broker_flat() -> None:
    raw = {
        "classification": {"state": "ok", "issue_class": "none"},
        "evidence": {
            "broker": {
                "positions_count": 0,
                "working_orders_count": 0,
                "positions_ok": True,
                "orders_ok": True,
                "fills_ok": True,
            }
        },
    }

    summary = summarize_e2e_sentinel_state(raw)

    assert summary["broker_snapshot_partial"] is False
    assert summary["broker_flat"] is True


def test_absent_broker_counts_do_not_imply_not_flat() -> None:
    summary = summarize_e2e_sentinel_state({"classification": {"state": "warning"}})

    assert summary["broker_snapshot_partial"] is False
    assert summary["positions_count"] is None
    assert summary["working_orders_count"] is None
    assert summary["broker_flat"] is None
