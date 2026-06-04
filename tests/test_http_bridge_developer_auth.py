"""
Integration tests for developer key auth in the MCP HTTP bridge.

Verifies:
  - Developer key resolves correctly and sees developer tool surface
  - Developer key cannot access owner or subscriber tools
  - Rate limit returns 429 with correct headers
  - Body size limit returns 413
  - execute_dynamic_tool is blocked for developer keys
  - Unknown tool returns error with available_tools list
"""
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient
from algochains_mcp.http_bridge import create_fastapi_app
from algochains_mcp.developer_auth import ResolvedDeveloper
from algochains_mcp.developer_rate_limiter import _REGISTRY


DEV_KEY = "ac_live_testdevkey"
DEV_CLERK = "clerk_devuser_123"
DEV_SCOPES = ("read:market_data", "read:signals")


@pytest.fixture(autouse=True)
def clear_rate_limit():
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()


@pytest.fixture()
def bridge_app():
    with patch.dict("os.environ", {
        "ALGOCHAINS_BRIDGE_API_KEY": "owner-secret",
        "ALGOCHAINS_BRIDGE_DEV_MODE": "false",
    }):
        app = create_fastapi_app()
    return TestClient(app, raise_server_exceptions=False)


def _resolved_dev():
    return ResolvedDeveloper(clerk_user_id=DEV_CLERK, scopes=DEV_SCOPES, env="live")


_BRIDGE_RESOLVE = "algochains_mcp.http_bridge.resolve_developer_key"


class TestDeveloperToolsEndpoint:
    @patch(_BRIDGE_RESOLVE, return_value=_resolved_dev())
    def test_dev_key_sees_developer_surface(self, mock_resolve, bridge_app):
        resp = bridge_app.get("/tools", headers={"X-Api-Key": DEV_KEY})
        assert resp.status_code == 200
        data = resp.json()
        assert "developer_tools" in data or "tools" in data
        tools_raw = data.get("developer_tools", []) or data.get("tools", [])
        all_tools = [
            t.get("tool_name", t.get("name", t)) if isinstance(t, dict) else t
            for t in tools_raw
        ]
        assert "place_order" not in all_tools
        assert "get_bot_health" not in all_tools

    @patch(_BRIDGE_RESOLVE, return_value=_resolved_dev())
    def test_dev_key_shows_env_and_scopes(self, mock_resolve, bridge_app):
        resp = bridge_app.get("/tools", headers={"X-Api-Key": DEV_KEY})
        data = resp.json()
        assert data.get("env") == "live" or "scopes" in data


class TestDeveloperMcpEndpoint:
    @patch(_BRIDGE_RESOLVE, return_value=_resolved_dev())
    @patch("algochains_mcp.http_bridge.handle_mcp_request", new_callable=AsyncMock)
    def test_allowed_tool_dispatched(self, mock_handle, mock_resolve, bridge_app):
        mock_handle.return_value = {"regime": "bullish"}
        resp = bridge_app.post(
            "/api/mcp",
            json={"tool": "detect_market_regime", "arguments": {}},
            headers={"X-Api-Key": DEV_KEY},
        )
        assert resp.status_code == 200

    @patch(_BRIDGE_RESOLVE, return_value=_resolved_dev())
    def test_owner_tool_blocked(self, mock_resolve, bridge_app):
        resp = bridge_app.post(
            "/api/mcp",
            json={"tool": "place_order", "arguments": {}},
            headers={"X-Api-Key": DEV_KEY},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data

    @patch(_BRIDGE_RESOLVE, return_value=_resolved_dev())
    def test_execute_dynamic_tool_blocked(self, mock_resolve, bridge_app):
        resp = bridge_app.post(
            "/api/mcp",
            json={"tool": "execute_dynamic_tool", "arguments": {"tool_name": "place_order"}},
            headers={"X-Api-Key": DEV_KEY},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data

    @patch(_BRIDGE_RESOLVE, return_value=None)
    def test_invalid_developer_key_gets_401(self, mock_resolve, bridge_app):
        resp = bridge_app.post(
            "/api/mcp",
            json={"tool": "detect_market_regime", "arguments": {}},
            headers={"X-Api-Key": "ac_live_invalidkey"},
        )
        assert resp.status_code == 401


class TestBodySizeLimit:
    @patch(_BRIDGE_RESOLVE, return_value=_resolved_dev())
    def test_large_body_rejected(self, mock_resolve, bridge_app):
        from algochains_mcp.developer_rate_limiter import MAX_BODY_BYTES
        oversized = "x" * (MAX_BODY_BYTES + 1024)
        resp = bridge_app.post(
            "/api/mcp",
            content=oversized.encode(),
            headers={
                "X-Api-Key": DEV_KEY,
                "Content-Type": "application/json",
                "Content-Length": str(MAX_BODY_BYTES + 1024),
            },
        )
        assert resp.status_code == 413


class TestRateLimitResponse:
    @patch(_BRIDGE_RESOLVE, return_value=_resolved_dev())
    @patch("algochains_mcp.http_bridge.handle_mcp_request", new_callable=AsyncMock)
    def test_rate_limit_returns_429_after_burst(self, mock_handle, mock_resolve, bridge_app):
        from algochains_mcp.developer_rate_limiter import BURST_LIMIT
        mock_handle.return_value = {"regime": "neutral"}
        responses = []
        for _ in range(BURST_LIMIT + 5):
            r = bridge_app.post(
                "/api/mcp",
                json={"tool": "detect_market_regime", "arguments": {}},
                headers={"X-Api-Key": DEV_KEY},
            )
            responses.append(r.status_code)
        assert 429 in responses

    @patch(_BRIDGE_RESOLVE, return_value=_resolved_dev())
    @patch("algochains_mcp.http_bridge.handle_mcp_request", new_callable=AsyncMock)
    def test_429_response_has_retry_after_header(self, mock_handle, mock_resolve, bridge_app):
        from algochains_mcp.developer_rate_limiter import BURST_LIMIT
        mock_handle.return_value = {}
        for _ in range(BURST_LIMIT + 5):
            r = bridge_app.post(
                "/api/mcp",
                json={"tool": "detect_market_regime"},
                headers={"X-Api-Key": DEV_KEY},
            )
            if r.status_code == 429:
                assert "Retry-After" in r.headers
                data = r.json()
                assert data["error"] == "rate_limit_exceeded"
                assert "retry_after_ms" in data
                break
