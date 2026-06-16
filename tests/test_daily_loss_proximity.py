import pytest

from algochains_mcp.daily_loss_proximity import evaluate_daily_loss_proximity
from algochains_mcp.security.guardrail import run_guardrail
from algochains_mcp import trading_guardrails as tg


@pytest.fixture
def isolated_guardrails(tmp_path, monkeypatch):
    state_path = tmp_path / "guardrails_state.json"
    monkeypatch.setattr(tg, "_STATE_PATH", state_path)
    tg.TradingGuardrails._instance = None
    tg._guardrails = None
    yield
    tg.TradingGuardrails._instance = None
    tg._guardrails = None


def test_daily_loss_alerts_at_eighty_percent_without_blocking():
    decision = evaluate_daily_loss_proximity(-400.0, 500.0, symbol="MNQ")

    assert decision.approved is True
    assert decision.warning is True
    assert decision.level == "alert"
    assert "alert threshold 80%" in decision.reason


def test_run_guardrail_blocks_default_scalper_entries_at_ninety_five_percent():
    result = run_guardrail(
        symbol="MNQ",
        side="BUY",
        daily_pnl=-475.0,
        gates=["daily_loss"],
    )

    assert result["approved"] is False
    assert result["gates_failed"] == 1
    assert "new scalper entries blocked at 95%" in result["reason"]


def test_run_guardrail_exempts_mnq_swing_below_hard_limit():
    result = run_guardrail(
        symbol="MNQ",
        side="BUY",
        daily_pnl=-475.0,
        strategy_type="swing",
        bot_name="MNQ_EMA_Swing",
        gates=["daily_loss"],
    )

    assert result["approved"] is True
    assert result["gates_failed"] == 0
    assert "MNQ swing exempt" in result["gate_results"][0]["reason"]


def test_order_guardrail_blocks_scalper_proximity_without_persisting_breaker(
    isolated_guardrails,
):
    guardrails = tg.TradingGuardrails()

    with pytest.raises(tg.GuardrailTripped, match="new scalper entries blocked at 95%"):
        guardrails.check_all(
            broker="tradovate",
            symbol="MNQ",
            qty_contracts=1,
            current_daily_pnl=-475.0,
            strategy_type="scalper",
        )

    status = guardrails.get_status()
    assert status["all_clear"] is True
    assert status["broker_circuit_breakers"]["tradovate"]["state"] == "CLOSED"


def test_order_guardrail_allows_mnq_swing_proximity_but_blocks_hard_limit(
    isolated_guardrails,
):
    guardrails = tg.TradingGuardrails()

    guardrails.check_all(
        broker="tradovate",
        symbol="MNQ",
        qty_contracts=1,
        current_daily_pnl=-475.0,
        strategy_type="swing",
        bot_name="MNQ_EMA_Swing",
    )

    with pytest.raises(tg.GuardrailTripped, match="Daily loss limit reached"):
        guardrails.check_all(
            broker="tradovate",
            symbol="MNQ",
            qty_contracts=1,
            current_daily_pnl=-500.0,
            strategy_type="swing",
            bot_name="MNQ_EMA_Swing",
        )

    status = guardrails.get_status()
    assert status["all_clear"] is False
    assert status["broker_circuit_breakers"]["tradovate"]["trip_reason"] == "daily_loss"
