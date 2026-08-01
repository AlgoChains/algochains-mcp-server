"""
Latency and payload regression tests.

Verifies:
1. Server cold-start import time is under 2 seconds
2. BM25 index build time is under 500ms
3. discover_tools (warm) latency is under 50ms
4. Tradovate get_orders respects the limit cap (no unbounded payload)
5. Tradovate get_fills returns empty list when called without filters (safety guard)
6. tool_manifest build completes under 1 second
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# ── cold-start latency ────────────────────────────────────────────────────────

# Thresholds (local, not CI broker-gated)
IMPORT_BUDGET_MS = 2000   # server.py should import within 2s on any dev machine
BM25_BUDGET_MS   = 500    # BM25 index build for 533 tools
DISCOVER_BUDGET_MS = 50   # warm discover call (BM25 query)
MANIFEST_BUDGET_MS = 1000 # tool manifest build


def test_server_import_time():
    """server.py import must complete under budget."""
    t0 = time.monotonic()
    import algochains_mcp.server  # noqa: F401
    elapsed_ms = (time.monotonic() - t0) * 1000
    assert elapsed_ms < IMPORT_BUDGET_MS, (
        f"server.py import took {elapsed_ms:.0f}ms — exceeds {IMPORT_BUDGET_MS}ms budget. "
        "Profile with: python3 -c 'import cProfile; cProfile.run(\"import algochains_mcp.server\")'"
    )
    print(f"server import: {elapsed_ms:.0f}ms ✅")


def test_bm25_index_build_time():
    """BM25 index build for 533 tools must be under budget."""
    import algochains_mcp.server as srv
    # Reset gateway to measure cold build
    import algochains_mcp.server
    algochains_mcp.server._dynamic_gateway = None  # type: ignore[attr-defined]

    t0 = time.monotonic()
    gw = srv._get_dynamic_gateway()
    elapsed_ms = (time.monotonic() - t0) * 1000
    assert elapsed_ms < BM25_BUDGET_MS, (
        f"BM25 index build took {elapsed_ms:.0f}ms — exceeds {BM25_BUDGET_MS}ms budget."
    )
    print(f"BM25 build: {elapsed_ms:.0f}ms ✅")


def test_discover_warm_latency():
    """discover_tools (warm index) must respond under budget."""
    import algochains_mcp.server as srv
    gw = srv._get_dynamic_gateway()  # warm
    t0 = time.monotonic()
    results = gw.discover("tradovate order positions", top_k=5)
    elapsed_ms = (time.monotonic() - t0) * 1000
    assert results, "discover returned empty results"
    assert elapsed_ms < DISCOVER_BUDGET_MS, (
        f"discover() took {elapsed_ms:.1f}ms — exceeds {DISCOVER_BUDGET_MS}ms budget."
    )
    print(f"discover warm: {elapsed_ms:.1f}ms ✅")


def test_manifest_build_time():
    """tool_manifest.build_manifest() must complete under budget."""
    import algochains_mcp.server as srv
    from algochains_mcp import tool_manifest
    t0 = time.monotonic()
    manifest = tool_manifest.build_manifest(
        tool_names=[t.name for t in srv.TOOLS_ANNOTATED],
        tier1_names={t.name for t in srv.TOOLS_TIER1},
        tool_mode="full",
    )
    elapsed_ms = (time.monotonic() - t0) * 1000
    assert elapsed_ms < MANIFEST_BUDGET_MS, (
        f"tool_manifest.build_manifest() took {elapsed_ms:.0f}ms — exceeds {MANIFEST_BUDGET_MS}ms budget."
    )
    assert manifest.get("tools"), "Manifest returned no tools"
    print(f"manifest build: {elapsed_ms:.0f}ms ✅")


# ── payload caps ──────────────────────────────────────────────────────────────

def test_get_orders_respects_limit():
    """TradovateConnector.get_orders respects the limit parameter.

    Creates a mock REST response with 500 orders and verifies that
    get_orders(limit=50) returns at most 50 regardless of response size.
    """
    from algochains_mcp.brokers.tradovate import TradovateConnector, TradovateConfig
    from unittest.mock import AsyncMock, MagicMock

    cfg = TradovateConfig(access_token="fake", env="demo")
    conn = TradovateConnector(cfg)

    # Fake 500 raw orders with minimal required fields
    fake_orders = [
        {"id": i, "ordStatus": "Working", "contractId": 1, "action": "Buy",
         "ordType": "Market", "qty": 1, "filledQty": 0, "avgFillPrice": 0}
        for i in range(500)
    ]

    # Mock the internal _get call
    async def _mock_get(path, *args, **kwargs):
        if path == "/order/list":
            return fake_orders
        if path == "/contract/item":
            return {"name": "MNQZ5"}
        return []

    conn._get = _mock_get  # type: ignore

    result = asyncio.run(conn.get_orders(limit=50))
    assert len(result) <= 50, (
        f"get_orders(limit=50) returned {len(result)} orders — limit not respected"
    )


def test_get_fills_safe_without_filters():
    """get_fills with no filters returns empty list (prevents full-history dump)."""
    from algochains_mcp.brokers.tradovate import TradovateConnector, TradovateConfig

    cfg = TradovateConfig(access_token="fake", env="demo")
    conn = TradovateConnector(cfg)

    # Mock _get to simulate a large fills list
    async def _mock_get(path, *args, **kwargs):
        return [{"id": i, "contractId": 1, "orderId": i, "timestamp": "2026-01-01T00:00:00Z"} for i in range(1000)]

    conn._get = _mock_get  # type: ignore

    # No filters → must return empty (safety guard against full dump)
    result = asyncio.run(conn.get_fills())
    assert result == [], (
        f"get_fills() without filters returned {len(result)} fills — "
        "expected [] to prevent full-history dump"
    )
