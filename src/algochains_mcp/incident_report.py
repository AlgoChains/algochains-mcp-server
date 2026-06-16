"""Read-only incident timeline summaries from control-tower logs/incidents/."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .live_bot_intelligence.heartbeat import (
    BOT_SCRIPT_NAMES,
    EXPECTED_DESKTOP_BOT_COUNT,
    scan_running_bot_keys,
)
from .paths import default_control_tower

_INCIDENT_NAME_RE = re.compile(r"^incident_\d{8}_\d{6}\.json$")
_BOT_COUNT_MISMATCH_RE = re.compile(r"bot\s+processes:\s*(\d+)/(\d+)", re.I)
_ALPHA_LOOP_ISSUE_RE = re.compile(r"alpha[_-]?loop|wal-?mode|immutable", re.I)


def _list_incident_files(incidents_dir: Path) -> list[Path]:
    if not incidents_dir.is_dir():
        return []
    files = [
        path
        for path in incidents_dir.iterdir()
        if path.is_file() and _INCIDENT_NAME_RE.match(path.name)
    ]
    return sorted(files, key=lambda path: path.name, reverse=True)


def _bounded_issues(payload: dict[str, Any], *, limit: int = 10) -> list[Any]:
    issues = payload.get("issues")
    if isinstance(issues, list):
        return issues[:limit]
    critical = payload.get("critical_issues")
    if isinstance(critical, list):
        return critical[:limit]
    failures = payload.get("failures")
    if isinstance(failures, list):
        return failures[:limit]
    return []


def _live_bot_processes() -> dict[str, Any]:
    running = scan_running_bot_keys()
    return {
        "running_count": len(running),
        "expected_count": EXPECTED_DESKTOP_BOT_COUNT,
        "processes": {bot_key: bot_key in running for bot_key in BOT_SCRIPT_NAMES},
        "all_running": len(running) >= EXPECTED_DESKTOP_BOT_COUNT
        and all(bot_key in running for bot_key in BOT_SCRIPT_NAMES),
    }


def _triage_notes(
    issues: list[Any],
    bot_processes: dict[str, Any] | None,
    recent_deploy: Any,
) -> list[str]:
    notes: list[str] = []
    running = expected = None
    if isinstance(bot_processes, dict):
        running = bot_processes.get("running") or bot_processes.get("running_count")
        expected = bot_processes.get("expected") or bot_processes.get("expected_count")

    for issue in issues:
        if not isinstance(issue, str):
            continue
        match = _BOT_COUNT_MISMATCH_RE.search(issue)
        if match:
            running = int(match.group(1))
            expected = int(match.group(2))

    if running == EXPECTED_DESKTOP_BOT_COUNT and expected == 4:
        notes.append(
            "Bot Processes 5/4 is a watchdog false positive: desktop failover runs "
            f"{EXPECTED_DESKTOP_BOT_COUNT} canonical bots (MNQ/CL/MES/NQ + Kalshi). "
            "Use bot_processes.expected_count=5 from get_bot_health."
        )

    deploy_text = str(recent_deploy or "")
    alpha_loop_in_issues = any(
        isinstance(issue, str) and _ALPHA_LOOP_ISSUE_RE.search(issue) for issue in issues
    )
    if alpha_loop_in_issues or _ALPHA_LOOP_ISSUE_RE.search(deploy_text):
        notes.append(
            "alpha_loop WAL-mode SQLite read failures are owned by algochains-control-tower "
            "(read-only URI needs immutable=1). Recent deploy metadata in the incident "
            "usually indicates the fix is already staged on the tower."
        )

    return notes


def get_incident_report(
    *,
    control_tower: Path | None = None,
    incident_id: str | None = None,
    limit: int = 1,
) -> dict[str, Any]:
    """Return the latest (or requested) critical-path incident timeline summary."""
    root = control_tower or default_control_tower()
    incidents_dir = root / "logs" / "incidents"
    files = _list_incident_files(incidents_dir)

    if incident_id:
        candidate = incidents_dir / incident_id
        if not candidate.name.endswith(".json"):
            candidate = incidents_dir / f"{incident_id}.json"
        files = [candidate] if candidate.exists() else []

    selected = files[: max(1, min(limit, 5))]
    reports: list[dict[str, Any]] = []

    for path in selected:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            reports.append(
                {
                    "incident_file": path.name,
                    "path": str(path),
                    "error": str(exc),
                }
            )
            continue

        if not isinstance(payload, dict):
            reports.append(
                {
                    "incident_file": path.name,
                    "path": str(path),
                    "error": "incident payload is not a JSON object",
                }
            )
            continue

        bot_processes = payload.get("bot_processes")
        if not isinstance(bot_processes, dict):
            bot_processes = payload.get("bots")

        bounded_issues = _bounded_issues(payload)
        recent_deploy = payload.get("recent_deploy")
        triage_notes = _triage_notes(
            bounded_issues,
            bot_processes if isinstance(bot_processes, dict) else None,
            recent_deploy,
        )

        reports.append(
            {
                "incident_file": path.name,
                "path": str(path),
                "title": payload.get("title") or payload.get("summary"),
                "captured_at": payload.get("captured_at") or payload.get("timestamp"),
                "issue_count": payload.get("issue_count") or len(bounded_issues),
                "issues": bounded_issues,
                "bot_processes": bot_processes if isinstance(bot_processes, dict) else None,
                "live_bot_processes": _live_bot_processes(),
                "triage_notes": triage_notes,
                "recent_deploy": recent_deploy,
                "active_alerts": payload.get("active_alerts"),
                "network": payload.get("network"),
                "token": payload.get("token"),
            }
        )

    status = "ok"
    if not reports:
        status = "missing"
    elif any("error" in report for report in reports):
        status = "degraded"

    return {
        "component": "incident-report",
        "status": status,
        "control_tower": str(root),
        "incidents_dir": str(incidents_dir),
        "incidents_available": len(_list_incident_files(incidents_dir)),
        "reports": reports,
    }
