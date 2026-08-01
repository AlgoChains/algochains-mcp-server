from __future__ import annotations

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
