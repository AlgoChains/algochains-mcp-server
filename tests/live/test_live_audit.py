"""
Live audit tests for AlgoChains MCP Server.

These tests contact real broker/data APIs and are excluded from default pytest
discovery. Run explicitly after sourcing real credentials:

    source .env && PYTEST_LIVE=1 pytest tests/live/test_live_audit.py -v
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

os.environ.setdefault("ALGOCHAINS_TOOL_MODE", "full")

_ALPACA_KEY = os.environ.get("ALPACA_API_KEY", "")
_ALPACA_SECRET = os.environ.get("ALPACA_SECRET_KEY", "")
_POLYGON_KEY = os.environ.get("POLYGON_API_KEY", "")
_MASSIVE_KEY = os.environ.get("MASSIVE_API_KEY", "")
_FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "")

_LIVE_ENABLED = os.environ.get("PYTEST_LIVE", "").lower() in {"1", "true", "yes"}
_HAVE_ALPACA = bool(_ALPACA_KEY and _ALPACA_SECRET)
_HAVE_POLYGON_OR_FINNHUB = bool(_POLYGON_KEY or _FINNHUB_KEY)
_HAVE_MASSIVE = bool(_MASSIVE_KEY)


pytestmark = pytest.mark.skipif(
    not _LIVE_ENABLED,
    reason="Live tests require PYTEST_LIVE=1 and real broker/data credentials.",
)


@pytest.mark.skipif(not _HAVE_ALPACA, reason="ALPACA_API_KEY / ALPACA_SECRET_KEY not set")
def test_alpaca_paper_live():
    """Live Alpaca paper account: connect, positions, orders."""
    async def _run():
        from algochains_mcp.brokers.registry import BrokerRegistry
        from algochains_mcp.config import load_config

        cfg = load_config()
        registry = BrokerRegistry(cfg)
        results = await registry.connect_all()
        if "alpaca" not in registry.list_available():
            pytest.xfail(
                f"Alpaca not available after connect (may be network/cred issue). Results: {results}"
            )
        broker = registry.get("alpaca")
        try:
            acct = await broker.get_account()
            assert acct, "get_account returned empty"
            positions = await broker.get_positions()
            assert isinstance(positions, list)
            orders = await broker.get_orders()
            assert isinstance(orders, list)
        except Exception as exc:
            pytest.xfail(f"Live Alpaca call failed (non-blocking): {exc}")

    asyncio.run(_run())


@pytest.mark.skipif(
    not _HAVE_POLYGON_OR_FINNHUB,
    reason="POLYGON_API_KEY and FINNHUB_API_KEY both absent",
)
def test_data_providers_live():
    """Live data provider health checks."""
    async def _run():
        from algochains_mcp.data_providers.registry import DataProviderRegistry

        reg = DataProviderRegistry()
        results = await reg.health_check_all()
        failed = {k: v for k, v in results.items() if not v}
        if failed:
            pytest.xfail(f"Data providers degraded (non-blocking): {list(failed)}")

    asyncio.run(_run())


@pytest.mark.skipif(not _HAVE_MASSIVE, reason="MASSIVE_API_KEY not set")
def test_massive_bad_sql_returns_error():
    """Massive provider handles invalid SQL gracefully."""
    async def _run():
        import algochains_mcp.server as srv

        eng = await srv._get_massive_provider()
        try:
            result = await eng.query_data("SELECT * FROM nonexistent_table_xyz")
            assert isinstance(result, dict) and "error" in result, (
                f"Expected error dict, got: {str(result)[:200]}"
            )
        except Exception as exc:
            assert (
                "sql" in str(exc).lower()
                or "table" in str(exc).lower()
                or "query" in str(exc).lower()
            ), f"Unexpected exception type for bad SQL: {type(exc).__name__}: {exc}"

    asyncio.run(_run())
