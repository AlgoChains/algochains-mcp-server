"""Integration tests for V8 + V9 modules."""
import asyncio
import pytest

# ── V8: Strategy Builder ─────────────────────────────────────────

from algochains_mcp.strategy_builder.spec import StrategySpec, StrategySpecValidator
from algochains_mcp.strategy_builder.template_manager import TemplateManager
from algochains_mcp.strategy_builder.deployer import StrategyDeployer
from algochains_mcp.strategy_builder.walk_forward import WalkForwardEngine

# ── V8: Social Trading ───────────────────────────────────────────

from algochains_mcp.social_trading.engine import SocialTradingEngine

# ── V8: Community Signals ────────────────────────────────────────

from algochains_mcp.community_signals.engine import CommunitySignalEngine

# ── V9: Risk Dashboard ──────────────────────────────────────────

from algochains_mcp.risk_dashboard.engine import RiskDashboardEngine

# ── V9: Compliance ───────────────────────────────────────────────

from algochains_mcp.compliance.engine import ComplianceEngine

# ── V9: Multi-Tenant ─────────────────────────────────────────────

from algochains_mcp.multi_tenant.engine import MultiTenantEngine


# ═════════════════════════════════════════════════════════════════
# Fixtures
# ═════════════════════════════════════════════════════════════════

SAMPLE_SPEC_DICT = {
    "name": "Test RSI Strategy",
    "symbols": ["AAPL", "MSFT"],
    "timeframe": "1h",
    "asset_class": "equity",
    "indicators": [{"name": "rsi", "period": 14, "source": "close"}],
    "entry_rules": {
        "long": {"conditions": [{"indicator": "rsi", "operator": "<", "value": 30}], "logic": "AND"},
    },
    "exit_rules": {"stop_loss": {"type": "atr_multiple", "multiplier": 2.0}},
    "position_sizing": {"method": "risk_pct", "risk_per_trade": 0.01, "max_positions": 3},
    "train_start": "2020-01-01",
    "train_end": "2022-12-31",
    "test_start": "2023-01-01",
    "test_end": "2024-12-31",
}

SAMPLE_PORTFOLIO = {
    "positions": [
        {"symbol": "AAPL", "market_value": 50000, "annual_volatility": 0.25, "beta": 1.1},
        {"symbol": "MSFT", "market_value": 30000, "annual_volatility": 0.22, "beta": 1.0},
        {"symbol": "JNJ", "market_value": 20000, "annual_volatility": 0.15, "beta": 0.7},
    ],
    "current_equity": 95000,
    "peak_equity": 100000,
    "equity": 100000,
    "margin_used": 40000,
    "daily_pnl": -500,
}


# ═════════════════════════════════════════════════════════════════
# V8: Strategy Builder Tests
# ═════════════════════════════════════════════════════════════════

class TestStrategySpec:
    def test_from_dict_roundtrip(self):
        spec = StrategySpec.from_dict(SAMPLE_SPEC_DICT)
        assert spec.name == "Test RSI Strategy"
        assert spec.symbols == ["AAPL", "MSFT"]
        d = spec.to_dict()
        assert d["name"] == "Test RSI Strategy"
        assert d["universe"]["timeframe"] == "1h"

    def test_validator_accepts_valid_spec(self):
        spec = StrategySpec.from_dict(SAMPLE_SPEC_DICT)
        v = StrategySpecValidator()
        result = v.validate(spec)
        assert result["valid"] is True
        assert len(result.get("errors", [])) == 0


class TestTemplateManager:
    def test_list_templates(self):
        mgr = TemplateManager()
        result = mgr.list_templates()
        assert result["count"] >= 5
        names = [t["name"] for t in result["templates"]]
        assert "RSI Momentum" in names
        assert "EMA Crossover Trend Following" in names

    def test_list_by_category(self):
        mgr = TemplateManager()
        result = mgr.list_templates(category="momentum")
        assert result["count"] >= 1
        assert all(t["category"] == "momentum" for t in result["templates"])

    def test_fork_template(self):
        mgr = TemplateManager()
        result = mgr.fork_template("tpl_momentum_rsi", new_name="My RSI", symbols=["NVDA"])
        assert result["success"] is True
        assert result["spec"]["name"] == "My RSI"
        assert "NVDA" in result["spec"]["universe"]["symbols"]

    def test_fork_nonexistent(self):
        mgr = TemplateManager()
        result = mgr.fork_template("tpl_does_not_exist")
        assert result["success"] is False


class TestDeployer:
    def test_deploy_and_list(self):
        deployer = StrategyDeployer()
        spec = StrategySpec.from_dict({**SAMPLE_SPEC_DICT, "status": "validated"})
        result = asyncio.run(
            deployer.deploy(spec, broker="alpaca", mode="paper", capital=25000)
        )
        assert result["success"] is True
        assert result["deployment"]["broker"] == "alpaca"
        assert result["deployment"]["mode"] == "paper"

        listing = asyncio.run(deployer.list_deployments())
        assert listing["count"] == 1

    def test_stop_deployment(self):
        deployer = StrategyDeployer()
        spec = StrategySpec.from_dict({**SAMPLE_SPEC_DICT, "status": "validated"})
        r = asyncio.run(deployer.deploy(spec, "alpaca"))
        dep_id = r["deployment"]["deployment_id"]
        stop = asyncio.run(deployer.stop_deployment(dep_id))
        assert stop["success"] is True
        assert stop["deployment"]["status"] == "stopped"


# ═════════════════════════════════════════════════════════════════
# V8: Social Trading Tests
# ═════════════════════════════════════════════════════════════════

class TestSocialTrading:
    def _engine(self):
        return SocialTradingEngine()

    def test_become_leader_requirements(self):
        eng = self._engine()
        result = asyncio.run(
            eng.become_leader("u1", "trader_joe", track_record={"track_record_days": 30, "total_trades": 10, "sharpe": 0.5, "max_drawdown": 0.5})
        )
        assert result["success"] is False
        assert len(result["requirements_failed"]) > 0

    def test_become_leader_success(self):
        eng = self._engine()
        result = asyncio.run(
            eng.become_leader("u1", "alpha_trader", track_record={"track_record_days": 180, "total_trades": 200, "sharpe": 2.5, "max_drawdown": 0.12})
        )
        assert result["success"] is True
        assert result["leader"]["handle"] == "alpha_trader"
        assert result["leader"]["ranking_score"] > 0

    def test_follow_unfollow(self):
        eng = self._engine()
        asyncio.run(
            eng.become_leader("leader1", "top_dog", track_record={"track_record_days": 365, "total_trades": 500, "sharpe": 3.0, "max_drawdown": 0.10})
        )
        follow = asyncio.run(eng.follow_leader("follower1", "leader1"))
        assert follow["success"] is True

        status = asyncio.run(eng.get_copy_status("follower1"))
        assert status["active_copies"] == 1

        unf = asyncio.run(eng.unfollow_leader("follower1", "leader1"))
        assert unf["success"] is True


# ═════════════════════════════════════════════════════════════════
# V8: Community Signals Tests
# ═════════════════════════════════════════════════════════════════

class TestCommunitySignals:
    def _engine(self):
        return CommunitySignalEngine()

    def test_publish_and_consensus(self):
        eng = self._engine()
        asyncio.run(eng.publish_signal("u1", "AAPL", "long", confidence=0.8))
        asyncio.run(eng.publish_signal("u2", "AAPL", "long", confidence=0.7))
        asyncio.run(eng.publish_signal("u3", "AAPL", "short", confidence=0.3))

        consensus = asyncio.run(eng.get_consensus("AAPL", "1h"))
        assert consensus["success"] is True
        assert consensus["signals"] == 3
        assert consensus["consensus"] == "bullish"

    def test_verify_signal(self):
        eng = self._engine()
        pub = asyncio.run(eng.publish_signal("u1", "TSLA", "long"))
        sid = pub["signal"]["signal_id"]

        ver = asyncio.run(eng.verify_signal(sid, {"broker": "alpaca", "order_id": "123", "fill_price": 250.0}))
        assert ver["success"] is True
        assert ver["category"] == "verified"

    def test_accuracy_tracking(self):
        eng = self._engine()
        p1 = asyncio.run(eng.publish_signal("u1", "NVDA", "long"))
        p2 = asyncio.run(eng.publish_signal("u1", "AMD", "short"))

        asyncio.run(eng.resolve_signal(p1["signal"]["signal_id"], "win"))
        asyncio.run(eng.resolve_signal(p2["signal"]["signal_id"], "loss"))

        acc = asyncio.run(eng.get_signal_accuracy("u1"))
        assert acc["accuracy_score"] == 0.5
        assert acc["correct"] == 1
        assert acc["total"] == 2


# ═════════════════════════════════════════════════════════════════
# V9: Risk Dashboard Tests
# ═════════════════════════════════════════════════════════════════

class TestRiskDashboard:
    def _engine(self):
        return RiskDashboardEngine()

    def test_var_parametric(self):
        eng = self._engine()
        result = asyncio.run(
            eng.calculate_var(SAMPLE_PORTFOLIO, "parametric", 0.95, 1)
        )
        assert result["success"] is True
        assert result["var_dollar"] > 0
        assert result["portfolio_value"] == 100000

    def test_expected_shortfall(self):
        eng = self._engine()
        result = asyncio.run(
            eng.calculate_expected_shortfall(SAMPLE_PORTFOLIO)
        )
        assert result["success"] is True
        assert result["es_dollar"] > result["var_dollar"]

    def test_stress_test_all(self):
        eng = self._engine()
        result = asyncio.run(eng.run_stress_test(SAMPLE_PORTFOLIO))
        assert result["success"] is True
        assert "covid_crash" in result["scenarios"]
        assert "gfc_2008" in result["scenarios"]
        assert result["scenarios"]["covid_crash"]["portfolio_loss"] < 0

    def test_stress_test_single(self):
        eng = self._engine()
        result = asyncio.run(
            eng.run_stress_test(SAMPLE_PORTFOLIO, scenario="flash_crash")
        )
        assert result["success"] is True
        assert len(result["scenarios"]) == 1

    def test_drawdown_monitor(self):
        eng = self._engine()
        result = asyncio.run(eng.get_drawdown_monitor(SAMPLE_PORTFOLIO))
        assert result["success"] is True
        assert result["drawdown_pct"] > 0
        assert result["status"] == "recovery"

    def test_margin_utilization(self):
        eng = self._engine()
        result = asyncio.run(eng.get_margin_utilization(SAMPLE_PORTFOLIO))
        assert result["success"] is True
        assert result["utilization_pct"] == 40.0
        assert result["status"] == "healthy"

    def test_greeks(self):
        eng = self._engine()
        portfolio = {"positions": [
            {"symbol": "AAPL_C_200", "delta": 0.6, "gamma": 0.05, "theta": -0.03, "vega": 0.15, "quantity": 10, "multiplier": 100},
        ]}
        result = asyncio.run(eng.get_greeks_exposure(portfolio))
        assert result["success"] is True
        assert result["greeks"]["delta"] == 600.0

    def test_risk_alerts(self):
        eng = self._engine()
        asyncio.run(eng.configure_risk_alert("drawdown", 0.03))
        triggered = asyncio.run(eng.check_risk_alerts(SAMPLE_PORTFOLIO))
        assert triggered["success"] is True
        assert triggered["triggered"] >= 1  # 5% drawdown > 3% threshold

    def test_concentration(self):
        eng = self._engine()
        result = asyncio.run(eng.get_concentration_risk(SAMPLE_PORTFOLIO))
        assert result["success"] is True
        assert result["hhi"] > 0
        assert result["top_3_weight_pct"] == 100.0


# ═════════════════════════════════════════════════════════════════
# V9: Compliance Tests
# ═════════════════════════════════════════════════════════════════

class TestCompliance:
    def _engine(self):
        return ComplianceEngine()

    def test_pre_trade_pass(self):
        eng = self._engine()
        order = {"symbol": "AAPL", "qty": 10, "price": 200, "side": "buy"}
        account = {"equity": 100000, "daily_pnl": -100}
        result = asyncio.run(eng.pre_trade_check(order, account))
        assert result["success"] is True
        assert result["passed"] is True

    def test_pre_trade_position_limit(self):
        eng = self._engine()
        order = {"symbol": "AAPL", "qty": 100, "price": 200, "side": "buy"}  # $20k = 20% of equity
        account = {"equity": 100000, "daily_pnl": 0}
        result = asyncio.run(eng.pre_trade_check(order, account))
        assert result["passed"] is False or any(v["rule"] == "position_limit" for v in result["violations"])

    def test_kill_switch(self):
        eng = self._engine()
        asyncio.run(eng.activate_kill_switch("test halt"))
        order = {"symbol": "AAPL", "qty": 1, "price": 200, "side": "buy"}
        result = asyncio.run(eng.pre_trade_check(order, {"equity": 100000, "daily_pnl": 0}))
        assert result["passed"] is False
        assert any(v["rule"] == "kill_switch" for v in result["violations"])

        asyncio.run(eng.deactivate_kill_switch("test resume"))

    def test_audit_trail_integrity(self):
        eng = self._engine()
        asyncio.run(eng.activate_kill_switch("test1"))
        asyncio.run(eng.deactivate_kill_switch("test2"))
        trail = asyncio.run(eng.get_audit_trail())
        assert trail["success"] is True
        assert trail["chain_valid"] is True
        assert trail["total_entries"] >= 2

    def test_best_execution(self):
        eng = self._engine()
        trades = [
            {"symbol": "AAPL", "side": "buy", "fill_price": 200.05, "mid_price": 200.00, "venue": "NASDAQ"},
            {"symbol": "MSFT", "side": "buy", "fill_price": 400.50, "mid_price": 400.00, "venue": "NYSE"},
        ]
        result = asyncio.run(eng.best_execution_report(trades))
        assert result["success"] is True
        assert result["trades"] == 2
        assert result["avg_slippage_bps"] > 0

    def test_compliance_profile(self):
        eng = self._engine()
        asyncio.run(eng.set_compliance_profile("conservative", {"max_daily_loss": 5000, "max_order_value": 100000}))
        profile = asyncio.run(eng.get_compliance_profile("conservative"))
        assert profile["success"] is True
        assert profile["limits"]["max_daily_loss"] == 5000


# ═════════════════════════════════════════════════════════════════
# V9: Multi-Tenant Tests
# ═════════════════════════════════════════════════════════════════

class TestMultiTenant:
    def _engine(self):
        return MultiTenantEngine()

    def test_create_tenant(self):
        eng = self._engine()
        result = asyncio.run(
            eng.create_tenant("Acme Trading", "admin@acme.com", "growth")
        )
        assert result["success"] is True
        assert result["tenant"]["name"] == "Acme Trading"
        assert result["tenant"]["tier"] == "growth"
        assert result["tenant"]["api_key"].startswith("ak_")

    def test_sub_accounts(self):
        eng = self._engine()
        t = asyncio.run(eng.create_tenant("TestCo", "test@co.com"))
        tid = t["tenant"]["tenant_id"]

        sa = asyncio.run(eng.create_sub_account(tid, "user1", "Trader A", ["read", "trade"]))
        assert sa["success"] is True

        listing = asyncio.run(eng.list_sub_accounts(tid))
        assert listing["count"] == 1

    def test_sub_account_limit(self):
        eng = self._engine()
        t = asyncio.run(eng.create_tenant("SmallCo", "s@co.com", "starter"))
        tid = t["tenant"]["tenant_id"]
        # Starter tier allows 5 sub-accounts
        for i in range(5):
            asyncio.run(eng.create_sub_account(tid, f"u{i}", f"User {i}"))
        overflow = asyncio.run(eng.create_sub_account(tid, "u99", "Overflow"))
        assert overflow["success"] is False

    def test_billing(self):
        eng = self._engine()
        t = asyncio.run(eng.create_tenant("BillCo", "b@co.com", "professional"))
        tid = t["tenant"]["tenant_id"]
        bill = asyncio.run(eng.get_billing_summary(tid))
        assert bill["success"] is True
        assert bill["monthly_base"] == 499

    def test_branding(self):
        eng = self._engine()
        t = asyncio.run(eng.create_tenant("BrandCo", "brand@co.com"))
        tid = t["tenant"]["tenant_id"]
        result = asyncio.run(eng.set_branding(tid, {"primary_color": "#FF0000", "app_name": "TradePro"}))
        assert result["success"] is True
        assert result["branding"]["primary_color"] == "#FF0000"

    def test_broker_routing(self):
        eng = self._engine()
        t = asyncio.run(eng.create_tenant("RouteCo", "r@co.com"))
        tid = t["tenant"]["tenant_id"]
        result = asyncio.run(eng.configure_broker_routing(tid, {"equity": "alpaca", "forex": "oanda"}))
        assert result["success"] is True
        assert result["routing"]["brokers"]["equity"] == "alpaca"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
