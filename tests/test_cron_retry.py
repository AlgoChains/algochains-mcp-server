from __future__ import annotations

import json
from datetime import datetime, timezone

from algochains_mcp.cron_retry import (
    CRON_RETRIES_FILE,
    backoff_ms,
    enqueue_trigger_retry,
    get_cron_retry_status,
    is_retryable_connection_error,
    list_pending_retries,
    run_cron_retries,
)


def _iso_in_past(seconds: int = 1) -> str:
    return datetime.fromtimestamp(
        datetime.now(timezone.utc).timestamp() - seconds,
        tz=timezone.utc,
    ).isoformat()


def test_is_retryable_connection_error_detects_transient_transport_failures():
    assert is_retryable_connection_error(RuntimeError("connect ECONNREFUSED 127.0.0.1:8090"))
    assert is_retryable_connection_error(RuntimeError("ConnectTimeout: [Errno 60] Operation timed out"))
    assert is_retryable_connection_error(RuntimeError("HTTP 503 Service Unavailable"))
    assert not is_retryable_connection_error(RuntimeError("invalid strategy config"))


def test_backoff_ms_doubles_up_to_cap():
    assert backoff_ms(1) == 1_000
    assert backoff_ms(2) == 2_000
    assert backoff_ms(3) == 4_000
    assert backoff_ms(7) == 60_000


def test_run_cron_retries_reports_healthy_empty_queue(tmp_path, monkeypatch):
    retries_file = tmp_path / "cron_retries.json"
    monkeypatch.setattr("algochains_mcp.cron_retry.CRON_RETRIES_FILE", retries_file)

    result = run_cron_retries(retries_file=retries_file)

    assert result["status"] == "ok"
    assert result["lines"] == ["[OK] No pending cron retries"]
    assert result["formatted_line"] == "[OK] No pending cron retries"
    assert result["pending_count"] == 0


def test_get_cron_retry_status_matches_empty_queue(tmp_path, monkeypatch):
    retries_file = tmp_path / "cron_retries.json"
    monkeypatch.setattr("algochains_mcp.cron_retry.CRON_RETRIES_FILE", retries_file)

    status = get_cron_retry_status(retries_file=retries_file)

    assert status["status"] == "ok"
    assert status["formatted_line"] == "[OK] No pending cron retries"


def test_run_cron_retries_waits_until_backoff_expires(tmp_path, monkeypatch):
    retries_file = tmp_path / "cron_retries.json"
    monkeypatch.setattr("algochains_mcp.cron_retry.CRON_RETRIES_FILE", retries_file)

    enqueue_trigger_retry(
        "abc12345",
        "detect-market-regime --json",
        RuntimeError("connect ECONNREFUSED"),
        retries_file=retries_file,
    )

    result = run_cron_retries(
        lambda *_args: (_ for _ in ()).throw(RuntimeError("should not execute yet")),
        retries_file=retries_file,
    )

    assert result["status"] == "wait"
    assert result["pending_count"] == 1
    assert result["lines"][0].startswith("[WAIT] trigger abc12345 retry in ")
    assert "attempt 1/5" in result["lines"][0]


def test_run_cron_retries_executes_due_entries_and_clears_queue(tmp_path, monkeypatch):
    retries_file = tmp_path / "cron_retries.json"
    monkeypatch.setattr("algochains_mcp.cron_retry.CRON_RETRIES_FILE", retries_file)

    retries_file.write_text(
        json.dumps(
            [
                {
                    "trigger_id": "due12345",
                    "command": "detect-market-regime --json",
                    "attempt": 2,
                    "max_attempts": 5,
                    "next_retry_at": _iso_in_past(),
                    "last_error": "connect ECONNREFUSED",
                    "enqueued_at": _iso_in_past(),
                }
            ]
        ),
        encoding="utf-8",
    )

    calls: list[str] = []

    def execute(trigger_id: str, command: str) -> None:
        calls.append(f"{trigger_id}:{command}")

    result = run_cron_retries(execute, retries_file=retries_file)

    assert result["status"] == "retry"
    assert calls == ["due12345:detect-market-regime --json"]
    assert list_pending_retries(retries_file) == []
    assert result["lines"][0].startswith("[RETRY] trigger due12345 attempt 2/5:")


def test_run_cron_retries_requeues_retryable_failures(tmp_path, monkeypatch):
    retries_file = tmp_path / "cron_retries.json"
    monkeypatch.setattr("algochains_mcp.cron_retry.CRON_RETRIES_FILE", retries_file)

    retries_file.write_text(
        json.dumps(
            [
                {
                    "trigger_id": "fail1234",
                    "command": "get-bot-health --json",
                    "attempt": 1,
                    "max_attempts": 5,
                    "next_retry_at": _iso_in_past(),
                    "last_error": "connect ECONNREFUSED",
                    "enqueued_at": _iso_in_past(),
                }
            ]
        ),
        encoding="utf-8",
    )

    def execute(_trigger_id: str, _command: str) -> None:
        raise RuntimeError("connect ECONNREFUSED 127.0.0.1:8090")

    result = run_cron_retries(execute, retries_file=retries_file)

    assert result["status"] == "wait"
    assert result["pending_count"] == 1
    assert list_pending_retries(retries_file)[0].attempt == 2
    assert result["lines"][0].startswith("[RETRY] trigger fail1234 attempt 1/5:")
    assert result["lines"][1].startswith("[WAIT] trigger fail1234 retry in ")


def test_run_cron_retries_drops_exhausted_retryable_failures(tmp_path, monkeypatch):
    retries_file = tmp_path / "cron_retries.json"
    monkeypatch.setattr("algochains_mcp.cron_retry.CRON_RETRIES_FILE", retries_file)

    retries_file.write_text(
        json.dumps(
            [
                {
                    "trigger_id": "deadbeef",
                    "command": "get-bot-health --json",
                    "attempt": 5,
                    "max_attempts": 5,
                    "next_retry_at": _iso_in_past(),
                    "last_error": "connect ECONNREFUSED",
                    "enqueued_at": _iso_in_past(),
                }
            ]
        ),
        encoding="utf-8",
    )

    def execute(_trigger_id: str, _command: str) -> None:
        raise RuntimeError("connect ECONNREFUSED 127.0.0.1:8090")

    result = run_cron_retries(execute, retries_file=retries_file)

    assert result["status"] == "failed"
    assert list_pending_retries(retries_file) == []
    assert result["lines"][1].startswith("[FAILED] trigger deadbeef after 5 attempts:")


def test_cron_retry_state_file_default_path():
    assert CRON_RETRIES_FILE.name == "cron_retries.json"
    assert CRON_RETRIES_FILE.parent.name == ".algochains"
