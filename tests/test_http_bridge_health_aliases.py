from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from algochains_mcp.http_bridge import create_fastapi_app


FAKE_OWNER_KEY = "test-owner-key-12345"


def _make_client() -> TestClient:
    env_patch = {
        "ALGOCHAINS_BRIDGE_API_KEY": FAKE_OWNER_KEY,
        "ALGOCHAINS_BRIDGE_DEV_MODE": "false",
        "SUPABASE_URL": "",
        "SUPABASE_SERVICE_ROLE_KEY": "",
    }
    with patch.dict(os.environ, env_patch):
        app = create_fastapi_app()
    return TestClient(app, raise_server_exceptions=False)


def test_api_bots_adds_collection_aliases_to_legacy_metric_map() -> None:
    client = _make_client()
    raw_metrics = {
        "mnq": {
            "bot_id": "mnq",
            "symbol": "MNQ",
            "is_running": True,
        },
        "cl": {
            "bot_id": "cl",
            "symbol": "CL",
            "is_running": False,
        },
    }

    with patch(
        "algochains_mcp.http_bridge.handle_mcp_request",
        new_callable=AsyncMock,
        return_value=raw_metrics,
    ) as mock_handle:
        response = client.get("/api/bots", headers={"X-Api-Key": FAKE_OWNER_KEY})

    assert response.status_code == 200
    body = response.json()
    assert body["mnq"]["symbol"] == "MNQ"
    assert body["cl"]["symbol"] == "CL"
    assert [bot["bot_id"] for bot in body["bots"]] == ["mnq", "cl"]
    assert body["bot_count"] == 2
    assert body["total"] == 2
    assert body["running"] == 1
    mock_handle.assert_awaited_once_with(
        "get_all_bot_metrics",
        {},
        is_owner=True,
        caller_scope=None,
    )


def test_api_bots_keeps_existing_collection_payload() -> None:
    client = _make_client()
    raw_metrics = {
        "bots": [
            {
                "bot_id": "mnq",
                "symbol": "MNQ",
                "is_running": True,
            }
        ],
        "bot_count": 1,
        "total": 1,
        "running": 1,
    }

    with patch(
        "algochains_mcp.http_bridge.handle_mcp_request",
        new_callable=AsyncMock,
        return_value=raw_metrics,
    ):
        response = client.get("/api/bots", headers={"X-Api-Key": FAKE_OWNER_KEY})

    assert response.status_code == 200
    assert response.json() == raw_metrics
