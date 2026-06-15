from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from algochains_mcp.http_bridge import create_fastapi_app


OWNER_KEY = "owner-test-token"


def _client(extra_env: dict[str, str] | None = None) -> TestClient:
    env = {
        "ALGOCHAINS_BRIDGE_API_KEY": OWNER_KEY,
        "OWNER_EMAIL": "owner@test.algochains.ai",
        "ALGOCHAINS_BRIDGE_DEV_MODE": "false",
        "SUPABASE_URL": "",
        "SUPABASE_SERVICE_ROLE_KEY": "",
    }
    if extra_env:
        env.update(extra_env)
    with patch.dict(os.environ, env):
        return TestClient(create_fastapi_app(), raise_server_exceptions=False)


class _FakeResult:
    def __init__(self, data: list[dict]):
        self.data = data


class _FakeQuery:
    def __init__(self, client: "_FakeSupabase", table_name: str):
        self.client = client
        self.table_name = table_name

    def select(self, *_args):
        return self

    def limit(self, *_args):
        return self

    def execute(self):
        return _FakeResult(self.client.rows_by_table.get(self.table_name, []))


class _FakeSupabase:
    def __init__(self, rows_by_table: dict[str, list[dict]]):
        self.rows_by_table = rows_by_table

    def table(self, table_name: str):
        return _FakeQuery(self, table_name)


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


def test_api_signal_health_returns_state_file_aliases(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    signal_health = {
        "MNQ_Upgraded_Scalper": {
            "last_signal_time": "2026-06-15T07:20:00+00:00",
            "last_trade_result": "submitted",
            "last_confidence": 0.82,
            "last_regime": "trend",
            "advisory_path": "qwen3",
        }
    }
    (state_dir / "signal_health.json").write_text(json.dumps(signal_health))
    client = _client({"ALGOCHAINS_CONTROL_TOWER": str(tmp_path)})

    resp = client.get("/api/signal-health", headers={"X-Api-Key": OWNER_KEY})

    assert resp.status_code == 200
    data = resp.json()
    assert data["signal_health"] == signal_health
    assert data["signal_count"] == 1
    assert data["signals"][0]["bot"] == "MNQ_Upgraded_Scalper"
    assert data["signals"][0]["confidence"] == 0.82


def test_api_subscribers_uses_platform_subscription_fallback():
    client = _client()
    platform_rows = [
        {
            "id": "sub-1",
            "user_id": "user-1",
            "bot_id": "MNQ",
            "status": "trial",
            "broker": "tradovate",
            "broker_connected": True,
            "created_at": "2026-06-15T07:00:00+00:00",
        }
    ]
    fake_sb = _FakeSupabase(
        {
            "subscriber_bot_assignments": [],
            "subscriber_paper_accounts": [],
            "subscriber_heartbeats": [],
            "marketplace_botsubscription": [],
            "algochains_subscriptions": platform_rows,
        }
    )

    with patch("algochains_mcp.marketplace.supabase_tools._get_sb_client", return_value=fake_sb):
        resp = client.get("/api/subscribers", headers={"X-Api-Key": OWNER_KEY})

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["active"] == 1
    assert data["platform_subscription_count"] == 1
    assert data["active_platform_subscriptions"] == 1
    assert data["subscribers"][0]["subscriber_id"] == "user-1"
    assert data["subscribers"][0]["platform_subscription_count"] == 1
