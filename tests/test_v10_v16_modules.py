"""Integration tests for V10-V16 modules."""
import asyncio
import pytest

# ── V10: ML/AI-Native Strategy Engine ────────────────────────────
from algochains_mcp.ml_engine.feature_engine import FeatureEngine
from algochains_mcp.ml_engine.model_trainer import ModelTrainer
from algochains_mcp.ml_engine.model_registry import ModelRegistry
from algochains_mcp.ml_engine.rl_agent import RLAgentEngine
from algochains_mcp.ml_engine.gpu_dispatcher import GPUDispatcher
from algochains_mcp.ml_engine.llm_strategy_gen import LLMStrategyGenerator

# ── V11: Institutional-Grade Execution ───────────────────────────
from algochains_mcp.execution_engine.order_manager import InstitutionalOrderManager
from algochains_mcp.execution_engine.smart_order_router import SmartOrderRouter
from algochains_mcp.execution_engine.algo_executor import AlgoExecutor
from algochains_mcp.execution_engine.fix_gateway import FIXGateway
from algochains_mcp.execution_engine.tca_engine import TCAEngine
from algochains_mcp.execution_engine.venue_manager import VenueManager

# ── V12: Real-Time Analytics ────────────────────────────────────
from algochains_mcp.realtime_analytics.pnl_streamer import PnLStreamer
from algochains_mcp.realtime_analytics.order_flow_analyzer import OrderFlowAnalyzer
from algochains_mcp.realtime_analytics.microstructure import MicrostructureEngine
from algochains_mcp.realtime_analytics.regime_detector import RegimeDetector
from algochains_mcp.realtime_analytics.alert_engine import AlertEngine

# ── V13: Alternative Data Marketplace ───────────────────────────
from algochains_mcp.alt_data.sentiment_engine import SentimentEngine
from algochains_mcp.alt_data.satellite_engine import SatelliteDataEngine
from algochains_mcp.alt_data.web_scraper import WebScraperEngine
from algochains_mcp.alt_data.sec_filing_engine import SECFilingEngine
from algochains_mcp.alt_data.social_media_engine import SocialMediaEngine
from algochains_mcp.alt_data.alt_data_marketplace import AltDataMarketplace

# ── V14: Autonomous Agent Swarm ─────────────────────────────────
from algochains_mcp.agent_swarm.agent_orchestrator import AgentOrchestrator
from algochains_mcp.agent_swarm.task_planner import TaskPlanner
from algochains_mcp.agent_swarm.agent_memory import AgentMemory
from algochains_mcp.agent_swarm.tool_router import ToolRouter
from algochains_mcp.agent_swarm.consensus_engine import ConsensusEngine
from algochains_mcp.agent_swarm.agent_monitor import AgentMonitor

# ── V15: DeFi & Cross-Chain ─────────────────────────────────────
from algochains_mcp.defi_engine.dex_aggregator import DEXAggregator
from algochains_mcp.defi_engine.yield_optimizer import YieldOptimizer
from algochains_mcp.defi_engine.bridge_engine import BridgeEngine
from algochains_mcp.defi_engine.mev_protector import MEVProtector
from algochains_mcp.defi_engine.governance_engine import GovernanceEngine
from algochains_mcp.defi_engine.defi_risk_engine import DeFiRiskEngine

# ── V16: Cloud SaaS Platform ───────────────────────────────────
from algochains_mcp.cloud_saas.tenant_manager import TenantManager
from algochains_mcp.cloud_saas.billing_engine import BillingEngine
from algochains_mcp.cloud_saas.strategy_marketplace import StrategyMarketplace
from algochains_mcp.cloud_saas.white_label_engine import WhiteLabelEngine
from algochains_mcp.cloud_saas.api_gateway import APIGateway


# ═════════════════════════════════════════════════════════════════
# V10: ML/AI-Native Strategy Engine Tests
# ═════════════════════════════════════════════════════════════════

class TestFeatureEngine:
    def _engine(self):
        return FeatureEngine()

    def test_create_feature_set(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.create_feature_set(
            name="RSI Features",
            features=[{"name": "rsi_14", "type": "indicator", "params": {"period": 14}}],
            target="returns_1d"
        ))
        assert result["success"] is True
        assert result["feature_set"]["name"] == "RSI Features"
        assert "feature_set_id" in result["feature_set"]

    def test_list_feature_sets(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        loop.run_until_complete(eng.create_feature_set("Set A", [{"name": "rsi"}]))
        result = loop.run_until_complete(eng.list_feature_sets())
        assert result["success"] is True
        assert result["count"] >= 1

    def test_compute_features(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        fs = loop.run_until_complete(eng.create_feature_set("Compute Test", [{"name": "ema_20"}]))
        fs_id = fs["feature_set"]["feature_set_id"]
        result = loop.run_until_complete(eng.compute_features(fs_id, "AAPL", "2024-01-01", "2024-06-30"))
        assert result["success"] is True
        assert result["symbol"] == "AAPL"

    def test_get_feature_importance(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        fs = loop.run_until_complete(eng.create_feature_set("Imp Test", [{"name": "vol"}]))
        fs_id = fs["feature_set"]["feature_set_id"]
        result = loop.run_until_complete(eng.get_feature_importance(fs_id))
        assert result["success"] is True


class TestModelTrainer:
    def _engine(self):
        return ModelTrainer()

    def test_train_model(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.train(
            feature_set_id="fs_test",
            model_type="xgboost",
            hyperparameters={"max_depth": 6, "n_estimators": 100},
            train_split=0.8
        ))
        assert result["success"] is True
        assert result["model"]["model_type"] == "xgboost"
        assert "model_id" in result["model"]

    def test_evaluate_model(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        trained = loop.run_until_complete(eng.train("fs_1", "random_forest"))
        model_id = trained["model"]["model_id"]
        result = loop.run_until_complete(eng.evaluate(model_id))
        assert result["success"] is True
        assert "metrics" in result

    def test_predict(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        trained = loop.run_until_complete(eng.train("fs_1", "lstm"))
        model_id = trained["model"]["model_id"]
        result = loop.run_until_complete(eng.predict(model_id, "AAPL", {"rsi": 35, "ema": 150}))
        assert result["success"] is True
        assert "prediction_id" in result

    def test_explain_prediction(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        trained = loop.run_until_complete(eng.train("fs_1", "xgboost"))
        model_id = trained["model"]["model_id"]
        pred = loop.run_until_complete(eng.predict(model_id, "AAPL"))
        pred_id = pred["prediction_id"]
        result = loop.run_until_complete(eng.explain(model_id, pred_id))
        assert result["success"] is True


class TestModelRegistry:
    def _engine(self):
        return ModelRegistry()

    def test_register_and_list(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        reg = loop.run_until_complete(eng.register("m1", "RSI Model", version="1.0.0", metrics={"sharpe": 2.5}))
        assert reg["success"] is True
        listing = loop.run_until_complete(eng.list_models())
        assert listing["count"] >= 1

    def test_promote_model(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        reg = loop.run_until_complete(eng.register("m2", "BB Model"))
        rid = reg["registered"]["registry_id"]
        result = loop.run_until_complete(eng.promote(rid, "production"))
        assert result["success"] is True
        assert result["stage"] == "production"

    def test_compare_models(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        r1 = loop.run_until_complete(eng.register("m3", "Model A", metrics={"sharpe": 2.0}))
        r2 = loop.run_until_complete(eng.register("m4", "Model B", metrics={"sharpe": 3.0}))
        result = loop.run_until_complete(eng.compare(["m3", "m4"]))
        assert result["success"] is True
        assert len(result["comparison"]) == 2

    def test_archive_model(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        reg = loop.run_until_complete(eng.register("m5", "Old Model"))
        rid = reg["registered"]["registry_id"]
        result = loop.run_until_complete(eng.archive(rid, reason="outdated"))
        assert result["success"] is True


class TestRLAgent:
    def _engine(self):
        return RLAgentEngine()

    def test_create_and_train(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        agent = loop.run_until_complete(eng.create_agent("Test Agent", "ppo"))
        assert agent["success"] is True
        agent_id = agent["agent"]["agent_id"]
        trained = loop.run_until_complete(eng.train(agent_id, episodes=100))
        assert trained["success"] is True

    def test_evaluate_rl(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        agent = loop.run_until_complete(eng.create_agent("Eval Agent", "dqn"))
        agent_id = agent["agent"]["agent_id"]
        result = loop.run_until_complete(eng.evaluate(agent_id, episodes=50))
        assert result["success"] is True

    def test_get_state(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        agent = loop.run_until_complete(eng.create_agent("State Agent", "sac"))
        agent_id = agent["agent"]["agent_id"]
        result = loop.run_until_complete(eng.get_state(agent_id))
        assert result["success"] is True


class TestGPUDispatcher:
    def _engine(self):
        return GPUDispatcher()

    def test_dispatch_task(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.dispatch("training", {"model": "xgboost", "data": "100k_rows"}))
        assert result["success"] is True
        assert "task_id" in result

    def test_gpu_status(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.status())
        assert result["success"] is True
        assert "gpus" in result


class TestLLMStrategyGen:
    def _engine(self):
        return LLMStrategyGenerator()

    def test_generate_strategy(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.generate(
            "Mean reversion strategy for tech stocks using RSI",
            asset_class="equity",
            risk_tolerance="medium"
        ))
        assert result["success"] is True
        assert "spec" in result


# ═════════════════════════════════════════════════════════════════
# V11: Institutional-Grade Execution Tests
# ═════════════════════════════════════════════════════════════════

class TestInstitutionalOrderManager:
    def _engine(self):
        return InstitutionalOrderManager()

    def test_validate_order(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        order = {"symbol": "AAPL", "qty": 1000, "side": "buy", "price": 200, "type": "limit"}
        result = loop.run_until_complete(eng.validate_order(order, "acc_001"))
        assert result["success"] is True

    def test_submit_order(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        order = {"symbol": "MSFT", "qty": 500, "side": "sell", "price": 400}
        result = loop.run_until_complete(eng.submit_order(order, "acc_001"))
        assert result["success"] is True
        assert "order_id" in result

    def test_get_order_status(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        order = {"symbol": "AAPL", "qty": 100, "side": "buy", "price": 200}
        submitted = loop.run_until_complete(eng.submit_order(order))
        oid = submitted["order_id"]
        result = loop.run_until_complete(eng.get_order_status(oid))
        assert result["success"] is True


class TestSmartOrderRouter:
    def _engine(self):
        return SmartOrderRouter()

    def test_route_order(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        order = {"symbol": "AAPL", "qty": 5000, "side": "buy"}
        result = loop.run_until_complete(eng.route(order, routing_strategy="best_price"))
        assert result["success"] is True

    def test_venue_analytics(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.get_venue_analytics())
        assert result["success"] is True


class TestAlgoExecutor:
    def _engine(self):
        return AlgoExecutor()

    def test_start_twap(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        order = {"symbol": "AAPL", "qty": 10000, "side": "buy"}
        result = loop.run_until_complete(eng.start("twap", order, {"duration_minutes": 60}))
        assert result["success"] is True
        assert "execution_id" in result

    def test_stop_algo(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        order = {"symbol": "MSFT", "qty": 5000, "side": "sell"}
        started = loop.run_until_complete(eng.start("vwap", order))
        eid = started["execution_id"]
        result = loop.run_until_complete(eng.stop(eid))
        assert result["success"] is True

    def test_get_status(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        order = {"symbol": "TSLA", "qty": 1000, "side": "buy"}
        started = loop.run_until_complete(eng.start("iceberg", order))
        eid = started["execution_id"]
        result = loop.run_until_complete(eng.get_status(eid))
        assert result["success"] is True


class TestFIXGateway:
    def _engine(self):
        return FIXGateway()

    def test_connect_disconnect(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        conn = loop.run_until_complete(eng.connect("NYSE", "ALGO001", "NYSEFIX"))
        assert conn["success"] is True
        sid = conn["session"]["session_id"]
        disc = loop.run_until_complete(eng.disconnect(sid))
        assert disc["success"] is True

    def test_session_status(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        conn = loop.run_until_complete(eng.connect("NASDAQ", "ALGO002", "NASDFIX"))
        sid = conn["session"]["session_id"]
        result = loop.run_until_complete(eng.get_session_status(sid))
        assert result["success"] is True


class TestTCAEngine:
    def _engine(self):
        return TCAEngine()

    def test_analyze(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        trades = [
            {"symbol": "AAPL", "side": "buy", "qty": 100, "fill_price": 200.05, "arrival_price": 200.00},
            {"symbol": "MSFT", "side": "sell", "qty": 50, "fill_price": 399.80, "arrival_price": 400.00},
        ]
        result = loop.run_until_complete(eng.analyze(trades))
        assert result["success"] is True

    def test_get_report(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.get_report("2024-01-01", "2024-06-30"))
        assert result["success"] is True

    def test_implementation_shortfall(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        orders = [{"symbol": "AAPL", "decision_price": 199, "fill_price": 200.05, "qty": 100}]
        result = loop.run_until_complete(eng.implementation_shortfall(orders))
        assert result["success"] is True


class TestVenueManager:
    def _engine(self):
        return VenueManager()

    def test_register_and_list(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        reg = loop.run_until_complete(eng.register("NYSE", "exchange"))
        assert reg["success"] is True
        listing = loop.run_until_complete(eng.list_venues())
        assert listing["count"] >= 1

    def test_set_priority(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        reg = loop.run_until_complete(eng.register("BATS", "exchange"))
        vid = reg["venue"]["venue_id"]
        result = loop.run_until_complete(eng.set_priority(vid, 1))
        assert result["success"] is True


# ═════════════════════════════════════════════════════════════════
# V12: Real-Time Analytics Tests
# ═════════════════════════════════════════════════════════════════

class TestPnLStreamer:
    def _engine(self):
        return PnLStreamer()

    def test_start_stream(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.start_stream("acc_001", ["AAPL", "MSFT"]))
        assert result["success"] is True

    def test_get_snapshot(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.get_snapshot("acc_001"))
        assert result["success"] is True

    def test_get_history(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.get_history("acc_001", interval="1h", lookback="24h"))
        assert result["success"] is True


class TestOrderFlowAnalyzer:
    def _engine(self):
        return OrderFlowAnalyzer()

    def test_analyze(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.analyze("AAPL", lookback_minutes=60))
        assert result["success"] is True

    def test_heatmap(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.get_heatmap("AAPL"))
        assert result["success"] is True

    def test_volume_profile(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.get_volume_profile("AAPL"))
        assert result["success"] is True


class TestMicrostructure:
    def _engine(self):
        return MicrostructureEngine()

    def test_analyze(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.analyze("AAPL"))
        assert result["success"] is True

    def test_toxicity(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.get_toxicity("AAPL"))
        assert result["success"] is True


class TestRegimeDetector:
    def _engine(self):
        return RegimeDetector()

    def test_detect(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.detect("AAPL"))
        assert result["success"] is True
        assert "regime" in result

    def test_history(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.get_history("AAPL"))
        assert result["success"] is True

    def test_transition_matrix(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.get_transition_matrix("AAPL"))
        assert result["success"] is True


class TestAlertEngine:
    def _engine(self):
        return AlertEngine()

    def test_create_and_list(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        created = loop.run_until_complete(eng.create_alert(
            "Price Alert", {"type": "price_cross", "symbol": "AAPL", "level": 200},
            actions=["notify"], channels=["slack"]
        ))
        assert created["success"] is True
        listing = loop.run_until_complete(eng.list_alerts())
        assert listing["count"] >= 1

    def test_delete_alert(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        created = loop.run_until_complete(eng.create_alert("Del Test", {"type": "volume_spike"}))
        aid = created["alert"]["alert_id"]
        result = loop.run_until_complete(eng.delete_alert(aid))
        assert result["success"] is True

    def test_alert_history(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.get_history())
        assert result["success"] is True


# ═════════════════════════════════════════════════════════════════
# V13: Alternative Data Marketplace Tests
# ═════════════════════════════════════════════════════════════════

class TestSentimentEngine:
    def _engine(self):
        return SentimentEngine()

    def test_analyze(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.analyze("AAPL", source="news"))
        assert result["success"] is True

    def test_history(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.get_history("AAPL"))
        assert result["success"] is True

    def test_signal(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.get_signal("AAPL"))
        assert result["success"] is True


class TestSatelliteEngine:
    def _engine(self):
        return SatelliteDataEngine()

    def test_analyze(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.analyze("Cushing, OK", "oil_storage", symbol="CL"))
        assert result["success"] is True

    def test_timeseries(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.get_timeseries("loc_001", "fill_rate"))
        assert result["success"] is True


class TestWebScraper:
    def _engine(self):
        return WebScraperEngine()

    def test_scrape_and_list(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        job = loop.run_until_complete(eng.scrape("https://example.com/data", schedule="daily"))
        assert job["success"] is True
        listing = loop.run_until_complete(eng.list_jobs())
        assert listing["count"] >= 1

    def test_get_results(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        job = loop.run_until_complete(eng.scrape("https://example.com"))
        jid = job["job"]["job_id"]
        result = loop.run_until_complete(eng.get_results(jid))
        assert result["success"] is True


class TestSECFilingEngine:
    def _engine(self):
        return SECFilingEngine()

    def test_analyze(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.analyze("AAPL", "10-K"))
        assert result["success"] is True

    def test_insider_trades(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.get_insider_trades("AAPL"))
        assert result["success"] is True

    def test_institutional_holdings(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.get_institutional_holdings("AAPL"))
        assert result["success"] is True


class TestSocialMediaEngine:
    def _engine(self):
        return SocialMediaEngine()

    def test_analyze(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.analyze("AAPL", platform="twitter"))
        assert result["success"] is True

    def test_momentum(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.get_momentum("AAPL"))
        assert result["success"] is True

    def test_feed(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.get_feed(["AAPL", "TSLA"]))
        assert result["success"] is True


class TestAltDataMarketplace:
    def _engine(self):
        return AltDataMarketplace()

    def test_browse(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.browse(category="sentiment"))
        assert result["success"] is True

    def test_subscribe_and_sample(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        sub = loop.run_until_complete(eng.subscribe("ds_001"))
        assert sub["success"] is True
        sample = loop.run_until_complete(eng.get_sample("ds_001"))
        assert sample["success"] is True

    def test_quality(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.get_quality("ds_001"))
        assert result["success"] is True


# ═════════════════════════════════════════════════════════════════
# V14: Autonomous Agent Swarm Tests
# ═════════════════════════════════════════════════════════════════

class TestAgentOrchestrator:
    def _engine(self):
        return AgentOrchestrator()

    def test_spawn_and_list(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        spawned = loop.run_until_complete(eng.spawn("Alpha", "trader", capital_allocation=50000))
        assert spawned["success"] is True
        listing = loop.run_until_complete(eng.list_agents())
        assert listing["count"] >= 1

    def test_get_detail(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        spawned = loop.run_until_complete(eng.spawn("Beta", "researcher"))
        aid = spawned["agent"]["agent_id"]
        result = loop.run_until_complete(eng.get_detail(aid))
        assert result["success"] is True

    def test_terminate(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        spawned = loop.run_until_complete(eng.spawn("Gamma", "monitor"))
        aid = spawned["agent"]["agent_id"]
        result = loop.run_until_complete(eng.terminate(aid, reason="test cleanup"))
        assert result["success"] is True

    def test_set_parameters(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        spawned = loop.run_until_complete(eng.spawn("Delta", "trader"))
        aid = spawned["agent"]["agent_id"]
        result = loop.run_until_complete(eng.set_parameters(aid, {"risk_limit": 0.02}))
        assert result["success"] is True


class TestTaskPlanner:
    def _engine(self):
        return TaskPlanner()

    def test_create_plan(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.create_plan("Backtest RSI strategy on 5 symbols"))
        assert result["success"] is True
        assert "plan_id" in result

    def test_get_plan(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        created = loop.run_until_complete(eng.create_plan("Optimize parameters"))
        pid = created["plan_id"]
        result = loop.run_until_complete(eng.get_plan(pid))
        assert result["success"] is True


class TestAgentMemory:
    def _engine(self):
        return AgentMemory()

    def test_store_and_query(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        stored = loop.run_until_complete(eng.store("agent_1", "episodic", "AAPL RSI crossed below 30"))
        assert stored["success"] is True
        queried = loop.run_until_complete(eng.query("RSI AAPL", agent_id="agent_1"))
        assert queried["success"] is True

    def test_stats(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        loop.run_until_complete(eng.store("agent_2", "semantic", "Market is bullish"))
        result = loop.run_until_complete(eng.get_stats(agent_id="agent_2"))
        assert result["success"] is True


class TestToolRouter:
    def _engine(self):
        return ToolRouter()

    def test_route(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.route("agent_1", "analyze_sentiment", {"symbol": "AAPL"}))
        assert result["success"] is True

    def test_permissions(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.get_permissions("agent_1"))
        assert result["success"] is True


class TestConsensusEngine:
    def _engine(self):
        return ConsensusEngine()

    def test_request_and_get(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        req = loop.run_until_complete(eng.request(
            {"action": "buy", "symbol": "AAPL", "confidence": 0.8},
            ["agent_1", "agent_2", "agent_3"]
        ))
        assert req["success"] is True
        cid = req["consensus_id"]
        result = loop.run_until_complete(eng.get_result(cid))
        assert result["success"] is True

    def test_history(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.get_history())
        assert result["success"] is True


class TestAgentMonitor:
    def _engine(self):
        return AgentMonitor()

    def test_health(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.get_health("agent_1"))
        assert result["success"] is True

    def test_dashboard(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.get_dashboard())
        assert result["success"] is True

    def test_performance(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.get_performance("agent_1"))
        assert result["success"] is True


# ═════════════════════════════════════════════════════════════════
# V15: DeFi & Cross-Chain Tests
# ═════════════════════════════════════════════════════════════════

class TestDEXAggregator:
    def _engine(self):
        return DEXAggregator()

    def test_get_quote(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.get_quote("WETH", "USDC", 1.0, chain="ethereum"))
        assert result["success"] is True
        assert "quote_id" in result

    def test_execute_swap(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        quote = loop.run_until_complete(eng.get_quote("WETH", "USDC", 0.5))
        qid = quote["quote_id"]
        result = loop.run_until_complete(eng.execute_swap(qid))
        assert result["success"] is True

    def test_liquidity(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.get_liquidity("WETH", "USDC"))
        assert result["success"] is True


class TestYieldOptimizer:
    def _engine(self):
        return YieldOptimizer()

    def test_scan(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.scan(min_apy=5.0))
        assert result["success"] is True

    def test_deploy_and_withdraw(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        deployed = loop.run_until_complete(eng.deploy("opp_001", 10000))
        assert deployed["success"] is True
        pid = deployed["position"]["position_id"]
        withdrawn = loop.run_until_complete(eng.withdraw(pid))
        assert withdrawn["success"] is True

    def test_get_positions(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.get_positions())
        assert result["success"] is True


class TestBridgeEngine:
    def _engine(self):
        return BridgeEngine()

    def test_bridge_and_status(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        bridged = loop.run_until_complete(eng.bridge("USDC", 1000, "ethereum", "arbitrum"))
        assert bridged["success"] is True
        tid = bridged["transfer"]["transfer_id"]
        status = loop.run_until_complete(eng.get_status(tid))
        assert status["success"] is True

    def test_list_routes(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.list_routes("USDC", "ethereum", "polygon"))
        assert result["success"] is True


class TestMEVProtector:
    def _engine(self):
        return MEVProtector()

    def test_check_risk(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        tx = {"to": "0xdex", "value": 1000, "data": "swap_call"}
        result = loop.run_until_complete(eng.check_risk(tx, chain="ethereum"))
        assert result["success"] is True

    def test_submit_protected(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        tx = {"to": "0xdex", "value": 500, "data": "swap"}
        result = loop.run_until_complete(eng.submit_protected(tx))
        assert result["success"] is True

    def test_analytics(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.get_analytics())
        assert result["success"] is True


class TestGovernanceEngine:
    def _engine(self):
        return GovernanceEngine()

    def test_get_proposals(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.get_proposals("uniswap"))
        assert result["success"] is True

    def test_vote(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.vote("prop_001", "for", reason="beneficial"))
        assert result["success"] is True

    def test_power(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.get_power("aave"))
        assert result["success"] is True


class TestDeFiRiskEngine:
    def _engine(self):
        return DeFiRiskEngine()

    def test_assess(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.assess("aave", chain="ethereum"))
        assert result["success"] is True

    def test_portfolio_risk(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.get_portfolio_risk())
        assert result["success"] is True

    def test_liquidation_monitor(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.monitor_liquidation("pos_001"))
        assert result["success"] is True

    def test_insurance(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.get_insurance("aave", coverage_amount=100000))
        assert result["success"] is True


# ═════════════════════════════════════════════════════════════════
# V16: Cloud SaaS Platform Tests
# ═════════════════════════════════════════════════════════════════

class TestTenantManager:
    def _engine(self):
        return TenantManager()

    def test_create_tenant(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.create_tenant("Acme Corp", "admin@acme.com", plan="starter"))
        assert result["success"] is True
        assert result["tenant"]["company_name"] == "Acme Corp"

    def test_get_tenant(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        created = loop.run_until_complete(eng.create_tenant("GetCo", "get@co.com"))
        tid = created["tenant"]["tenant_id"]
        result = loop.run_until_complete(eng.get_tenant(tid))
        assert result["success"] is True

    def test_update_tenant(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        created = loop.run_until_complete(eng.create_tenant("UpdCo", "upd@co.com"))
        tid = created["tenant"]["tenant_id"]
        result = loop.run_until_complete(eng.update_tenant(tid, {"plan": "professional"}))
        assert result["success"] is True


class TestBillingEngine:
    def _engine(self):
        return BillingEngine()

    def test_get_usage(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.get_usage("tenant_001"))
        assert result["success"] is True

    def test_get_invoice(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.get_invoice("tenant_001"))
        assert result["success"] is True

    def test_list_invoices(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.list_invoices("tenant_001"))
        assert result["success"] is True

    def test_update_payment(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.update_payment("tenant_001", {"type": "card", "last4": "4242"}))
        assert result["success"] is True


class TestStrategyMarketplace:
    def _engine(self):
        return StrategyMarketplace()

    def test_publish(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.publish(
            "strat_001", {"type": "monthly", "price": 99},
            description="RSI momentum strategy", tags=["momentum", "equity"]
        ))
        assert result["success"] is True

    def test_browse(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.browse(category="momentum"))
        assert result["success"] is True

    def test_subscribe(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.subscribe("tenant_001", "strat_001", allocation=25000))
        assert result["success"] is True


class TestWhiteLabelEngine:
    def _engine(self):
        return WhiteLabelEngine()

    def test_configure(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.configure("tenant_001", {
            "primary_color": "#0066FF", "logo_url": "https://example.com/logo.png", "app_name": "TradePro"
        }))
        assert result["success"] is True

    def test_get_config(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        loop.run_until_complete(eng.configure("tenant_002", {"primary_color": "#FF0000"}))
        result = loop.run_until_complete(eng.get_config("tenant_002"))
        assert result["success"] is True


class TestAPIGateway:
    def _engine(self):
        return APIGateway()

    def test_generate_key(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.generate_key("tenant_001", "Production Key", rate_limit=5000))
        assert result["success"] is True
        assert "key_id" in result

    def test_list_keys(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        loop.run_until_complete(eng.generate_key("tenant_002", "Test Key"))
        result = loop.run_until_complete(eng.list_keys("tenant_002"))
        assert result["success"] is True

    def test_revoke_key(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        generated = loop.run_until_complete(eng.generate_key("tenant_003", "Temp Key"))
        kid = generated["key_id"]
        result = loop.run_until_complete(eng.revoke_key(kid))
        assert result["success"] is True

    def test_get_usage(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.get_usage("tenant_001"))
        assert result["success"] is True

    def test_platform_health(self):
        eng = self._engine()
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(eng.get_health())
        assert result["success"] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
