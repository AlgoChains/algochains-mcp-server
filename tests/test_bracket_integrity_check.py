from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from algochains_mcp.live_bot_intelligence import bot_ops


def _write_position_state(tmp_path, symbol: str, *, flat: bool, qty: int = 0) -> None:
    logs = tmp_path / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    payload = {
        "direction": "long" if qty > 0 else "flat",
        "qty": qty,
        "entry_price": 100.0,
        "flat": flat,
        "timestamp": "2026-06-16T17:30:00Z",
    }
    (logs / f"{symbol.lower()}_position_state.json").write_text(json.dumps(payload))


def test_bracket_integrity_check_ok_when_flat(tmp_path, monkeypatch):
    monkeypatch.setattr(bot_ops, "CONTROL_TOWER", tmp_path)
    monkeypatch.setattr(
        bot_ops,
        "_fetch_tradovate_book",
        lambda: {
            "status": "OK",
            "positions": [{"contractId": 1, "contractName": "CLQ6", "netPos": 0}],
            "working_orders": [],
            "environment": "LIVE",
        },
    )

    result = bot_ops.bracket_integrity_check()

    assert result["status"] == "OK"
    assert result["checked_count"] == 0
    assert result["formatted_line"] == "[OK] All non-MNQ positions have stop+target brackets (0 checked)"


def test_bracket_integrity_check_alert_when_missing_target(tmp_path, monkeypatch):
    monkeypatch.setattr(bot_ops, "CONTROL_TOWER", tmp_path)
    monkeypatch.setattr(
        bot_ops,
        "_fetch_tradovate_book",
        lambda: {
            "status": "OK",
            "positions": [{"contractId": 10, "contractName": "CLQ6", "netPos": 1, "netPrice": 75.0}],
            "working_orders": [{"contractId": 10, "orderType": "Stop", "id": 100}],
            "environment": "LIVE",
        },
    )

    result = bot_ops.bracket_integrity_check()

    assert result["status"] == "ALERT"
    assert result["checked_count"] == 1
    assert result["missing_brackets"][0]["missing_target"] is True


def test_bracket_integrity_check_skips_mnq(tmp_path, monkeypatch):
    monkeypatch.setattr(bot_ops, "CONTROL_TOWER", tmp_path)
    monkeypatch.setattr(
        bot_ops,
        "_fetch_tradovate_book",
        lambda: {
            "status": "OK",
            "positions": [{"contractId": 20, "contractName": "MNQH6", "netPos": 2, "netPrice": 21000.0}],
            "working_orders": [],
            "environment": "LIVE",
        },
    )

    result = bot_ops.bracket_integrity_check()

    assert result["status"] == "OK"
    assert result["checked_count"] == 0


def test_bracket_integrity_check_degraded_when_local_state_open_but_broker_flat(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(bot_ops, "CONTROL_TOWER", tmp_path)
    _write_position_state(tmp_path, "CL", flat=False, qty=1)
    monkeypatch.setattr(
        bot_ops,
        "_fetch_tradovate_book",
        lambda: {
            "status": "OK",
            "positions": [],
            "working_orders": [],
            "environment": "LIVE",
        },
    )

    result = bot_ops.bracket_integrity_check()

    assert result["status"] == "DEGRADED"
    assert result["checked_count"] == 0
    assert result["local_exposure"][0]["bot"] == "cl"
    assert result["formatted_line"].startswith("[DEGRADED]")


def test_get_bracket_guardian_status_runs_live_check_when_zero_positions(
    tmp_path, monkeypatch
):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "bracket_guardian_state.json").write_text(
        json.dumps(
            {
                "last_check": "2026-06-16T17:25:00Z",
                "positions_count": 0,
                "working_orders_count": 0,
                "unprotected_since": {},
            }
        )
    )
    monkeypatch.setattr(bot_ops, "CONTROL_TOWER", tmp_path)

    live_payload = {
        "status": "OK",
        "message": "All non-MNQ positions flat (0 checked)",
        "checked_count": 0,
        "formatted_line": "[OK] All non-MNQ positions have stop+target brackets (0 checked)",
    }
    monkeypatch.setattr(bot_ops, "bracket_integrity_check", lambda: live_payload)

    result = bot_ops.get_bracket_guardian_status()

    assert result["live_check"] == live_payload
    assert result["formatted_line"] == live_payload["formatted_line"]
    assert result["status"] == "OK"


def test_get_bracket_guardian_status_surfaces_degraded_live_check(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "bracket_guardian_state.json").write_text(
        json.dumps({"positions_count": 0, "working_orders_count": 0})
    )
    monkeypatch.setattr(bot_ops, "CONTROL_TOWER", tmp_path)
    monkeypatch.setattr(
        bot_ops,
        "bracket_integrity_check",
        lambda: {
            "status": "DEGRADED",
            "message": "Broker returned 0 non-MNQ positions but 1 bot state file(s) show open CL/MES/NQ exposure — bracket verification failed open",
            "checked_count": 0,
            "formatted_line": "[DEGRADED] Broker returned 0 non-MNQ positions but 1 bot state file(s) show open CL/MES/NQ exposure — bracket verification failed open",
        },
    )

    result = bot_ops.get_bracket_guardian_status()

    assert result["status"] == "DEGRADED"
    assert result["formatted_line"].startswith("[DEGRADED]")
