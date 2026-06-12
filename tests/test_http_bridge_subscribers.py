from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from algochains_mcp.http_bridge import create_fastapi_app


OWNER_KEY = "test-owner-key-12345"


class _Response:
    def __init__(self, data: list[dict[str, Any]]):
        self.data = data


class _TableQuery:
    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = rows

    def select(self, _columns: str) -> "_TableQuery":
        return self

    def limit(self, _limit: int) -> "_TableQuery":
        return self

    def execute(self) -> _Response:
        return _Response(self._rows)


class _Supabase:
    def __init__(self, tables: dict[str, list[dict[str, Any]]]):
        self._tables = tables

    def table(self, name: str) -> _TableQuery:
        return _TableQuery(self._tables.get(name, []))


def _client() -> TestClient:
    env = {
        "ALGOCHAINS_BRIDGE_API_KEY": OWNER_KEY,
        "OWNER_EMAIL": "owner@test.algochains.ai",
        "ALGOCHAINS_BRIDGE_DEV_MODE": "false",
    }
    with patch.dict(os.environ, env):
        return TestClient(create_fastapi_app(), raise_server_exceptions=False)


def test_subscriber_health_requires_owner_key():
    response = _client().get("/api/subscribers")

    assert response.status_code == 401


def test_subscriber_health_rolls_up_paper_pnl_for_command_center():
    now = datetime.now(timezone.utc)
    rows = {
        "marketplace_botsubscription": [
            {
                "id": f"sub-{i}",
                "status": "active",
                "subscriber_email": f"user{i}@example.com",
            }
            for i in range(12)
        ],
        "subscriber_bot_assignments": [
            {"subscriber_id": f"user-{i}", "bot": "MNQ", "paused": False}
            for i in range(12)
        ],
        "subscriber_paper_accounts": [
            {
                "subscriber_id": "user-1",
                "starting_balance_usd": 1000,
                "current_balance_usd": 1101.6,
                "realized_pnl_usd": 101.6,
                "fills_count": 3,
            },
            {
                "subscriber_id": "user-2",
                "starting_balance_usd": 2000,
                "current_balance_usd": 2200,
                "realized_pnl_usd": 200,
                "fills_count": 4,
            },
        ],
        "subscriber_heartbeats": [
            {
                "subscriber_id": "user-1",
                "last_seen": (now - timedelta(seconds=7.25)).isoformat(),
                "pnl_today_usd": 0,
                "fills_today": 0,
            }
        ],
    }
    fake_supabase = _Supabase(rows)

    with patch(
        "algochains_mcp.marketplace.supabase_tools._get_sb_client",
        return_value=fake_supabase,
    ):
        response = _client().get("/api/subscribers", headers={"X-Api-Key": OWNER_KEY})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["subscriber_count"] == 12
    assert body["subscriptions_count"] == 12
    assert body["paper_pnl_usd"] == 301.6
    assert body["paper_pnl"] == 301.6
    assert body["paper_pnl_rollup_usd"] == 301.6
    assert body["paper_realized_pnl_usd"] == 301.6
    assert body["copy_trade"]["schema_ok"] is True
    assert body["copy_trade"]["subs"] == 12
    assert body["copy_trade"]["paper_pnl_usd"] == 301.6
    assert body["copy_trade"]["paper_pnl"] == 301.6
    assert body["copy_trade"]["paper_pnl_rollup_usd"] == 301.6
