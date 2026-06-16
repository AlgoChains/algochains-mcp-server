"""Trading-system health audit for MCP triage (mirrors control-tower watchdog checks)."""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .bot_log_paths import (
    BOT_SCRIPT_NAMES,
    STALE_LOG_SECONDS,
    resolve_bot_log,
    sync_bot_log_legacy_aliases,
)
from .paths import default_control_tower

_LEGACY_INACTIVE_MARKERS = (
    "cl_bot_live.log",
    "mes_swing.log",
    "nq_swing.log",
)


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


def _reconcile_watchdog_snapshot(
    snapshot_payload: dict[str, Any] | None,
    *,
    bots: dict[str, Any],
) -> dict[str, Any]:
    """Downgrade known false positives emitted by legacy control-tower audits."""
    if not isinstance(snapshot_payload, dict):
        return {"reconciled": False, "false_positive_issues": [], "remaining_critical": []}

    raw_critical = snapshot_payload.get("critical_issues")
    if not isinstance(raw_critical, list):
        raw_critical = snapshot_payload.get("issues")
    if not isinstance(raw_critical, list):
        return {"reconciled": False, "false_positive_issues": [], "remaining_critical": []}

    false_positives: list[str] = []
    remaining: list[str] = []
    for issue in raw_critical:
        text = str(issue)
        lowered = text.lower()
        if (
            "inactive" in lowered
            and any(marker in lowered for marker in _LEGACY_INACTIVE_MARKERS)
            and bots.get("cl", {}).get("active")
            and bots.get("cl", {}).get("legacy_stale_mismatch")
        ):
            false_positives.append(text)
            continue
        remaining.append(text)

    return {
        "reconciled": bool(false_positives),
        "false_positive_issues": false_positives,
        "remaining_critical": remaining,
    }


def format_system_health_line(payload: dict[str, Any]) -> str:
    """Compact watchdog-compatible status line for Slack posts."""
    status = payload.get("status", "unknown")
    critical = payload.get("critical_issues") or []
    reconciliation = payload.get("watchdog_reconciliation") or {}
    false_positives = reconciliation.get("false_positive_issues") or []

    if status == "ok":
        return "[OK] Trading system health audit passed"
    if status == "degraded" and not critical:
        return f"[WARN] Trading system health degraded ({len(payload.get('issues') or [])} issue(s))"
    if false_positives and not critical:
        return (
            "[WARN] Trading system health: legacy log false positive reconciled; "
            f"{len(false_positives)} inactive alert(s) suppressed"
        )
    if critical:
        joined = "; ".join(str(item) for item in critical[:3])
        return f"[FAILED] Trading system health audit FAILED. Critical issues: {joined}"
    return f"[{str(status).upper()}] Trading system health audit"


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

    health_snapshot = _read_health_snapshot(root)
    snapshot_payload = None
    if isinstance(health_snapshot.get("payload"), dict):
        snapshot_payload = health_snapshot["payload"]

    reconciliation = _reconcile_watchdog_snapshot(snapshot_payload, bots=bots)
    if reconciliation.get("false_positive_issues"):
        for fp_issue in reconciliation["false_positive_issues"]:
            note = (
                f"Reconciled watchdog false positive: {fp_issue} "
                f"(CL active on {bots.get('cl', {}).get('log_path')})"
            )
            if note not in issues:
                issues.append(note)

    status = "ok"
    if critical_issues:
        status = "failed"
    elif issues:
        status = "degraded"

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
        "watchdog_reconciliation": reconciliation,
        "health_snapshot": health_snapshot,
        "checked_at_unix": int(current_time),
    }
    payload["summary"] = format_system_health_line(payload)
    payload["formatted_line"] = payload["summary"]
    return payload


def repair_trading_system_health(
    *,
    control_tower: Path | None = None,
    dry_run: bool = False,
    now: float | None = None,
) -> dict[str, Any]:
    """Repair legacy log alias drift and return a fresh health audit."""
    root = control_tower or default_control_tower()
    sync_result = sync_bot_log_legacy_aliases(root, dry_run=dry_run, now=now)
    health = get_system_health(control_tower=root, now=now)
    return {
        "repair": sync_result,
        "health": health,
        "formatted_line": health.get("formatted_line"),
    }
