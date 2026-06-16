from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from algochains_mcp.live_bot_intelligence.log_paths import (
    resolve_bot_log_path,
    summarize_price_source_failures,
)
from algochains_mcp.live_bot_intelligence import bot_ops


def test_mnq_log_path_prefers_demo_env_over_existing_live_log(tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (tmp_path / ".env").write_text("TRADOVATE_ENV=demo\n")
    (logs_dir / "futures_bot_live.log").write_text("stale live log\n")
    (logs_dir / "futures_bot_demo.log").write_text("demo T4-FAIL-CLOSED\n")

    result = resolve_bot_log_path("mnq", tmp_path)

    assert result.mode == "demo"
    assert result.source == "control_tower_env"
    assert result.path == logs_dir / "futures_bot_demo.log"


def test_mnq_log_path_uses_signal_health_demo_when_env_absent(tmp_path):
    logs_dir = tmp_path / "logs"
    state_dir = tmp_path / "state"
    logs_dir.mkdir()
    state_dir.mkdir()
    (logs_dir / "futures_bot_demo.log").write_text("demo log\n")
    (state_dir / "signal_health.json").write_text(
        json.dumps({"ws_health": {"status": "DEMO"}})
    )

    result = resolve_bot_log_path("mnq", tmp_path)

    assert result.mode == "demo"
    assert result.source == "signal_health"
    assert result.path.name == "futures_bot_demo.log"


def test_mnq_log_path_falls_back_to_existing_demo_log(tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "futures_bot_demo.log").write_text("demo only\n")

    result = resolve_bot_log_path("mnq", tmp_path)

    assert result.mode == "demo"
    assert result.source == "existing_file"
    assert result.path.name == "futures_bot_demo.log"


def test_price_source_summary_detects_two_source_fail_closed():
    result = summarize_price_source_failures(
        [
            "2026-06-16 ERROR T4-FAIL-CLOSED: No live market price: "
            "REST price fetch failed AND md_quote_feed unavailable.",
        ]
    )

    assert result["status"] == "fail_closed"
    assert result["fail_closed_count"] == 1
    assert result["rest_price_fetch_failed"] is True
    assert result["md_quote_feed_unavailable"] is True
    assert result["both_price_sources_down"] is True


def test_ai_pipeline_health_reads_demo_log_for_mnq_price_source(tmp_path, monkeypatch):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (tmp_path / ".env").write_text("TRADOVATE_ENV=demo\nPIPELINE_TIMEOUT_SECONDS=5\n")
    (logs_dir / "futures_bot_live.log").write_text("Multi-agent APPROVED\n")
    (logs_dir / "futures_bot_demo.log").write_text(
        "2026-06-16 ERROR T4-FAIL-CLOSED: No live market price: "
        "REST price fetch failed AND md_quote_feed unavailable.\n"
    )
    monkeypatch.setattr(bot_ops, "CONTROL_TOWER", tmp_path)

    result = bot_ops.get_ai_pipeline_health("mnq")

    assert result["log_mode"] == "demo"
    assert result["log_path"].endswith("futures_bot_demo.log")
    assert result["price_source_health"]["status"] == "fail_closed"
    assert result["price_source_health"]["both_price_sources_down"] is True


def test_get_bot_health_reports_demo_log_price_source_failure(tmp_path, monkeypatch):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (tmp_path / ".env").write_text("TRADOVATE_ENV=demo\n")
    (logs_dir / "futures_bot_live.log").write_text("stale live log\n")
    (logs_dir / "futures_bot_demo.log").write_text(
        "2026-06-16 ERROR T4-FAIL-CLOSED: No live market price: "
        "REST price fetch failed AND md_quote_feed unavailable.\n"
    )
    monkeypatch.setenv("ALGOCHAINS_CONTROL_TOWER", str(tmp_path))

    import algochains_mcp.server as srv

    result = asyncio.run(srv.call_tool("get_bot_health", {"bot": "mnq"}))
    text = result[0].text if hasattr(result[0], "text") else str(result[0])
    payload = json.loads(text)
    mnq = payload["bots"]["mnq"]

    assert mnq["log_mode"] == "demo"
    assert mnq["log_path"].endswith("futures_bot_demo.log")
    assert mnq["error_count_last_100"] == 1
    assert mnq["price_source_health"]["both_price_sources_down"] is True

