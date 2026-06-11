from __future__ import annotations

import asyncio
import json
import os


def _decode(result):
    text = result[0].text if hasattr(result[0], "text") else str(result[0])
    return json.loads(text)


def test_status_probe_reports_running_process_and_bounded_evidence(tmp_path):
    from algochains_mcp.adaptive_brain_status import get_adaptive_brain_status

    script = tmp_path / "autonomous" / "adaptive_brain.py"
    script.parent.mkdir(parents=True)
    script.write_text("# daemon entrypoint\n")

    log = tmp_path / "logs" / "adaptive_brain.log"
    log.parent.mkdir(parents=True)
    log.write_text("INFO started\nERROR transient failure\nINFO recovered\n")

    state = tmp_path / "state" / "adaptive_brain_status.json"
    state.parent.mkdir(parents=True)
    state.write_text(
        json.dumps(
            {
                "status": "ok",
                "mode": "watchdog",
                "last_heartbeat_utc": "2026-06-11T17:25:00Z",
                "consecutive_failures": 0,
                "api_key": "must-not-leak",
            }
        )
    )

    now = 1_800_000_000
    os.utime(log, (now - 12, now - 12))
    os.utime(state, (now - 7, now - 7))
    ps_output = (
        "treycsa 4321 0.0 0.1 100 200 ?? S 5:25PM 0:01 "
        f"python {script}\n"
    )

    data = get_adaptive_brain_status(control_tower=tmp_path, ps_output=ps_output, now=now)

    assert data["status"] == "running"
    assert data["running"] is True
    assert data["processes"] == [{"pid": 4321, "command": f"python {script}"}]
    assert data["script"]["present"] is True
    assert data["log"]["age_seconds"] == 12
    assert data["log"]["error_count_last_100"] == 1
    assert data["state"]["age_seconds"] == 7
    assert data["state"]["fields"]["status"] == "ok"
    assert "api_key" not in data["state"]["fields"]
    assert set(data["evidence"]) == {"process_match", "script_file", "log_file", "state_file"}


def test_status_probe_degrades_without_fabricating_health(tmp_path):
    from algochains_mcp.adaptive_brain_status import get_adaptive_brain_status

    data = get_adaptive_brain_status(control_tower=tmp_path, ps_output="", now=1_800_000_000)

    assert data["daemon"] == "adaptive_brain.py"
    assert data["status"] == "unknown"
    assert data["running"] is False
    assert data["script"]["present"] is False
    assert data["processes"] == []
    assert data["evidence"] == []


def test_adaptive_brain_status_tool_is_registered_tier1_and_read_only():
    import algochains_mcp.server as srv
    from algochains_mcp.tool_danger_tiers import TIER_READ_ONLY, get_tool_tier

    all_names = {tool.name for tool in srv.TOOLS}
    tier1_names = {tool.name for tool in srv.TOOLS_TIER1}

    assert "get_adaptive_brain_status" in all_names
    assert "get_adaptive_brain_status" in tier1_names
    assert "get_adaptive_brain_status" in srv.TIER1_TOOL_NAMES
    assert get_tool_tier("get_adaptive_brain_status") == TIER_READ_ONLY


def test_adaptive_brain_status_tool_is_callable(tmp_path, monkeypatch):
    monkeypatch.setenv("ALGOCHAINS_CONTROL_TOWER", str(tmp_path))
    (tmp_path / "autonomous").mkdir()
    (tmp_path / "autonomous" / "adaptive_brain.py").write_text("# daemon entrypoint\n")

    import algochains_mcp.server as srv

    data = _decode(asyncio.run(srv.call_tool("get_adaptive_brain_status", {})))

    assert data["daemon"] == "adaptive_brain.py"
    assert data["status"] in {"not_running", "running"}
    assert data["script"]["present"] is True
    assert data["control_tower"] == str(tmp_path)
