from __future__ import annotations

import json

from algochains_mcp.live_bot_intelligence import bot_ops


def _write_jsonl(path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")


def test_pipeline_health_reads_control_tower_timeout_before_mcp_env(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("PIPELINE_TIMEOUT_SECONDS=5\n")
    monkeypatch.setenv("PIPELINE_TIMEOUT_SECONDS", "8")
    monkeypatch.setattr(bot_ops, "CONTROL_TOWER", tmp_path)

    log_path = tmp_path / "logs" / "futures_bot_live.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("Pipeline timed out after 5.2s pipeline shadow_mode=True\n")
    _write_jsonl(
        tmp_path / "logs" / "decision_latency.jsonl",
        [
            {"event": "scan", "multi_agent_ms": 4100},
            {"event": "multi_agent_timeout", "multi_agent_ms": 5200},
        ],
    )

    result = bot_ops.get_ai_pipeline_health("mnq")

    assert result["mode"] == "shadow_timeout"
    assert result["pipeline_timeout_config_s"] == 5.0
    assert result["pipeline_timeout_config_source"] == "control_tower_.env"
    assert result["recent_timeout_samples_s"] == [5.2]
    assert result["decision_latency"]["metrics"]["multi_agent_ms"]["over_timeout_rate"] == 0.5


def test_pipeline_health_summarizes_multi_agent_p95_and_desktop_fallbacks(tmp_path, monkeypatch):
    monkeypatch.delenv("PIPELINE_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setattr(bot_ops, "CONTROL_TOWER", tmp_path)

    log_path = tmp_path / "logs" / "futures_bot_live.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("Multi-agent APPROVED\n")
    _write_jsonl(
        tmp_path / "logs" / "decision_latency.jsonl",
        [
            {"event": "scan", "multi_agent_ms": 1000},
            {"event": "scan", "multi_agent_ms": 2000},
            {"event": "scan", "multi_agent_ms": 9000},
        ],
    )
    _write_jsonl(
        tmp_path / "logs" / "desktop_inference_latency.jsonl",
        [
            {
                "model_id": "qwen3",
                "runtime": "ollama",
                "prompt_class": "validator",
                "latency_s": 4.2,
                "ok": True,
            },
            {
                "model_id": "qwen3",
                "runtime": "ollama",
                "prompt_class": "validator",
                "latency_s": 7.0,
                "ok": False,
                "fallback_reason": "ollama_timeout",
            },
        ],
    )

    result = bot_ops.get_ai_pipeline_health("mnq")

    assert result["mode"] == "active"
    assert result["pipeline_timeout_config_s"] == 8.0
    decision = result["decision_latency"]["metrics"]["multi_agent_ms"]
    assert decision["p95_ms"] == 9000.0
    assert decision["over_timeout_count"] == 1
    desktop = result["desktop_inference"]["groups"]["qwen3|ollama|validator"]
    assert desktop["p95_s"] == 7.0
    assert desktop["failure_rate"] == 0.5
    assert desktop["fallback_reasons"] == ["ollama_timeout"]
