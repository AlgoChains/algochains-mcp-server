from __future__ import annotations

import asyncio
import json


def _payload_from_call_tool(result) -> dict:
    text = result[0].text if hasattr(result[0], "text") else str(result[0])
    return json.loads(text)


def test_get_bot_health_uses_fresher_mnq_demo_log(tmp_path, monkeypatch):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    live_log = logs_dir / "futures_bot_live.log"
    demo_log = logs_dir / "futures_bot_demo.log"
    live_log.write_text("INFO stale live heartbeat\n")
    demo_log.write_text(
        "[DEMO] ERROR T4-FAIL-CLOSED - MNQ: REST price fetch failed AND md_quote_feed unavailable\n"
    )
    live_mtime = 1_700_000_000
    demo_mtime = live_mtime + 60
    monkeypatch.setenv("ALGOCHAINS_CONTROL_TOWER", str(tmp_path))

    import algochains_mcp.server as srv

    # Force mtimes after writes so the health surface chooses the incident log.
    import os

    os.utime(live_log, (live_mtime, live_mtime))
    os.utime(demo_log, (demo_mtime, demo_mtime))

    payload = _payload_from_call_tool(asyncio.run(srv.call_tool("get_bot_health", {"bot": "mnq"})))
    mnq = payload["bots"]["mnq"]

    assert mnq["active_log"] == "logs/futures_bot_demo.log"
    assert mnq["log_candidates"] == ["logs/futures_bot_live.log", "logs/futures_bot_demo.log"]
    assert mnq["error_count_last_100"] == 1
    assert "T4-FAIL-CLOSED" in mnq["last_line_preview"]


def test_get_bot_health_preserves_mnq_live_log_fallback(tmp_path, monkeypatch):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "futures_bot_live.log").write_text("INFO live heartbeat\n")
    monkeypatch.setenv("ALGOCHAINS_CONTROL_TOWER", str(tmp_path))

    import algochains_mcp.server as srv

    payload = _payload_from_call_tool(asyncio.run(srv.call_tool("get_bot_health", {"bot": "mnq"})))
    mnq = payload["bots"]["mnq"]

    assert mnq["active_log"] == "logs/futures_bot_live.log"
    assert mnq["log_candidates"] == ["logs/futures_bot_live.log", "logs/futures_bot_demo.log"]
    assert mnq["last_line_preview"] == "INFO live heartbeat"
