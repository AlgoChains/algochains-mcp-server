from __future__ import annotations

import asyncio
import json
import os
import time

from algochains_mcp.live_bot_intelligence import metrics_parser
from algochains_mcp.live_bot_intelligence.log_paths import resolve_bot_log_path


def test_resolve_bot_log_path_uses_demo_log_when_it_is_the_only_mnq_log(tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    demo_log = logs_dir / "futures_bot_demo.log"
    demo_log.write_text("T4-FAIL-CLOSED - MNQ No live market price\n")

    assert resolve_bot_log_path(tmp_path, "mnq") == demo_log


def test_resolve_bot_log_path_prefers_newer_mnq_log(tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    live_log = logs_dir / "futures_bot_live.log"
    demo_log = logs_dir / "futures_bot_demo.log"
    live_log.write_text("old live heartbeat\n")
    demo_log.write_text("new demo heartbeat\n")

    old = time.time() - 120
    new = time.time()
    os.utime(live_log, (old, old))
    os.utime(demo_log, (new, new))

    assert resolve_bot_log_path(tmp_path, "mnq") == demo_log


def test_parse_bot_metrics_reads_demo_mnq_fail_closed_log(tmp_path, monkeypatch):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "futures_bot_demo.log").write_text(
        "T4-FAIL-CLOSED - MNQ No live market price: REST price fetch failed "
        "AND md_quote_feed unavailable.\n"
    )
    monkeypatch.setattr(metrics_parser, "CONTROL_TOWER", tmp_path)

    metrics = metrics_parser.parse_bot_metrics("mnq")

    assert metrics.error_count_1h == 1
    assert "T4-FAIL-CLOSED" in metrics.last_error


def test_get_bot_health_reports_active_demo_mnq_log(tmp_path, monkeypatch):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    demo_log = logs_dir / "futures_bot_demo.log"
    demo_log.write_text(
        "T4-FAIL-CLOSED - MNQ No live market price: REST price fetch failed "
        "AND md_quote_feed unavailable.\n"
    )
    monkeypatch.setenv("ALGOCHAINS_CONTROL_TOWER", str(tmp_path))

    import algochains_mcp.server as srv

    result = asyncio.run(srv.call_tool("get_bot_health", {"bot": "mnq"}))
    text = result[0].text if hasattr(result[0], "text") else str(result[0])
    payload = json.loads(text)
    mnq = payload["bots"]["mnq"]

    assert mnq["log_path"] == str(demo_log)
    assert mnq["error_count_last_100"] == 1
    assert "T4-FAIL-CLOSED" in mnq["last_line_preview"]
