from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from algochains_mcp.live_bot_intelligence import bot_ops


def _write_jsonl(path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")


def test_ai_pipeline_health_prefers_control_tower_timeout_config(tmp_path, monkeypatch):
    (tmp_path / "logs").mkdir()
    (tmp_path / ".env").write_text("PIPELINE_TIMEOUT_SECONDS=5\n")
    (tmp_path / "logs" / "futures_bot_live.log").write_text("Multi-agent APPROVED\n")
    monkeypatch.setattr(bot_ops, "CONTROL_TOWER", tmp_path)
    monkeypatch.setenv("PIPELINE_TIMEOUT_SECONDS", "8")

    result = bot_ops.get_ai_pipeline_health("mnq")

    assert result["pipeline_timeout_config_s"] == 5.0
    assert result["pipeline_timeout_config_source"] == "control_tower_env"
    assert result["mode"] == "active"


def test_ai_pipeline_health_reports_decision_and_desktop_telemetry(tmp_path, monkeypatch):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (tmp_path / ".env").write_text("PIPELINE_TIMEOUT_SECONDS=5\n")
    (logs_dir / "futures_bot_live.log").write_text("Pipeline timed out after 5s\n")
    _write_jsonl(
        logs_dir / "decision_latency.jsonl",
        [
            {"event": "multi_agent_timeout", "multi_agent_ms": 6100, "desktop_inference_ms": 5200},
            {"event": "multi_agent_timeout", "multi_agent_ms": 5900, "desktop_inference_ms": 5100},
            {"event": "decision_complete", "multi_agent_ms": 900, "cloud_fallback_ms": 700},
        ],
    )
    _write_jsonl(
        logs_dir / "desktop_inference_latency.jsonl",
        [
            {
                "model_id": "qwen3",
                "runtime": "ollama",
                "prompt_class": "validation",
                "latency_s": 5.2,
                "ok": False,
                "fallback_reason": "timeout",
            },
            {
                "model_id": "qwen3",
                "runtime": "ollama",
                "prompt_class": "validation",
                "latency_s": 4.9,
                "ok": True,
            },
        ],
    )
    monkeypatch.setattr(bot_ops, "CONTROL_TOWER", tmp_path)

    result = bot_ops.get_ai_pipeline_health("mnq")

    assert result["mode"] == "shadow_timeout"
    assert result["pipeline_timeout_detected"] is True
    assert result["pipeline_timeout_log_detected"] is True
    assert result["pipeline_timeout_event_rate"] == 0.6667
    assert result["multi_agent_p95_over_timeout"] is True
    assert result["decision_latency"]["metrics"]["multi_agent_ms"]["p95_ms"] == 6100.0
    assert result["decision_latency"]["slo"]["pipeline_timeout_ms"] == 5000.0

    desktop_group = result["desktop_inference"]["groups"]["qwen3|ollama|validation"]
    assert desktop_group["failure_rate"] == 0.5
    assert desktop_group["fallback_reasons"] == ["timeout"]


def test_market_data_feed_health_detects_dual_source_fail_closed(tmp_path, monkeypatch):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "futures_bot_demo.log").write_text(
        "\n".join(
            [
                "2026-06-16 05:28:04 ERROR REST price fetch failed for MNQ: timeout",
                "2026-06-16 05:28:04 ERROR md_quote_feed unavailable for MNQ",
                "2026-06-16 05:28:04 T4-FAIL-CLOSED — MNQ No live market price",
                "2026-06-16 05:28:04 Order aborted (fail-closed)",
            ]
        )
        + "\n"
    )
    monkeypatch.setattr(bot_ops, "CONTROL_TOWER", tmp_path)

    result = bot_ops.get_market_data_feed_health("mnq")

    assert result["status"] == "critical"
    assert result["fail_closed_seen"] is True
    assert result["sources"]["rest_price_fetch"]["status"] == "down"
    assert result["sources"]["md_quote_feed"]["status"] == "down"
    assert any("futures_bot_demo.log" in path for path in result["logs_checked"])


def test_get_bot_health_includes_market_data_feed_diagnostics(tmp_path, monkeypatch):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "futures_bot_demo.log").write_text(
        "ERROR REST price fetch failed AND md_quote_feed unavailable\n"
        "T4-FAIL-CLOSED — MNQ No live market price\n"
    )
    monkeypatch.setenv("ALGOCHAINS_CONTROL_TOWER", str(tmp_path))

    import algochains_mcp.server as srv

    result = asyncio.run(srv.call_tool("get_bot_health", {"bot": "mnq"}))
    text = result[0].text if hasattr(result[0], "text") else str(result[0])
    payload = json.loads(text)
    feed = payload["market_data_feeds"]["mnq"]

    assert feed["status"] == "critical"
    assert feed["sources"]["rest_price_fetch"]["status"] == "down"
    assert feed["sources"]["md_quote_feed"]["status"] == "down"
    assert feed["fail_closed_seen"] is True
