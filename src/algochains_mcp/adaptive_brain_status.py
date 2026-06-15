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


def _launchd_domains() -> list[str]:
    uid = os.getuid()
    return [
        f"gui/{uid}/{LAUNCHD_LABEL}",
        f"user/{uid}/{LAUNCHD_LABEL}",
        f"system/{LAUNCHD_LABEL}",
    ]


def _parse_launchctl_print(output: str) -> dict[str, object]:
    evidence: dict[str, object] = {}
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or " = " not in stripped:
            continue
        key, value = stripped.split(" = ", 1)
        value = value.strip().strip('"')
        normalized_key = key.strip().lower().replace(" ", "_")
        if normalized_key == "pid":
            try:
                evidence["pid"] = int(value)
            except ValueError:
                evidence["pid"] = None
        elif normalized_key in {"state", "program", "path"}:
            evidence[normalized_key] = value
        elif normalized_key in {"last_exit_code", "last_exit_status"}:
            try:
                evidence["last_exit_status"] = int(value)
            except ValueError:
                evidence["last_exit_status"] = value
    return evidence


def _is_launchd_running(evidence: dict[str, object]) -> bool:
    state = str(evidence.get("state", "")).lower()
    pid = evidence.get("pid")
    return state in {"running", "spawned"} or (isinstance(pid, int) and pid > 0)


def _probe_launchd() -> dict[str, object]:
    checked_labels = _launchd_domains()
    errors: list[dict[str, object]] = []

    for label in checked_labels:
        try:
            result = subprocess.run(
                ["launchctl", "print", label],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except FileNotFoundError:
            return {
                "available": False,
                "running": False,
                "label": None,
                "checked_labels": checked_labels,
                "error": "launchctl_unavailable",
            }
        except subprocess.TimeoutExpired:
            errors.append({"label": label, "error": "timeout"})
            continue
        except Exception as exc:
            errors.append({"label": label, "error": type(exc).__name__})
            continue

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            errors.append({
                "label": label,
                "returncode": result.returncode,
                "stderr": stderr[:240],
            })
            continue

        evidence = _parse_launchctl_print(result.stdout or "")
        running = _is_launchd_running(evidence)
        evidence.update({
            "available": True,
            "running": running,
            "label": label,
            "checked_labels": checked_labels,
        })
        if errors:
            evidence["errors"] = errors
        return evidence

    return {
        "available": True,
        "running": False,
        "label": None,
        "checked_labels": checked_labels,
        "errors": errors,
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
    launchd_evidence: dict[str, object] | None = None,
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
    launchd = launchd_evidence if launchd_evidence is not None else _probe_launchd()
    launchd_running = bool(launchd.get("running"))
    process_running = bool(processes)
    last_line_preview = ""
    error_count_tail = 0
    if log_path is not None:
        last_line_preview, error_count_tail = _tail_preview(log_path)

    status = "running" if process_running or launchd_running else "not_running"
    if not root.exists():
        status = "control_tower_missing"
    elif not script_path.exists():
        status = "script_missing"

    pid = processes[0]["pid"] if processes else launchd.get("pid")
    liveness_evidence = "process" if process_running else "launchd" if launchd_running else "none"

    return {
        "daemon": SCRIPT_NAME,
        "status": status,
        "running": process_running or launchd_running,
        "process_running": process_running,
        "launchd_running": launchd_running,
        "liveness_evidence": liveness_evidence,
        "pid": pid if isinstance(pid, int) else None,
        "processes": processes,
        "launchd": launchd,
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
