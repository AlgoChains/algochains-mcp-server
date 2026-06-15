from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from algochains_mcp.http_bridge import create_fastapi_app


OWNER_KEY = "owner-test-token"


def _client() -> TestClient:
    env = {
        "ALGOCHAINS_BRIDGE_API_KEY": OWNER_KEY,
        "OWNER_EMAIL": "owner@test.algochains.ai",
        "ALGOCHAINS_BRIDGE_DEV_MODE": "false",
        "SUPABASE_URL": "",
        "SUPABASE_SERVICE_ROLE_KEY": "",
    }
    with patch.dict(os.environ, env):
        return TestClient(create_fastapi_app(), raise_server_exceptions=False)


def test_api_bots_exposes_list_and_count_aliases():
    client = _client()
    metrics = {
        "mnq": {"bot_id": "mnq", "symbol": "MNQ", "is_running": True},
        "cl": {"bot_id": "cl", "symbol": "CL", "is_running": True},
    }
    with patch("algochains_mcp.http_bridge.handle_mcp_request", new_callable=AsyncMock) as mock_handle:
        mock_handle.return_value = metrics
        resp = client.get("/api/bots", headers={"X-Api-Key": OWNER_KEY})

    assert resp.status_code == 200
    data = resp.json()
    assert data["mnq"]["symbol"] == "MNQ"
    assert data["bot_count"] == 2
    assert [bot["bot_id"] for bot in data["bots"]] == ["mnq", "cl"]
    mock_handle.assert_awaited_once_with(
        "get_all_bot_metrics",
        {},
        is_owner=True,
        caller_scope=None,
    )


def test_api_system_alias_wraps_heartbeat_payload():
    client = _client()
    heartbeat = {
        "desktop_mode": "primary",
        "desktop_bots_running": 4,
        "timestamp": "2026-06-12T03:45:55+00:00",
    }
    with patch("algochains_mcp.http_bridge.handle_mcp_request", new_callable=AsyncMock) as mock_handle:
        mock_handle.return_value = heartbeat
        resp = client.get("/api/system", headers={"X-Api-Key": OWNER_KEY})

    assert resp.status_code == 200
    data = resp.json()
    assert data["desktop_mode"] == "primary"
    assert data["system"]["desktop_bots_running"] == 4
    assert data["heartbeat"]["timestamp"] == "2026-06-12T03:45:55+00:00"
    mock_handle.assert_awaited_once_with(
        "get_system_heartbeat",
        {},
        is_owner=True,
        caller_scope=None,
    )


def test_api_signal_health_reads_control_tower_state(tmp_path):
    control_tower = tmp_path / "algochains-control-tower"
    state_dir = control_tower / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "signal_health.json").write_text(
        json.dumps(
            {
                "MNQ_Upgraded_Scalper": {
                    "last_signal_time": "2026-06-15T07:20:00Z",
                    "last_trade_result": "filled",
                    "last_confidence": 0.82,
                    "last_regime": "trend",
                    "advisory_path": "desktop",
                }
            }
        )
    )

    env = {
        "ALGOCHAINS_BRIDGE_API_KEY": OWNER_KEY,
        "OWNER_EMAIL": "owner@test.algochains.ai",
        "ALGOCHAINS_BRIDGE_DEV_MODE": "false",
        "ALGOCHAINS_CONTROL_TOWER": str(control_tower),
    }
    with patch.dict(os.environ, env):
        client = TestClient(create_fastapi_app(), raise_server_exceptions=False)
        resp = client.get("/api/signal-health", headers={"X-Api-Key": OWNER_KEY})

    assert resp.status_code == 200
    data = resp.json()
    assert data["signal_count"] == 1
    assert data["signal_health"]["MNQ_Upgraded_Scalper"]["last_confidence"] == 0.82
    assert data["signals"] == [
        {
            "bot": "MNQ_Upgraded_Scalper",
            "last_signal_ts": "2026-06-15T07:20:00Z",
            "last_outcome": "filled",
            "confidence": 0.82,
            "regime": "trend",
            "advisory_path": "desktop",
        }
    ]


def test_api_signal_health_requires_owner_key():
    client = _client()

    resp = client.get("/api/signal-health")

    assert resp.status_code == 401
