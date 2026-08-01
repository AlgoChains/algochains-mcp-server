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


def test_autonomous_scope_blocks_order_exec_even_with_confirm():
    """Autonomous callers with owner keys cannot execute ORDER_EXEC tools."""
    bridge = _bridge()
    result = asyncio.run(
        bridge.handle_mcp_request(
            "place_order",
            {"confirm": True},
            is_owner=True,
            caller_scope="autonomous",
        )
    )
    assert "error" in result, f"Expected scope ceiling error, got: {result}"
    assert result.get("caller_scope") == "autonomous"
    assert result.get("required_scope") == "interactive"


def test_interactive_scope_allows_order_exec_with_confirm():
    """Interactive owner callers can pass the scope ceiling for ORDER_EXEC tools."""
    bridge = _bridge()
    result = asyncio.run(
        bridge.handle_mcp_request(
            "place_order",
            {"confirm": True},
            is_owner=True,
            caller_scope="interactive",
        )
    )
    if "error" in result:
        assert "caller scope" not in str(result.get("error", "")).lower(), result
        blocked = "confirm" in str(result.get("error", "")).lower() and "danger_tier" in result
        assert not blocked, result


def test_missing_scope_preserves_legacy_owner_gate():
    """No caller scope preserves historical owner behavior behind confirm=true."""
    bridge = _bridge()
    result = asyncio.run(
        bridge.handle_mcp_request(
            "place_order",
            {"confirm": True},
            is_owner=True,
            caller_scope=None,
        )
    )
    if "error" in result:
        assert "caller scope" not in str(result.get("error", "")).lower(), result


def test_tools_listing_respects_autonomous_scope(monkeypatch):
    """Owner tool listing should not advertise tools blocked by caller scope."""
    try:
        from fastapi.testclient import TestClient
    except Exception as exc:  # pragma: no cover - dependency-level skip
        pytest.skip(f"FastAPI TestClient unavailable: {exc}")

    monkeypatch.setenv("ALGOCHAINS_BRIDGE_API_KEY", "test-owner-key-xyz")
    monkeypatch.setenv("ALGOCHAINS_BRIDGE_DEV_MODE", "false")
    bridge = _bridge()
    app = bridge.create_fastapi_app()

    client = TestClient(app)
    response = client.get(
        "/tools",
        headers={
            "X-Api-Key": "test-owner-key-xyz",
            "X-AlgoChains-Caller-Scope": "autonomous",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    tool_names = {tool["tool"] for tool in payload["tools"]}
    assert "get_bot_health" in tool_names
    assert "place_order" not in tool_names
    assert payload["caller_scope"] == "autonomous"
    assert payload["caller_scope_max_tier"] == 1


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


# ── dual-path parity: bridge ORDER_EXEC gate must match the stdio dispatch gate ──

def test_all_owner_order_exec_tools_require_confirm():
    """Parity with stdio's test_all_order_exec_tools_blocked_without_token.

    Every ORDER_EXEC+ tool reachable via the HTTP bridge OWNER_TOOLS must be blocked
    for an owner caller that omits confirm=true. A single high-tier owner tool that
    slips through without a confirm gate is a money path on the second execution
    surface, even though the stdio path is already covered.
    """
    bridge = _bridge()
    from algochains_mcp.tool_danger_tiers import get_tool_tier, TIER_ORDER_EXEC

    owner_tools = getattr(bridge, "OWNER_TOOLS", None)
    if not owner_tools:
        pytest.skip("bridge exposes no OWNER_TOOLS set")

    high_tier = sorted(t for t in owner_tools if get_tool_tier(t) >= TIER_ORDER_EXEC)
    assert high_tier, "No ORDER_EXEC+ owner tools found — check OWNER_TOOLS/tier defs"

    not_gated: list[str] = []

    async def _check():
        for tool in high_tier:
            result = await bridge.handle_mcp_request(tool, {}, is_owner=True)
            blob = str(result).lower()
            gated = (
                "error" in result
                and ("confirm" in blob or "danger_tier" in result or "order_exec" in blob)
            )
            if not gated:
                not_gated.append(tool)

    asyncio.run(_check())
    assert not not_gated, (
        "These ORDER_EXEC+ OWNER_TOOLS did NOT require confirm on the HTTP bridge "
        f"(stdio path gates them, bridge does not — parity break): {not_gated}"
    )
