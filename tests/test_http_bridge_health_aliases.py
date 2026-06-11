from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from algochains_mcp.http_bridge import create_fastapi_app


OWNER_KEY = "test-owner-key-health"
OWNER_HEADERS = {"X-Api-Key": OWNER_KEY}


class _McpText:
    def __init__(self, payload: dict) -> None:
        self.text = json.dumps(payload)


def _make_client() -> TestClient:
    env_patch = {
        "ALGOCHAINS_BRIDGE_API_KEY": OWNER_KEY,
        "ALGOCHAINS_BRIDGE_DEV_MODE": "false",
        "SUPABASE_URL": "",
        "SUPABASE_SERVICE_ROLE_KEY": "",
    }
    with patch.dict(os.environ, env_patch):
        app = create_fastapi_app()
    return TestClient(app, raise_server_exceptions=False)


def test_api_bots_preserves_legacy_keys_and_adds_list_shape() -> None:
    client = _make_client()
    tool_payload = {
        "mnq": {"bot_id": "mnq", "symbol": "MNQ", "is_running": True},
        "cl": {"symbol": "CL", "is_running": True},
    }

    with patch(
        "algochains_mcp.server.call_tool",
        new_callable=AsyncMock,
        return_value=[_McpText(tool_payload)],
    ) as call_tool:
        response = client.get("/api/bots", headers=OWNER_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["mnq"]["symbol"] == "MNQ"
    assert body["cl"]["symbol"] == "CL"
    assert body["bot_count"] == 2
    assert {bot["bot_id"] for bot in body["bots"]} == {"mnq", "cl"}
    call_tool.assert_awaited_once_with("get_all_bot_metrics", {})


def test_api_system_wraps_system_heartbeat_payload() -> None:
    client = _make_client()
    tool_payload = {
        "mac_alive": True,
        "desktop_mode": "standby",
        "desktop_bots_running": 4,
        "timestamp": "2026-06-11T20:58:40+00:00",
    }

    with patch(
        "algochains_mcp.server.call_tool",
        new_callable=AsyncMock,
        return_value=[_McpText(tool_payload)],
    ) as call_tool:
        response = client.get("/api/system", headers=OWNER_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["desktop_mode"] == "standby"
    assert body["system"] == tool_payload
    assert body["heartbeat"] == tool_payload
    call_tool.assert_awaited_once_with("get_system_heartbeat", {})


def test_api_system_requires_owner_key() -> None:
    client = _make_client()

    response = client.get("/api/system", headers={"X-Api-Key": "wrong"})

    assert response.status_code == 401
