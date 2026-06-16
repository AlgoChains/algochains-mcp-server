from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from algochains_mcp import cron_retry as module


@pytest.fixture(autouse=True)
def isolated_algochains_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(module, "CRON_RETRIES_FILE", home / ".algochains" / "cron_retries.json")
    monkeypatch.setattr(module, "TRIGGERS_FILE", home / ".algochains" / "triggers.json")
    yield


def _write_retries(entries: list[dict]) -> None:
    path = module.CRON_RETRIES_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def test_is_retryable_connection_error_detects_transient_failures():
    assert module.is_retryable_connection_error("connect ECONNREFUSED 127.0.0.1:8090")
    assert module.is_retryable_connection_error("ConnectTimeout: [Errno 60] Operation timed out")
    assert module.is_retryable_connection_error("HTTP 503 Service Unavailable")
    assert not module.is_retryable_connection_error("invalid strategy config")


def test_backoff_ms_doubles_up_to_cap():
    assert module.backoff_ms(1) == 1_000
    assert module.backoff_ms(2) == 2_000
    assert module.backoff_ms(3) == 4_000
    assert module.backoff_ms(7) == 60_000


def test_get_cron_retry_status_reports_empty_queue():
    result = module.get_cron_retry_status()

    assert result["status"] == "ok"
    assert result["formatted_line"] == "[OK] No pending cron retries"
    assert result["pending_count"] == 0


def test_get_cron_retry_status_reports_wait_for_future_retries():
    future = datetime.fromtimestamp(
        datetime.now(tz=timezone.utc).timestamp() + 3600,
        tz=timezone.utc,
    ).isoformat()
    _write_retries(
        [
            {
                "trigger_id": "abc12345",
                "command": "detect-market-regime --json",
                "attempt": 1,
                "max_attempts": 5,
                "next_retry_at": future,
                "last_error": "connect ECONNREFUSED",
                "enqueued_at": future,
            }
        ]
    )

    result = module.get_cron_retry_status()

    assert result["status"] == "wait"
    assert result["pending_count"] == 1
    assert result["formatted_line"].startswith("[WAIT] trigger abc12345 retry in")


def test_run_cron_retries_executes_due_entries():
    past = datetime.fromtimestamp(0, tz=timezone.utc).isoformat()
    _write_retries(
        [
            {
                "trigger_id": "due12345",
                "command": "detect-market-regime --json",
                "attempt": 2,
                "max_attempts": 5,
                "next_retry_at": past,
                "last_error": "connect ECONNREFUSED",
                "enqueued_at": past,
            }
        ]
    )

    calls: list[tuple[str, str]] = []

    def execute(trigger_id: str, command: str) -> None:
        calls.append((trigger_id, command))

    result = module.run_cron_retries(execute=execute)

    assert result["status"] == "retry"
    assert calls == [("due12345", "detect-market-regime --json")]
    assert module._load_retries() == []
    assert result["lines"][0].startswith("[RETRY] trigger due12345 attempt 2/5:")


def test_run_cron_retries_requeues_retryable_failures():
    past = datetime.fromtimestamp(0, tz=timezone.utc).isoformat()
    _write_retries(
        [
            {
                "trigger_id": "fail1234",
                "command": "get-bot-health --json",
                "attempt": 1,
                "max_attempts": 5,
                "next_retry_at": past,
                "last_error": "connect ECONNREFUSED",
                "enqueued_at": past,
            }
        ]
    )

    def execute(_trigger_id: str, _command: str) -> None:
        raise RuntimeError("connect ECONNREFUSED 127.0.0.1:8090")

    result = module.run_cron_retries(execute=execute)

    assert result["status"] == "wait"
    assert result["pending_count"] == 1
    assert module._load_retries()[0]["attempt"] == 2
    assert result["lines"][0].startswith("[RETRY] trigger fail1234 attempt 1/5:")
    assert result["lines"][1].startswith("[WAIT] trigger fail1234 retry in")


def test_run_cron_retries_drops_exhausted_failures():
    past = datetime.fromtimestamp(0, tz=timezone.utc).isoformat()
    _write_retries(
        [
            {
                "trigger_id": "deadbeef",
                "command": "get-bot-health --json",
                "attempt": 5,
                "max_attempts": 5,
                "next_retry_at": past,
                "last_error": "connect ECONNREFUSED",
                "enqueued_at": past,
            }
        ]
    )

    def execute(_trigger_id: str, _command: str) -> None:
        raise RuntimeError("connect ECONNREFUSED 127.0.0.1:8090")

    result = module.run_cron_retries(execute=execute)

    assert result["status"] == "failed"
    assert module._load_retries() == []
    assert result["lines"][1].startswith("[FAILED] trigger deadbeef after 5 attempts:")
