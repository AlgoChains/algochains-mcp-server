"""
adaptive_brain.py — Read-only liveness status for the control-tower daemon.

The adaptive brain runs in the adjacent algochains-control-tower repository,
not inside this MCP package. This helper intentionally reports only bounded
process/state/log metadata so MCP liveness checks can diagnose daemon failures
without importing or executing control-tower code.
"""
from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from algochains_mcp.paths import default_control_tower


PROCESS_PATTERNS = ("adaptive_brain.py", "autonomous/adaptive_brain.py")
STATE_CANDIDATES = (
    Path("state/adaptive_brain_state.json"),
    Path("state/adaptive_brain_status.json"),
    Path("state/adaptive_brain.json"),
)
LOG_CANDIDATES = (
    Path("logs/adaptive_brain.log"),
    Path("logs/adaptive_brain_daemon.log"),
)


def _read_ps_output() -> tuple[str, str | None]:
    """Return ps output plus an optional error string."""
    try:
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout, None
    except Exception as exc:
        return "", str(exc)


def _extract_pids(ps_output: str) -> list[int]:
    """Extract process IDs for adaptive_brain.py from ps output."""
    pids: list[int] = []
    for line in ps_output.splitlines():
        if "grep" in line:
            continue
        if not any(pattern in line for pattern in PROCESS_PATTERNS):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            pids.append(int(parts[1]))
        except ValueError:
            continue
    return pids


def _first_existing(control_tower: Path, candidates: tuple[Path, ...]) -> Path | None:
    for rel_path in candidates:
        candidate = control_tower / rel_path
        if candidate.exists():
            return candidate
    return None


def _parse_timestamp(value: Any) -> float | None:
    """Parse epoch or ISO timestamps used by control-tower state files."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 1_000_000_000_000:
            timestamp /= 1000.0
        return timestamp
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return _parse_timestamp(float(text))
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def _timestamp_from_state(state: dict[str, Any]) -> tuple[float | None, str | None]:
    for key in (
        "unix",
        "timestamp_unix",
        "timestamp",
        "last_heartbeat",
        "last_heartbeat_utc",
        "last_check_utc",
        "last_run_utc",
        "updated_at",
        "generated_at",
    ):
        parsed = _parse_timestamp(state.get(key))
        if parsed is not None:
            return parsed, key
    return None, None


def _read_state(path: Path | None, now: float) -> dict[str, Any]:
    if path is None:
        return {"present": False}
    try:
        raw = json.loads(path.read_text())
        if not isinstance(raw, dict):
            return {"present": True, "path": str(path), "error": "state payload is not an object"}
        state_timestamp, timestamp_key = _timestamp_from_state(raw)
        status: dict[str, Any] = {
            "present": True,
            "path": str(path),
            "mtime_age_seconds": max(0, int(now - path.stat().st_mtime)),
            "reported_status": raw.get("status") or raw.get("state"),
            "last_error": raw.get("last_error") or raw.get("error"),
        }
        if state_timestamp is not None:
            status["timestamp_key"] = timestamp_key
            status["timestamp_age_seconds"] = max(0, int(now - state_timestamp))
        return status
    except Exception as exc:
        return {"present": True, "path": str(path), "error": f"parse failure: {exc}"}


def _read_log(path: Path | None, now: float) -> dict[str, Any]:
    if path is None:
        return {"present": False}
    status: dict[str, Any] = {
        "present": True,
        "path": str(path),
        "mtime_age_seconds": max(0, int(now - path.stat().st_mtime)),
    }
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            handle.seek(max(0, size - 4096))
            tail = handle.read().decode("utf-8", errors="replace")
        lines = [line.strip() for line in tail.splitlines() if line.strip()]
        if lines:
            status["last_line_preview"] = lines[-1][:240]
            status["error_count_tail"] = sum(
                1
                for line in lines
                if any(token in line for token in ("ERROR", "Exception", "Traceback", "CRITICAL"))
            )
    except Exception as exc:
        status["error"] = f"log read failure: {exc}"
    return status


def _classify(
    *,
    pids: list[int],
    state: dict[str, Any],
    log: dict[str, Any],
    ps_error: str | None,
    stale_after_seconds: int,
) -> tuple[str, str]:
    if ps_error and not pids:
        return "unknown", f"Could not inspect process list: {ps_error}"
    if not pids:
        return "dead", "adaptive_brain.py process is not running"

    age_candidates = [
        state.get("timestamp_age_seconds"),
        state.get("mtime_age_seconds") if state.get("present") else None,
        log.get("mtime_age_seconds") if log.get("present") else None,
    ]
    stale_ages = [
        int(age)
        for age in age_candidates
        if isinstance(age, (int, float)) and age > stale_after_seconds
    ]
    if stale_ages:
        return (
            "stale",
            f"adaptive_brain.py is running but telemetry is stale ({max(stale_ages)}s old)",
        )

    if state.get("error"):
        return "degraded", str(state["error"])
    if log.get("error"):
        return "degraded", str(log["error"])
    return "healthy", "adaptive_brain.py process is running"


def get_adaptive_brain_status(
    *,
    control_tower: Path | None = None,
    ps_output: str | None = None,
    now: float | None = None,
    stale_after_seconds: int = 900,
) -> dict[str, Any]:
    """Return a bounded read-only status snapshot for adaptive_brain.py."""
    control_tower = control_tower or default_control_tower()
    now = time.time() if now is None else now
    ps_error: str | None = None
    if ps_output is None:
        ps_output, ps_error = _read_ps_output()
    pids = _extract_pids(ps_output)

    state_path = _first_existing(control_tower, STATE_CANDIDATES)
    log_path = _first_existing(control_tower, LOG_CANDIDATES)
    state = _read_state(state_path, now)
    log = _read_log(log_path, now)
    status, detail = _classify(
        pids=pids,
        state=state,
        log=log,
        ps_error=ps_error,
        stale_after_seconds=stale_after_seconds,
    )

    return {
        "status": status,
        "detail": detail,
        "running": bool(pids),
        "pids": pids,
        "script": "adaptive_brain.py",
        "control_tower": str(control_tower),
        "control_tower_exists": control_tower.exists(),
        "stale_after_seconds": stale_after_seconds,
        "state": state,
        "log": log,
        "generated_at": int(now),
    }
