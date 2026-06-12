from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from algochains_mcp.http_bridge import create_fastapi_app


OWNER_KEY = "test-owner-key-health"


class _Metric:
    def __init__(self, bot_id: str, daily_pnl: float):
        self.bot_id = bot_id
        self.daily_pnl = daily_pnl

    def to_dict(self) -> dict:
        return {"bot_id": self.bot_id, "daily_pnl": self.daily_pnl}


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


def test_api_bots_returns_legacy_keys_and_array_shape():
    client = _make_client()
    metrics = {
        "mnq": _Metric("mnq", 12.5),
        "cl": _Metric("cl", -1.25),
    }
    with patch(
        "algochains_mcp.live_bot_intelligence.metrics_parser.parse_all_bots",
        return_value=metrics,
    ):
        resp = client.get("/api/bots", headers={"X-Api-Key": OWNER_KEY})

    assert resp.status_code == 200
    body = resp.json()
    assert body["mnq"] == {"bot_id": "mnq", "daily_pnl": 12.5}
    assert body["cl"] == {"bot_id": "cl", "daily_pnl": -1.25}
    assert body["bot_count"] == 2
    assert body["bots"] == [
        {"bot_id": "mnq", "daily_pnl": 12.5},
        {"bot_id": "cl", "daily_pnl": -1.25},
    ]


def test_api_system_wraps_heartbeat_payload():
    client = _make_client()
    heartbeat = {
        "this_node": "desktop",
        "desktop_mode": "primary",
        "desktop_bots_running": 4,
    }
    with patch(
        "algochains_mcp.http_bridge.handle_mcp_request",
        new_callable=AsyncMock,
        return_value=heartbeat,
    ):
        resp = client.get("/api/system", headers={"X-Api-Key": OWNER_KEY})

    assert resp.status_code == 200
    body = resp.json()
    assert body["system"] == heartbeat
    assert body["heartbeat"] == heartbeat
    assert "as_of" in body

