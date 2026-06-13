"""Compatibility route tests for Command Center health probes."""
from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from algochains_mcp.http_bridge import create_fastapi_app


OWNER_KEY = "test-owner-key-compat"


def _client() -> TestClient:
    with patch.dict(
        os.environ,
        {
            "ALGOCHAINS_BRIDGE_API_KEY": OWNER_KEY,
            "ALGOCHAINS_BRIDGE_DEV_MODE": "false",
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "service-role",
        },
    ):
        return TestClient(create_fastapi_app(), raise_server_exceptions=False)


class _Response:
    def __init__(self, data: list[dict[str, Any]]):
        self.data = data


class _Query:
    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = rows

    def select(self, _columns: str) -> "_Query":
        return self

    def limit(self, _limit: int) -> "_Query":
        return self

    def execute(self) -> _Response:
        return _Response(self._rows)


class _Supabase:
    def __init__(self, tables: dict[str, list[dict[str, Any]]]):
        self._tables = tables

    def table(self, name: str) -> _Query:
        return _Query(self._tables.get(name, []))


def test_marketplace_route_returns_listing_payload():
    client = _client()
    expected = {"total": 3, "listings": [{"id": "mnq", "name": "MNQ"}]}

    with patch(
        "algochains_mcp.http_bridge.handle_mcp_request",
        new_callable=AsyncMock,
        return_value=expected,
    ) as handle:
        resp = client.get("/api/marketplace?asset_class=futures&status=live&limit=3")

    assert resp.status_code == 200
    assert resp.json() == expected
    handle.assert_awaited_once_with(
        "get_marketplace_listings",
        {"asset_class": "futures", "status": "live", "limit": 3},
        is_owner=False,
    )


def test_subscribers_route_requires_owner_key():
    client = _client()

    resp = client.get("/api/subscribers")

    assert resp.status_code == 401


def test_subscribers_route_aggregates_copy_trade_state():
    client = _client()
    sb = _Supabase(
        {
            "subscriber_bot_assignments": [
                {"subscriber_id": "sub-1", "bot": "MNQ", "paused": False},
                {"subscriber_id": "sub-1", "bot": "CL", "paused": True},
                {"subscriber_id": "sub-2", "bot": "MES", "paused": False},
            ],
            "subscriber_paper_accounts": [
                {
                    "subscriber_id": "sub-1",
                    "current_balance_usd": 9955,
                    "starting_balance_usd": 10000,
                    "realized_pnl_usd": -45.0,
                }
            ],
            "subscriber_heartbeats": [
                {"subscriber_id": "sub-1", "last_seen": "2026-06-11T00:00:00Z"}
            ],
            "marketplace_botsubscription": [
                {"subscriber_id": "sub-2", "status": "active", "id": "sub-row-1"}
            ],
        }
    )

    with patch("algochains_mcp.marketplace.supabase_tools._get_sb_client", return_value=sb):
        resp = client.get("/api/subscribers", headers={"X-Api-Key": OWNER_KEY})

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert body["active"] == 2
    assert body["assignment_count"] == 3
    assert body["active_assignments"] == 2
    assert body["subscription_count"] == 1
    assert body["active_subscriptions"] == 1
    assert body["paper_account_count"] == 1
    assert body["paper_pnl_usd"] == -45.0
    assert body["paper_pnl"] == -45.0
    assert body["paper_pnl_rollup_usd"] == -45.0
    assert body["heartbeat_count"] == 1
    assert {row["subscriber_id"] for row in body["subscribers"]} == {"sub-1", "sub-2"}
    subscriber = next(row for row in body["subscribers"] if row["subscriber_id"] == "sub-1")
    assert subscriber["paper_account"]["realized_pnl_usd"] == -45.0
    assert subscriber["paper_pnl_usd"] == -45.0
    assert subscriber["paper_pnl"] == -45.0
    assert subscriber["paper_pnl_rollup_usd"] == -45.0
