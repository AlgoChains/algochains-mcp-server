from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace


def _ps_result(stdout: str) -> SimpleNamespace:
    return SimpleNamespace(stdout=stdout)


def test_adaptive_brain_status_detects_python_daemon(tmp_path, monkeypatch):
    from algochains_mcp.live_bot_intelligence import adaptive_brain as adaptive

    script = tmp_path / "autonomous" / "adaptive_brain.py"
    script.parent.mkdir(parents=True)
    script.write_text("print('adaptive brain')\n")
    state = tmp_path / "state" / "adaptive_brain_status.json"
    state.parent.mkdir()
    state.write_text(json.dumps({"phase": "monitoring"}))
    log = tmp_path / "logs" / "adaptive_brain.log"
    log.parent.mkdir()
    log.write_text("alive\n")

    ps = (
        "ubuntu 321 0.0 0.1 123 456 ? S 00:00 0:00 "
        f"python3 -u {script} --loop\n"
    )
    monkeypatch.setattr(adaptive, "CONTROL_TOWER", tmp_path)
    monkeypatch.setattr(adaptive.subprocess, "run", lambda *a, **k: _ps_result(ps))

    result = adaptive.get_adaptive_brain_status()

    assert result["status"] == "running"
    assert result["running"] is True
    assert result["pid"] == 321
    assert result["script_exists"] is True
    assert result["state"] == {"phase": "monitoring"}
    assert result["log_path"] == str(log)


def test_adaptive_brain_status_ignores_shell_and_search_false_positives(tmp_path, monkeypatch):
    from algochains_mcp.live_bot_intelligence import adaptive_brain as adaptive

    ps = "\n".join([
        "ubuntu 111 0.0 0.0 123 456 ? S 00:00 0:00 bash -lc rg adaptive_brain.py /workspace",
        "ubuntu 112 0.0 0.0 123 456 ? S 00:00 0:00 sh -c echo adaptive_brain.py",
        "ubuntu 113 0.0 0.0 123 456 ? S 00:00 0:00 grep adaptive_brain.py",
    ])
    monkeypatch.setattr(adaptive, "CONTROL_TOWER", tmp_path)
    monkeypatch.setattr(adaptive.subprocess, "run", lambda *a, **k: _ps_result(ps))

    result = adaptive.get_adaptive_brain_status()

    assert result["status"] == "not_running"
    assert result["running"] is False
    assert result["pid"] is None


def test_adaptive_brain_status_registered_and_dispatched(monkeypatch):
    import algochains_mcp.live_bot_intelligence.adaptive_brain as adaptive
    import algochains_mcp.server as srv

    monkeypatch.setattr(
        adaptive,
        "get_adaptive_brain_status",
        lambda: {"daemon": "adaptive_brain.py", "status": "running", "running": True},
    )
    monkeypatch.setattr(srv, "_config", SimpleNamespace(tool_mode="smart"))

    registered = {tool.name for tool in srv.TOOLS_ANNOTATED}
    smart = {tool.name for tool in srv.TOOLS_TIER1}

    assert "get_adaptive_brain_status" in registered
    assert "get_adaptive_brain_status" in srv.TIER1_TOOL_NAMES
    assert "get_adaptive_brain_status" in smart

    response = asyncio.run(srv.call_tool("get_adaptive_brain_status", {}))
    payload = json.loads(response[0].text)

    assert payload["status"] == "running"
    assert payload["running"] is True
