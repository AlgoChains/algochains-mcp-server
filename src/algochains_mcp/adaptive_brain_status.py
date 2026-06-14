"""Read-only adaptive brain daemon status for MCP health surfaces."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
from pathlib import Path

from .paths import default_control_tower

SCRIPT_NAME = "adaptive_brain.py"
LAUNCHD_LABEL = "com.algochains.adaptive-brain"
SCRIPT_RELATIVE_PATH = Path("autonomous") / SCRIPT_NAME
LOG_CANDIDATES = (
    Path("logs") / "adaptive_brain.log",
    Path("logs") / "adaptive_brain_live.log",
)
STATE_CANDIDATES = (
    Path("state") / "adaptive_brain_status.json",
    Path("state") / "adaptive_brain_state.json",
    Path("logs") / "adaptive_brain_state.json",
)
SHELL_OR_SEARCH_COMMANDS = {
    "bash",
    "cat",
    "fish",
    "grep",
    "less",
    "more",
    "rg",
    "sed",
    "sh",
    "tail",
    "tmux",
    "vim",
    "zsh",
}
PYTHON_EXECUTABLES = {
    "python",
    "python3",
    "python3.10",
    "python3.11",
    "python3.12",
    "python3.13",
    "python3.14",
}


def _command_tokens(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def is_adaptive_brain_command(command: str) -> bool:
    """Return true only for direct/Python execution of adaptive_brain.py.

    Process-table scans routinely include shell, grep, rg, or test commands that
    mention the script name. Those are evidence that someone searched for the
    daemon, not evidence that the daemon itself is alive.
    """
    tokens = _command_tokens(command)
    if not tokens:
        return False

    executable = Path(tokens[0]).name
    if executable in SHELL_OR_SEARCH_COMMANDS:
        return False

    if executable == SCRIPT_NAME:
        return True

    if executable not in PYTHON_EXECUTABLES:
        return False

    for index, token in enumerate(tokens[1:], start=1):
        if tokens[index - 1] == "-c":
            # A Python one-liner containing the script string is not the daemon.
            return False
        if token == "-m":
            module_name = tokens[index + 1] if index + 1 < len(tokens) else ""
            return module_name == "adaptive_brain" or module_name.endswith(".adaptive_brain")
        if Path(token).name == SCRIPT_NAME:
            return True

    return False


def _parse_ps_aux(ps_output: str) -> list[dict[str, object]]:
    matches: list[dict[str, object]] = []
    for line in ps_output.splitlines():
        if not line.strip() or line.lstrip().startswith("USER "):
            continue
        parts = line.split(None, 10)
        if len(parts) < 11 or not parts[1].isdigit():
            continue
        command = parts[10]
        if is_adaptive_brain_command(command):
            matches.append({"pid": int(parts[1]), "command": command})
    return matches


def _parse_launchctl_print(output: str, *, target: str | None = None) -> dict[str, object]:
    status: dict[str, object] = {
        "label": LAUNCHD_LABEL,
        "target": target,
        "available": True,
        "state": None,
        "pid": None,
        "last_exit_status": None,
        "program": None,
    }

    for line in output.splitlines():
        stripped = line.strip()
        if " = " not in stripped:
            continue
        key, value = stripped.split(" = ", 1)
        value = value.strip().strip('"')
        if key == "state":
            status["state"] = value
        elif key == "pid" and value.isdigit():
            status["pid"] = int(value)
        elif key in {"last exit code", "last exit status"}:
            try:
                status["last_exit_status"] = int(value)
            except ValueError:
                status["last_exit_status"] = value
        elif key == "program":
            status["program"] = value

    return status


def _read_launchd_status(launchctl_output: str | None = None) -> dict[str, object]:
    if launchctl_output is not None:
        return _parse_launchctl_print(launchctl_output, target="provided")

    try:
        uid = os.getuid()
    except AttributeError:
        uid = None

    targets = []
    if uid is not None:
        targets.extend([f"gui/{uid}/{LAUNCHD_LABEL}", f"user/{uid}/{LAUNCHD_LABEL}"])
    targets.append(f"system/{LAUNCHD_LABEL}")

    errors: list[str] = []
    for target in targets:
        try:
            proc = subprocess.run(
                ["launchctl", "print", target],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except FileNotFoundError:
            return {
                "label": LAUNCHD_LABEL,
                "available": False,
                "error": "launchctl_unavailable",
            }
        except Exception as exc:
            errors.append(f"{target}: {exc}")
            continue

        stdout = getattr(proc, "stdout", "") or ""
        stderr = getattr(proc, "stderr", "") or ""
        returncode = getattr(proc, "returncode", 0)
        if returncode == 0 and stdout.strip():
            parsed = _parse_launchctl_print(stdout, target=target)
            parsed["returncode"] = returncode
            return parsed
        if stderr.strip():
            errors.append(f"{target}: {stderr.strip()[:160]}")

    return {
        "label": LAUNCHD_LABEL,
        "available": False,
        "error": "; ".join(errors)[:320] if errors else "launchd_label_not_found",
    }


def _safe_age_seconds(path: Path, now: float) -> int | None:
    try:
        return max(0, int(now - path.stat().st_mtime))
    except OSError:
        return None


def _first_existing(root: Path, candidates: tuple[Path, ...]) -> Path | None:
    for relative in candidates:
        path = root / relative
        if path.exists():
            return path
    return None


def _tail_preview(path: Path, *, max_bytes: int = 4096) -> tuple[str, int]:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            handle.seek(max(0, size - max_bytes))
            tail = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return "", 0

    lines = [line.strip() for line in tail.splitlines() if line.strip()]
    last_line = lines[-1][:240] if lines else ""
    error_count = sum(
        1
        for line in lines
        if any(marker in line for marker in ("ERROR", "Exception", "Traceback", " 401", " 422"))
    )
    return last_line, error_count


def _read_state(path: Path | None) -> dict[str, object]:
    if path is None:
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def get_adaptive_brain_status(
    *,
    control_tower: Path | None = None,
    ps_output: str | None = None,
    launchctl_output: str | None = None,
    now: float | None = None,
) -> dict[str, object]:
    """Return bounded, read-only daemon evidence for adaptive_brain.py."""
    root = control_tower or default_control_tower()
    current_time = time.time() if now is None else now
    script_path = root / SCRIPT_RELATIVE_PATH
    log_path = _first_existing(root, LOG_CANDIDATES)
    state_path = _first_existing(root, STATE_CANDIDATES)

    if ps_output is None:
        try:
            ps_output = subprocess.run(
                ["ps", "aux"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            ).stdout
        except Exception:
            ps_output = ""

    processes = _parse_ps_aux(ps_output)
    launchd_status = _read_launchd_status(launchctl_output)
    launchd_state = str(launchd_status.get("state") or "").lower()
    launchd_pid = launchd_status.get("pid")
    launchd_running = launchd_state == "running" or isinstance(launchd_pid, int)

    last_line_preview = ""
    error_count_tail = 0
    if log_path is not None:
        last_line_preview, error_count_tail = _tail_preview(log_path)

    process_running = bool(processes)
    status = "running" if process_running or launchd_running else "not_running"
    if status != "running" and not root.exists():
        status = "control_tower_missing"
    elif status != "running" and not script_path.exists():
        status = "script_missing"

    pid = processes[0]["pid"] if processes else launchd_pid if launchd_running else None
    liveness_evidence = "process" if process_running else "launchd" if launchd_running else None

    return {
        "daemon": SCRIPT_NAME,
        "status": status,
        "running": status == "running",
        "process_running": process_running,
        "launchd_running": launchd_running,
        "liveness_evidence": liveness_evidence,
        "pid": pid,
        "processes": processes,
        "launchd": launchd_status,
        "control_tower": str(root),
        "script_path": str(script_path),
        "script_exists": script_path.exists(),
        "log_path": str(log_path) if log_path else None,
        "log_exists": log_path is not None,
        "log_age_seconds": _safe_age_seconds(log_path, current_time) if log_path else None,
        "error_count_tail": error_count_tail,
        "last_line_preview": last_line_preview,
        "state_path": str(state_path) if state_path else None,
        "state_exists": state_path is not None,
        "state_age_seconds": _safe_age_seconds(state_path, current_time) if state_path else None,
        "state": _read_state(state_path),
    }
