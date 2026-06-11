from __future__ import annotations

import json
import os

from algochains_mcp.live_bot_intelligence.adaptive_brain import get_adaptive_brain_status


def test_adaptive_brain_status_dead_when_process_missing(tmp_path):
    result = get_adaptive_brain_status(
        control_tower=tmp_path,
        ps_output="root 1 0.0 0.0 python other_daemon.py\n",
        now=1_800_000_000,
    )

    assert result["status"] == "dead"
    assert result["running"] is False
    assert result["pids"] == []
    assert "not running" in result["detail"]


def test_adaptive_brain_status_healthy_with_running_process(tmp_path):
    now = 1_800_000_000
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_path = log_dir / "adaptive_brain.log"
    log_path.write_text("2026-06-10T23:58:00Z heartbeat ok\n")
    os.utime(log_path, (now, now))

    result = get_adaptive_brain_status(
        control_tower=tmp_path,
        ps_output="treycsa 4242 0.0 0.1 python autonomous/adaptive_brain.py\n",
        now=now,
    )

    assert result["status"] == "healthy"
    assert result["running"] is True
    assert result["pids"] == [4242]
    assert result["log"]["present"] is True
    assert "heartbeat ok" in result["log"]["last_line_preview"]


def test_adaptive_brain_status_stale_when_state_timestamp_old(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "adaptive_brain_state.json").write_text(
        json.dumps({"status": "ok", "timestamp": 1_799_998_000})
    )

    result = get_adaptive_brain_status(
        control_tower=tmp_path,
        ps_output="treycsa 4242 0.0 0.1 python adaptive_brain.py\n",
        now=1_800_000_000,
        stale_after_seconds=900,
    )

    assert result["status"] == "stale"
    assert result["state"]["timestamp_age_seconds"] == 2000
    assert "stale" in result["detail"]


def test_adaptive_brain_status_is_registered_tier1_tool():
    import algochains_mcp.server as server

    all_tool_names = {tool.name for tool in server.TOOLS_ANNOTATED}
    tier1_tool_names = {tool.name for tool in server.TOOLS_TIER1}

    assert "get_adaptive_brain_status" in all_tool_names
    assert "get_adaptive_brain_status" in tier1_tool_names
