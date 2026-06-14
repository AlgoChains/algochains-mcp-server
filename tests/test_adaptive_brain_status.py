from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from algochains_mcp.adaptive_brain_status import (
    get_adaptive_brain_status,
    is_adaptive_brain_command,
)


def _ps_line(pid: int, command: str) -> str:
    return f"trey {pid} 0.0 0.1 123 456 ?? S 04:00 0:00 {command}"


def _decode_tool_result(result) -> dict:
    text = result[0].text if hasattr(result[0], "text") else str(result[0])
    return json.loads(text)


def _make_control_tower(tmp_path: Path) -> Path:
    root = tmp_path / "algochains-control-tower"
    (root / "autonomous").mkdir(parents=True)
    (root / "logs").mkdir()
    (root / "state").mkdir()
    (root / "autonomous" / "adaptive_brain.py").write_text("# daemon\n", encoding="utf-8")
    (root / "logs" / "adaptive_brain.log").write_text(
        "booting adaptive brain\nheartbeat ok\n",
        encoding="utf-8",
    )
    (root / "state" / "adaptive_brain_status.json").write_text(
        '{"last_cycle": "2026-06-12T04:00:00Z"}',
        encoding="utf-8",
    )
    return root


def test_adaptive_brain_process_matching_rejects_shell_and_search_false_positives():
    assert is_adaptive_brain_command("/usr/bin/python3 -B -u autonomous/adaptive_brain.py")
    assert is_adaptive_brain_command("python -m autonomous.adaptive_brain")
    assert is_adaptive_brain_command("/Users/treycsa/CascadeProjects/algochains-control-tower/autonomous/adaptive_brain.py")

    assert not is_adaptive_brain_command("bash -lc 'python autonomous/adaptive_brain.py'")
    assert not is_adaptive_brain_command("rg adaptive_brain.py /workspace")
    assert not is_adaptive_brain_command("python -c 'print(\"adaptive_brain.py\")'")


def test_status_does_not_treat_false_positive_ps_lines_as_running(tmp_path):
    root = _make_control_tower(tmp_path)
    ps_output = "\n".join(
        [
            "USER PID %CPU %MEM VSZ RSS TTY STAT START TIME COMMAND",
            _ps_line(111, "bash -lc 'python autonomous/adaptive_brain.py'"),
            _ps_line(112, "rg adaptive_brain.py /workspace"),
        ]
    )

    status = get_adaptive_brain_status(control_tower=root, ps_output=ps_output, now=1_780_000_000)

    assert status["status"] == "not_running"
    assert status["running"] is False
    assert status["pid"] is None
    assert status["processes"] == []
    assert status["script_exists"] is True
    assert status["log_exists"] is True
    assert status["state"]["last_cycle"] == "2026-06-12T04:00:00Z"


def test_status_reports_actual_python_daemon_process(tmp_path):
    root = _make_control_tower(tmp_path)
    script = root / "autonomous" / "adaptive_brain.py"
    ps_output = "\n".join(
        [
            "USER PID %CPU %MEM VSZ RSS TTY STAT START TIME COMMAND",
            _ps_line(222, f"/usr/bin/python3 -B -u {script}"),
        ]
    )

    status = get_adaptive_brain_status(control_tower=root, ps_output=ps_output, now=1_780_000_000)

    assert status["status"] == "running"
    assert status["running"] is True
    assert status["pid"] == 222
    assert status["last_line_preview"] == "heartbeat ok"


def test_adaptive_brain_status_registered_and_callable(monkeypatch, tmp_path):
    import algochains_mcp.adaptive_brain_status as adaptive_status
    import algochains_mcp.server as srv

    root = _make_control_tower(tmp_path)
    monkeypatch.setenv("ALGOCHAINS_CONTROL_TOWER", str(root))

    def fake_run(*_args, **_kwargs):
        script = root / "autonomous" / "adaptive_brain.py"
        return SimpleNamespace(
            stdout="\n".join(
                [
                    "USER PID %CPU %MEM VSZ RSS TTY STAT START TIME COMMAND",
                    _ps_line(333, f"/usr/bin/python3 -B -u {script}"),
                ]
            )
        )

    monkeypatch.setattr(adaptive_status.subprocess, "run", fake_run)

    assert "get_adaptive_brain_status" in {tool.name for tool in srv.TOOLS_ANNOTATED}
    assert "get_adaptive_brain_status" in {tool.name for tool in srv.TOOLS_TIER1}

    result = asyncio.run(srv.call_tool("get_adaptive_brain_status", {}))
    payload = _decode_tool_result(result)

    assert payload["daemon"] == "adaptive_brain.py"
    assert payload["status"] == "running"
    assert payload["pid"] == 333
