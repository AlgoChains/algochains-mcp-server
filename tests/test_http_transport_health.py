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

