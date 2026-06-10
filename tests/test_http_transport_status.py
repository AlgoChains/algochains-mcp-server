from __future__ import annotations

import pytest


pytest.importorskip("fastapi")


def test_http_transport_status_alias_returns_ok():
    from fastapi.testclient import TestClient

    from algochains_mcp.http_transport import create_http_app

    client = TestClient(create_http_app(mcp_server=object()), raise_server_exceptions=False)

    health = client.get("/health")
    status = client.get("/status")

    assert health.status_code == 200
    assert status.status_code == 200
    assert status.json() == health.json()
    assert status.json()["status"] == "ok"
