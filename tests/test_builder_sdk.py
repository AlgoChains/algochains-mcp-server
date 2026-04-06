"""Tests for Builder SDK module."""
import pytest
from algochains_mcp.builder_sdk.data_warehouse import DataWarehouseClient, DataQuery
from algochains_mcp.builder_sdk.strategy_runner import (
    BacktestConfig, BacktestResult, StrategyRunner,
)
from algochains_mcp.builder_sdk.submission_pipeline import (
    StrategySubmission, SubmissionPipeline,
)


class TestDataWarehouse:
    def test_list_warehouses(self):
        client = DataWarehouseClient()
        result = client.list_warehouses()
        assert "warehouses" in result
        assert "crypto" in result["warehouses"]
        assert "stocks" in result["warehouses"]
        assert "forex" in result["warehouses"]

    def test_query_validation(self):
        query = DataQuery(asset_class="invalid", ticker="AAPL")
        errors = query.validate()
        assert len(errors) > 0

    def test_valid_query(self):
        query = DataQuery(asset_class="stocks", ticker="AAPL", limit=1000)
        errors = query.validate()
        assert len(errors) == 0

    def test_limit_cap(self):
        query = DataQuery(asset_class="stocks", ticker="AAPL", limit=200_000)
        errors = query.validate()
        assert any("100,000" in e for e in errors)

    def test_usage_tracking(self):
        client = DataWarehouseClient()
        usage = client.get_usage()
        assert usage["queries_this_session"] == 0


class TestBacktestResult:
    def test_marketplace_gates_passing(self):
        result = BacktestResult(
            sharpe_ratio=2.5, total_trades=200,
            max_drawdown_pct=8.0, win_rate=60.0,
            profit_factor=2.0,
        )
        gates = result.passes_marketplace_gates()
        assert gates["passes_all"]
        assert gates["tier"] in ("platinum", "gold")

    def test_marketplace_gates_failing(self):
        result = BacktestResult(
            sharpe_ratio=0.5, total_trades=20,
            max_drawdown_pct=50.0, win_rate=30.0,
        )
        gates = result.passes_marketplace_gates()
        assert not gates["passes_all"]

    def test_tier_classification(self):
        platinum = BacktestResult(
            sharpe_ratio=3.0, total_trades=250,
            max_drawdown_pct=5.0, win_rate=65.0,
            profit_factor=2.5,
        )
        assert platinum._classify_tier() == "platinum"

        rejected = BacktestResult(
            sharpe_ratio=0.3, total_trades=10,
            max_drawdown_pct=60.0, win_rate=25.0,
        )
        assert rejected._classify_tier() == "rejected"


class TestStrategyRunner:
    def test_capabilities(self):
        runner = StrategyRunner()
        caps = runner.get_capabilities()
        assert "engines" in caps
        assert "built_in" in caps["engines"]

    @pytest.mark.asyncio
    async def test_empty_data_warning(self):
        runner = StrategyRunner()
        config = BacktestConfig(symbol="AAPL")
        result = await runner.run_backtest(config)
        assert len(result.warnings) > 0

    @pytest.mark.asyncio
    async def test_vectorized_backtest(self):
        runner = StrategyRunner()
        config = BacktestConfig(symbol="TEST")
        data = [{"close": 100 + i * 0.5, "window_start": f"2024-01-{i+1:02d}"}
                for i in range(50)]
        result = await runner.run_backtest(config, data=data)
        assert result.total_trades > 0
        assert result.execution_time_ms > 0


class TestSubmissionPipeline:
    def test_guide(self):
        pipeline = SubmissionPipeline()
        guide = pipeline.get_submission_guide()
        assert "steps" in guide
        assert len(guide["steps"]) == 7
        assert "ip_protection" in guide
        assert guide["revenue_split"] == "70% creator / 30% AlgoChains"

    def test_submission_validation_errors(self):
        sub = StrategySubmission(
            symbol="", strategy_type="", timeframe="hour",
            oos_sharpe=0, oos_trades=0, max_drawdown_pct=-1,
        )
        errors = sub.validate()
        assert len(errors) >= 3

    @pytest.mark.asyncio
    async def test_valid_submission_platinum(self):
        pipeline = SubmissionPipeline()
        sub = StrategySubmission(
            symbol="AAPL", strategy_type="trend", timeframe="hour",
            oos_sharpe=2.8, oos_trades=200, max_drawdown_pct=8.0,
            is_sharpe=3.2, win_rate=60.0, profit_factor=2.1,
            mcpt_p_value=0.01, mcpt_permutations=1000,
            wf_folds=5, wf_avg_oos_sharpe=2.5, wf_worst_fold=1.8,
        )
        result = await pipeline.submit(sub)
        assert result.passed
        assert result.tier in ("platinum", "gold")
        assert result.score > 70

    @pytest.mark.asyncio
    async def test_rejected_submission(self):
        pipeline = SubmissionPipeline()
        sub = StrategySubmission(
            symbol="AAPL", strategy_type="trend", timeframe="hour",
            oos_sharpe=0.5, oos_trades=20, max_drawdown_pct=45.0,
            mcpt_p_value=0.2,
        )
        result = await pipeline.submit(sub)
        assert not result.passed
        assert result.tier == "rejected"

    def test_listing_payload(self):
        sub = StrategySubmission(
            symbol="AAPL", strategy_type="trend", timeframe="hour",
            oos_sharpe=2.0, oos_trades=100, max_drawdown_pct=10.0,
        )
        payload = sub.to_listing_payload()
        assert payload["strategy_file"] is None
        assert payload["symbol"] == "AAPL"
        assert "mcpt_metadata" in payload

    def test_hmac_signature(self):
        pipeline = SubmissionPipeline(signal_secret="test_secret_key")
        sig = pipeline.create_signal_signature({"symbol": "AAPL", "side": "buy"})
        assert len(sig) == 64  # SHA-256 hex digest
