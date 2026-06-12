"""
Read-only status surface for the control-tower adaptive brain daemon.

The daemon itself lives in the sibling ``algochains-control-tower`` checkout on
operator machines. This module intentionally reports bounded evidence from the
local host instead of fabricating liveness when the daemon is absent.
"""
from __future__ import annotations

import json
import shlex
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from algochains_mcp.paths import default_control_tower

CONTROL_TOWER = default_control_tower()
DAEMON_SCRIPT = "adaptive_brain.py"


def _file_age_seconds(path: Path) -> float | None:
    try:
        return round(time.time() - path.stat().st_mtime, 1)
    except OSError:
        return None


def _first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        try:
            if path.exists():
                return path
        except OSError:
            continue
    return None


def _read_json(path: Path | None) -> dict | None:
    if path is None:
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _command_from_ps_line(line: str) -> str:
    parts = line.split(None, 10)
    return parts[10] if len(parts) >= 11 else line


def _line_is_adaptive_brain_process(line: str) -> bool:
    """Return True only for direct/python execution of adaptive_brain.py.

    Watchdog triage commands often include the script name in shell, grep, rg, or
    pytest command lines. Those are evidence of investigation, not daemon
    liveness, so they must not count as a running adaptive brain.
    """
    if DAEMON_SCRIPT not in line:
        return False

    command = _command_from_ps_line(line)
    try:
        argv = shlex.split(command)
    except ValueError:
        return False
    if not argv:
        return False

    executable = Path(argv[0]).name
    if executable == DAEMON_SCRIPT:
        return True
    if not (executable == "python" or executable.startswith("python3")):
        return False

    return any(Path(arg).name == DAEMON_SCRIPT for arg in argv[1:])


def _pid_from_ps_line(line: str) -> int | None:
    parts = line.split()
    if len(parts) < 2:
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None


def _find_adaptive_brain_process() -> dict | None:
    try:
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return None

    for line in result.stdout.splitlines():
        if _line_is_adaptive_brain_process(line):
            return {
                "pid": _pid_from_ps_line(line),
                "command": _command_from_ps_line(line),
            }
    return None


def get_adaptive_brain_status() -> dict:
    """Return local adaptive-brain daemon status with bounded evidence."""
    control_tower = CONTROL_TOWER
    script_path = _first_existing([
        control_tower / "autonomous" / DAEMON_SCRIPT,
        control_tower / DAEMON_SCRIPT,
    ])
    state_path = _first_existing([
        control_tower / "state" / "adaptive_brain_status.json",
        control_tower / "state" / "adaptive_brain.json",
        control_tower / "logs" / "adaptive_brain_status.json",
    ])
    log_path = _first_existing([
        control_tower / "logs" / "adaptive_brain.log",
        control_tower / "logs" / "adaptive_brain_live.log",
        control_tower / "autonomous" / "adaptive_brain.log",
    ])
    process = _find_adaptive_brain_process()

    return {
        "daemon": DAEMON_SCRIPT,
        "status": "running" if process else "not_running",
        "running": process is not None,
        "pid": process.get("pid") if process else None,
        "command": process.get("command") if process else None,
        "control_tower": str(control_tower),
        "script_path": str(script_path) if script_path else "",
        "script_exists": script_path is not None,
        "state_path": str(state_path) if state_path else "",
        "state_age_seconds": _file_age_seconds(state_path) if state_path else None,
        "state": _read_json(state_path),
        "log_path": str(log_path) if log_path else "",
        "log_age_seconds": _file_age_seconds(log_path) if log_path else None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
