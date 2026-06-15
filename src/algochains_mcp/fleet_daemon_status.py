"""Read-only control-tower daemon liveness summary.

This module mirrors the evidence used by the Mac launchd fleet watchdog without
mutating launchd state. It is intentionally conservative: process matches must
look like direct Python/script execution, and duplicate daemon names are
collapsed before summary counts are computed.
"""
from __future__ import annotations

import json
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .paths import default_control_tower


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


@dataclass(frozen=True)
class DaemonSpec:
    name: str
    script_candidates: tuple[Path, ...]
    log_candidates: tuple[Path, ...] = ()
    state_candidates: tuple[Path, ...] = ()
    launchd_labels: tuple[str, ...] = ()


DEFAULT_DAEMONS: tuple[DaemonSpec, ...] = (
    DaemonSpec(
        name="autonomous_watchdog.py",
        script_candidates=(
            Path("autonomous") / "autonomous_watchdog.py",
            Path("autonomous_watchdog.py"),
        ),
        log_candidates=(
            Path("logs") / "autonomous_watchdog.log",
            Path("logs") / "watchdog.log",
        ),
        state_candidates=(
            Path("state") / "autonomous_watchdog_state.json",
            Path("state") / "watchdog_state.json",
        ),
        launchd_labels=("com.algochains.autonomous-watchdog",),
    ),
    DaemonSpec(
        name="tradovate_token_guardian.py",
        script_candidates=(
            Path("tradovate_token_guardian.py"),
            Path("autonomous") / "tradovate_token_guardian.py",
        ),
        log_candidates=(
            Path("logs") / "tradovate_token_guardian.log",
            Path("logs") / "token_guardian.log",
        ),
        state_candidates=(
            Path("state") / "tradovate_token_guardian_state.json",
            Path("state") / "tradovate_token_state.json",
        ),
        launchd_labels=("com.algochains.tradovate-token-guardian",),
    ),
    DaemonSpec(
        name="adaptive_brain.py",
        script_candidates=(Path("autonomous") / "adaptive_brain.py",),
        log_candidates=(
            Path("logs") / "adaptive_brain.log",
            Path("logs") / "adaptive_brain_live.log",
        ),
        state_candidates=(
            Path("state") / "adaptive_brain_status.json",
            Path("state") / "adaptive_brain_state.json",
            Path("logs") / "adaptive_brain_state.json",
        ),
        launchd_labels=("com.algochains.adaptive-brain",),
    ),
    DaemonSpec(
        name="health_endpoint.py",
        script_candidates=(
            Path("health_endpoint.py"),
            Path("autonomous") / "health_endpoint.py",
            Path("scripts") / "health_endpoint.py",
        ),
        log_candidates=(
            Path("logs") / "health_endpoint.log",
            Path("logs") / "health.log",
        ),
        state_candidates=(
            Path("state") / "health_endpoint_state.json",
            Path("state") / "health_state.json",
        ),
        launchd_labels=("com.algochains.health-endpoint",),
    ),
)

_DEFAULT_BY_NAME = {spec.name: spec for spec in DEFAULT_DAEMONS}
_MANIFEST_CANDIDATES = (
    Path("MANIFEST"),
    Path("daemon_MANIFEST"),
    Path("daemon_manifest.json"),
    Path("launchd_manifest.json"),
    Path("config") / "daemon_manifest.json",
    Path("config") / "launchd_manifest.json",
    Path("LaunchAgents") / "MANIFEST",
)


def _command_tokens(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def is_daemon_command(command: str, script_name: str) -> bool:
    """Return true only for direct/Python execution of a daemon script."""
    tokens = _command_tokens(command)
    if not tokens:
        return False

    executable = Path(tokens[0]).name
    if executable in SHELL_OR_SEARCH_COMMANDS:
        return False
    if executable == script_name:
        return True
    if executable not in PYTHON_EXECUTABLES:
        return False

    module_stem = Path(script_name).stem
    for index, token in enumerate(tokens[1:], start=1):
        if tokens[index - 1] == "-c":
            return False
        if token == "-m":
            module_name = tokens[index + 1] if index + 1 < len(tokens) else ""
            return module_name == module_stem or module_name.endswith(f".{module_stem}")
        if Path(token).name == script_name:
            return True

    return False


def _parse_ps_aux(ps_output: str, script_name: str) -> list[dict[str, object]]:
    matches: list[dict[str, object]] = []
    for line in ps_output.splitlines():
        if not line.strip() or line.lstrip().startswith("USER "):
            continue
        parts = line.split(None, 10)
        if len(parts) < 11 or not parts[1].isdigit():
            continue
        command = parts[10]
        if is_daemon_command(command, script_name):
            matches.append({"pid": int(parts[1]), "command": command})
    return matches


def _safe_age_seconds(path: Path | None, now: float) -> int | None:
    if path is None:
        return None
    try:
        return max(0, int(now - path.stat().st_mtime))
    except OSError:
        return None


def _first_existing(root: Path, candidates: Iterable[Path]) -> Path | None:
    for relative in candidates:
        path = root / relative
        if path.exists():
            return path
    return None


def _tail_preview(path: Path | None, *, max_bytes: int = 4096) -> tuple[str, int]:
    if path is None:
        return "", 0
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


def _read_command_output(command: list[str]) -> str:
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout
    except Exception:
        return ""


def _parse_launchctl_labels(launchctl_output: str) -> set[str]:
    labels: set[str] = set()
    for raw_line in launchctl_output.splitlines():
        line = raw_line.strip()
        if not line or line.lower().startswith("pid "):
            continue
        parts = line.split()
        if not parts:
            continue
        label = parts[-1]
        if label.startswith("com.algochains."):
            labels.add(label)
    return labels


def _labels_from_json(value: object) -> set[str]:
    labels: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            if isinstance(key, str) and key.startswith("com.algochains."):
                labels.add(key)
            labels.update(_labels_from_json(nested))
    elif isinstance(value, list):
        for item in value:
            labels.update(_labels_from_json(item))
    elif isinstance(value, str) and value.startswith("com.algochains."):
        labels.add(value)
    return labels


def _extract_manifest_labels(text: str) -> set[str]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if parsed is not None:
        labels = _labels_from_json(parsed)
        if labels:
            return labels
    return set(re.findall(r"\bcom\.algochains\.[A-Za-z0-9_.-]+\b", text))


def _read_manifest_labels(root: Path) -> tuple[set[str], str | None]:
    for relative in _MANIFEST_CANDIDATES:
        path = root / relative
        if not path.exists():
            continue
        try:
            labels = _extract_manifest_labels(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if labels:
            return labels, str(path)
    return set(), None


def _dedupe_names(names: Iterable[str]) -> tuple[list[str], list[str]]:
    seen: set[str] = set()
    unique: list[str] = []
    duplicates: list[str] = []
    for raw_name in names:
        name = Path(str(raw_name)).name
        if name in seen:
            duplicates.append(name)
            continue
        seen.add(name)
        unique.append(name)
    return unique, duplicates


def _daemon_status(
    spec: DaemonSpec,
    *,
    root: Path,
    ps_output: str,
    launchctl_labels: set[str],
    now: float,
) -> dict[str, object]:
    script_path = _first_existing(root, spec.script_candidates)
    primary_script_path = root / spec.script_candidates[0]
    log_path = _first_existing(root, spec.log_candidates)
    state_path = _first_existing(root, spec.state_candidates)
    processes = _parse_ps_aux(ps_output, spec.name)
    label_loaded = bool(set(spec.launchd_labels) & launchctl_labels)
    last_line_preview, error_count_tail = _tail_preview(log_path)

    status = "running" if processes else "not_running"
    if not processes:
        if not root.exists():
            status = "control_tower_missing"
        elif script_path is None:
            status = "script_missing"

    return {
        "daemon": spec.name,
        "status": status,
        "running": bool(processes),
        "pid": processes[0]["pid"] if processes else None,
        "processes": processes,
        "script_path": str(script_path or primary_script_path),
        "script_exists": script_path is not None,
        "log_path": str(log_path) if log_path else None,
        "log_exists": log_path is not None,
        "log_age_seconds": _safe_age_seconds(log_path, now),
        "error_count_tail": error_count_tail,
        "last_line_preview": last_line_preview,
        "state_path": str(state_path) if state_path else None,
        "state_exists": state_path is not None,
        "state_age_seconds": _safe_age_seconds(state_path, now),
        "state": _read_state(state_path),
        "launchd_labels": list(spec.launchd_labels),
        "launchd_loaded": label_loaded,
    }


def get_fleet_daemon_status(
    *,
    control_tower: Path | None = None,
    daemon_names: Iterable[str] | None = None,
    ps_output: str | None = None,
    launchctl_output: str | None = None,
    manifest_labels: set[str] | None = None,
    now: float | None = None,
) -> dict[str, object]:
    """Return bounded read-only liveness evidence for control-tower daemons."""
    root = control_tower or default_control_tower()
    current_time = time.time() if now is None else now
    requested_names = list(daemon_names) if daemon_names is not None else [spec.name for spec in DEFAULT_DAEMONS]
    unique_names, duplicate_names = _dedupe_names(requested_names)

    unknown_names = [name for name in unique_names if name not in _DEFAULT_BY_NAME]
    specs = [_DEFAULT_BY_NAME[name] for name in unique_names if name in _DEFAULT_BY_NAME]

    if ps_output is None:
        ps_output = _read_command_output(["ps", "aux"])
    if launchctl_output is None:
        launchctl_output = _read_command_output(["launchctl", "list"])
    launchctl_labels = _parse_launchctl_labels(launchctl_output)

    detected_manifest_labels, manifest_source = _read_manifest_labels(root)
    manifest_label_set = manifest_labels if manifest_labels is not None else detected_manifest_labels
    orphan_labels = sorted(launchctl_labels - manifest_label_set) if manifest_label_set else []
    expected_label_set = set().union(*(set(spec.launchd_labels) for spec in specs)) if specs else set()
    untracked_launchd_labels = sorted(launchctl_labels - expected_label_set)

    daemons = [
        _daemon_status(
            spec,
            root=root,
            ps_output=ps_output,
            launchctl_labels=launchctl_labels,
            now=current_time,
        )
        for spec in specs
    ]
    dead_daemons = [daemon for daemon in daemons if not daemon["running"]]
    status = "ok" if not dead_daemons and not orphan_labels and not unknown_names else "degraded"

    return {
        "status": status,
        "control_tower": str(root),
        "daemon_count": len(daemons),
        "running_count": len(daemons) - len(dead_daemons),
        "dead_count": len(dead_daemons),
        "dead_daemons": [daemon["daemon"] for daemon in dead_daemons],
        "daemons": daemons,
        "duplicates_ignored": duplicate_names,
        "unknown_daemons": unknown_names,
        "launchctl_available": bool(launchctl_output.strip()),
        "launchctl_algochains_labels": sorted(launchctl_labels),
        "manifest_source": manifest_source,
        "manifest_label_count": len(manifest_label_set),
        "orphan_launchd_labels": orphan_labels,
        "untracked_launchd_labels": untracked_launchd_labels,
        "note": (
            "orphan_launchd_labels requires a control-tower manifest; "
            "untracked_launchd_labels only compares against this MCP tool's daemon subset."
            if not manifest_label_set and launchctl_labels else ""
        ),
    }

