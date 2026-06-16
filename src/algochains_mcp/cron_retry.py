"""Cron trigger retry queue for CRON-RETRY watchdog triage.

Mirrors the AlgoChains CLI ``trigger retry`` handler in ``src/cli/src/triggers/retry.ts``.
Pending retries live in ``~/.algochains/cron_retries.json``; trigger definitions in
``~/.algochains/triggers.json``.
"""
from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

CONFIG_DIR = Path.home() / ".algochains"
CRON_RETRIES_FILE = CONFIG_DIR / "cron_retries.json"
TRIGGERS_FILE = CONFIG_DIR / "triggers.json"

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


@dataclass(frozen=True)
class PendingRetry:
    trigger_id: str
    command: str
    attempt: int
    max_attempts: int
    next_retry_at: str
    last_error: str
    enqueued_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "trigger_id": self.trigger_id,
            "command": self.command,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "next_retry_at": self.next_retry_at,
            "last_error": self.last_error,
            "enqueued_at": self.enqueued_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> PendingRetry:
        return cls(
            trigger_id=str(payload["trigger_id"]),
            command=str(payload["command"]),
            attempt=int(payload.get("attempt", 1)),
            max_attempts=int(payload.get("max_attempts", MAX_ATTEMPTS)),
            next_retry_at=str(payload["next_retry_at"]),
            last_error=str(payload.get("last_error", "")),
            enqueued_at=str(payload.get("enqueued_at", "")),
        )


ExecuteFn = Callable[[str, str], None]


def _ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def _load_retries(retries_file: Path | None = None) -> list[PendingRetry]:
    path = retries_file or CRON_RETRIES_FILE
    if not path.exists():
        return []
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    retries: list[PendingRetry] = []
    for item in parsed:
        if isinstance(item, dict) and item.get("trigger_id"):
            retries.append(PendingRetry.from_dict(item))
    return retries


def _save_retries(retries: list[PendingRetry], retries_file: Path | None = None) -> None:
    path = retries_file or CRON_RETRIES_FILE
    _ensure_config_dir()
    path.write_text(
        json.dumps([entry.to_dict() for entry in retries], indent=2),
        encoding="utf-8",
    )
    try:
        path.chmod(0o600)
    except OSError:
        pass


def is_retryable_connection_error(error: object) -> bool:
    message = str(error).lower()
    return any(pattern in message for pattern in RETRYABLE_PATTERNS)


def backoff_ms(attempt: int) -> int:
    exponent = max(0, attempt - 1)
    return min(BASE_DELAY_MS * (2**exponent), MAX_DELAY_MS)


def list_pending_retries(retries_file: Path | None = None) -> list[PendingRetry]:
    return _load_retries(retries_file)


def enqueue_trigger_retry(
    trigger_id: str,
    command: str,
    error: object,
    *,
    attempt: int = 1,
    retries_file: Path | None = None,
) -> PendingRetry:
    retries = [entry for entry in _load_retries(retries_file) if entry.trigger_id != trigger_id]
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    entry = PendingRetry(
        trigger_id=trigger_id,
        command=command,
        attempt=attempt,
        max_attempts=MAX_ATTEMPTS,
        next_retry_at=datetime.fromtimestamp(
            (now_ms + backoff_ms(attempt)) / 1000,
            tz=timezone.utc,
        ).isoformat(),
        last_error=str(error)[:500],
        enqueued_at=datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).isoformat(),
    )
    retries.append(entry)
    _save_retries(retries, retries_file)
    return entry


def remove_trigger_retry(trigger_id: str, retries_file: Path | None = None) -> None:
    retries = [entry for entry in _load_retries(retries_file) if entry.trigger_id != trigger_id]
    _save_retries(retries, retries_file)


def _format_wait_line(entry: PendingRetry, now_ms: int) -> str:
    due_ms = int(datetime.fromisoformat(entry.next_retry_at.replace("Z", "+00:00")).timestamp() * 1000)
    wait_sec = max(0, (due_ms - now_ms + 999) // 1000)
    return (
        f"[WAIT] trigger {entry.trigger_id} retry in {wait_sec}s "
        f"(attempt {entry.attempt}/{entry.max_attempts})"
    )


def _format_retry_line(entry: PendingRetry) -> str:
    return (
        f"[RETRY] trigger {entry.trigger_id} attempt {entry.attempt}/"
        f"{entry.max_attempts}: {entry.command}"
    )


def _format_failed_line(entry: PendingRetry) -> str:
    return (
        f"[FAILED] trigger {entry.trigger_id} after {entry.max_attempts} attempts: "
        f"{entry.last_error[:120]}"
    )


def get_cron_retry_status(*, retries_file: Path | None = None, now_ms: int | None = None) -> dict[str, Any]:
    """Read-only snapshot of the cron retry queue for watchdog triage."""
    current_ms = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
    retries = _load_retries(retries_file)
    lines: list[str] = []

    if not retries:
        lines = ["[OK] No pending cron retries"]
        status = "ok"
    else:
        for entry in retries:
            due_ms = int(
                datetime.fromisoformat(entry.next_retry_at.replace("Z", "+00:00")).timestamp() * 1000
            )
            if due_ms > current_ms:
                lines.append(_format_wait_line(entry, current_ms))
            else:
                lines.append(_format_retry_line(entry))
        status = "wait" if any(
            int(datetime.fromisoformat(entry.next_retry_at.replace("Z", "+00:00")).timestamp() * 1000)
            > current_ms
            for entry in retries
        ) else "retry"

    return {
        "component": "cron-retry",
        "status": status,
        "pending_count": len(retries),
        "lines": lines,
        "formatted_line": lines[0] if len(lines) == 1 else "\n".join(lines),
        "retries": [entry.to_dict() for entry in retries],
        "retries_file": str(retries_file or CRON_RETRIES_FILE),
    }


def _default_execute_trigger(trigger_id: str, command: str) -> None:
    trigger = _load_trigger(trigger_id)
    if trigger is None:
        raise RuntimeError(f"Trigger not found: {trigger_id}")

    algochains = shutil.which("algochains")
    if not algochains:
        raise RuntimeError("algochains CLI not found on PATH — cannot execute trigger retry")

    argv = [algochains, *shlex.split(command)]
    try:
        subprocess.run(
            argv,
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(detail or str(exc)) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"trigger execution timed out after 120s: {trigger_id}") from exc


def _load_trigger(trigger_id: str, triggers_file: Path | None = None) -> dict[str, Any] | None:
    path = triggers_file or TRIGGERS_FILE
    if not path.exists():
        return None
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, list):
        return None
    for item in parsed:
        if isinstance(item, dict) and item.get("id") == trigger_id:
            return item
    return None


def run_cron_retries(
    execute: ExecuteFn | None = None,
    *,
    retries_file: Path | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Process due cron retries with exponential backoff; emit explicit watchdog lines."""
    current_ms = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
    retries = _load_retries(retries_file)
    lines: list[str] = []

    if not retries:
        payload = {
            "component": "cron-retry",
            "status": "ok",
            "lines": ["[OK] No pending cron retries"],
            "formatted_line": "[OK] No pending cron retries",
            "pending_count": 0,
            "retries": [],
            "retries_file": str(retries_file or CRON_RETRIES_FILE),
        }
        return payload

    runner = execute or _default_execute_trigger
    saw_retry = False
    saw_failed = False
    remaining: list[PendingRetry] = []

    for entry in retries:
        due_ms = int(
            datetime.fromisoformat(entry.next_retry_at.replace("Z", "+00:00")).timestamp() * 1000
        )
        if due_ms > current_ms:
            lines.append(_format_wait_line(entry, current_ms))
            remaining.append(entry)
            continue

        lines.append(_format_retry_line(entry))
        saw_retry = True

        try:
            runner(entry.trigger_id, entry.command)
            remove_trigger_retry(entry.trigger_id, retries_file)
            continue
        except Exception as error:
            next_attempt = entry.attempt + 1
            if next_attempt > entry.max_attempts or not is_retryable_connection_error(error):
                lines.append(_format_failed_line(PendingRetry(
                    trigger_id=entry.trigger_id,
                    command=entry.command,
                    attempt=entry.attempt,
                    max_attempts=entry.max_attempts,
                    next_retry_at=entry.next_retry_at,
                    last_error=str(error),
                    enqueued_at=entry.enqueued_at,
                )))
                saw_failed = True
                continue

            requeued = PendingRetry(
                trigger_id=entry.trigger_id,
                command=entry.command,
                attempt=next_attempt,
                max_attempts=entry.max_attempts,
                next_retry_at=datetime.fromtimestamp(
                    (int(datetime.now(timezone.utc).timestamp() * 1000) + backoff_ms(next_attempt)) / 1000,
                    tz=timezone.utc,
                ).isoformat(),
                last_error=str(error)[:500],
                enqueued_at=entry.enqueued_at,
            )
            lines.append(_format_wait_line(requeued, int(datetime.now(timezone.utc).timestamp() * 1000)))
            remaining.append(requeued)

    _save_retries(remaining, retries_file)

    if saw_failed:
        status = "failed"
    elif remaining:
        status = "wait"
    elif saw_retry:
        status = "retry"
    else:
        status = "ok"

    formatted_line = lines[0] if len(lines) == 1 else "\n".join(lines)
    return {
        "component": "cron-retry",
        "status": status,
        "lines": lines,
        "formatted_line": formatted_line,
        "pending_count": len(remaining),
        "retries": [entry.to_dict() for entry in remaining],
        "retries_file": str(retries_file or CRON_RETRIES_FILE),
    }
