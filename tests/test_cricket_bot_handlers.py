"""Tests for handlers/cricket_bot.py — Avi's external cricket-bot API tools.

Covers:
  - fail-closed dict when CRICKET_BOT_API_KEY / CRICKET_BOT_API_URL are missing
  - correct path + X-API-Key header + query-param passthrough per real contract
  - auth-rejected and unreachable error shapes
  - argument validation (platform / action / limit clamping)
  - registration parity with server TOOLS list + owner surface + danger tiers
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from algochains_mcp.handlers.cricket_bot import (
    CRICKET_BOT_HANDLERS,
    get_cricket_bot_matches,
    get_cricket_bot_performance,
    get_cricket_bot_signals,
    get_cricket_bot_tournaments,
    get_cricket_bot_trades,
)

_ENV = {
    "CRICKET_BOT_API_KEY": "test-key-123",
    "CRICKET_BOT_API_URL": "http://cricket.test/api",
}


def _mock_response(status_code: int = 200, body: Any = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body if body is not None else {}
    return resp


def _patch_client(resp: MagicMock):
    """Patch httpx.AsyncClient used inside the handler module."""
    client = MagicMock()
    client.get = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return patch("algochains_mcp.handlers.cricket_bot.httpx.AsyncClient", return_value=client), client


# ── Fail-closed when unconfigured ────────────────────────────────────────────

@pytest.mark.parametrize("handler", list(CRICKET_BOT_HANDLERS.values()))
async def test_fail_closed_without_env(handler, monkeypatch):
    monkeypatch.delenv("CRICKET_BOT_API_KEY", raising=False)
    monkeypatch.delenv("CRICKET_BOT_API_URL", raising=False)
    result = await handler({})
    assert result["error"] == "cricket_bot_unavailable"
    assert "no mock data" in result["detail"].lower()


async def test_fail_closed_with_only_key(monkeypatch):
    monkeypatch.setenv("CRICKET_BOT_API_KEY", "k")
    monkeypatch.delenv("CRICKET_BOT_API_URL", raising=False)
    result = await get_cricket_bot_tournaments({})
    assert result["error"] == "cricket_bot_unavailable"


# ── Contract: path, headers, params ──────────────────────────────────────────

async def test_performance_passes_contract_params(monkeypatch):
    for k, v in _ENV.items():
        monkeypatch.setenv(k, v)
    patcher, client = _patch_client(_mock_response(200, {"combined": {"total_trades": 5}}))
    with patcher:
        result = await get_cricket_bot_performance(
            {"platform": "polymarket", "tournament": "MLC 2026", "innings": 2}
        )
    assert result["status"] == "ok"
    assert result["combined"] == {"total_trades": 5}
    url = client.get.call_args.args[0]
    kwargs = client.get.call_args.kwargs
    assert url == "http://cricket.test/api/performance"
    assert kwargs["headers"]["X-API-Key"] == "test-key-123"
    assert kwargs["params"] == {"platform": "polymarket", "tournament": "MLC 2026", "innings": 2}


async def test_trades_clamps_limit_and_drops_none(monkeypatch):
    for k, v in _ENV.items():
        monkeypatch.setenv(k, v)
    patcher, client = _patch_client(_mock_response(200, {"trades": [], "count": 0}))
    with patcher:
        result = await get_cricket_bot_trades({"limit": 9999})
    assert result["status"] == "ok"
    params = client.get.call_args.kwargs["params"]
    assert params["limit"] == 500  # API max
    assert params["offset"] == 0
    assert "tournament" not in params  # None params dropped
    assert "innings" not in params


async def test_matches_and_tournaments_paths(monkeypatch):
    for k, v in _ENV.items():
        monkeypatch.setenv(k, v)
    patcher, client = _patch_client(_mock_response(200, {"matches": [], "count": 0}))
    with patcher:
        await get_cricket_bot_matches({"tournament": "MLC", "innings": 1})
    assert client.get.call_args.args[0] == "http://cricket.test/api/matches"

    patcher, client = _patch_client(_mock_response(200, {"tournaments": []}))
    with patcher:
        await get_cricket_bot_tournaments({})
    assert client.get.call_args.args[0] == "http://cricket.test/api/tournaments"


async def test_signals_action_passthrough(monkeypatch):
    for k, v in _ENV.items():
        monkeypatch.setenv(k, v)
    patcher, client = _patch_client(_mock_response(200, {"signals": [], "count": 0}))
    with patcher:
        result = await get_cricket_bot_signals({"action": "skip", "limit": 5})
    assert result["status"] == "ok"
    params = client.get.call_args.kwargs["params"]
    assert params["action"] == "SKIP"  # normalized to upper
    assert params["limit"] == 5


# ── Argument validation ──────────────────────────────────────────────────────

async def test_bad_platform_rejected(monkeypatch):
    for k, v in _ENV.items():
        monkeypatch.setenv(k, v)
    result = await get_cricket_bot_performance({"platform": "nyse"})
    assert result["error"] == "bad_arguments"


async def test_bad_action_rejected(monkeypatch):
    for k, v in _ENV.items():
        monkeypatch.setenv(k, v)
    result = await get_cricket_bot_signals({"action": "HOLD"})
    assert result["error"] == "bad_arguments"


# ── Upstream failure shapes ──────────────────────────────────────────────────

async def test_auth_rejected_shape(monkeypatch):
    for k, v in _ENV.items():
        monkeypatch.setenv(k, v)
    patcher, _ = _patch_client(_mock_response(401))
    with patcher:
        result = await get_cricket_bot_tournaments({})
    assert result["error"] == "cricket_bot_auth_rejected"
    assert "rotated" in result["detail"]


async def test_unreachable_shape(monkeypatch):
    for k, v in _ENV.items():
        monkeypatch.setenv(k, v)
    client = MagicMock()
    client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    with patch("algochains_mcp.handlers.cricket_bot.httpx.AsyncClient", return_value=client):
        result = await get_cricket_bot_tournaments({})
    assert result["error"] == "cricket_bot_unreachable"


async def test_non_json_shape(monkeypatch):
    for k, v in _ENV.items():
        monkeypatch.setenv(k, v)
    resp = _mock_response(200)
    resp.json.side_effect = ValueError("not json")
    patcher, _ = _patch_client(resp)
    with patcher:
        result = await get_cricket_bot_tournaments({})
    assert result["error"] == "cricket_bot_bad_response"


# ── Registration parity ──────────────────────────────────────────────────────

def test_registered_in_server_tools_and_registry():
    from algochains_mcp import server

    tool_names = {t.name for t in server.TOOLS}
    for name in CRICKET_BOT_HANDLERS:
        assert name in tool_names, f"{name} missing from server TOOLS list"
        assert name in server._HANDLER_REGISTRY, f"{name} missing from _HANDLER_REGISTRY"


def test_owner_surface_and_danger_tier():
    from algochains_mcp.http_bridge import OWNER_TOOLS, PUBLIC_TOOLS
    from algochains_mcp.tool_danger_tiers import TIER_READ_ONLY, get_danger_tier

    for name in CRICKET_BOT_HANDLERS:
        assert name in OWNER_TOOLS, f"{name} must be owner-gated on the HTTP bridge"
        assert name not in PUBLIC_TOOLS, f"{name} must not be public"
        assert get_danger_tier(name) == TIER_READ_ONLY


def test_manifest_declares_required_env():
    from algochains_mcp.tool_manifest import _TOOL_OVERRIDES

    for name in CRICKET_BOT_HANDLERS:
        entry = _TOOL_OVERRIDES.get(name)
        assert entry is not None, f"{name} missing from tool manifest overrides"
        assert set(entry["required_env"]) == {"CRICKET_BOT_API_KEY", "CRICKET_BOT_API_URL"}
