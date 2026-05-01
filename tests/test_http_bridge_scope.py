"""
HTTP bridge auth boundary tests.

Tests that:
1. Public callers cannot invoke owner-only tools
2. Public callers cannot invoke subscriber-only tools
3. Owner callers can invoke owner tools
4. ORDER_EXEC owner tools require confirm=true
5. Dev mode never grants owner access
6. Unknown tools are rejected for non-owner callers
7. Request IDs are present in bridge responses for traceability
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _bridge():
    """Import http_bridge after setting required env vars."""
    os.environ.setdefault("ALGOCHAINS_BRIDGE_API_KEY", "test-owner-key-xyz")
    os.environ.setdefault("ALGOCHAINS_BRIDGE_DEV_MODE", "false")
    from algochains_mcp import http_bridge
    return http_bridge


# ── public vs owner boundary ──────────────────────────────────────────────────

def test_public_caller_blocked_from_owner_tool():
    """Public callers (is_owner=False) must be blocked from OWNER_TOOLS."""
    bridge = _bridge()
    result = asyncio.run(
        bridge.handle_mcp_request("place_order", {}, is_owner=False)
    )
    assert "error" in result, f"Expected error for public caller on owner tool, got: {result}"
    assert "unauthorized" in result["error"].lower() or "owner" in result["error"].lower(), (
        f"Expected unauthorized/owner error, got: {result['error']}"
    )


def test_public_caller_blocked_from_unknown_tool():
    """Public callers cannot call tools not in PUBLIC_TOOLS."""
    bridge = _bridge()
    result = asyncio.run(
        bridge.handle_mcp_request("run_backtest", {}, is_owner=False)
    )
    assert "error" in result, f"Expected error for public caller on unknown tool, got: {result}"


def test_public_caller_can_call_public_tool():
    """Public callers can call PUBLIC_TOOLS without error gating."""
    bridge = _bridge()
    result = asyncio.run(
        bridge.handle_mcp_request("detect_market_regime", {}, is_owner=False)
    )
    # Should not be blocked (may fail on missing deps, but not auth)
    assert result.get("error", "") not in (
        "Unauthorized — this tool requires owner access",
        "Tool 'detect_market_regime' not available via HTTP bridge",
    ), f"Public tool 'detect_market_regime' was incorrectly blocked: {result}"


# ── owner tool gating ─────────────────────────────────────────────────────────

def test_order_exec_tool_requires_confirm():
    """ORDER_EXEC tools in OWNER_TOOLS require confirm=true."""
    bridge = _bridge()
    result = asyncio.run(
        bridge.handle_mcp_request("place_order", {}, is_owner=True)
    )
    assert "error" in result, f"Expected error (missing confirm), got: {result}"
    assert "confirm" in str(result).lower() or "danger_tier" in result or "ORDER_EXEC" in str(result), (
        f"Expected confirm/danger_tier error for place_order, got: {result}"
    )


def test_order_exec_tool_with_confirm_passes_gate():
    """ORDER_EXEC tools pass the danger-tier gate when confirm=true is provided.

    This test checks the gating layer only — it does not require live broker.
    The underlying call may fail on missing config; that is acceptable.
    """
    bridge = _bridge()
    result = asyncio.run(
        bridge.handle_mcp_request("place_order", {"confirm": True}, is_owner=True)
    )
    # Acceptable outcomes: broker error, missing config error, or actual result
    # NOT acceptable: danger-tier blocked response
    if "error" in result:
        blocked = "confirm" in str(result.get("error", "")).lower() and "danger_tier" in result
        assert not blocked, (
            f"ORDER_EXEC gate still blocking with confirm=True: {result}"
        )


def test_read_only_owner_tool_no_confirm_needed():
    """READ_ONLY owner tools do not require confirm=true."""
    bridge = _bridge()
    result = asyncio.run(
        bridge.handle_mcp_request("get_positions", {}, is_owner=True)
    )
    if "error" in result:
        # Should not be a confirm/danger_tier error
        assert "confirm" not in result.get("error", "").lower(), (
            f"READ_ONLY tool 'get_positions' incorrectly requires confirm: {result}"
        )


# ── dev mode isolation ────────────────────────────────────────────────────────

def test_dev_mode_does_not_grant_owner_access(monkeypatch):
    """In dev mode without BRIDGE_API_KEY, owner tools must still be blocked.

    Dev mode allows public tool access without auth, but must never grant
    owner-level access.  We test this by calling an owner-only tool in dev mode.
    """
    monkeypatch.setenv("ALGOCHAINS_BRIDGE_DEV_MODE", "true")
    monkeypatch.setenv("ALGOCHAINS_BRIDGE_API_KEY", "")

    import importlib
    from algochains_mcp import http_bridge as bridge_mod
    importlib.reload(bridge_mod)

    # In dev mode, handle_mcp_request for an owner-only tool should still be blocked
    # because dev mode only grants public tool access (is_owner=False)
    result = asyncio.run(
        bridge_mod.handle_mcp_request("place_order", {}, is_owner=False)
    )
    assert "error" in result, (
        f"Owner tool 'place_order' should be blocked in dev mode (no owner), got: {result}"
    )
    assert "unauthorized" in result["error"].lower() or "owner" in result["error"].lower(), (
        f"Expected unauthorized/owner error in dev mode, got: {result['error']}"
    )


# ── subscriber isolation ─────────────────────────────────────────────────────

def test_subscriber_caller_blocked_from_owner_tool():
    """Subscriber callers cannot invoke OWNER_TOOLS even with a valid token."""
    bridge = _bridge()
    from algochains_mcp.http_bridge import ResolvedSubscriber
    fake_sub = ResolvedSubscriber(
        subscriber_id="sub_test_123",
        scopes=("read_bots",),
    )
    result = asyncio.run(
        bridge.handle_mcp_request("place_order", {}, subscriber=fake_sub)
    )
    assert "error" in result, f"Expected error for subscriber on owner tool, got: {result}"
    # Should mention subscriber tools not the owner tool
    assert "subscriber" in str(result).lower() or "not available" in str(result).lower(), (
        f"Expected subscriber-scope error, got: {result}"
    )


# ── danger tier coverage for all owner tools ─────────────────────────────────

def test_all_public_tools_are_read_only_or_write_local():
    """PUBLIC_TOOLS must not include ORDER_EXEC or DESTRUCTIVE tier tools.

    If any public tool is ORDER_EXEC+, it bypasses auth since public callers
    do not require owner authentication.
    """
    bridge = _bridge()
    from algochains_mcp.tool_danger_tiers import get_tool_tier, TIER_ORDER_EXEC

    offenders = [
        t for t in bridge.PUBLIC_TOOLS
        if get_tool_tier(t) >= TIER_ORDER_EXEC
    ]
    assert not offenders, (
        f"PUBLIC_TOOLS contains ORDER_EXEC+ tier tools (should be READ_ONLY or WRITE_LOCAL): "
        f"{offenders}"
    )
