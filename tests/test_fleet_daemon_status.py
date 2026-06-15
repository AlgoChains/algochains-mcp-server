from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from algochains_mcp.fleet_daemon_status import (
    get_fleet_daemon_status,
    is_daemon_command,
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
    (root / "tradovate_token_guardian.py").write_text("# daemon\n", encoding="utf-8")
    (root / "autonomous" / "autonomous_watchdog.py").write_text("# daemon\n", encoding="utf-8")
    (root / "health_endpoint.py").write_text("# daemon\n", encoding="utf-8")
    (root / "logs" / "adaptive_brain.log").write_text("boot\nheartbeat ok\n", encoding="utf-8")
    (root / "state" / "adaptive_brain_status.json").write_text('{"last_cycle": "ok"}', encoding="utf-8")
    return root


def test_daemon_process_matching_rejects_shell_and_search_false_positives():
    assert is_daemon_command("/usr/bin/python3 -B -u autonomous/adaptive_brain.py", "adaptive_brain.py")
    assert is_daemon_command("python -m autonomous.adaptive_brain", "adaptive_brain.py")
    assert is_daemon_command("/Users/trey/algochains-control-tower/tradovate_token_guardian.py", "tradovate_token_guardian.py")

    assert not is_daemon_command("bash -lc 'python autonomous/adaptive_brain.py'", "adaptive_brain.py")
    assert not is_daemon_command("rg adaptive_brain.py /workspace", "adaptive_brain.py")
    assert not is_daemon_command("python -c 'print(\"adaptive_brain.py\")'", "adaptive_brain.py")


def test_fleet_status_dedupes_duplicate_daemon_names_and_dead_count(tmp_path):
    root = _make_control_tower(tmp_path)
    ps_output = "\n".join(
        [
            "USER PID %CPU %MEM VSZ RSS TTY STAT START TIME COMMAND",
            _ps_line(222, f"/usr/bin/python3 -B -u {root / 'autonomous' / 'adaptive_brain.py'}"),
            _ps_line(333, f"/usr/bin/python3 -B -u {root / 'tradovate_token_guardian.py'}"),
        ]
    )

    status = get_fleet_daemon_status(
        control_tower=root,
        daemon_names=[
            "autonomous_watchdog.py",
            "tradovate_token_guardian.py",
            "adaptive_brain.py",
            "health_endpoint.py",
            "adaptive_brain.py",
        ],
        ps_output=ps_output,
        launchctl_output="",
        now=1_780_000_000,
    )

    assert status["daemon_count"] == 4
    assert status["running_count"] == 2
    assert status["dead_count"] == 2
    assert status["dead_daemons"] == ["autonomous_watchdog.py", "health_endpoint.py"]
    assert status["duplicates_ignored"] == ["adaptive_brain.py"]


def test_fleet_status_reports_manifest_orphan_launchd_labels(tmp_path):
    root = _make_control_tower(tmp_path)
    launchctl_output = "\n".join(
        [
            "PID\tStatus\tLabel",
            "123\t0\tcom.algochains.adaptive-brain",
            "-\t0\tcom.algochains.ollama-0305",
        ]
    )

    status = get_fleet_daemon_status(
        control_tower=root,
        daemon_names=["adaptive_brain.py"],
        ps_output="",
        launchctl_output=launchctl_output,
        manifest_labels={"com.algochains.adaptive-brain"},
        now=1_780_000_000,
    )

    assert status["orphan_launchd_labels"] == ["com.algochains.ollama-0305"]
    assert "com.algochains.ollama-0305" in status["untracked_launchd_labels"]


def test_fleet_daemon_status_registered_and_callable(monkeypatch, tmp_path):
    import algochains_mcp.fleet_daemon_status as fleet_status
    import algochains_mcp.server as srv

    root = _make_control_tower(tmp_path)
    monkeypatch.setenv("ALGOCHAINS_CONTROL_TOWER", str(root))

    def fake_run(command, *_args, **_kwargs):
        if command == ["ps", "aux"]:
            return SimpleNamespace(
                stdout="\n".join(
                    [
                        "USER PID %CPU %MEM VSZ RSS TTY STAT START TIME COMMAND",
                        _ps_line(444, f"/usr/bin/python3 -B -u {root / 'autonomous' / 'adaptive_brain.py'}"),
                    ]
                )
            )
        if command == ["launchctl", "list"]:
            return SimpleNamespace(stdout="444\t0\tcom.algochains.adaptive-brain\n")
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(fleet_status.subprocess, "run", fake_run)

    assert "get_fleet_daemon_status" in {tool.name for tool in srv.TOOLS_ANNOTATED}
    assert "get_fleet_daemon_status" in {tool.name for tool in srv.TOOLS_TIER1}

    result = asyncio.run(srv.call_tool("get_fleet_daemon_status", {}))
    payload = _decode_tool_result(result)

    assert payload["daemon_count"] == 4
    assert "adaptive_brain.py" not in payload["dead_daemons"]
    assert payload["dead_count"] == 3

