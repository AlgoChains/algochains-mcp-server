from __future__ import annotations

import asyncio
import json

from algochains_mcp.daily_loss_proximity import get_daily_loss_proximity


def test_proximity_ok_when_within_limit(tmp_path, monkeypatch):
    root = tmp_path / "control-tower"
    (root / "logs").mkdir(parents=True)
    (root / "logs" / "futures_bot_live.log").write_text("boot\n", encoding="utf-8")
    (root / "logs" / "cl_futures_live.log").write_text("boot\n", encoding="utf-8")
    (root / "logs" / "mes_swing_live.log").write_text("boot\n", encoding="utf-8")
    (root / "logs" / "nq_swing_live.log").write_text("boot\n", encoding="utf-8")

    monkeypatch.setenv("ALGOCHAINS_CONTROL_TOWER", str(root))

    payload = get_daily_loss_proximity(daily_loss_limit_usd=500.0)

    assert payload["status"] == "ok"
    assert payload["pnl_verified"] is True
    assert payload["daily_pnl_usd"] == 0.0
    assert payload["loss_utilization_pct"] == 0.0
    assert payload["block_scalper_entries"] is False
    assert payload["summary_line"].startswith("[OK]")


def test_proximity_warn_and_block_thresholds(tmp_path, monkeypatch):
    import algochains_mcp.live_bot_intelligence.metrics_parser as metrics_parser

    root = tmp_path / "control-tower"
    (root / "state").mkdir(parents=True)
    monkeypatch.setattr(metrics_parser, "parse_bot_metrics_from_supabase", lambda _bot_id, _sb_client=None: None)
    (root / "state" / "fleet_daily_pnl.json").write_text(
        json.dumps({"daily_pnl_usd": -410.0}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ALGOCHAINS_CONTROL_TOWER", str(root))

    warn = get_daily_loss_proximity(
        daily_loss_limit_usd=500.0,
        alert_at_pct=80.0,
        block_scalper_at_pct=95.0,
        control_tower=root,
    )
    assert warn["status"] == "warn"
    assert warn["alert_triggered"] is True
    assert warn["block_scalper_entries"] is False
    assert warn["summary_line"].startswith("[WARN]")

    (root / "state" / "fleet_daily_pnl.json").write_text(
        json.dumps({"daily_pnl_usd": -480.0}),
        encoding="utf-8",
    )
    block = get_daily_loss_proximity(
        daily_loss_limit_usd=500.0,
        alert_at_pct=80.0,
        block_scalper_at_pct=95.0,
        control_tower=root,
    )
    assert block["status"] == "block_scalpers"
    assert block["block_scalper_entries"] is True
    assert block["summary_line"].startswith("[BLOCK]")


def test_proximity_fails_closed_without_verified_sources(tmp_path, monkeypatch):
    import algochains_mcp.live_bot_intelligence.metrics_parser as metrics_parser

    root = tmp_path / "control-tower"
    root.mkdir()
    monkeypatch.setenv("ALGOCHAINS_CONTROL_TOWER", str(root))
    monkeypatch.delenv("TODAY_REALIZED_PNL", raising=False)
    monkeypatch.setattr(metrics_parser, "parse_bot_metrics_from_supabase", lambda _bot_id, _sb_client=None: None)

    payload = get_daily_loss_proximity(daily_loss_limit_usd=500.0, control_tower=root)

    assert payload["status"] == "pnl_unverified"
    assert payload["pnl_verified"] is False
    assert payload["summary_line"].startswith("[DEGRADED]")


def test_daily_loss_proximity_registered_and_callable(tmp_path, monkeypatch):
    import algochains_mcp.server as srv

    root = tmp_path / "control-tower"
    (root / "logs").mkdir(parents=True)
    for name in (
        "futures_bot_live.log",
        "cl_futures_live.log",
        "mes_swing_live.log",
        "nq_swing_live.log",
    ):
        (root / "logs" / name).write_text("boot\n", encoding="utf-8")
    monkeypatch.setenv("ALGOCHAINS_CONTROL_TOWER", str(root))

    assert "get_daily_loss_proximity" in {tool.name for tool in srv.TOOLS_ANNOTATED}
    assert "get_daily_loss_proximity" in {tool.name for tool in srv.TOOLS_TIER1}

    result = asyncio.run(srv.call_tool("get_daily_loss_proximity", {}))
    text = result[0].text if hasattr(result[0], "text") else str(result[0])
    payload = json.loads(text)
    assert payload["status"] == "ok"
    assert "summary_line" in payload
