"""Cron trigger retry queue for MCP health surfaces and CRON-RETRY watchdog."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

CRON_RETRIES_FILE = Path.home() / ".algochains" / "cron_retries.json"
TRIGGERS_FILE = Path.home() / ".algochains" / "triggers.json"

BASE_DELAY_MS = 1_000
MAX_DELAY_MS = 60_000
MAX_ATTEMPTS = 5

RETRYABLE_PATTERNS = (
    "econnrefused",
    "etimedout",
    "enotfound",
    "econnreset",
    "enetunreach",
    "connecttimeout",
    "connecterror",
    "connection reset",
    "connection refused",
    "operation timed out",
    "socket hang up",
    "network error",
    "fetch failed",
    "server unreachable",
    "503",
    "502",
    "504",
    "eai_again",
    "getaddrinfo",
)


def _ensure_config_dir() -> None:
    config_dir = CRON_RETRIES_FILE.parent
    config_dir.mkdir(parents=True, exist_ok=True)


def _load_retries() -> list[dict[str, Any]]:
    if not CRON_RETRIES_FILE.exists():
        return []
    try:
        parsed = json.loads(CRON_RETRIES_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _save_retries(retries: list[dict[str, Any]]) -> None:
    _ensure_config_dir()
    CRON_RETRIES_FILE.write_text(json.dumps(retries, indent=2), encoding="utf-8")
    try:
        CRON_RETRIES_FILE.chmod(0o600)
    except OSError:
        pass


def is_retryable_connection_error(error: Any) -> bool:
    msg = str(error).lower()
    return any(pattern in msg for pattern in RETRYABLE_PATTERNS)


def backoff_ms(attempt: int) -> int:
    exponent = max(0, attempt - 1)
    return min(BASE_DELAY_MS * (2**exponent), MAX_DELAY_MS)


def _format_wait_line(entry: dict[str, Any], now_ms: int) -> str:
    due_at = _parse_iso_ms(entry.get("next_retry_at"))
    wait_sec = max(0, int((due_at - now_ms + 999) // 1000)) if due_at is not None else 0
    attempt = entry.get("attempt", 1)
    max_attempts = entry.get("max_attempts", MAX_ATTEMPTS)
    trigger_id = entry.get("trigger_id", "unknown")
    return f"[WAIT] trigger {trigger_id} retry in {wait_sec}s (attempt {attempt}/{max_attempts})"


def _format_retry_line(entry: dict[str, Any]) -> str:
    attempt = entry.get("attempt", 1)
    max_attempts = entry.get("max_attempts", MAX_ATTEMPTS)
    trigger_id = entry.get("trigger_id", "unknown")
    command = entry.get("command", "")
    return f"[RETRY] trigger {trigger_id} attempt {attempt}/{max_attempts}: {command}"


def _format_failed_line(entry: dict[str, Any]) -> str:
    max_attempts = entry.get("max_attempts", MAX_ATTEMPTS)
    trigger_id = entry.get("trigger_id", "unknown")
    last_error = str(entry.get("last_error", ""))[:120]
    return f"[FAILED] trigger {trigger_id} after {max_attempts} attempts: {last_error}"


def _parse_iso_ms(value: Any) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def _status_payload(
    *,
    status: str,
    lines: list[str],
    pending_count: int,
    pending: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    formatted_line = lines[0] if len(lines) == 1 else " | ".join(lines)
    return {
        "status": status,
        "lines": lines,
        "formatted_line": formatted_line,
        "pending_count": pending_count,
        "pending_retries": pending or [],
        "retries_file": str(CRON_RETRIES_FILE),
        "retries_file_exists": CRON_RETRIES_FILE.exists(),
        "checked_at": datetime.now(tz=timezone.utc).isoformat(),
    }


def get_cron_retry_status() -> dict[str, Any]:
    """Read pending cron retries without executing them."""
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    retries = _load_retries()
    if not retries:
        return _status_payload(
            status="ok",
            lines=["[OK] No pending cron retries"],
            pending_count=0,
        )

    lines: list[str] = []
    due_count = 0
    for entry in retries:
        due_at = _parse_iso_ms(entry.get("next_retry_at"))
        if due_at is not None and due_at <= now_ms:
            due_count += 1
            lines.append(_format_retry_line(entry))
        else:
            lines.append(_format_wait_line(entry, now_ms))

    status = "retry" if due_count else "wait"
    return _status_payload(
        status=status,
        lines=lines,
        pending_count=len(retries),
        pending=retries,
    )


def _load_trigger(trigger_id: str) -> dict[str, Any] | None:
    if not TRIGGERS_FILE.exists():
        return None
    try:
        triggers = json.loads(TRIGGERS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(triggers, list):
        return None
    for trigger in triggers:
        if isinstance(trigger, dict) and trigger.get("id") == trigger_id:
            return trigger
    return None


def _cli_candidates() -> list[list[str]]:
    candidates: list[list[str]] = []
    algochains = shutil.which("algochains")
    if algochains:
        candidates.append([algochains])

    node = shutil.which("node")
    repo_root = Path(__file__).resolve().parents[2]
    cli_dist = repo_root / "src" / "cli" / "dist" / "index.js"
    if node and cli_dist.exists():
        candidates.append([node, str(cli_dist)])

    return candidates


def _execute_trigger_by_id(trigger_id: str) -> None:
    trigger = _load_trigger(trigger_id)
    if trigger is None:
        raise RuntimeError(f"Trigger not found: {trigger_id}")

    command = str(trigger.get("command", "")).strip()
    if not command:
        raise RuntimeError(f"Trigger {trigger_id} has empty command")

    last_error: Exception | None = None
    for prefix in _cli_candidates():
        try:
            proc = subprocess.run(
                [*prefix, *command.split()],
                capture_output=True,
                text=True,
                timeout=120,
                env=os.environ.copy(),
            )
            if proc.returncode == 0:
                return
            last_error = RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}")
        except (OSError, subprocess.TimeoutExpired) as exc:
            last_error = exc

    raise last_error or RuntimeError(
        f"Unable to execute trigger {trigger_id}; install algochains CLI or configure triggers.json"
    )


def _remove_trigger_retry(trigger_id: str) -> None:
    remaining = [entry for entry in _load_retries() if entry.get("trigger_id") != trigger_id]
    _save_retries(remaining)


def run_cron_retries(
    execute: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    """Process due cron retries with exponential backoff."""
    executor = execute or (lambda trigger_id, _command: _execute_trigger_by_id(trigger_id))
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    retries = _load_retries()
    if not retries:
        return _status_payload(
            status="ok",
            lines=["[OK] No pending cron retries"],
            pending_count=0,
        )

    lines: list[str] = []
    saw_retry = False
    saw_failed = False
    remaining: list[dict[str, Any]] = []

    for entry in retries:
        due_at = _parse_iso_ms(entry.get("next_retry_at"))
        if due_at is None or due_at > now_ms:
            lines.append(_format_wait_line(entry, now_ms))
            remaining.append(entry)
            continue

        lines.append(_format_retry_line(entry))
        saw_retry = True
        trigger_id = str(entry.get("trigger_id", ""))
        command = str(entry.get("command", ""))

        try:
            executor(trigger_id, command)
            continue
        except Exception as error:
            if not is_retryable_connection_error(error):
                lines.append(_format_failed_line({**entry, "last_error": str(error)}))
                saw_failed = True
                continue

            next_attempt = int(entry.get("attempt", 1)) + 1
            max_attempts = int(entry.get("max_attempts", MAX_ATTEMPTS))
            if next_attempt > max_attempts:
                lines.append(_format_failed_line({**entry, "last_error": str(error)}))
                saw_failed = True
                continue

            requeued = {
                **entry,
                "attempt": next_attempt,
                "next_retry_at": datetime.fromtimestamp(
                    (now_ms + backoff_ms(next_attempt)) / 1000,
                    tz=timezone.utc,
                ).isoformat(),
                "last_error": str(error)[:500],
            }
            lines.append(_format_wait_line(requeued, now_ms))
            remaining.append(requeued)

    _save_retries(remaining)

    if saw_failed:
        status = "failed"
    elif remaining:
        status = "wait" if not saw_retry else "wait"
    elif saw_retry:
        status = "retry"
    else:
        status = "ok"

    return _status_payload(
        status=status,
        lines=lines,
        pending_count=len(remaining),
        pending=remaining,
    )


def run_cron_retries_via_cli() -> dict[str, Any] | None:
    """Delegate to `algochains trigger retry --json` when the CLI is available."""
    for prefix in _cli_candidates():
        try:
            proc = subprocess.run(
                [*prefix, "trigger", "retry", "--json"],
                capture_output=True,
                text=True,
                timeout=120,
                env=os.environ.copy(),
            )
            if proc.stdout.strip():
                parsed = json.loads(proc.stdout)
                if isinstance(parsed, dict):
                    lines = parsed.get("lines") or []
                    if lines and "formatted_line" not in parsed:
                        parsed["formatted_line"] = (
                            lines[0] if len(lines) == 1 else " | ".join(lines)
                        )
                    parsed.setdefault("retries_file", str(CRON_RETRIES_FILE))
                    parsed.setdefault("retries_file_exists", CRON_RETRIES_FILE.exists())
                    parsed.setdefault("checked_at", datetime.now(tz=timezone.utc).isoformat())
                    return parsed
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            continue
    return None
