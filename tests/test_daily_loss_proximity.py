from algochains_mcp.daily_loss_proximity import evaluate_daily_loss_proximity
from algochains_mcp.security.guardrail import run_guardrail


def test_zero_daily_pnl_is_zero_percent_loss():
    proximity = evaluate_daily_loss_proximity(0.0, 500.0, symbol="MNQ")

    assert proximity.loss_usd == 0.0
    assert proximity.loss_fraction == 0.0
    assert proximity.alert is False
    assert proximity.block_scalper_entry is False
    assert "0%" in proximity.message
    assert "-0%" not in proximity.message


def test_daily_loss_alerts_at_eighty_percent_without_blocking():
    proximity = evaluate_daily_loss_proximity(-400.0, 500.0, symbol="MNQ")

    assert proximity.alert is True
    assert proximity.block_scalper_entry is False
    assert "alert 80%" in proximity.message


def test_daily_loss_blocks_unlabeled_scalper_entries_at_ninety_five_percent():
    proximity = evaluate_daily_loss_proximity(-475.0, 500.0, symbol="MNQ")

    assert proximity.alert is True
    assert proximity.block_scalper_entry is True
    assert "new scalper entries blocked" in proximity.message


def test_mnq_swing_is_exempt_from_ninety_five_percent_scalper_block():
    proximity = evaluate_daily_loss_proximity(
        -475.0,
        500.0,
        symbol="MNQ",
        strategy_type="swing",
        bot_name="MNQ_EMA_Swing",
    )

    assert proximity.alert is True
    assert proximity.mnq_swing_exempt is True
    assert proximity.block_scalper_entry is False
    assert "MNQ swing exempt" in proximity.message


def test_run_guardrail_daily_loss_gate_blocks_default_entry_at_ninety_five_percent():
    result = run_guardrail(
        symbol="MNQ",
        side="BUY",
        daily_pnl=-475.0,
        gates=["daily_loss"],
    )

    assert result["approved"] is False
    assert result["gates_failed"] == 1
    assert "new scalper entries blocked" in result["reason"]


def test_run_guardrail_daily_loss_gate_allows_mnq_swing_exemption():
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
    assert result["gate_results"][0]["passed"] is True
    assert "MNQ swing exempt" in result["gate_results"][0]["reason"]

