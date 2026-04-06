"""Tests for Account Protection module."""
import pytest
from algochains_mcp.account_protection.engine import (
    AccountProtectionEngine,
    ProtectionConfig,
)
from algochains_mcp.account_protection.guards import (
    AccountSnapshot,
    BuyingPowerGuard,
    ConcentrationGuard,
    ConsecutiveLossGuard,
    DailyLossGuard,
    DrawdownGuard,
    FatFingerGuard,
    GuardSeverity,
    MarginGuard,
    MaxPositionsGuard,
    OrderIntent,
    PositionSizeGuard,
    TimeRestrictionGuard,
    VolatilityKillswitch,
)


def _order(symbol="AAPL", side="buy", qty=10, notional=1850.0):
    return OrderIntent(
        broker="alpaca", symbol=symbol, side=side, qty=qty,
        notional_value=notional,
    )


def _account(equity=100_000, buying_power=50_000, daily_pnl=0, positions=None):
    return AccountSnapshot(
        equity=equity, cash=50_000, buying_power=buying_power,
        daily_pnl=daily_pnl, peak_equity=equity,
        open_positions=positions or [],
    )


class TestPositionSizeGuard:
    def test_normal_order_passes(self):
        guard = PositionSizeGuard()
        result = guard.check(_order(notional=5000), _account())
        assert result.passed

    def test_oversized_order_blocked(self):
        guard = PositionSizeGuard(max_order_pct=25.0)
        result = guard.check(_order(notional=30_000), _account())
        assert not result.passed
        assert result.severity == GuardSeverity.BLOCK

    def test_zero_equity_blocked(self):
        guard = PositionSizeGuard()
        result = guard.check(_order(), _account(equity=0))
        assert not result.passed


class TestDailyLossGuard:
    def test_no_loss_passes(self):
        guard = DailyLossGuard()
        result = guard.check(_order(), _account(daily_pnl=500))
        assert result.passed

    def test_soft_limit_blocks(self):
        guard = DailyLossGuard(soft_limit_pct=2.0)
        result = guard.check(_order(), _account(daily_pnl=-2500))
        assert not result.passed

    def test_hard_limit_recommends_flatten(self):
        guard = DailyLossGuard(hard_limit_pct=5.0)
        result = guard.check(_order(), _account(daily_pnl=-6000))
        assert not result.passed
        assert "flatten" in result.details.get("action", "")


class TestDrawdownGuard:
    def test_no_drawdown_passes(self):
        guard = DrawdownGuard()
        acct = _account(equity=100_000)
        acct.peak_equity = 100_000
        result = guard.check(_order(), acct)
        assert result.passed

    def test_drawdown_blocks(self):
        guard = DrawdownGuard(block_pct=10.0)
        acct = _account(equity=85_000)
        acct.peak_equity = 100_000
        result = guard.check(_order(), acct)
        assert not result.passed

    def test_emergency_flatten(self):
        guard = DrawdownGuard(flatten_pct=15.0)
        acct = _account(equity=80_000)
        acct.peak_equity = 100_000
        result = guard.check(_order(), acct)
        assert not result.passed
        assert "emergency_flatten" in result.details.get("action", "")


class TestFatFingerGuard:
    def test_normal_order_passes(self):
        guard = FatFingerGuard(max_notional=100_000)
        result = guard.check(_order(notional=5000), _account())
        assert result.passed

    def test_huge_notional_blocked(self):
        guard = FatFingerGuard(max_notional=50_000)
        result = guard.check(_order(notional=60_000), _account())
        assert not result.passed


class TestVIXKillswitch:
    def test_normal_vix_passes(self):
        guard = VolatilityKillswitch()
        guard.update_vix(18.0)
        result = guard.check(_order(), _account())
        assert result.passed

    def test_high_vix_blocks(self):
        guard = VolatilityKillswitch(block_vix=35.0)
        guard.update_vix(40.0)
        result = guard.check(_order(), _account())
        assert not result.passed

    def test_extreme_vix_flatten(self):
        guard = VolatilityKillswitch(flatten_vix=50.0)
        guard.update_vix(55.0)
        result = guard.check(_order(), _account())
        assert not result.passed
        assert "emergency_flatten" in result.details.get("action", "")


class TestMaxPositionsGuard:
    def test_under_limit_passes(self):
        guard = MaxPositionsGuard(max_positions=5)
        acct = _account(positions=[{"symbol": "AAPL"}, {"symbol": "MSFT"}])
        result = guard.check(_order(symbol="GOOGL"), acct)
        assert result.passed

    def test_at_limit_blocks(self):
        guard = MaxPositionsGuard(max_positions=2)
        acct = _account(positions=[{"symbol": "AAPL"}, {"symbol": "MSFT"}])
        result = guard.check(_order(symbol="GOOGL"), acct)
        assert not result.passed


class TestConcentrationGuard:
    def test_diversified_passes(self):
        guard = ConcentrationGuard(max_single_pct=20.0)
        acct = _account(positions=[{"symbol": "AAPL", "market_value": 10_000}])
        result = guard.check(_order(notional=5_000), acct)
        assert result.passed

    def test_concentrated_blocks(self):
        guard = ConcentrationGuard(max_single_pct=20.0)
        acct = _account(positions=[{"symbol": "AAPL", "market_value": 18_000}])
        result = guard.check(_order(symbol="AAPL", notional=5_000), acct)
        assert not result.passed


class TestAccountProtectionEngine:
    def test_safe_order_allowed(self):
        engine = AccountProtectionEngine()
        report = engine.check_order(_order(notional=1000), _account())
        assert report.order_allowed

    def test_dangerous_order_blocked(self):
        config = ProtectionConfig(max_daily_loss_pct=1.0)
        engine = AccountProtectionEngine(config)
        acct = _account(daily_pnl=-2000)
        report = engine.check_order(_order(), acct)
        assert not report.order_allowed
        assert len(report.blocks) > 0

    def test_presets(self):
        engine = AccountProtectionEngine()
        config = engine.apply_preset("conservative")
        assert config["max_daily_loss_pct"] == 1.0

        config = engine.apply_preset("aggressive")
        assert config["max_daily_loss_pct"] == 5.0

    def test_disabled_allows_everything(self):
        config = ProtectionConfig(enabled=False)
        engine = AccountProtectionEngine(config)
        acct = _account(daily_pnl=-50_000)
        report = engine.check_order(_order(), acct)
        assert report.order_allowed

    def test_audit_log(self):
        engine = AccountProtectionEngine()
        engine.check_order(_order(), _account())
        engine.check_order(_order(), _account())
        log = engine.get_audit_log()
        assert len(log) == 2
