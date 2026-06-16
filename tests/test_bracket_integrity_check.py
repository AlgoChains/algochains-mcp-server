from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from algochains_mcp.live_bot_intelligence import bot_ops


def _write_position_state(tmp_path, symbol: str, *, flat: bool, qty: int = 0) -> None:
    logs = tmp_path / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / f"{symbol.lower()}_position_state.json").write_text(
        json.dumps(
            {
                "direction": "long" if qty > 0 else None,
                "qty": qty,
                "entry_price": 100.0,
                "flat": flat,
                "timestamp": "2026-06-16T10:00:00Z",
            }
        )
    )


def test_bracket_integrity_check_ok_when_flat_and_verified(monkeypatch):
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
    monkeypatch.setattr(bot_ops, "_non_mnq_bot_state_exposure", lambda: [])

    result = bot_ops.bracket_integrity_check()

    assert result["status"] == "OK"
    assert result["checked_count"] == 0
    assert result["slack_summary"] == (
        "[OK] All non-MNQ positions have stop+target brackets (0 checked)"
    )


def test_bracket_integrity_check_degraded_when_broker_skipped(monkeypatch):
    monkeypatch.setattr(
        bot_ops,
        "_fetch_tradovate_book",
        lambda: {
            "status": "CONFIG_ERROR",
            "error": "TRADOVATE_ENV not set — cannot determine which account to check",
        },
    )
    monkeypatch.setattr(bot_ops, "_non_mnq_bot_state_exposure", lambda: [])

    result = bot_ops.bracket_integrity_check()

    assert result["status"] == "DEGRADED"
    assert result["verification_skipped"] is True
    assert result["slack_summary"].startswith("[DEGRADED] Bracket integrity verification skipped")


def test_bracket_integrity_check_degraded_when_bot_state_shows_exposure(monkeypatch):
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
    monkeypatch.setattr(
        bot_ops,
        "_non_mnq_bot_state_exposure",
        lambda: [{"bot": "cl", "symbol": "CL", "qty": 1}],
    )

    result = bot_ops.bracket_integrity_check()

    assert result["status"] == "DEGRADED"
    assert result["checked_count"] == 0
    assert "Bot state shows" in result["message"]


def test_bracket_integrity_check_alert_when_missing_target(monkeypatch):
    monkeypatch.setattr(
        bot_ops,
        "_fetch_tradovate_book",
        lambda: {
            "status": "OK",
            "positions": [
                {"contractId": 11, "contractName": "CLM6", "netPos": 1, "netPrice": 70.0},
            ],
            "working_orders": [
                {"contractId": 11, "orderType": "Stop"},
            ],
            "environment": "LIVE",
        },
    )
    monkeypatch.setattr(bot_ops, "_non_mnq_bot_state_exposure", lambda: [])

    result = bot_ops.bracket_integrity_check()

    assert result["status"] == "INCOMPLETE_BRACKETS"
    assert result["checked_count"] == 1
    assert len(result["incomplete"]) == 1
    assert result["incomplete"][0]["has_stop"] is True
    assert result["incomplete"][0]["has_target"] is False


def test_bracket_integrity_check_skips_mnq_positions(monkeypatch):
    monkeypatch.setattr(
        bot_ops,
        "_fetch_tradovate_book",
        lambda: {
            "status": "OK",
            "positions": [
                {"contractId": 1, "contractName": "MNQM6", "netPos": 2, "netPrice": 21000.0},
                {
                    "contractId": 2,
                    "contractName": "MESM6",
                    "netPos": 1,
                    "netPrice": 5900.0,
                },
            ],
            "working_orders": [
                {"contractId": 2, "orderType": "Stop"},
                {"contractId": 2, "orderType": "Limit"},
            ],
            "environment": "LIVE",
        },
    )
    monkeypatch.setattr(bot_ops, "_non_mnq_bot_state_exposure", lambda: [])

    result = bot_ops.bracket_integrity_check()

    assert result["status"] == "OK"
    assert result["checked_count"] == 1
    assert len(result["complete"]) == 1
    assert result["complete"][0]["contractName"] == "MESM6"


def test_get_bracket_guardian_status_runs_live_check_when_zero_positions(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "bracket_guardian_state.json").write_text(
        json.dumps(
            {
                "last_check": "2026-06-16T10:00:00Z",
                "positions_count": 0,
                "working_orders_count": 0,
                "unprotected_since": {},
            }
        )
    )
    monkeypatch.setattr(bot_ops, "CONTROL_TOWER", tmp_path)
    monkeypatch.setattr(
        bot_ops,
        "bracket_integrity_check",
        lambda: {
            "status": "DEGRADED",
            "checked_count": 0,
            "slack_summary": "[DEGRADED] Bracket integrity verification skipped — TRADOVATE_ENV not set (0 checked)",
        },
    )

    result = bot_ops.get_bracket_guardian_status()

    assert result["live_verification"]["status"] == "DEGRADED"
    assert result["status"] == "DEGRADED"
    assert result["slack_summary"].startswith("[DEGRADED]")


def test_non_mnq_bot_state_exposure_reads_state_files(tmp_path, monkeypatch):
    monkeypatch.setattr(bot_ops, "CONTROL_TOWER", tmp_path)
    _write_position_state(tmp_path, "CL", flat=False, qty=1)
    _write_position_state(tmp_path, "MES", flat=True, qty=0)
    _write_position_state(tmp_path, "NQ", flat=True, qty=0)

    exposure = bot_ops._non_mnq_bot_state_exposure()

    assert len(exposure) == 1
    assert exposure[0]["bot"] == "cl"
    assert exposure[0]["qty"] == 1
