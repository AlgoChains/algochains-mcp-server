"""Tests for the marketplace bridge HTTP client."""
import asyncio
import pytest
import httpx
from unittest.mock import AsyncMock

from algochains_mcp.config import MarketplaceConfig
from algochains_mcp.errors import (
    ListingNotFoundError,
    MarketplaceError,
    MarketplaceNotConfiguredError,
    RateLimitError,
    SubscriptionError,
)
from algochains_mcp.marketplace.bridge import MarketplaceBridge
from algochains_mcp.marketplace.contracts import LISTING_CREATE_PATH, listing_update_path


def _mock_config() -> MarketplaceConfig:
    return MarketplaceConfig(
        django_url="https://test.algochains.ai",
        listing_api_key="test-key",
        ingest_api_key="test-ingest",
        creator_username="testuser",
    )


class TestBridgeInit:
    def test_creates_with_config(self):
        bridge = MarketplaceBridge(_mock_config())
        assert bridge._client is None
        assert bridge.cfg.django_url == "https://test.algochains.ai"

    @pytest.mark.asyncio
    async def test_close_when_no_client(self):
        bridge = MarketplaceBridge(_mock_config())
        await bridge.close()  # should not raise


class TestCheckResponse:
    def test_429_raises_rate_limit(self):
        bridge = MarketplaceBridge(_mock_config())
        resp = httpx.Response(
            status_code=429,
            headers={"Retry-After": "45"},
            request=httpx.Request("GET", "https://test.algochains.ai/api/v1/listings/"),
        )
        with pytest.raises(RateLimitError) as exc_info:
            bridge._check_response(resp)
        assert exc_info.value.retry_after == 45

    def test_500_raises_marketplace_error(self):
        bridge = MarketplaceBridge(_mock_config())
        resp = httpx.Response(
            status_code=500,
            text="Internal Server Error",
            request=httpx.Request("GET", "https://test.algochains.ai/api/v1/listings/"),
        )
        with pytest.raises(MarketplaceError) as exc_info:
            bridge._check_response(resp)
        assert "500" in str(exc_info.value)

    def test_200_does_not_raise(self):
        bridge = MarketplaceBridge(_mock_config())
        resp = httpx.Response(
            status_code=200,
            request=httpx.Request("GET", "https://test.algochains.ai/api/v1/listings/"),
        )
        bridge._check_response(resp)  # should not raise

    def test_400_json_body(self):
        bridge = MarketplaceBridge(_mock_config())
        resp = httpx.Response(
            status_code=400,
            json={"detail": "Invalid slug"},
            request=httpx.Request("POST", "https://test.algochains.ai/api/v1/listings/"),
        )
        with pytest.raises(MarketplaceError, match="Invalid slug"):
            bridge._check_response(resp)


@pytest.mark.asyncio
async def test_browse_fails_fast_without_listing_key(monkeypatch):
    monkeypatch.delenv("ALGOCHAINS_SKIP_MARKETPLACE_KEY_CHECK", raising=False)
    bridge = MarketplaceBridge(
        MarketplaceConfig(
            django_url="https://test.algochains.ai",
            listing_api_key="",
            ingest_api_key="x",
            creator_username="u",
        )
    )
    with pytest.raises(MarketplaceNotConfiguredError, match="LISTING_API_KEY"):
        await bridge.browse_listings()


@pytest.mark.asyncio
async def test_ingest_fails_fast_without_ingest_key(monkeypatch):
    monkeypatch.delenv("ALGOCHAINS_SKIP_MARKETPLACE_KEY_CHECK", raising=False)
    bridge = MarketplaceBridge(
        MarketplaceConfig(
            django_url="https://test.algochains.ai",
            listing_api_key="listing",
            ingest_api_key="",
            creator_username="u",
        )
    )
    with pytest.raises(MarketplaceNotConfiguredError, match="METRICS_INGEST_API_KEY"):
        await bridge.ingest_metrics("slug", {})


@pytest.mark.asyncio
async def test_publish_uses_canonical_create_route():
    bridge = MarketplaceBridge(_mock_config())
    client = AsyncMock()
    client.post.return_value = httpx.Response(
        status_code=201,
        json={"id": 42, "slug": "mnq"},
        request=httpx.Request("POST", f"https://test.algochains.ai{LISTING_CREATE_PATH}"),
    )
    bridge._client = client

    await bridge.publish_listing({"name": "MNQ"})

    client.post.assert_awaited_once_with(LISTING_CREATE_PATH, json={"name": "MNQ"})


@pytest.mark.asyncio
async def test_update_uses_canonical_update_route():
    bridge = MarketplaceBridge(_mock_config())
    client = AsyncMock()
    client.patch.return_value = httpx.Response(
        status_code=200,
        json={"slug": "mnq"},
        request=httpx.Request("PATCH", "https://test.algochains.ai/api/v1/listings/mnq/update/"),
    )
    bridge._client = client

    await bridge.update_listing("mnq", {"description": "updated"})

    client.patch.assert_awaited_once_with(
        listing_update_path("mnq"), json={"description": "updated"}
    )


def test_paper_subscribe_does_not_require_broker(monkeypatch):
    monkeypatch.setenv("ALGOCHAINS_SKIP_MARKETPLACE_KEY_CHECK", "1")
    bridge = MarketplaceBridge(_mock_config())
    client = AsyncMock()
    client.post.return_value = httpx.Response(
        status_code=200,
        json={"ok": True},
        request=httpx.Request("POST", "https://test.algochains.ai/api/v1/listings/mnq/subscribe/"),
    )
    bridge._client = client

    out = asyncio.run(bridge.subscribe("mnq", mode="paper"))

    assert out == {"ok": True}
    client.post.assert_awaited_once()
    assert client.post.await_args.kwargs["json"] == {"mode": "paper"}
