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

# Watchdog snapshot text when CL writes to cl_futures_live.log but legacy path is stale.
_CL_LEGACY_INACTIVE_MARKERS = ("cl_bot_live.log", "cl_bot_live")


def is_cl_legacy_inactive_false_positive(issue: str, cl_bot: dict[str, Any]) -> bool:
    """True when watchdog flagged stale legacy CL log but canonical/process evidence is live."""
    text = issue.lower()
    if "inactive" not in text:
        return False
    if not any(marker in issue for marker in _CL_LEGACY_INACTIVE_MARKERS):
        return False
    return bool(cl_bot.get("active")) and bool(cl_bot.get("legacy_stale_mismatch"))


def _extract_snapshot_issue_strings(snapshot_info: dict[str, Any]) -> list[str]:
    payload = snapshot_info.get("payload")
    if not isinstance(payload, dict):
        return []
    collected: list[str] = []
    for key in ("critical_issues", "issues", "critical", "failures", "detail"):
        raw = payload.get(key)
        if isinstance(raw, list):
            collected.extend(str(item) for item in raw)
        elif isinstance(raw, str) and raw.strip():
            collected.append(raw.strip())
    return collected


def format_trading_system_health_line(payload: dict[str, Any]) -> str:
    """Single-line summary for trading-system-health Slack / watchdog posts."""
    status = str(payload.get("effective_status") or payload.get("status", "unknown")).upper()
    prefix = {
        "OK": "[OK]",
        "DEGRADED": "[DEGRADED]",
        "FAILED": "[FAILED]",
    }.get(status, "[ERROR]")

    parts: list[str] = []
    cl_bot = (payload.get("bots") or {}).get("cl") or {}
    false_positives = payload.get("false_positive_issues") or []
    effective_critical = payload.get("effective_critical_issues") or payload.get("critical_issues") or []

    if false_positives and cl_bot.get("active"):
        canonical = cl_bot.get("log_path") or "cl_futures_live.log"
        parts.append(f"CL bot OK ({canonical}; legacy cl_bot_live.log stale — watchdog false positive)")

    for issue in effective_critical:
        if issue not in false_positives:
            parts.append(issue)

    non_critical = [
        issue
        for issue in (payload.get("issues") or [])
        if issue not in false_positives and issue not in effective_critical
    ]
    parts.extend(non_critical[:2])

    if not parts:
        if status == "OK":
            return f"{prefix} Trading system health audit passed"
        return f"{prefix} Trading system health audit {status.lower()}"

    return f"{prefix} {'; '.join(parts)}"


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

    status = "ok"
    if critical_issues:
        status = "failed"
    elif issues:
        status = "degraded"

    cl_bot = bots.get("cl") or {}
    false_positive_issues: list[str] = []
    for issue in list(critical_issues) + list(issues):
        if is_cl_legacy_inactive_false_positive(issue, cl_bot):
            false_positive_issues.append(issue)

    health_snapshot = _read_health_snapshot(root)
    for snapshot_issue in _extract_snapshot_issue_strings(health_snapshot):
        if is_cl_legacy_inactive_false_positive(snapshot_issue, cl_bot):
            if snapshot_issue not in false_positive_issues:
                false_positive_issues.append(snapshot_issue)

    effective_critical_issues = [
        issue for issue in critical_issues if issue not in false_positive_issues
    ]
    effective_status = status
    if status == "failed" and not effective_critical_issues:
        effective_status = "degraded" if issues else "ok"
    elif status == "failed" and effective_critical_issues:
        effective_status = "failed"

    payload: dict[str, Any] = {
        "component": "trading-system-health",
        "status": status,
        "effective_status": effective_status,
        "stale_log_threshold_seconds": STALE_LOG_SECONDS,
        "control_tower": str(root),
        "control_tower_exists": root.exists(),
        "bots": bots,
        "disk": {"control_tower": disk_root, "home": disk_home},
        "issues": issues,
        "critical_issues": critical_issues,
        "false_positive_issues": false_positive_issues,
        "effective_critical_issues": effective_critical_issues,
        "health_snapshot": health_snapshot,
        "checked_at_unix": int(current_time),
    }
    payload["formatted_line"] = format_trading_system_health_line(payload)
    return payload
