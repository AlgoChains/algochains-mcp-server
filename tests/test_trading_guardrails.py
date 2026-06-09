import json

import pytest

from algochains_mcp import trading_guardrails as tg


@pytest.fixture
def isolated_guardrails(tmp_path, monkeypatch):
    state_path = tmp_path / "guardrails_state.json"
    monkeypatch.setattr(tg, "_STATE_PATH", state_path)
    tg.TradingGuardrails._instance = None
    tg._guardrails = None
    yield state_path
    tg.TradingGuardrails._instance = None
    tg._guardrails = None


def test_expired_ai_loop_breaker_is_cleared_after_restart(isolated_guardrails, monkeypatch):
    state_path = isolated_guardrails
    now_epoch = 1_781_000_000.0
    state_path.write_text(
        json.dumps(
            {
                "tradovate": {
                    "state": "OPEN",
                    "tripped_at": 123.0,
                    "tripped_at_epoch": now_epoch - 600,
                    "expires_at_epoch": now_epoch - 300,
                    "trip_reason": "ai_loop_detected",
                    "trip_message": "Tool call rate exceeded",
                    "cooldown_sec": 300,
                    "trip_count_today": 137,
                }
            }
        )
    )
    monkeypatch.setattr(tg.time, "monotonic", lambda: 1.0)
    monkeypatch.setattr(tg.time, "time", lambda: now_epoch)

    guardrails = tg.TradingGuardrails()
    status = guardrails.get_status()

    assert status["all_clear"] is True
    assert status["broker_circuit_breakers"] == {}
    assert json.loads(state_path.read_text()) == {}


def test_unexpired_ai_loop_breaker_survives_restart(isolated_guardrails, monkeypatch):
    state_path = isolated_guardrails
    now_epoch = 1_781_000_000.0
    state_path.write_text(
        json.dumps(
            {
                "alpaca": {
                    "state": "OPEN",
                    "tripped_at": 123.0,
                    "tripped_at_epoch": now_epoch - 60,
                    "expires_at_epoch": now_epoch + 240,
                    "trip_reason": "ai_loop_detected",
                    "trip_message": "Tool call rate exceeded",
                    "cooldown_sec": 300,
                    "trip_count_today": 2,
                }
            }
        )
    )
    monkeypatch.setattr(tg.time, "monotonic", lambda: 1.0)
    monkeypatch.setattr(tg.time, "time", lambda: now_epoch)

    guardrails = tg.TradingGuardrails()
    status = guardrails.get_status()

    assert status["all_clear"] is False
    assert status["broker_circuit_breakers"]["alpaca"]["state"] == "OPEN"
    assert status["broker_circuit_breakers"]["alpaca"]["cooldown_remaining_sec"] == 240
    assert status["broker_circuit_breakers"]["alpaca"]["expires_at_epoch"] == now_epoch + 240


@pytest.mark.parametrize(
    "reason",
    [
        "daily_loss",
        "drawdown",
        "consecutive_losses",
        "manual_trip",
        "state_file_corrupt",
    ],
)
def test_hard_safety_breakers_do_not_auto_clear_on_expiry(
    isolated_guardrails, monkeypatch, reason
):
    state_path = isolated_guardrails
    now_epoch = 1_781_000_000.0
    state_path.write_text(
        json.dumps(
            {
                "tradovate": {
                    "state": "OPEN",
                    "tripped_at": 123.0,
                    "tripped_at_epoch": now_epoch - 600,
                    "expires_at_epoch": now_epoch - 300,
                    "trip_reason": reason,
                    "trip_message": f"{reason} breaker",
                    "cooldown_sec": 300,
                    "trip_count_today": 1,
                }
            }
        )
    )
    monkeypatch.setattr(tg.time, "monotonic", lambda: 1.0)
    monkeypatch.setattr(tg.time, "time", lambda: now_epoch)

    guardrails = tg.TradingGuardrails()
    status = guardrails.get_status()

    assert status["all_clear"] is False
    assert status["broker_circuit_breakers"]["tradovate"]["state"] == "OPEN"
    assert status["broker_circuit_breakers"]["tradovate"]["trip_reason"] == reason


def test_half_open_breaker_allows_test_call_after_restart(
    isolated_guardrails, monkeypatch
):
    """A persisted HALF_OPEN breaker must not deadlock after restart.

    Before the fix, half_open_test_allowed was not persisted and restored as
    False — _check_cb_state then raised "waiting for test call result" forever,
    so the one test order that could close the breaker could never be placed.
    """
    state_path = isolated_guardrails
    now_epoch = 1_781_000_000.0
    state_path.write_text(
        json.dumps(
            {
                "tradovate": {
                    "state": "HALF_OPEN",
                    "tripped_at": 123.0,
                    "tripped_at_epoch": now_epoch - 600,
                    "expires_at_epoch": now_epoch - 300,
                    "trip_reason": "daily_loss",
                    "trip_message": "Daily P&L breached",
                    "cooldown_sec": 300,
                    "trip_count_today": 1,
                }
            }
        )
    )
    monkeypatch.setattr(tg.time, "monotonic", lambda: 1.0)
    monkeypatch.setattr(tg.time, "time", lambda: now_epoch)

    guardrails = tg.TradingGuardrails()

    # The one HALF_OPEN test call must be allowed (no GuardrailTripped)
    guardrails._check_cb_state("tradovate")

    # ...and a confirmed fill closes the breaker
    guardrails.record_order_success("tradovate")
    status = guardrails.get_status()
    assert status["broker_circuit_breakers"]["tradovate"]["state"] == "CLOSED"
    assert status["all_clear"] is True

    # Round-trip: the flag is now persisted explicitly
    persisted = json.loads(state_path.read_text())
    assert persisted["tradovate"]["half_open_test_allowed"] is False


def test_legacy_monotonic_ai_loop_state_is_dropped_on_restart(
    isolated_guardrails, monkeypatch
):
    state_path = isolated_guardrails
    state_path.write_text(
        json.dumps(
            {
                "oanda": {
                    "state": "OPEN",
                    "tripped_at": 985969.060761708,
                    "trip_reason": "ai_loop_detected",
                    "trip_message": "Tool call rate exceeded",
                    "cooldown_sec": 300,
                    "trip_count_today": 137,
                }
            }
        )
    )
    monkeypatch.setattr(tg.time, "monotonic", lambda: 1.0)
    monkeypatch.setattr(tg.time, "time", lambda: 1_781_000_000.0)

    guardrails = tg.TradingGuardrails()
    status = guardrails.get_status()

    assert status["all_clear"] is True
    assert status["broker_circuit_breakers"] == {}
    assert json.loads(state_path.read_text()) == {}
