"""Read-only liveness/status probe for the control-tower adaptive brain daemon."""
from __future__ import annotations

import json
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

from algochains_mcp.paths import default_control_tower


SCRIPT_NAME = "adaptive_brain.py"
STALE_AFTER_SECONDS = 600

STATE_CANDIDATES = (
    "state/adaptive_brain_status.json",
    "state/adaptive_brain_health.json",
    "state/adaptive_brain.json",
    "logs/adaptive_brain_status.json",
)

LOG_CANDIDATES = (
    "logs/adaptive_brain.log",
    "logs/adaptive_brain_live.log",
    "autonomous/adaptive_brain.log",
)

SAFE_STATE_KEYS = (
    "status",
    "mode",
    "last_heartbeat",
    "last_heartbeat_utc",
    "last_run_at",
    "last_success_at",
    "last_error_at",
    "current_task",
    "last_action",
    "consecutive_failures",
    "restart_count",
    "version",
)


def _age_seconds(path: Path, now: float) -> int | None:
    try:
        return max(0, int(now - path.stat().st_mtime))
    except OSError:
        return None


def _first_existing(root: Path, candidates: tuple[str, ...]) -> Path | None:
    for rel in candidates:
        path = root / rel
        try:
            if path.exists():
                return path
        except OSError:
            continue
    return None


def _tail_text(path: Path, max_bytes: int = 8192) -> str:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            handle.seek(max(0, size - max_bytes))
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _bounded_log_summary(log_path: Path | None, now: float) -> dict[str, Any]:
    if log_path is None:
        return {"present": False}

    tail = _tail_text(log_path)
    lines = [line for line in tail.splitlines() if line.strip()]
    error_count = sum(
        1
        for line in lines[-100:]
        if any(token in line for token in ("ERROR", "Exception", "Traceback", " 401", " 422"))
    )
    return {
        "present": True,
        "path": str(log_path),
        "age_seconds": _age_seconds(log_path, now),
        "error_count_last_100": error_count,
        "last_line_preview": lines[-1][:200] if lines else "",
    }


def _bounded_state_summary(state_path: Path | None, now: float) -> dict[str, Any]:
    if state_path is None:
        return {"present": False}

    summary: dict[str, Any] = {
        "present": True,
        "path": str(state_path),
        "age_seconds": _age_seconds(state_path, now),
    }
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        summary["parse_error"] = str(exc)
        return summary

    if isinstance(payload, dict):
        safe_fields = {key: payload.get(key) for key in SAFE_STATE_KEYS if key in payload}
        if safe_fields:
            summary["fields"] = safe_fields
    return summary


def _ps_aux() -> str:
    try:
        proc = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=5)
    except Exception:
        return ""
    return proc.stdout or ""


def _command_runs_script(command: str, script_name: str) -> bool:
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    if not tokens:
        return False

    executable = Path(tokens[0]).name
    if executable.startswith("python"):
        return any(Path(token).name == script_name for token in tokens[1:])
    return executable == script_name


def _process_matches(ps_output: str, script_name: str = SCRIPT_NAME) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for line in ps_output.splitlines():
        if script_name not in line or "grep " in line:
            continue
        parts = line.split(None, 10)
        if len(parts) < 11:
            continue
        try:
            pid = int(parts[1])
        except ValueError:
            continue
        command = parts[10]
        if not _command_runs_script(command, script_name):
            continue
        matches.append({"pid": pid, "command": command[:300]})
    return matches


def get_adaptive_brain_status(
    *,
    control_tower: Path | None = None,
    ps_output: str | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Return real liveness evidence for ``adaptive_brain.py`` without side effects."""
    resolved_root = control_tower or default_control_tower()
    root = Path(resolved_root)
    observed_at = time.time() if now is None else now

    script_candidates = (root / "autonomous" / SCRIPT_NAME, root / SCRIPT_NAME)
    script_path = next((path for path in script_candidates if path.exists()), script_candidates[0])
    log_path = _first_existing(root, LOG_CANDIDATES)
    state_path = _first_existing(root, STATE_CANDIDATES)
    processes = _process_matches(_ps_aux() if ps_output is None else ps_output)

    log = _bounded_log_summary(log_path, observed_at)
    state = _bounded_state_summary(state_path, observed_at)

    running = bool(processes)
    if running:
        status = "running"
    elif script_path.exists():
        status = "not_running"
    else:
        status = "unknown"

    evidence: list[str] = []
    if running:
        evidence.append("process_match")
    if log.get("present"):
        evidence.append("log_file")
    if state.get("present"):
        evidence.append("state_file")
    if script_path.exists():
        evidence.append("script_file")

    return {
        "daemon": SCRIPT_NAME,
        "status": status,
        "running": running,
        "stale_after_seconds": STALE_AFTER_SECONDS,
        "control_tower": str(root),
        "script": {
            "present": script_path.exists(),
            "path": str(script_path),
        },
        "processes": processes,
        "log": log,
        "state": state,
        "evidence": evidence,
        "note": (
            "Process is running"
            if running
            else "No adaptive_brain.py process found; status is based on local filesystem evidence only"
        ),
    }
