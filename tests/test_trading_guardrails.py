import json
from types import SimpleNamespace

import pytest

from algochains_mcp.brokers.base import AccountInfo, Order, OrderSide, OrderStatus, OrderType, Quote
from algochains_mcp import trading_guardrails as tg
from algochains_mcp.server import _compute_consecutive_losses_from_fills


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


def test_latest_winning_fill_is_authoritative_zero_loss_streak():
    """Fresh broker fills must not be overwritten by stale signal_health state."""
    losses, authoritative = _compute_consecutive_losses_from_fills(
        [
            SimpleNamespace(realized_pnl=-12.50),
            SimpleNamespace(realized_pnl=8.25),
        ]
    )

    assert losses == 0
    assert authoritative is True


def test_latest_breakeven_fill_is_not_treated_as_missing_pnl():
    losses, authoritative = _compute_consecutive_losses_from_fills(
        [
            SimpleNamespace(realized_pnl=-12.50),
            SimpleNamespace(realized_pnl=0.0),
        ]
    )

    assert losses == 0
    assert authoritative is True


def test_unparseable_latest_fill_allows_reconciliation_fallback():
    losses, authoritative = _compute_consecutive_losses_from_fills(
        [
            SimpleNamespace(realized_pnl=-12.50),
            SimpleNamespace(realized_pnl=None),
        ]
    )

    assert losses == 0
    assert authoritative is False


def test_place_order_uses_fresh_broker_winner_over_stale_signal_health(
    isolated_guardrails, monkeypatch, tmp_path
):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "signal_health.json").write_text(
        json.dumps({"MNQ_Upgraded_Scalper": {"consecutive_losses": tg.MAX_CONSECUTIVE_LOSSES}})
    )
    monkeypatch.setenv("ALGOCHAINS_CONTROL_TOWER", str(tmp_path))
    monkeypatch.setenv("ALGOCHAINS_REQUIRE_CONFIRMATION", "0")

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url):
            return SimpleNamespace(text="DATE,OPEN,HIGH,LOW,CLOSE\n2026-06-12,0,0,0,12.5\n")

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    class FakeBroker:
        async def get_fills(self):
            return [
                SimpleNamespace(realized_pnl=-12.50),
                SimpleNamespace(realized_pnl=8.25),
            ]

        async def get_quote(self, symbol):
            return Quote(symbol=symbol, bid=100.0, ask=101.0, last=100.5)

        async def get_account(self):
            return AccountInfo(
                broker="tradovate",
                account_id="test",
                equity=100_000.0,
                cash=100_000.0,
                buying_power=100_000.0,
            )

        async def place_order(
            self,
            symbol,
            side,
            qty,
            order_type=OrderType.MARKET,
            limit_price=None,
            stop_price=None,
            trail_pct=None,
            time_in_force="day",
        ):
            return Order(
                id="ok-1",
                broker="tradovate",
                symbol=symbol,
                side=side,
                order_type=order_type,
                qty=qty,
                status=OrderStatus.ACCEPTED,
            )

    class FakeRegistry:
        def get(self, name):
            return FakeBroker() if name == "tradovate" else None

    import asyncio
    import algochains_mcp.server as srv

    result = asyncio.run(
        srv._dispatch_tool(
            "place_order",
            {
                "broker": "tradovate",
                "symbol": "MNQ",
                "side": OrderSide.BUY.value,
                "qty": 1,
                "order_type": OrderType.MARKET.value,
            },
            FakeRegistry(),
        )
    )
    payload = json.loads(result[0].text)

    assert payload["id"] == "ok-1"
    assert payload["status"] == OrderStatus.ACCEPTED.value


def test_place_order_fails_closed_when_live_quote_unavailable(
    isolated_guardrails, monkeypatch, tmp_path
):
    monkeypatch.setenv("ALGOCHAINS_CONTROL_TOWER", str(tmp_path))
    monkeypatch.setenv("ALGOCHAINS_REQUIRE_CONFIRMATION", "0")

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url):
            return SimpleNamespace(text="DATE,OPEN,HIGH,LOW,CLOSE\n2026-06-12,0,0,0,12.5\n")

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    class FakeBroker:
        def __init__(self):
            self.place_order_called = False

        async def get_fills(self):
            return []

        async def get_quote(self, symbol):
            raise RuntimeError("REST price fetch failed")

        async def get_account(self):
            return AccountInfo(
                broker="tradovate",
                account_id="test",
                equity=100_000.0,
                cash=100_000.0,
                buying_power=100_000.0,
            )

        async def place_order(
            self,
            symbol,
            side,
            qty,
            order_type=OrderType.MARKET,
            limit_price=None,
            stop_price=None,
            trail_pct=None,
            time_in_force="day",
        ):
            self.place_order_called = True
            return Order(
                id="should-not-submit",
                broker="tradovate",
                symbol=symbol,
                side=side,
                order_type=order_type,
                qty=qty,
                status=OrderStatus.ACCEPTED,
            )

    fake_broker = FakeBroker()

    class FakeRegistry:
        def get(self, name):
            return fake_broker if name == "tradovate" else None

    import asyncio
    import algochains_mcp.server as srv

    result = asyncio.run(
        srv._dispatch_tool(
            "place_order",
            {
                "broker": "tradovate",
                "symbol": "MNQ",
                "side": OrderSide.BUY.value,
                "qty": 1,
                "order_type": OrderType.MARKET.value,
                "estimated_notional": 60_000,
            },
            FakeRegistry(),
        )
    )
    payload = json.loads(result[0].text)

    assert fake_broker.place_order_called is False
    assert payload["error_type"] == "GuardrailTripped"
    assert payload["reason"] == tg.GuardrailReason.MARKET_PRICE_UNAVAILABLE.value
    assert payload["order_submitted"] is False
    assert "No live market price" in payload["message"]


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


def test_half_open_breaker_allows_only_one_test_call(
    isolated_guardrails, monkeypatch
):
    """HALF_OPEN is a single-probe state, not an unlimited recovery window."""
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
                    "trip_reason": "order_velocity",
                    "trip_message": "Order velocity breached",
                    "cooldown_sec": 300,
                    "half_open_test_allowed": True,
                    "trip_count_today": 1,
                }
            }
        )
    )
    monkeypatch.setattr(tg.time, "monotonic", lambda: 1.0)
    monkeypatch.setattr(tg.time, "time", lambda: now_epoch)

    guardrails = tg.TradingGuardrails()

    guardrails._check_cb_state("tradovate")
    persisted = json.loads(state_path.read_text())
    assert persisted["tradovate"]["half_open_test_allowed"] is False

    with pytest.raises(tg.GuardrailTripped, match="waiting for test call result"):
        guardrails._check_cb_state("tradovate")


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
