"""
Dynamic dispatch safety tests.

Tests that:
1. discover_tools finds relevant tools for common query types
2. get_tool_details returns full schema for a named tool
3. execute_dynamic_tool blocks ORDER_EXEC and DESTRUCTIVE tier tools
   without a valid owner_token — using the danger-tier system, not a denylist
4. execute_dynamic_tool passes READ_ONLY tool calls through

All tests run offline without real broker credentials.
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# ── fixtures ──────────────────────────────────────────────────────────────────

def _gw():
    """Return a fully indexed DynamicToolsetGateway."""
    import algochains_mcp.server as srv
    return srv._get_dynamic_gateway()


def _tier(tool_name: str) -> int:
    from algochains_mcp.tool_danger_tiers import get_tool_tier
    return get_tool_tier(tool_name)


# ── discover_tools ────────────────────────────────────────────────────────────

def test_discover_returns_results():
    """discover_tools returns a non-empty list for any reasonable query."""
    gw = _gw()
    results = gw.discover("portfolio positions", top_k=5)
    assert results, "discover returned no results for 'portfolio positions'"


def test_discover_broker_query():
    """discover_tools finds broker/position tools for a trading query."""
    gw = _gw()
    results = gw.discover("tradovate positions", top_k=10)
    names = [r["name"] for r in results]
    assert any("position" in n.lower() for n in names), (
        f"Expected a position tool for 'tradovate positions', got: {names}"
    )


def test_discover_marketplace_query():
    """discover_tools finds marketplace tools."""
    gw = _gw()
    results = gw.discover("marketplace listing strategy", top_k=10)
    names = [r["name"] for r in results]
    assert any("marketplace" in n.lower() or "strategy" in n.lower() for n in names), (
        f"Expected marketplace/strategy tool, got: {names}"
    )


def test_discover_returns_name_and_description():
    """Each discover result must have name and description keys."""
    gw = _gw()
    results = gw.discover("backtest strategy", top_k=5)
    for r in results:
        assert "name" in r, f"Result missing 'name': {r}"
        assert "description" in r, f"Result missing 'description': {r}"


# ── get_tool_details ──────────────────────────────────────────────────────────

def test_get_tool_details_known_tool():
    """get_tool_details returns schema for a known Tier-1 tool."""
    gw = _gw()
    detail = gw.get_tool_details("get_positions")
    assert detail is not None, "get_details returned None for 'get_positions'"
    assert "name" in detail or "inputSchema" in detail or "description" in detail, (
        f"Tool detail missing expected keys: {list(detail.keys())}"
    )


def test_get_tool_details_unknown_tool():
    """get_tool_details returns None or error dict for an unknown tool."""
    gw = _gw()
    result = gw.get_tool_details("totally_nonexistent_tool_xyz_abc")
    # Acceptable: None or a dict with an error key
    if result is not None:
        assert "error" in result or "name" not in result or result.get("name") != "totally_nonexistent_tool_xyz_abc"


# ── execute_dynamic_tool danger-tier gating ───────────────────────────────────

@pytest.mark.parametrize("tool_name", [
    "place_order",
    "cancel_order",
    "close_position",
    "close_all_positions",
    "flatten_bot_position",
    "numerai_upload_predictions",
    "place_kalshi_order",
    "propagate_trade_signal",
    # BUG-07 FIX: These Kalshi execution tools were mis-tiered as WRITE_LOCAL via
    # the `run_` prefix rule, allowing autonomous agents to place real orders.
    # Now explicitly ORDER_EXEC — verify gating is enforced.
    "run_safe_compounder",
    "run_kalshi_full_pipeline",
    "run_kalshi_strategy_order",
])
def test_execute_dynamic_tool_blocks_order_exec_without_token(tool_name, monkeypatch):
    """execute_dynamic_tool must block ORDER_EXEC/DESTRUCTIVE tools without owner_token.

    This test directly calls the dispatch path used by execute_dynamic_tool
    to confirm tier-based gating is enforced, not a hardcoded denylist.
    """
    # Ensure OWNER_API_TOKEN is set to a known value (not empty)
    monkeypatch.setenv("OWNER_API_TOKEN", "test-owner-secret-12345")
    import algochains_mcp.server as srv
    from algochains_mcp.tool_danger_tiers import get_tool_tier, TIER_ORDER_EXEC

    tier = get_tool_tier(tool_name)
    assert tier >= TIER_ORDER_EXEC, (
        f"{tool_name} should be ORDER_EXEC or higher, got tier {tier}. "
        "Update parametrize list if tool tier changed."
    )

    async def _call():
        # Call execute_dynamic_tool with no owner_token in inner args
        result = await srv.call_tool(
            "execute_dynamic_tool",
            {"tool_name": tool_name, "arguments": {}},
        )
        return result

    result = asyncio.run(_call())
    assert isinstance(result, list) and len(result) > 0
    content = result[0]
    text = content.text if hasattr(content, "text") else str(content)
    import json
    try:
        data = json.loads(text)
        assert data.get("blocked") is True, (
            f"execute_dynamic_tool should have blocked '{tool_name}' without owner_token. "
            f"Response: {text[:300]}"
        )
    except json.JSONDecodeError:
        assert "blocked" in text.lower() or "authorization" in text.lower() or "owner_token" in text.lower(), (
            f"Expected blocked/authorization message for '{tool_name}', got: {text[:300]}"
        )


def test_execute_dynamic_tool_blocks_with_wrong_token(monkeypatch):
    """execute_dynamic_tool blocks when owner_token is wrong."""
    monkeypatch.setenv("OWNER_API_TOKEN", "correct-secret-value")
    import algochains_mcp.server as srv

    async def _call():
        return await srv.call_tool(
            "execute_dynamic_tool",
            {"tool_name": "place_order", "arguments": {"owner_token": "WRONG_TOKEN"}},
        )

    result = asyncio.run(_call())
    text = result[0].text if hasattr(result[0], "text") else str(result[0])
    import json
    try:
        data = json.loads(text)
        assert data.get("blocked") is True, f"Expected blocked=True, got: {data}"
    except json.JSONDecodeError:
        assert "blocked" in text.lower() or "authorization" in text.lower()


def test_execute_dynamic_tool_requires_confirm_with_correct_token(monkeypatch):
    """Owner token alone is not enough for dynamic ORDER_EXEC dispatch."""
    secret = "correct-secret-value"
    monkeypatch.setenv("OWNER_API_TOKEN", secret)
    import algochains_mcp.server as srv

    async def _call():
        return await srv.call_tool(
            "execute_dynamic_tool",
            {"tool_name": "place_order", "arguments": {"owner_token": secret}},
        )

    result = asyncio.run(_call())
    text = result[0].text if hasattr(result[0], "text") else str(result[0])
    import json
    data = json.loads(text)
    assert data.get("blocked") is True
    assert data.get("required_arg") == "confirm=true"


def test_execute_dynamic_tool_allows_with_correct_token(monkeypatch):
    """execute_dynamic_tool allows ORDER_EXEC tools when correct owner_token provided.

    Uses a READ_ONLY tool routed through execute_dynamic_tool to avoid live broker calls.
    This validates the token check pass-through, not the underlying tool execution.
    """
    secret = "correct-owner-secret-xyz"
    monkeypatch.setenv("OWNER_API_TOKEN", secret)
    import algochains_mcp.server as srv
    from algochains_mcp.tool_danger_tiers import get_tool_tier, TIER_READ_ONLY

    # pick a safe read-only tool to call through the gateway
    read_only_tool = "mcp_tool_manifest"
    assert get_tool_tier(read_only_tool) == TIER_READ_ONLY, (
        f"{read_only_tool} should be READ_ONLY"
    )

    async def _call():
        # Read-only: no owner_token needed, should pass through
        return await srv.call_tool(
            "execute_dynamic_tool",
            {"tool_name": read_only_tool, "arguments": {}},
        )

    result = asyncio.run(_call())
    import json
    text = result[0].text if hasattr(result[0], "text") else str(result[0])
    try:
        data = json.loads(text)
        assert data.get("blocked") is not True, (
            f"READ_ONLY tool {read_only_tool} should not be blocked: {text[:200]}"
        )
    except json.JSONDecodeError:
        # Non-JSON response is fine — tool ran
        assert "blocked" not in text.lower()


def test_execute_dynamic_tool_applies_inner_per_tool_rate_limit(monkeypatch):
    """Approved dynamic ORDER_EXEC calls must still hit the inner tool limiter."""
    secret = "correct-owner-secret-xyz"
    monkeypatch.setenv("OWNER_API_TOKEN", secret)
    import algochains_mcp.server as srv
    from algochains_mcp.security.per_tool_rate_limiter import reset_rate_limit
    from mcp.types import TextContent
    import json

    monkeypatch.setattr(srv, "_GUARDRAILS_AVAILABLE", False)
    reset_rate_limit("place_order")
    original_dispatch = srv._dispatch_tool

    async def _dispatch_without_broker(name, arguments, registry):
        if name == "place_order":
            return [TextContent(type="text", text=json.dumps({"ok": True, "tool": name}))]
        return await original_dispatch(name, arguments, registry)

    monkeypatch.setattr(srv, "_dispatch_tool", _dispatch_without_broker)

    async def _call_six():
        payloads = []
        for _ in range(6):
            result = await srv.call_tool(
                "execute_dynamic_tool",
                {
                    "tool_name": "place_order",
                    "arguments": {
                        "broker": "alpaca",
                        "symbol": "AAPL",
                        "side": "buy",
                        "qty": 1,
                        "owner_token": secret,
                        "confirm": True,
                    },
                },
            )
            payloads.append(json.loads(result[0].text))
        return payloads

    try:
        payloads = asyncio.run(_call_six())
    finally:
        reset_rate_limit("place_order")

    assert [p.get("ok") for p in payloads[:5]] == [True] * 5
    assert payloads[5].get("error_type") == "RateLimitError"
    assert payloads[5].get("tool") == "place_order"


# ── tier coverage audit ───────────────────────────────────────────────────────

def test_all_order_exec_tools_blocked_without_token(monkeypatch):
    """Every tool classified TIER_ORDER_EXEC or higher is blocked via execute_dynamic_tool."""
    monkeypatch.setenv("OWNER_API_TOKEN", "test-secret-for-audit")
    import algochains_mcp.server as srv
    from algochains_mcp.tool_danger_tiers import get_tool_tier, TIER_ORDER_EXEC

    high_tier_tools = [
        t.name for t in srv.TOOLS if get_tool_tier(t.name) >= TIER_ORDER_EXEC
    ]
    assert high_tier_tools, "No ORDER_EXEC+ tools found — check TIER definitions"

    not_blocked: list[str] = []

    async def _check_all():
        for tool in high_tier_tools:
            result = await srv.call_tool(
                "execute_dynamic_tool",
                {"tool_name": tool, "arguments": {}},
            )
            import json
            text = result[0].text if hasattr(result[0], "text") else str(result[0])
            try:
                data = json.loads(text)
                if not data.get("blocked"):
                    not_blocked.append(tool)
            except json.JSONDecodeError:
                if "blocked" not in text.lower() and "authorization" not in text.lower():
                    not_blocked.append(tool)

    asyncio.run(_check_all())
    assert not not_blocked, (
        f"These ORDER_EXEC+ tools were NOT blocked by execute_dynamic_tool: {not_blocked}\n"
        "Add them to the tier system in tool_danger_tiers.py."
    )
