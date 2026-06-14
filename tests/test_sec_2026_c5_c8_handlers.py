"""Direct handler gates for SEC-2026-C5–C8 (stdio path, not execute_dynamic_tool)."""
from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


@pytest.fixture(autouse=True)
def _handler_test_env(monkeypatch):
    """Full mode + clean guardrail state so handlers are reachable in isolation."""
    monkeypatch.setenv("OWNER_API_TOKEN", "test-owner-secret")
    monkeypatch.setenv("ALGOCHAINS_TOOL_MODE", "full")
    from algochains_mcp.trading_guardrails import get_guardrails

    g = get_guardrails()
    g._loop_detector._call_log.clear()
    g._loop_detector._hash_counts.clear()
    g._cb.clear()


def _parse(result):
    text = result[0].text if hasattr(result[0], "text") else str(result[0])
    return json.loads(text)


def test_get_broker_oauth_status_blocks_without_owner_token():
    import algochains_mcp.server as srv

    result = asyncio.run(srv.call_tool(
        "get_broker_oauth_status",
        {"broker": "tradovate", "user_id": "user-1"},
    ))
    data = _parse(result)
    assert "owner_token" in str(data).lower()


def test_generate_ide_config_redacts_without_owner_token():
    import algochains_mcp.server as srv

    result = asyncio.run(srv.call_tool("generate_ide_config", {"ide": "cursor"}))
    data = _parse(result)
    assert data.get("secrets_redacted") is True
    assert "owner_token" in data.get("warning", "")


def test_test_signal_propagation_requires_confirm():
    import algochains_mcp.server as srv

    result = asyncio.run(srv.call_tool(
        "test_signal_propagation",
        {"owner_token": "test-owner-secret"},
    ))
    data = _parse(result)
    assert data.get("required_arg") == "confirm=true" or "confirm=true" in str(data)


def test_list_support_tickets_blocks_without_owner_token():
    import algochains_mcp.server as srv

    result = asyncio.run(srv.call_tool("list_support_tickets", {}))
    data = _parse(result)
    assert "owner_token" in str(data).lower()
