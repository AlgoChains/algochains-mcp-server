"""Health/status endpoint tests for the streamable HTTP transport."""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from algochains_mcp.http_transport import create_http_app


def test_status_alias_matches_health() -> None:
    client = TestClient(create_http_app(mcp_server=object()))

    health = client.get("/health")
    status = client.get("/status")

    assert status.status_code == 200
    assert status.json() == health.json()


def test_mcp_fails_closed_without_transport_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALGOCHAINS_HTTP_TRANSPORT_SECRET", raising=False)
    monkeypatch.delenv("ALGOCHAINS_HTTP_ALLOW_UNAUTHENTICATED_DEV", raising=False)
    client = TestClient(create_http_app(mcp_server=object()))

    response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})

    assert response.status_code == 401


def test_mcp_allows_explicit_unauthenticated_dev_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALGOCHAINS_HTTP_TRANSPORT_SECRET", raising=False)
    monkeypatch.setenv("ALGOCHAINS_HTTP_ALLOW_UNAUTHENTICATED_DEV", "1")
    client = TestClient(create_http_app(mcp_server=object()))

    response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})

    assert response.status_code == 200
    assert response.json()["result"] == {}


def test_mcp_accepts_matching_transport_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALGOCHAINS_HTTP_TRANSPORT_SECRET", "transport-test-secret")
    monkeypatch.delenv("ALGOCHAINS_HTTP_ALLOW_UNAUTHENTICATED_DEV", raising=False)
    client = TestClient(create_http_app(mcp_server=object()))

    response = client.post(
        "/mcp",
        headers={"Authorization": "Bearer transport-test-secret"},
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
    )

    assert response.status_code == 200
