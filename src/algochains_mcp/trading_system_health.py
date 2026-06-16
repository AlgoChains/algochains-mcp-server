"""Trading-system health audit for MCP triage (mirrors control-tower watchdog checks)."""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .bot_log_paths import BOT_SCRIPT_NAMES, STALE_LOG_SECONDS, resolve_bot_log
from .paths import default_control_tower


def _disk_usage(path: Path) -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(path)
        used_pct = round((usage.used / usage.total) * 100, 1) if usage.total else 0.0
        free_pct = round((usage.free / usage.total) * 100, 1) if usage.total else 0.0
        status = "ok"
        if free_pct <= 5 or used_pct >= 95:
            status = "critical"
        elif free_pct <= 15 or used_pct >= 85:
            status = "warn"
        return {
            "path": str(path),
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
            "used_percent": used_pct,
            "free_percent": free_pct,
            "status": status,
        }
    except OSError as exc:
        return {"path": str(path), "status": "unknown", "error": str(exc)}


def _process_running(script_name: str, ps_output: str) -> bool:
    return script_name in ps_output


def _read_health_snapshot(control_tower: Path) -> dict[str, Any]:
    snapshot_path = control_tower / "logs" / "health_snapshot.json"
    if not snapshot_path.exists():
        return {"path": str(snapshot_path), "exists": False}
    try:
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"path": str(snapshot_path), "exists": True, "error": str(exc)}
    return {
        "path": str(snapshot_path),
        "exists": True,
        "payload": payload if isinstance(payload, dict) else {"raw": payload},
    }


def _snapshot_issue_strings(snapshot: dict[str, Any]) -> list[str]:
    payload = snapshot.get("payload")
    if not isinstance(payload, dict):
        return []
    collected: list[str] = []
    for key in ("critical_issues", "issues", "critical", "warnings"):
        value = payload.get(key)
        if isinstance(value, str):
            collected.append(value)
        elif isinstance(value, list):
            collected.extend(str(item) for item in value if item)
    return collected


def _is_cl_legacy_inactive_false_positive(issue: str, cl_bot: dict[str, Any]) -> bool:
    lowered = issue.lower()
    if "cl_bot_live.log" not in lowered or "inactive" not in lowered:
        return False
    return bool(cl_bot.get("active")) and bool(
        cl_bot.get("legacy_stale_mismatch") or cl_bot.get("log_fresh")
    )


def reconcile_health_snapshot(
    snapshot: dict[str, Any],
    *,
    bots: dict[str, Any],
) -> dict[str, Any]:
    """Separate watchdog snapshot issues from MCP-verified false positives."""
    cl_bot = bots.get("cl") or {}
    snapshot_issues = _snapshot_issue_strings(snapshot)
    false_positives: list[str] = []
    effective_critical: list[str] = []

    for issue in snapshot_issues:
        if _is_cl_legacy_inactive_false_positive(issue, cl_bot):
            false_positives.append(issue)
        elif any(
            marker in issue.lower()
            for marker in ("critical", "inactive", "failed", "down", "error")
        ):
            effective_critical.append(issue)
        else:
            effective_critical.append(issue)

    return {
        "snapshot_issue_count": len(snapshot_issues),
        "false_positive_issues": false_positives,
        "effective_critical_issues": effective_critical,
        "cl_legacy_inactive_false_positive": bool(false_positives),
    }


def format_trading_system_health(payload: dict[str, Any]) -> dict[str, Any]:
    """Compact summary for watchdog/Slack surfaces."""
    status = payload.get("status", "unknown")
    critical = list(payload.get("effective_critical_issues") or payload.get("critical_issues") or [])
    issues = list(payload.get("issues") or [])
    false_positives = list(payload.get("false_positive_issues") or [])
    reconciliation = payload.get("snapshot_reconciliation") or {}
    if reconciliation.get("cl_legacy_inactive_false_positive"):
        false_positives = list(
            dict.fromkeys(
                false_positives + list(reconciliation.get("false_positive_issues") or [])
            )
        )

    if status == "ok":
        formatted_line = "[OK] Trading system health audit passed"
    elif critical:
        formatted_line = (
            f"[FAILED] Trading system health audit: {len(critical)} critical issue(s)"
        )
    else:
        formatted_line = (
            f"[DEGRADED] Trading system health audit: {len(issues)} warning(s)"
        )

    summary_parts: list[str] = []
    if false_positives:
        summary_parts.append(
            "CL inactive alert is a legacy log false positive "
            f"({payload.get('bots', {}).get('cl', {}).get('log_path', 'cl_futures_live.log')})"
        )
    if critical:
        summary_parts.append("; ".join(critical[:3]))
    summary = ". ".join(summary_parts) if summary_parts else formatted_line

    return {
        "summary": summary,
        "formatted_line": formatted_line,
        "sev1_eligible": bool(critical),
        "false_positive_count": len(false_positives),
    }


def get_system_health(
    *,
    control_tower: Path | None = None,
    ps_output: str | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Bounded read-only audit aligned with trading-system-health watchdog checks."""
    root = control_tower or default_control_tower()
    current_time = time.time() if now is None else now

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

    bots: dict[str, Any] = {}
    issues: list[str] = []
    critical_issues: list[str] = []

    for bot_id, script_name in BOT_SCRIPT_NAMES.items():
        log_info = resolve_bot_log(root, bot_id, now=current_time)
        process_up = _process_running(script_name, ps_output)
        log_fresh = bool(log_info.get("log_fresh"))
        active = process_up or log_fresh

        bot_payload = {
            "script": script_name,
            "process_running": process_up,
            "log_path": str(log_info["path"]) if log_info.get("path") else None,
            "log_age_seconds": log_info.get("log_age_seconds"),
            "log_fresh": log_fresh,
            "legacy_stale_mismatch": log_info.get("legacy_stale_mismatch"),
            "log_candidates": log_info.get("candidates"),
            "active": active,
        }
        bots[bot_id] = bot_payload

        if not active:
            legacy = log_info.get("candidates", [])
            stale_legacy = next(
                (
                    c["relative"]
                    for c in legacy
                    if c.get("relative") == "logs/cl_bot_live.log" and c.get("exists")
                ),
                None,
            )
            if bot_id == "cl" and log_info.get("legacy_stale_mismatch") and stale_legacy:
                issues.append(
                    f"CL bot active on {log_info.get('canonical_relative')} but stale legacy log "
                    f"{stale_legacy} may trigger false inactive alerts"
                )
                bot_payload["false_positive_risk"] = True
            else:
                detail_log = stale_legacy or log_info.get("canonical_relative") or "log missing"
                msg = f"Bot appears inactive ({detail_log})"
                issues.append(msg)
                critical_issues.append(msg)
        elif bot_id == "cl" and log_info.get("legacy_stale_mismatch"):
            stale_legacy = next(
                (
                    c["relative"]
                    for c in log_info.get("candidates", [])
                    if c.get("relative") == "logs/cl_bot_live.log" and c.get("exists")
                ),
                None,
            )
            if stale_legacy:
                issues.append(
                    f"CL bot active on {log_info.get('canonical_relative')} but stale legacy log "
                    f"{stale_legacy} may trigger false inactive alerts"
                )
                bot_payload["false_positive_risk"] = True

    disk_root = _disk_usage(root if root.exists() else Path("/"))
    disk_home = _disk_usage(Path.home())

    for label, disk in (("control_tower", disk_root), ("home", disk_home)):
        if disk.get("status") == "critical":
            msg = (
                f"Disk space critical on {label}: {disk.get('free_percent')}% free "
                f"({disk.get('path')})"
            )
            issues.append(msg)
            critical_issues.append(msg)
        elif disk.get("status") == "warn":
            issues.append(
                f"Disk space warn on {label}: {disk.get('free_percent')}% free ({disk.get('path')})"
            )

    false_positive_issues: list[str] = []
    for bot_id, bot_payload in bots.items():
        if bot_id == "cl" and bot_payload.get("legacy_stale_mismatch") and bot_payload.get("active"):
            false_positive_issues.append(
                "Bot appears inactive in cl_bot_live.log (legacy path stale; "
                f"active on {bot_payload.get('log_path')})"
            )

    effective_critical = list(critical_issues)
    for fp in false_positive_issues:
        if fp in effective_critical:
            effective_critical.remove(fp)

    status = "ok"
    if effective_critical:
        status = "failed"
    elif issues:
        status = "degraded"

    health_snapshot = _read_health_snapshot(root)
    snapshot_reconciliation = reconcile_health_snapshot(health_snapshot, bots=bots)

    payload = {
        "component": "trading-system-health",
        "status": status,
        "stale_log_threshold_seconds": STALE_LOG_SECONDS,
        "control_tower": str(root),
        "control_tower_exists": root.exists(),
        "bots": bots,
        "disk": {"control_tower": disk_root, "home": disk_home},
        "issues": issues,
        "critical_issues": critical_issues,
        "effective_critical_issues": effective_critical,
        "false_positive_issues": false_positive_issues,
        "health_snapshot": health_snapshot,
        "snapshot_reconciliation": snapshot_reconciliation,
        "checked_at_unix": int(current_time),
    }
    payload.update(format_trading_system_health(payload))
    return payload
