import hashlib
import hmac
import json

import httpx
import pytest

from algochains_mcp import support_tickets


class _FakeClient:
    response = None
    request_args = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def request(self, method, url, **kwargs):
        type(self).request_args = (method, url, kwargs)
        return type(self).response


@pytest.mark.asyncio
async def test_create_routes_to_signed_idempotent_django_api(monkeypatch):
    monkeypatch.setenv("ALGOCHAINS_SUPPORT_API_URL", "https://django.test")
    monkeypatch.setenv("ALGOCHAINS_SUPPORT_API_SECRET", "shared-secret")
    monkeypatch.delenv("ALGOCHAINS_SUPPORT_LEGACY_WRITE_ENABLED", raising=False)
    monkeypatch.setattr(support_tickets.time, "time", lambda: 1_700_000_000)
    monkeypatch.setattr(support_tickets.httpx, "AsyncClient", _FakeClient)
    _FakeClient.response = httpx.Response(
        201,
        json={"ticket_id": "AC-12345", "status": "open"},
        request=httpx.Request("POST", "https://django.test/api/internal/v1/support/tickets/"),
    )

    result = await support_tickets.create_ticket(
        "Help", "Something broke", "USER@example.com", idempotency_key="evt-123"
    )

    assert result["success"] is True
    assert result["source"] == "django"
    method, url, kwargs = _FakeClient.request_args
    assert method == "POST"
    assert url == "https://django.test/api/internal/v1/support/tickets/"
    assert kwargs["headers"]["Idempotency-Key"] == "evt-123"
    payload = json.loads(kwargs["content"])
    assert payload["external_event_id"] == "evt-123"
    assert payload["user_email"] == "user@example.com"

    timestamp = kwargs["headers"]["X-AlgoChains-Timestamp"]
    body = kwargs["content"].decode()
    signed = f"{timestamp}.POST./api/internal/v1/support/tickets/.{body}".encode()
    expected = hmac.new(b"shared-secret", signed, hashlib.sha256).hexdigest()
    assert kwargs["headers"]["X-AlgoChains-Signature"] == f"sha256={expected}"


@pytest.mark.asyncio
async def test_create_fails_closed_when_canonical_unconfigured(monkeypatch):
    monkeypatch.delenv("ALGOCHAINS_SUPPORT_API_SECRET", raising=False)
    monkeypatch.delenv("ALGOCHAINS_SUPPORT_LEGACY_WRITE_ENABLED", raising=False)

    result = await support_tickets.create_ticket("Help", "Broken", "user@example.com")

    assert result["success"] is False
    assert "legacy writes are disabled" in result["error"]


@pytest.mark.asyncio
async def test_canonical_failure_does_not_silently_write_legacy(monkeypatch):
    monkeypatch.setenv("ALGOCHAINS_SUPPORT_API_SECRET", "shared-secret")
    monkeypatch.delenv("ALGOCHAINS_SUPPORT_LEGACY_WRITE_ENABLED", raising=False)
    monkeypatch.setattr(support_tickets.httpx, "AsyncClient", _FakeClient)
    _FakeClient.response = httpx.Response(
        503,
        json={"error": "unavailable"},
        request=httpx.Request("POST", "https://algochains.ai/api/internal/v1/support/tickets/"),
    )

    result = await support_tickets.create_ticket("Help", "Broken", "user@example.com")

    assert result["success"] is False
    assert "legacy writes are disabled" in result["error"]


@pytest.mark.asyncio
async def test_resolution_requires_deterministic_verification_receipt(monkeypatch):
    monkeypatch.setenv("ALGOCHAINS_SUPPORT_API_SECRET", "shared-secret")
    monkeypatch.setattr(support_tickets.httpx, "AsyncClient", _FakeClient)
    _FakeClient.request_args = None

    result = await support_tickets.update_ticket_status("AC-12345", "resolved")

    assert result["success"] is False
    assert "verification_receipt_id" in result["error"]
    assert _FakeClient.request_args is None


@pytest.mark.asyncio
async def test_verified_resolution_sends_receipt_and_confidence(monkeypatch):
    monkeypatch.setenv("ALGOCHAINS_SUPPORT_API_URL", "https://django.test")
    monkeypatch.setenv("ALGOCHAINS_SUPPORT_API_SECRET", "shared-secret")
    monkeypatch.setattr(support_tickets.httpx, "AsyncClient", _FakeClient)
    _FakeClient.response = httpx.Response(
        200,
        json={"ticket_id": "AC-12345", "status": "resolved"},
        request=httpx.Request(
            "PATCH", "https://django.test/api/internal/v1/support/tickets/AC-12345/"
        ),
    )

    result = await support_tickets.update_ticket_status(
        "AC-12345",
        "resolved",
        verification_receipt_id="receipt-123",
        resolution_confidence=0.99,
    )

    assert result["success"] is True
    _, _, kwargs = _FakeClient.request_args
    payload = json.loads(kwargs["content"])
    assert payload["verification_receipt_id"] == "receipt-123"
    assert payload["resolution_confidence"] == 0.99
