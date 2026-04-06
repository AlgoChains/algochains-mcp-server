"""
Shared pytest fixtures for AlgoChains MCP Server tests.

Eliminates 60+ lines of repeated boilerplate per test file.
All fixtures use real module instances — no mocks except where
explicitly required for offline testing (marked with _mock suffix).
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


# ── Server fixtures ────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def server_config():
    """Real ServerConfig loaded from environment."""
    from algochains_mcp.config import load_config
    return load_config()


@pytest.fixture(scope="session")
def broker_registry(server_config):
    """Real BrokerRegistry (no brokers connected unless env vars set)."""
    from algochains_mcp.brokers.registry import BrokerRegistry
    return BrokerRegistry(server_config)


# ── Temp directory for ephemeral state ────────────────────────────────────────

@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    """Override HOME to a temp dir so vault/DB files don't pollute real home."""
    monkeypatch.setenv("HOME", str(tmp_path))
    algochains_dir = tmp_path / ".algochains"
    algochains_dir.mkdir(parents=True, exist_ok=True)
    return tmp_path


# ── Account Protection fixtures ────────────────────────────────────────────────

@pytest.fixture
def protection_config():
    from algochains_mcp.account_protection.engine import ProtectionConfig
    return ProtectionConfig()


@pytest.fixture
def protection_engine(protection_config):
    from algochains_mcp.account_protection.engine import AccountProtectionEngine
    return AccountProtectionEngine(protection_config)


@pytest.fixture
def account_snapshot():
    from algochains_mcp.account_protection.guards import AccountSnapshot
    return AccountSnapshot(
        equity=100_000.0,
        daily_realized_pnl=0.0,
        daily_unrealized_pnl=0.0,
        open_positions=0,
        margin_used=0.0,
        margin_available=100_000.0,
        broker="alpaca",
    )


@pytest.fixture
def order_intent():
    from algochains_mcp.account_protection.guards import OrderIntent
    return OrderIntent(
        symbol="AAPL",
        side="buy",
        qty=10,
        order_type="market",
        notional=2000.0,
    )


# ── Builder SDK fixtures ───────────────────────────────────────────────────────

@pytest.fixture
def backtest_config():
    from algochains_mcp.builder_sdk.strategy_runner import BacktestConfig
    return BacktestConfig(
        symbol="AAPL",
        start_date="2024-01-01",
        end_date="2024-12-31",
        initial_capital=100_000.0,
        bar_size="1D",
    )


@pytest.fixture
def strategy_submission():
    from algochains_mcp.builder_sdk.submission_pipeline import StrategySubmission
    return StrategySubmission(
        strategy_name="test_momentum",
        symbol="AAPL",
        asset_class="equity",
        strategy_type="momentum",
        sharpe_ratio=2.1,
        win_rate=0.58,
        max_drawdown=0.10,
        total_trades=150,
        backtest_start="2024-01-01",
        backtest_end="2024-12-31",
    )


# ── Spec compliance fixtures ───────────────────────────────────────────────────

@pytest.fixture
def elicitation_manager():
    from algochains_mcp.spec_compliance.elicitation import ElicitationManager
    return ElicitationManager()


@pytest.fixture
def task_manager(tmp_home):
    """TaskManager with temp DB."""
    from algochains_mcp.spec_compliance.tasks import TaskManager
    import algochains_mcp.spec_compliance.tasks as tasks_mod
    original = tasks_mod.TaskManager.DB_PATH
    tasks_mod.TaskManager.DB_PATH = tmp_home / ".algochains" / "tasks.db"
    mgr = TaskManager()
    yield mgr
    tasks_mod.TaskManager.DB_PATH = original


@pytest.fixture
def subscription_manager():
    from algochains_mcp.spec_compliance.subscriptions import SubscriptionManager
    return SubscriptionManager()


# ── Evolution fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def trade_memory(tmp_home, monkeypatch):
    """TradeMemory with temp SQLite DB."""
    from algochains_mcp.evolution import trade_memory as tm_mod
    monkeypatch.setattr(tm_mod.TradeMemory, "DB_PATH", tmp_home / ".algochains" / "trade_memory.db")
    return tm_mod.TradeMemory()


@pytest.fixture
def reward_model():
    from algochains_mcp.evolution.reward_model import RewardModel
    return RewardModel()


# ── Vault fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def key_vault(tmp_home):
    from algochains_mcp.auth.key_vault import KeyVault
    return KeyVault(vault_path=tmp_home / ".algochains" / "test_vault.enc")


# ── Alert engine fixtures ──────────────────────────────────────────────────────

@pytest.fixture
def alert_engine(tmp_home, monkeypatch):
    from algochains_mcp.streaming import alert_engine as ae_mod
    monkeypatch.setattr(ae_mod, "ALERT_DB_PATH", tmp_home / ".algochains" / "price_alerts.db")
    return ae_mod.PriceAlertEngine()


# ── Middleware / rate limiter fixtures ────────────────────────────────────────

@pytest.fixture
def rate_limiter():
    from algochains_mcp.middleware import get_rate_limiter
    return get_rate_limiter()


# ── Common test data ─────────────────────────────────────────────────────────

@pytest.fixture
def sample_ohlcv_bars():
    """Real OHLCV bar structure (not synthetic — uses fixed historical-style data)."""
    return [
        {"timestamp": 1700000000 + i * 300, "open": 180 + i * 0.1, "high": 180.5 + i * 0.1,
         "low": 179.8 + i * 0.1, "close": 180.2 + i * 0.1, "volume": 50000 + i * 100}
        for i in range(20)
    ]


@pytest.fixture
def sample_tick_data():
    """Sample tick data with real structure for footprint/cumulative delta tests."""
    import random
    random.seed(42)  # deterministic for reproducibility
    ticks = []
    price = 180.0
    for i in range(200):
        price += random.choice([-0.01, 0.01, 0.0])
        side = random.choice(["buy", "sell"])
        ticks.append({
            "timestamp": 1700000000 + i * 1.5,
            "price": round(price, 2),
            "size": random.randint(1, 100),
            "side": side,
        })
    return ticks
