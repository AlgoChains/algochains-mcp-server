from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from algochains_mcp.live_bot_intelligence import bot_ops


def test_is_mnq_contract_excludes_mes_and_nq():
    assert bot_ops._is_mnq_contract("MNQM6")
    assert not bot_ops._is_mnq_contract("MESM6")
    assert not bot_ops._is_mnq_contract("NQM6")
    assert not bot_ops._is_mnq_contract("CLM6")


def test_bracket_integrity_ok_when_non_mnq_flat(monkeypatch):
    monkeypatch.setattr(
        bot_ops,
        "_load_tradovate_book",
        lambda: (
            [
                {"contractId": 1, "contractName": "MNQM6", "netPos": 2},
                {"contractId": 2, "contractName": "MESM6", "netPos": 0},
            ],
            [],
            "LIVE",
        ),
    )
    monkeypatch.setattr(bot_ops, "_non_mnq_bot_state_mismatch", lambda: [])

    result = bot_ops.bracket_integrity_check()

    assert result["status"] == "OK"
    assert result["checked_count"] == 0
    assert result["summary"] == "[OK] All non-MNQ positions have stop+target brackets (0 checked)"


def test_bracket_integrity_alert_when_stop_or_target_missing(monkeypatch):
    monkeypatch.setattr(
        bot_ops,
        "_load_tradovate_book",
        lambda: (
            [{"contractId": 10, "contractName": "CLM6", "netPos": 1, "netPrice": 70.0}],
            [
                {"contractId": 10, "orderType": "Stop", "contractName": "CLM6"},
            ],
            "LIVE",
        ),
    )
    monkeypatch.setattr(bot_ops, "_non_mnq_bot_state_mismatch", lambda: [])

    result = bot_ops.bracket_integrity_check()

    assert result["status"] == "INCOMPLETE_BRACKETS"
    assert result["checked_count"] == 1
    assert result["incomplete"][0]["has_stop"] is True
    assert result["incomplete"][0]["has_target"] is False
    assert "[ALERT]" in result["summary"]


def test_bracket_integrity_degraded_on_state_broker_mismatch(monkeypatch, tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "cl_position_state.json").write_text(
        json.dumps({"direction": "long", "qty": 1, "flat": False, "entry_price": 70.0}),
        encoding="utf-8",
    )
    monkeypatch.setattr(bot_ops, "CONTROL_TOWER", tmp_path)
    monkeypatch.setattr(
        bot_ops,
        "_load_tradovate_book",
        lambda: ([], [], "LIVE"),
    )

    result = bot_ops.bracket_integrity_check()

    assert result["status"] == "DEGRADED"
    assert result["checked_count"] == 0
    assert result["state_mismatch"]
    assert "[WARN]" in result["summary"]


def test_bracket_integrity_ok_when_both_legs_present(monkeypatch):
    monkeypatch.setattr(
        bot_ops,
        "_load_tradovate_book",
        lambda: (
            [{"contractId": 11, "contractName": "MESM6", "netPos": -2, "netPrice": 5500.0}],
            [
                {"contractId": 11, "orderType": "Stop", "contractName": "MESM6"},
                {"contractId": 11, "orderType": "Limit", "contractName": "MESM6"},
            ],
            "LIVE",
        ),
    )
    monkeypatch.setattr(bot_ops, "_non_mnq_bot_state_mismatch", lambda: [])

    result = bot_ops.bracket_integrity_check()

    assert result["status"] == "OK"
    assert result["checked_count"] == 1
    assert result["incomplete"] == []


def test_get_bracket_guardian_status_runs_live_check_when_guardian_reports_zero(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "bracket_guardian_state.json").write_text(
        json.dumps({"last_check": "2026-06-16T16:50:00Z", "positions_count": 0, "working_orders_count": 0}),
        encoding="utf-8",
    )
    monkeypatch.setattr(bot_ops, "CONTROL_TOWER", tmp_path)
    monkeypatch.setattr(
        bot_ops,
        "bracket_integrity_check",
        lambda: {
            "status": "DEGRADED",
            "summary": "[WARN] Non-MNQ bot state shows open exposure but broker returned 0 positions to check (0 checked)",
            "checked_count": 0,
        },
    )

    result = bot_ops.get_bracket_guardian_status()

    assert result["guardian_active"] is True
    assert result["status"] == "DEGRADED"
    assert result["live_check"]["status"] == "DEGRADED"
