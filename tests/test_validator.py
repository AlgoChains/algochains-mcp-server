"""Tests for the strategy validator gates."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from algochains_mcp.config import GatingConfig
from algochains_mcp.marketplace.validator import StrategyValidator


def test_valid_strategy_passes():
    v = StrategyValidator(GatingConfig(require_walk_forward=False))
    result = v.validate({
        "symbol": "AAPL",
        "strategy_type": "mean_reversion",
        "timeframe": "hour",
        "oos_sharpe": 2.1,
        "oos_trades": 100,
        "is_sharpe": 3.0,
        "max_drawdown_pct": 15.0,
        "win_rate": 55.0,
        "mcpt": {"p_value": 0.01, "permutations": 1000},
    })
    assert result.passed is True
    assert result.score >= 70
    assert result.tier in ("gold", "platinum")


def test_low_sharpe_rejected():
    v = StrategyValidator(GatingConfig(require_walk_forward=False))
    result = v.validate({
        "symbol": "AAPL",
        "strategy_type": "trend",
        "timeframe": "day",
        "oos_sharpe": 0.3,
        "oos_trades": 100,
        "max_drawdown_pct": 10.0,
    })
    assert result.passed is False
    assert "performance" in result.gate_results
    assert result.gate_results["performance"]["passed"] is False


def test_overfit_detected():
    v = StrategyValidator(GatingConfig(require_walk_forward=False))
    result = v.validate({
        "symbol": "SPY",
        "strategy_type": "momentum",
        "timeframe": "hour",
        "oos_sharpe": 1.5,
        "oos_trades": 80,
        "is_sharpe": 10.0,
        "max_drawdown_pct": 20.0,
    })
    assert result.passed is False
    assert result.gate_results["overfitting"]["passed"] is False


def test_mcpt_not_significant():
    v = StrategyValidator(GatingConfig(require_walk_forward=False))
    result = v.validate({
        "symbol": "TSLA",
        "strategy_type": "breakout",
        "timeframe": "15min",
        "oos_sharpe": 1.8,
        "oos_trades": 200,
        "is_sharpe": 2.5,
        "max_drawdown_pct": 25.0,
        "mcpt": {"p_value": 0.15, "permutations": 500},
    })
    assert result.gate_results["mcpt"]["passed"] is False


def test_schema_missing_fields():
    v = StrategyValidator(GatingConfig())
    result = v.validate({"symbol": "AAPL"})
    assert result.passed is False
    assert result.gate_results["schema"]["passed"] is False


def test_tier_classification():
    v = StrategyValidator(GatingConfig(require_walk_forward=False))
    result = v.validate({
        "symbol": "NVDA",
        "strategy_type": "trend",
        "timeframe": "day",
        "oos_sharpe": 2.5,
        "oos_trades": 150,
        "is_sharpe": 3.5,
        "max_drawdown_pct": 12.0,
        "mcpt": {"p_value": 0.002, "permutations": 2000},
        "paper_trading": {"days": 45, "trades": 80},
    })
    assert result.passed is True
    assert result.tier == "platinum"
    assert result.score == 100.0


if __name__ == "__main__":
    test_valid_strategy_passes()
    test_low_sharpe_rejected()
    test_overfit_detected()
    test_mcpt_not_significant()
    test_schema_missing_fields()
    test_tier_classification()
    print("All 6 tests passed!")
