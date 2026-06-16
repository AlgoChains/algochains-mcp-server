"""Read-only incident timeline summaries from control-tower logs/incidents/."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .paths import default_control_tower

_INCIDENT_NAME_RE = re.compile(r"^incident_\d{8}_\d{6}\.json$")


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

        reports.append(
            {
                "incident_file": path.name,
                "path": str(path),
                "title": payload.get("title") or payload.get("summary"),
                "captured_at": payload.get("captured_at") or payload.get("timestamp"),
                "issue_count": payload.get("issue_count") or len(_bounded_issues(payload)),
                "issues": _bounded_issues(payload),
                "bot_processes": bot_processes if isinstance(bot_processes, dict) else None,
                "recent_deploy": payload.get("recent_deploy"),
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
