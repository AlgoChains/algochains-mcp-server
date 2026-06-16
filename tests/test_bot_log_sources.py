from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from algochains_mcp.live_bot_intelligence import metrics_parser
from algochains_mcp.live_bot_intelligence.log_sources import (
    select_bot_log,
    summarize_price_source_health,
)


def _write_demo_fail_closed_log(root):
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "futures_bot_demo.log"
    log_path.write_text(
        "2026-06-16T12:28:32+00:00 ERROR T4-FAIL-CLOSED - MNQ\n"
        "No live market price: REST price fetch failed AND md_quote_feed unavailable.\n"
        "Order aborted (fail-closed).\n"
    )
    return log_path


def test_select_bot_log_uses_mnq_demo_when_live_missing(tmp_path):
    demo_log = _write_demo_fail_closed_log(tmp_path)

    selected = select_bot_log(tmp_path, "mnq")

    assert selected.variant == "demo"
    assert selected.path == demo_log
    assert selected.exists is True


def test_summarize_price_source_health_detects_dual_source_fail_closed():
    result = summarize_price_source_health(
        [
            "No live market price: REST price fetch failed AND md_quote_feed unavailable.",
            "Order aborted (fail-closed).",
        ]
    )

    assert result["status"] == "fail_closed"
    assert result["rest_price_fetch_failed"] is True
    assert result["md_quote_feed_unavailable"] is True
    assert result["independent_sources_down"] is True
    assert result["order_aborted"] is True


def test_parse_bot_metrics_surfaces_demo_price_source_failure(tmp_path, monkeypatch):
    demo_log = _write_demo_fail_closed_log(tmp_path)
    monkeypatch.setattr(metrics_parser, "CONTROL_TOWER", tmp_path)

    metrics = metrics_parser.parse_bot_metrics("mnq")

    assert metrics.log_variant == "demo"
    assert metrics.log_path == str(demo_log)
    assert metrics.price_source_status["status"] == "fail_closed"
    assert metrics.price_source_status["independent_sources_down"] is True


def test_get_bot_health_surfaces_demo_price_source_failure(tmp_path, monkeypatch):
    demo_log = _write_demo_fail_closed_log(tmp_path)
    monkeypatch.setenv("ALGOCHAINS_CONTROL_TOWER", str(tmp_path))

    import algochains_mcp.server as srv

    result = asyncio.run(srv.call_tool("get_bot_health", {"bot": "mnq"}))
    text = result[0].text if hasattr(result[0], "text") else str(result[0])
    payload = json.loads(text)
    mnq = payload["bots"]["mnq"]

    assert mnq["log_variant"] == "demo"
    assert mnq["log_path"] == str(demo_log)
    assert mnq["price_source_status"]["status"] == "fail_closed"
    assert mnq["price_source_status"]["independent_sources_down"] is True
