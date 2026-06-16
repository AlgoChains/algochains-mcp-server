"""Read-only latency monitor status for MCP / control-tower watchdog triage."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .bot_log_paths import resolve_bot_log
from .paths import default_control_tower
from .tradovate_token_status import resolve_tradovate_probe_token, summarize_tradovate_token_state

DEFAULT_LATENCY_THRESHOLD_MS = 500
WATCHED_BOTS = ("mnq", "cl")
SCHEDULER_STATE_CANDIDATES = (
    Path("state") / "scheduler_state.json",
    Path("state") / "latency_monitor_state.json",
)


def _tradovate_base_url() -> str:
    env = os.environ.get("TRADOVATE_ENV", "live").strip().lower()
    if env == "demo":
        return "https://demo.tradovateapi.com"
    return "https://live.tradovateapi.com"


def _latency_threshold_ms() -> int:
    raw = os.environ.get("ALGOCHAINS_TRADOVATE_LATENCY_THRESHOLD_MS", "").strip()
    if not raw:
        return DEFAULT_LATENCY_THRESHOLD_MS
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_LATENCY_THRESHOLD_MS


def _probe_tradovate(token: str, *, base_url: str, timeout_s: float = 10.0) -> dict[str, Any]:
    if not token:
        return {
            "http_status": None,
            "latency_ms": None,
            "ok": False,
            "error": "no_probe_token",
        }

    url = f"{base_url}/v1/account/list"
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        method="GET",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            latency_ms = round((time.monotonic() - started) * 1000, 1)
            return {
                "http_status": response.status,
                "latency_ms": latency_ms,
                "ok": response.status == 200,
            }
    except urllib.error.HTTPError as exc:
        latency_ms = round((time.monotonic() - started) * 1000, 1)
        return {
            "http_status": exc.code,
            "latency_ms": latency_ms,
            "ok": False,
            "error": exc.reason,
        }
    except Exception as exc:
        latency_ms = round((time.monotonic() - started) * 1000, 1)
        return {
            "http_status": None,
            "latency_ms": latency_ms,
            "ok": False,
            "error": str(exc),
        }


def _scheduler_state(root: Path) -> dict[str, Any]:
    env_state = os.environ.get("ALGOCHAINS_SCHEDULER_STATE", "").strip()
    if env_state:
        return {
            "state": env_state,
            "source": "env:ALGOCHAINS_SCHEDULER_STATE",
            "expected_disabled": env_state.lower() == "disabled",
        }

    for relative in SCHEDULER_STATE_CANDIDATES:
        path = root / relative
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            state = str(payload.get("state") or payload.get("scheduler_state") or payload.get("status") or "unknown")
            return {
                "state": state,
                "source": str(relative),
                "expected_disabled": state.lower() == "disabled",
                "payload": payload,
            }

    return {
        "state": "disabled",
        "source": "default",
        "expected_disabled": True,
        "note": "Official gateway handles scheduling",
    }


def _bot_log_ages(root: Path, *, now: float) -> dict[str, Any]:
    ages: dict[str, Any] = {}
    for bot_id in WATCHED_BOTS:
        info = resolve_bot_log(root, bot_id, now=now)
        ages[bot_id] = {
            "log_age_seconds": info.get("log_age_seconds"),
            "log_fresh": info.get("log_fresh"),
            "log_path": str(info["path"]) if info.get("path") else None,
        }
    return ages


def _execution_layer_healthy(log_ages: dict[str, Any]) -> bool:
    fresh = [entry.get("log_fresh") for entry in log_ages.values()]
    return bool(fresh) and all(fresh)


def _formatted_block(
    *,
    latency_ms: float | None,
    http_status: int | None,
    scheduler_state: str,
    log_ages: dict[str, Any],
) -> str:
    latency_text = f"{latency_ms:.0f}ms" if latency_ms is not None else "n/a"
    status_text = str(http_status) if http_status is not None else "n/a"
    scheduler_note = ""
    if scheduler_state.lower() == "disabled":
        scheduler_note = "(expected — official gateway handles scheduling)"
    lines = [
        f"TRADOVATE_LATENCY={latency_text} STATUS={status_text}",
        f"SCHEDULER_STATE={scheduler_state}{scheduler_note}",
    ]
    for bot_id in WATCHED_BOTS:
        age = log_ages.get(bot_id, {}).get("log_age_seconds")
        lines.append(f"LOG_AGE {bot_id.upper()}={age if age is not None else 'n/a'}s")
    return "\n".join(lines)


def get_latency_monitor_status(
    *,
    control_tower: Path | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    root = control_tower or default_control_tower()
    current_time = time.time() if now is None else float(now)
    threshold_ms = _latency_threshold_ms()
    base_url = _tradovate_base_url()

    token, token_source = resolve_tradovate_probe_token(root)
    token_summary = summarize_tradovate_token_state(root, now=current_time)
    probe = _probe_tradovate(token, base_url=base_url)
    scheduler = _scheduler_state(root)
    log_ages = _bot_log_ages(root, now=current_time)
    execution_healthy = _execution_layer_healthy(log_ages)

    latency_ms = probe.get("latency_ms")
    http_status = probe.get("http_status")
    latency_exceeded = isinstance(latency_ms, (int, float)) and latency_ms > threshold_ms

    issues: list[str] = []
    status = "OK"

    if http_status == 401:
        if execution_healthy and token_summary.get("status") in {"ok", "expiring_soon", "unknown_expiry"}:
            status = "DEGRADED"
            issues.append(
                "Tradovate probe returned 401 while bot logs are fresh — monitor token/env mismatch; "
                "execution layer appears healthy"
            )
        else:
            status = "AUTH_FAIL"
            issues.append("Tradovate probe returned 401 — refresh Token Guardian / shared token artifacts")
    elif http_status not in (200, None):
        status = "DEGRADED"
        issues.append(f"Tradovate probe returned HTTP {http_status}")
    elif probe.get("error") and http_status is None:
        status = "DEGRADED"
        issues.append(f"Tradovate probe failed: {probe.get('error')}")
    elif latency_exceeded:
        status = "WARN"
        issues.append(
            f"Tradovate latency {latency_ms:.0f}ms exceeds threshold {threshold_ms}ms"
        )

    prefix = {
        "OK": "[OK]",
        "WARN": "[WARN]",
        "DEGRADED": "[DEGRADED]",
        "AUTH_FAIL": "[AUTH_FAIL]",
    }.get(status, "[UNKNOWN]")
    formatted = _formatted_block(
        latency_ms=latency_ms if isinstance(latency_ms, (int, float)) else None,
        http_status=http_status if isinstance(http_status, int) else None,
        scheduler_state=str(scheduler.get("state") or "unknown"),
        log_ages=log_ages,
    )
    summary = f"{prefix} LATENCY-MONITOR\n{formatted}"
    if issues:
        summary = f"{prefix} LATENCY-MONITOR — {'; '.join(issues)}\n{formatted}"

    return {
        "component": "latency-monitor",
        "status": status,
        "summary": summary,
        "formatted_line": formatted,
        "tradovate": {
            "base_url": base_url,
            "probe_token_source": token_source,
            "http_status": http_status,
            "latency_ms": latency_ms,
            "latency_threshold_ms": threshold_ms,
            "latency_exceeded": latency_exceeded,
            "token_summary": token_summary,
        },
        "scheduler": scheduler,
        "log_ages": log_ages,
        "execution_layer_healthy": execution_healthy,
        "issues": issues,
        "control_tower": str(root),
        "checked_at": datetime.fromtimestamp(current_time, tz=timezone.utc).isoformat(),
        "action": (
            "Wire control-tower LATENCY-MONITOR to get_latency_monitor_status and probe "
            "tradovate_token_live.txt before env tokens."
            if status in {"DEGRADED", "AUTH_FAIL"}
            else None
        ),
    }
