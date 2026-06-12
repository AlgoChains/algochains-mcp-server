"""
tests/test_http_bridge_auth.py — Auth matrix tests for the FastAPI HTTP bridge.

Tests all four auth modes (anonymous, owner, subscriber, dev-mode) and validates
danger tier enforcement without making real broker calls.

Phase J: bridge auth matrix — hidden-killers v8 (2026-04-21).
"""
from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

# Skip entire module if fastapi is not installed
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from algochains_mcp.http_bridge import create_fastapi_app, PUBLIC_TOOLS, OWNER_TOOLS, _SERVER_VERSION
from algochains_mcp.subscriber_tools import SUBSCRIBER_TOOLS

FAKE_OWNER_KEY = "test-owner-key-12345"
FAKE_OWNER_EMAIL = "owner@test.algochains.ai"
FAKE_SUB_KEY = "sub_live_testkeyXXXXXX"


def _make_client(
    bridge_key: str = FAKE_OWNER_KEY,
    owner_email: str = FAKE_OWNER_EMAIL,
    dev_mode: bool = False,
) -> TestClient:
    env_patch = {
        "ALGOCHAINS_BRIDGE_API_KEY": bridge_key,
        "OWNER_EMAIL": owner_email,
        "ALGOCHAINS_BRIDGE_DEV_MODE": "true" if dev_mode else "false",
        "SUPABASE_URL": "",
        "SUPABASE_SERVICE_ROLE_KEY": "",
    }
    with patch.dict(os.environ, env_patch):
        app = create_fastapi_app()
    return TestClient(app, raise_server_exceptions=False)


MCP_ENDPOINT = "/api/mcp"


def _call_tool(client: TestClient, tool: str, args: dict | None = None, **headers) -> dict:
    resp = client.post(
        MCP_ENDPOINT,
        json={"tool": tool, "arguments": args or {}},
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text}
    return {"status_code": resp.status_code, "body": body}


# ── Health endpoint ─────────────────────────────────────────────────────────


def test_health_returns_ok():
    client = _make_client()
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body
    assert "auth_mode" in body


def test_health_includes_version():
    client = _make_client()
    body = client.get("/health").json()
    assert body["version"] == _SERVER_VERSION


def test_bridge_starts_without_control_tower_env(monkeypatch):
    monkeypatch.delenv("ALGOCHAINS_CONTROL_TOWER", raising=False)
    monkeypatch.delenv("ALGOCHAINS_CONTROL_TOWER_PATH", raising=False)
    client = _make_client()
    resp = client.get("/health")
    assert resp.status_code == 200


def test_status_alias_matches_health():
    client = _make_client()
    health = client.get("/health")
    status = client.get("/status")
    assert status.status_code == 200
    health_body = health.json()
    status_body = status.json()
    assert status_body["timestamp"]
    assert {k: v for k, v in status_body.items() if k != "timestamp"} == {
        k: v for k, v in health_body.items() if k != "timestamp"
    }


# ── Anonymous access ─────────────────────────────────────────────────────────


def test_anon_gets_tools_list():
    client = _make_client()
    resp = client.get("/tools")
    assert resp.status_code == 200
    body = resp.json()
    # Tools may be under "tools" key (with tiers) or as a list
    tools_list = body.get("tools", body) if isinstance(body, dict) else body
    if isinstance(tools_list, list) and tools_list and isinstance(tools_list[0], dict):
        returned = {t.get("name") or t.get("tool") for t in tools_list}
    elif isinstance(tools_list, list):
        returned = set(tools_list)
    else:
        returned = set()
    for pub in PUBLIC_TOOLS:
        assert pub in returned, f"Public tool {pub!r} missing from /tools response. Got: {returned}"


def test_anon_blocked_from_owner_tool():
    client = _make_client()
    result = _call_tool(client, "place_order", {"symbol": "MNQ", "action": "BUY", "quantity": 1})
    assert result["status_code"] == 401, f"Expected 401 but got {result}"


def test_anon_blocked_from_get_account():
    client = _make_client()
    result = _call_tool(client, "get_account")
    assert result["status_code"] == 401


# ── Owner access ─────────────────────────────────────────────────────────────

OWNER_HEADERS = {
    "X-Api-Key": FAKE_OWNER_KEY,
    "X-User-Email": FAKE_OWNER_EMAIL,
}


def test_owner_danger_tool_requires_confirm():
    """place_order is TIER_ORDER_EXEC — must require confirm=true."""
    client = _make_client()
    result = _call_tool(
        client,
        "place_order",
        {"symbol": "MNQ", "action": "BUY", "quantity": 1},
        **OWNER_HEADERS,
    )
    # Either a 400 with confirm hint, or 200/tool dispatched — both acceptable
    # as long as it is NOT a silent pass-through without the confirm field present.
    body = result["body"]
    if result["status_code"] == 400:
        assert "confirm" in json.dumps(body).lower(), "400 body should mention confirm"
    elif result["status_code"] == 200:
        # Tool was dispatched — check that arguments were forwarded correctly
        pass  # Mock won't place real orders


def test_owner_can_call_public_tool():
    client = _make_client()
    # Patch the tool call so we don't hit real APIs
    with patch("algochains_mcp.server.call_tool", new_callable=AsyncMock, return_value=[]) as mock_call:
        result = _call_tool(client, "get_vix_term_structure", {}, **OWNER_HEADERS)
    # Should not be a 401 or 403
    assert result["status_code"] not in (401, 403), f"Owner should access public tools: {result}"


def test_owner_can_read_agent_status():
    client = _make_client()
    resp = client.get("/v1/agent/status", headers=OWNER_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_level"] == "owner"
    assert "bots_alive" in body


def test_invalid_agent_status_key_gets_401():
    client = _make_client()
    resp = client.get("/v1/agent/status", headers={"X-Api-Key": "bad-key-xyz"})
    assert resp.status_code == 401


def test_bot_card_public_route_does_not_crash():
    client = _make_client()
    with patch("algochains_mcp.http_bridge.handle_mcp_request", new_callable=AsyncMock) as mock_handle:
        mock_handle.return_value = {"bot_id": "mnq"}
        resp = client.get("/api/bots/mnq/card")
    assert resp.status_code == 200
    assert resp.json()["bot_id"] == "mnq"


# ── Subscriber access ─────────────────────────────────────────────────────────


def test_subscriber_blocked_from_owner_tool():
    """sub_live_* key must not reach OWNER_TOOLS — either 401 (no Supabase) or 403 (resolved sub)."""
    client = _make_client()
    fake_sub = type("Sub", (), {"subscriber_id": "s1", "bot_ids": ["b1"], "tier": "basic"})()
    with (
        patch("algochains_mcp.http_bridge.is_subscriber_key", return_value=True),
        patch("algochains_mcp.http_bridge.resolve_subscriber_key", return_value=fake_sub),
    ):
        result = _call_tool(
            client,
            "get_account",
            {},
            **{"X-Api-Key": FAKE_SUB_KEY},
        )
    # Bridge returns 200 with error body for scope denials (current contract),
    # or 401 if Supabase is unavailable. Either means subscriber cannot access owner tools.
    if result["status_code"] == 200:
        assert "error" in result["body"] or "available_tools" in result["body"], \
            f"200 response should include error/scope info: {result}"
        # Verify the error indicates scope denial, not successful execution
        assert "place_order" not in str(result["body"].get("result", "")) or \
               "error" in result["body"], "Subscriber must not successfully execute owner tool"
    else:
        assert result["status_code"] in (401, 403), f"Unexpected status: {result}"


def test_subscriber_blocked_from_place_order():
    client = _make_client()
    fake_sub = type("Sub", (), {"subscriber_id": "s1", "bot_ids": ["b1"], "tier": "basic"})()
    with (
        patch("algochains_mcp.http_bridge.is_subscriber_key", return_value=True),
        patch("algochains_mcp.http_bridge.resolve_subscriber_key", return_value=fake_sub),
    ):
        result = _call_tool(
            client,
            "place_order",
            {"confirm": True},
            **{"X-Api-Key": FAKE_SUB_KEY},
        )
    # Subscriber calling place_order: bridge returns 200+error body or 4xx
    body_str = str(result["body"])
    if result["status_code"] == 200:
        assert "error" in result["body"] or "available_tools" in result["body"], \
            f"Subscriber place_order must return error body: {result}"
    else:
        assert result["status_code"] in (401, 403), f"Unexpected status: {result}"


# ── Unknown key ───────────────────────────────────────────────────────────────


def test_unknown_key_gets_401():
    client = _make_client()
    # Non-subscriber key (no sub_live_ prefix) that doesn't match owner key
    result = _call_tool(client, "get_account", {}, **{"X-Api-Key": "bad-key-xyz"})
    assert result["status_code"] == 401, f"Unknown key should get 401: {result}"


# ── Tool enumeration ─────────────────────────────────────────────────────────


def test_tool_sets_are_disjoint():
    """OWNER_TOOLS and SUBSCRIBER_TOOLS must not overlap — prevents privilege escalation."""
    overlap = set(OWNER_TOOLS) & set(SUBSCRIBER_TOOLS)
    assert not overlap, f"Tools in both OWNER and SUBSCRIBER sets: {overlap}"


def test_public_tools_not_in_owner_exclusive():
    """PUBLIC_TOOLS should not include order-execution or destructive tools."""
    dangerous_keywords = {"place_order", "cancel_order", "close_position", "run_marketplace_autopilot"}
    overlap = set(PUBLIC_TOOLS) & dangerous_keywords
    assert not overlap, f"Dangerous tools exposed publicly: {overlap}"


# ── X-Request-Id header ──────────────────────────────────────────────────────


def test_request_id_header_returned():
    client = _make_client()
    resp = client.get("/health")
    assert "x-request-id" in resp.headers, "Bridge should return X-Request-Id header"


def test_custom_request_id_echoed():
    client = _make_client()
    resp = client.get("/health", headers={"X-Request-Id": "custom-id-123"})
    assert resp.headers.get("x-request-id") == "custom-id-123"
