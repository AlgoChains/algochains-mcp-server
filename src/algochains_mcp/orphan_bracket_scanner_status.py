"""Read-only ORPHAN-BRACKET-SCANNER daemon status for MCP health surfaces."""
from __future__ import annotations

import json
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

from .paths import default_control_tower

SCRIPT_NAME = "orphan_bracket_scanner.py"
SCRIPT_CANDIDATES = (
    Path("autonomous") / SCRIPT_NAME,
    Path("scripts") / SCRIPT_NAME,
    Path(SCRIPT_NAME),
)
LOG_CANDIDATES = (
    Path("logs") / "orphan_bracket_scanner.log",
    Path("logs") / "orphan_bracket_scanner_live.log",
)
STATE_CANDIDATES = (
    Path("state") / "orphan_bracket_scanner_state.json",
    Path("state") / "orphan_bracket_scanner.json",
    Path("logs") / "orphan_bracket_scanner_state.json",
)
GUARDIAN_STATE = Path("state") / "bracket_guardian_state.json"
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


def is_orphan_bracket_scanner_command(command: str) -> bool:
    """Return true only for direct/Python execution of orphan_bracket_scanner.py."""
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
            return False
        if token == "-m":
            module_name = tokens[index + 1] if index + 1 < len(tokens) else ""
            return module_name == "orphan_bracket_scanner" or module_name.endswith(".orphan_bracket_scanner")
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
        if is_orphan_bracket_scanner_command(command):
            matches.append({"pid": int(parts[1]), "command": command})
    return matches


def _safe_age_seconds(path: Path | None, now: float) -> int | None:
    if path is None:
        return None
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


def _read_state(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _normalize_gate_fields(state: dict[str, Any]) -> dict[str, Any]:
    """Normalize control-tower gate keys used by ORPHAN-BRACKET-SCANNER Slack posts."""
    gate = state.get("gate") or state.get("GATE")
    if gate is None and isinstance(state.get("gate_decision"), str):
        gate = state["gate_decision"]

    swing = state.get("swing") or state.get("SWING")
    mnq_swing_protect = (
        state.get("mnq_swing_protect")
        or state.get("MNQ_SWING_PROTECT")
        or state.get("mnq_swing_protect_enabled")
    )

    return {
        "gate": gate,
        "swing": swing,
        "mnq_swing_protect": mnq_swing_protect,
    }


def _unknown_flat_orders(state: dict[str, Any]) -> list[Any]:
    raw = state.get("unknown_flat_orders")
    if isinstance(raw, list):
        return raw
    return []


def get_orphan_bracket_scanner_status(
    *,
    control_tower: Path | None = None,
    ps_output: str | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Return bounded, read-only evidence for the ORPHAN-BRACKET-SCANNER daemon."""
    root = control_tower or default_control_tower()
    current_time = time.time() if now is None else now
    script_path = _first_existing(root, SCRIPT_CANDIDATES)
    log_path = _first_existing(root, LOG_CANDIDATES)
    state_path = _first_existing(root, STATE_CANDIDATES)
    guardian_path = root / GUARDIAN_STATE

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
    scanner_state = _read_state(state_path)
    guardian_state = _read_state(guardian_path if guardian_path.exists() else None)
    merged_state = {**guardian_state, **scanner_state}
    gate_fields = _normalize_gate_fields(merged_state)
    unknown_flat = _unknown_flat_orders(scanner_state) or _unknown_flat_orders(guardian_state)

    last_check = (
        scanner_state.get("last_check")
        or scanner_state.get("last_scan")
        or guardian_state.get("last_orphan_scan")
        or guardian_state.get("last_check")
    )
    orphans_cancelled = (
        scanner_state.get("orphans_cancelled")
        or scanner_state.get("cancelled_count")
        or guardian_state.get("orphans_cancelled")
        or 0
    )
    orphans_found = (
        scanner_state.get("orphans_found")
        or scanner_state.get("orphan_count")
        or len(unknown_flat)
    )

    status = "running" if processes else "not_running"
    if not root.exists():
        status = "control_tower_missing"
    elif script_path is None:
        status = "script_missing"

    gate = str(gate_fields.get("gate") or "").upper()
    if gate == "PROCEED":
        scan_status = "OK"
    elif gate in {"BLOCK", "HALT", "STOP"}:
        scan_status = "BLOCKED"
    elif unknown_flat:
        scan_status = "ORPHAN_ORDERS"
    else:
        scan_status = "UNKNOWN"

    return {
        "scanner": "ORPHAN-BRACKET-SCANNER",
        "status": status,
        "scan_status": scan_status,
        "running": bool(processes),
        "pid": processes[0]["pid"] if processes else None,
        "processes": processes,
        "control_tower": str(root),
        "script_path": str(script_path) if script_path else None,
        "script_exists": script_path is not None,
        "log_path": str(log_path) if log_path else None,
        "log_exists": log_path is not None,
        "log_age_seconds": _safe_age_seconds(log_path, current_time),
        "state_path": str(state_path) if state_path else None,
        "state_exists": state_path is not None,
        "state_age_seconds": _safe_age_seconds(state_path, current_time),
        "guardian_state_path": str(guardian_path) if guardian_path.exists() else None,
        "last_check": last_check,
        "orphans_found": orphans_found,
        "orphans_cancelled": orphans_cancelled,
        "unknown_flat_orders": unknown_flat,
        "unknown_flat_order_count": len(unknown_flat),
        **gate_fields,
        "state": scanner_state,
        "guardian_state": {
            "last_check": guardian_state.get("last_check"),
            "unknown_flat_order_count": len(_unknown_flat_orders(guardian_state)),
        },
    }
