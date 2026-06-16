from __future__ import annotations

import json
from pathlib import Path

from algochains_mcp.daily_loss_proximity import get_daily_loss_proximity


def _make_control_tower(tmp_path: Path) -> Path:
    root = tmp_path / "algochains-control-tower"
    (root / "state").mkdir(parents=True)
    return root


def test_ok_when_verified_zero_pnl(tmp_path, monkeypatch):
    root = _make_control_tower(tmp_path)
    (root / "state" / "daily_loss_proximity_state.json").write_text(
        json.dumps({"daily_pnl": 0.0, "pnl_verified": True}),
        encoding="utf-8",
    )
    monkeypatch.delenv("TODAY_REALIZED_PNL", raising=False)

    result = get_daily_loss_proximity(control_tower=root)

    assert result["status"] == "OK"
    assert result["daily_pnl_usd"] == 0.0
    assert result["utilization_pct"] == 0.0
    assert result["buffer_usd"] == 500.0
    assert "[OK]" in result["summary"]


def test_warn_at_eighty_percent(tmp_path, monkeypatch):
    root = _make_control_tower(tmp_path)
    (root / "state" / "daily_loss_proximity_state.json").write_text(
        json.dumps({"daily_pnl": -400.0, "pnl_verified": True}),
        encoding="utf-8",
    )
    monkeypatch.delenv("TODAY_REALIZED_PNL", raising=False)

    result = get_daily_loss_proximity(control_tower=root)

    assert result["status"] == "WARN"
    assert result["utilization_pct"] == 80.0
    assert result["block_new_scalper_entries"] is False


def test_block_scalpers_at_ninety_five_percent(tmp_path, monkeypatch):
    root = _make_control_tower(tmp_path)
    (root / "state" / "daily_loss_proximity_state.json").write_text(
        json.dumps({"daily_pnl": -475.0, "pnl_verified": True}),
        encoding="utf-8",
    )
    monkeypatch.delenv("TODAY_REALIZED_PNL", raising=False)

    result = get_daily_loss_proximity(control_tower=root)

    assert result["status"] == "BLOCK"
    assert result["utilization_pct"] == 95.0
    assert result["block_new_scalper_entries"] is True
    assert result["mnq_swing_exempt"] is True


def test_degraded_when_zero_pnl_unverified(tmp_path, monkeypatch):
    root = _make_control_tower(tmp_path)
    (root / "state" / "daily_loss_proximity_state.json").write_text(
        json.dumps({"daily_pnl": 0.0, "pnl_verified": False}),
        encoding="utf-8",
    )
    monkeypatch.delenv("TODAY_REALIZED_PNL", raising=False)
    monkeypatch.setattr(
        "algochains_mcp.daily_loss_proximity._reconcile_bot_daily_pnl",
        lambda _root: (None, "bots:stale", False, {}),
    )

    result = get_daily_loss_proximity(control_tower=root)

    assert result["status"] == "DEGRADED"
    assert result["pnl_verified"] is False
    assert "[DEGRADED]" in result["summary"]


def test_degraded_when_zero_pnl_missing_verification_flag(tmp_path, monkeypatch):
    root = _make_control_tower(tmp_path)
    (root / "state" / "daily_loss_proximity_state.json").write_text(
        json.dumps({"daily_pnl": 0.0}),
        encoding="utf-8",
    )
    monkeypatch.delenv("TODAY_REALIZED_PNL", raising=False)
    monkeypatch.setattr(
        "algochains_mcp.daily_loss_proximity._reconcile_bot_daily_pnl",
        lambda _root: (None, "bots:stale", False, {}),
    )

    result = get_daily_loss_proximity(control_tower=root)

    assert result["status"] == "DEGRADED"
    assert result["pnl_verified"] is False
    assert "[DEGRADED]" in result["summary"]


def test_env_pnl_overrides_state(tmp_path, monkeypatch):
    root = _make_control_tower(tmp_path)
    (root / "state" / "daily_loss_proximity_state.json").write_text(
        json.dumps({"daily_pnl": 0.0, "pnl_verified": True}),
        encoding="utf-8",
    )
    monkeypatch.setenv("TODAY_REALIZED_PNL", "-250")

    result = get_daily_loss_proximity(control_tower=root)

    assert result["status"] == "OK"
    assert result["daily_pnl_usd"] == -250.0
    assert result["pnl_source"] == "env:TODAY_REALIZED_PNL"


def test_degraded_when_no_pnl_source(tmp_path, monkeypatch):
    root = _make_control_tower(tmp_path)
    monkeypatch.delenv("TODAY_REALIZED_PNL", raising=False)
    monkeypatch.setattr(
        "algochains_mcp.daily_loss_proximity._reconcile_bot_daily_pnl",
        lambda _root: (None, "unknown", False, {}),
    )

    result = get_daily_loss_proximity(control_tower=root)

    assert result["status"] == "DEGRADED"
    assert result["daily_pnl_usd"] is None
    assert result["pnl_source"] == "unknown"
    assert result["formatted_line"] == result["summary"]


def test_formatted_line_matches_summary(tmp_path, monkeypatch):
    root = _make_control_tower(tmp_path)
    (root / "state" / "daily_loss_proximity_state.json").write_text(
        json.dumps({"daily_pnl": -400.0, "pnl_verified": True}),
        encoding="utf-8",
    )
    monkeypatch.delenv("TODAY_REALIZED_PNL", raising=False)

    result = get_daily_loss_proximity(control_tower=root)

    assert result["formatted_line"] == result["summary"]
    assert result["formatted_line"].startswith("[WARN]")


def test_reconciles_from_bot_metrics_when_state_missing(tmp_path, monkeypatch):
    root = _make_control_tower(tmp_path)
    monkeypatch.delenv("TODAY_REALIZED_PNL", raising=False)
    monkeypatch.setattr(
        "algochains_mcp.daily_loss_proximity._reconcile_bot_daily_pnl",
        lambda _root: (
            -125.0,
            "bots:aggregate",
            True,
            {"fresh_bots": ["mnq"], "bot_breakdown": {"mnq": {"daily_pnl_usd": -125.0}}},
        ),
    )

    result = get_daily_loss_proximity(control_tower=root)

    assert result["status"] == "OK"
    assert result["daily_pnl_usd"] == -125.0
    assert result["pnl_source"] == "bots:aggregate"
    assert result["pnl_verified"] is True
    assert result["reconciliation"]["fresh_bots"] == ["mnq"]


def test_reconciles_from_bot_metrics_when_unverified_zero(tmp_path, monkeypatch):
    root = _make_control_tower(tmp_path)
    (root / "state" / "daily_loss_proximity_state.json").write_text(
        json.dumps({"daily_pnl": 0.0}),
        encoding="utf-8",
    )
    monkeypatch.delenv("TODAY_REALIZED_PNL", raising=False)
    monkeypatch.setattr(
        "algochains_mcp.daily_loss_proximity._reconcile_bot_daily_pnl",
        lambda _root: (
            0.0,
            "bots:aggregate",
            True,
            {"fresh_bots": ["cl"], "bot_breakdown": {"cl": {"daily_pnl_usd": 0.0}}},
        ),
    )

    result = get_daily_loss_proximity(control_tower=root)

    assert result["status"] == "OK"
    assert result["pnl_verified"] is True
    assert result["pnl_source"] == "bots:aggregate"


def test_state_verified_flag_alias(tmp_path, monkeypatch):
    root = _make_control_tower(tmp_path)
    (root / "state" / "daily_loss_proximity_state.json").write_text(
        json.dumps({"daily_pnl": -50.0, "verified": True}),
        encoding="utf-8",
    )
    monkeypatch.delenv("TODAY_REALIZED_PNL", raising=False)

    result = get_daily_loss_proximity(control_tower=root)

    assert result["status"] == "OK"
    assert result["pnl_verified"] is True
    assert result["pnl_source"] == "state:daily_pnl"
