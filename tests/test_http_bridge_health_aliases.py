from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from algochains_mcp.http_bridge import create_fastapi_app


OWNER_KEY = "test-owner-key-health"
OWNER_HEADERS = {"X-Api-Key": OWNER_KEY}


def _make_client(control_tower: str | None = None) -> TestClient:
    env_patch = {
        "ALGOCHAINS_BRIDGE_API_KEY": OWNER_KEY,
        "ALGOCHAINS_BRIDGE_DEV_MODE": "false",
        "SUPABASE_URL": "",
        "SUPABASE_SERVICE_ROLE_KEY": "",
    }
    if control_tower is not None:
        env_patch["ALGOCHAINS_CONTROL_TOWER"] = control_tower
    with patch.dict(os.environ, env_patch):
        app = create_fastapi_app()
    return TestClient(app, raise_server_exceptions=False)


def _mcp_text(payload: dict) -> list[SimpleNamespace]:
    return [SimpleNamespace(text=json.dumps(payload))]


def test_api_bots_returns_watchdog_envelope_without_losing_legacy_keys() -> None:
    client = _make_client()
    raw_metrics = {
        "mnq": {"bot_id": "mnq", "symbol": "MNQ", "is_running": True},
        "cl": {"bot_id": "cl", "symbol": "CL", "is_running": True},
    }
    with patch(
        "algochains_mcp.server.call_tool",
        new_callable=AsyncMock,
        return_value=_mcp_text(raw_metrics),
    ) as mock_call:
        response = client.get("/api/bots", headers=OWNER_HEADERS)

    assert response.status_code == 200
    payload = response.json()
    assert payload["mnq"]["symbol"] == "MNQ"
    assert payload["bot_count"] == 2
    assert [bot["bot_id"] for bot in payload["bots"]] == ["cl", "mnq"]
    mock_call.assert_awaited_once_with("get_all_bot_metrics", {})


def test_api_system_alias_wraps_heartbeat_payload() -> None:
    client = _make_client()
    heartbeat = {
        "timestamp": "2026-06-11T18:10:00+00:00",
        "desktop_mode": "primary",
        "desktop_bots_running": 4,
    }
    with patch(
        "algochains_mcp.server.call_tool",
        new_callable=AsyncMock,
        return_value=_mcp_text(heartbeat),
    ) as mock_call:
        response = client.get("/api/system", headers=OWNER_HEADERS)

    assert response.status_code == 200
    payload = response.json()
    assert payload["timestamp"] == heartbeat["timestamp"]
    assert payload["system"] == heartbeat
    assert payload["heartbeat"] == heartbeat
    mock_call.assert_awaited_once_with("get_system_heartbeat", {})


def test_api_signal_health_alias_reads_control_tower_state(tmp_path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "signal_health.json").write_text(
        json.dumps(
            {
                "MNQ_Upgraded_Scalper": {
                    "last_signal_time": "2026-06-11T18:09:00Z",
                    "last_trade_result": "WIN",
                    "last_confidence": 0.82,
                    "last_regime": "trend",
                    "advisory_path": "validator",
                }
            }
        )
    )
    client = _make_client(str(tmp_path))

    response = client.get("/api/signal-health", headers=OWNER_HEADERS)

    assert response.status_code == 200
    payload = response.json()
    assert payload["signal_count"] == 1
    assert payload["signals"][0]["bot"] == "MNQ_Upgraded_Scalper"
    assert payload["signals"][0]["confidence"] == 0.82
    assert "MNQ_Upgraded_Scalper" in payload["signal_health"]


@pytest.mark.parametrize("path", ["/api/bots", "/api/system", "/api/signal-health"])
def test_health_aliases_require_owner_key(path: str, tmp_path) -> None:
    client = _make_client(str(tmp_path))

    response = client.get(path)

    assert response.status_code == 401
