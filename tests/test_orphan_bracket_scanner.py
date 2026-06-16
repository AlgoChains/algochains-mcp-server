from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from algochains_mcp.live_bot_intelligence import bot_ops


def test_check_orphan_bracket_orders_ok_when_no_orphans(tmp_path, monkeypatch):
    monkeypatch.setattr(bot_ops, "CONTROL_TOWER", tmp_path)
    monkeypatch.setattr(
        bot_ops,
        "_fetch_tradovate_book",
        lambda: {
            "status": "OK",
            "positions": [{"contractId": 1, "contractName": "MNQH6", "netPos": 0}],
            "working_orders": [],
            "environment": "LIVE",
        },
    )

    result = bot_ops.check_orphan_bracket_orders()

    assert result["status"] == "OK"
    assert result["orphan_count"] == 0
    assert result["gate"] == "PROCEED"
    assert result["formatted_line"].startswith("[OK]")


def test_check_orphan_bracket_orders_detects_stop_on_flat_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(bot_ops, "CONTROL_TOWER", tmp_path)
    monkeypatch.setattr(
        bot_ops,
        "_fetch_tradovate_book",
        lambda: {
            "status": "OK",
            "positions": [{"contractId": 10, "contractName": "CLQ6", "netPos": 0}],
            "working_orders": [
                {
                    "id": 100,
                    "contractId": 10,
                    "contractName": "CLQ6",
                    "orderType": "Stop",
                    "action": "Sell",
                    "orderQty": 1,
                }
            ],
            "environment": "LIVE",
        },
    )

    result = bot_ops.check_orphan_bracket_orders()

    assert result["status"] == "ORPHAN_ORDERS"
    assert result["orphan_count"] == 1
    assert result["orphan_orders"][0]["orderId"] == 100
    assert result["formatted_line"].startswith("[ORPHAN_ORDERS]")


def test_check_orphan_bracket_orders_protects_mnq_when_swing_active(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    logs.mkdir(parents=True)
    (logs / "mnq_position_state.json").write_text(
        json.dumps({"direction": "long", "qty": 1, "flat": False, "entry_price": 21000.0})
    )
    monkeypatch.setattr(bot_ops, "CONTROL_TOWER", tmp_path)
    monkeypatch.setattr(
        bot_ops,
        "_fetch_tradovate_book",
        lambda: {
            "status": "OK",
            "positions": [{"contractId": 20, "contractName": "MNQH6", "netPos": 0}],
            "working_orders": [
                {
                    "id": 200,
                    "contractId": 20,
                    "contractName": "MNQH6",
                    "orderType": "Stop",
                    "action": "Sell",
                    "orderQty": 1,
                }
            ],
            "environment": "LIVE",
        },
    )

    result = bot_ops.check_orphan_bracket_orders()

    assert result["status"] == "OK"
    assert result["orphan_count"] == 0
    assert result["swing"] == "YES"
    assert len(result["swing_protected_orphans"]) == 1


def test_get_orphan_bracket_scanner_status_reads_state_and_live(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "orphan_bracket_scanner_state.json").write_text(
        json.dumps(
            {
                "last_check": "2026-06-16T22:00:00Z",
                "last_cancel_count": 0,
                "unknown_flat_orders": [],
                "gate": "PROCEED",
                "swing": "YES",
            }
        )
    )
    monkeypatch.setattr(bot_ops, "CONTROL_TOWER", tmp_path)

    live_payload = {
        "status": "OK",
        "message": "No orphan bracket orders (0 working orders checked)",
        "orphan_count": 0,
        "orphan_orders": [],
        "swing_protected_orphans": [],
        "working_orders_count": 0,
        "mnq_swing_protect": True,
        "swing": "YES",
        "gate": "PROCEED",
        "formatted_line": "[OK] No orphan bracket orders on flat contracts (0 working orders checked)",
        "checked_at": "2026-06-16T22:00:25Z",
    }
    monkeypatch.setattr(bot_ops, "check_orphan_bracket_orders", lambda: live_payload)

    result = bot_ops.get_orphan_bracket_scanner_status()

    assert result["scanner_active"] is True
    assert result["formatted_line"] == live_payload["formatted_line"]
    assert result["gate"] == "PROCEED"
    assert result["swing"] == "YES"


def test_get_bracket_guardian_status_surfaces_orphan_fields(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "bracket_guardian_state.json").write_text(
        json.dumps(
            {
                "last_check": "2026-06-16T22:00:00Z",
                "positions_count": 0,
                "working_orders_count": 2,
                "unknown_flat_orders": [{"orderId": 55}],
                "gate": "PROCEED",
                "swing": "YES",
            }
        )
    )
    monkeypatch.setattr(bot_ops, "CONTROL_TOWER", tmp_path)
    monkeypatch.setattr(
        bot_ops,
        "bracket_integrity_check",
        lambda: {
            "status": "OK",
            "checked_count": 0,
            "formatted_line": "[OK] All non-MNQ positions have stop+target brackets (0 checked)",
        },
    )

    result = bot_ops.get_bracket_guardian_status()

    assert result["unknown_flat_orders_count"] == 1
    assert result["gate"] == "PROCEED"
    assert result["swing"] == "YES"
    assert result["mnq_swing_protect"] is True
