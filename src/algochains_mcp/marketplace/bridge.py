"""
Marketplace bridge — HTTP client for the AlgoChains Django API.

Handles listing CRUD, subscription management, and metrics ingestion
against the algochains.ai backend.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

import os

from ..config import MarketplaceConfig
from ..errors import (
    ListingNotFoundError,
    MarketplaceError,
    MarketplaceNotConfiguredError,
    RateLimitError,
    SubscriptionError,
)

logger = logging.getLogger("algochains_mcp.marketplace.bridge")

_DEFAULT_TIMEOUT = 30.0


def _skip_marketplace_key_check() -> bool:
    return os.environ.get("ALGOCHAINS_SKIP_MARKETPLACE_KEY_CHECK", "").lower() in ("1", "true", "yes")


class MarketplaceBridge:
    """HTTP bridge to the AlgoChains Django marketplace."""

    def __init__(self, config: MarketplaceConfig):
        self.cfg = config
        self._client: Optional[httpx.AsyncClient] = None

    def _require_listing_key(self) -> None:
        if _skip_marketplace_key_check():
            return
        key = (self.cfg.listing_api_key or "").strip()
        if not key:
            raise MarketplaceNotConfiguredError(
                "LISTING_API_KEY is empty. Generate an API key in Django admin and set LISTING_API_KEY. "
                "Dev-only bypass: ALGOCHAINS_SKIP_MARKETPLACE_KEY_CHECK=1",
                details={"missing": "LISTING_API_KEY"},
            )

    def _require_ingest_key(self) -> None:
        if _skip_marketplace_key_check():
            return
        key = (self.cfg.ingest_api_key or "").strip()
        if not key:
            raise MarketplaceNotConfiguredError(
                "METRICS_INGEST_API_KEY is empty. Required for metrics ingestion. "
                "Dev-only bypass: ALGOCHAINS_SKIP_MARKETPLACE_KEY_CHECK=1",
                details={"missing": "METRICS_INGEST_API_KEY"},
            )

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.cfg.django_url,
                headers={
                    "Authorization": f"Api-Key {self.cfg.listing_api_key}",
                    "Content-Type": "application/json",
                    "X-AlgoChains-Creator": self.cfg.creator_username,
                },
                timeout=_DEFAULT_TIMEOUT,
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ── Listings ──────────────────────────────────────────────────

    async def browse_listings(
        self,
        asset_class: str | None = None,
        strategy_type: str | None = None,
        min_sharpe: float | None = None,
        limit: int = 20,
    ) -> list[dict]:
        self._require_listing_key()
        client = await self._ensure_client()
        params: dict[str, Any] = {"limit": limit}
        if asset_class:
            params["asset_class"] = asset_class
        if strategy_type:
            params["strategy_type"] = strategy_type
        if min_sharpe is not None:
            params["min_sharpe"] = min_sharpe

        resp = await client.get("/api/v1/listings/", params=params)
        self._check_response(resp)
        return resp.json().get("results", [])

    async def get_listing(self, slug: str) -> dict:
        self._require_listing_key()
        client = await self._ensure_client()
        resp = await client.get(f"/api/v1/listings/{slug}/")
        if resp.status_code == 404:
            raise ListingNotFoundError(f"Listing '{slug}' not found")
        self._check_response(resp)
        return resp.json()

    async def publish_listing(self, data: dict) -> dict:
        self._require_listing_key()
        client = await self._ensure_client()
        resp = await client.post("/api/v1/listings/", json=data)
        self._check_response(resp)
        return resp.json()

    async def update_listing(self, slug: str, data: dict) -> dict:
        self._require_listing_key()
        client = await self._ensure_client()
        resp = await client.patch(f"/api/v1/listings/{slug}/", json=data)
        if resp.status_code == 404:
            raise ListingNotFoundError(f"Listing '{slug}' not found")
        self._check_response(resp)
        return resp.json()

    # ── Subscriptions ─────────────────────────────────────────────

    async def subscribe(self, slug: str, broker: str | None = None, mode: str = "paper") -> dict:
        self._require_listing_key()
        client = await self._ensure_client()
        payload = {"mode": mode}
        if mode != "paper" or broker:
            payload["broker"] = broker
        resp = await client.post(
            f"/api/v1/listings/{slug}/subscribe/",
            json=payload,
        )
        if resp.status_code == 404:
            raise ListingNotFoundError(f"Listing '{slug}' not found")
        if resp.status_code == 409:
            raise SubscriptionError("Already subscribed to this listing")
        self._check_response(resp)
        return resp.json()

    async def unsubscribe(self, slug: str) -> bool:
        self._require_listing_key()
        client = await self._ensure_client()
        resp = await client.post(f"/api/v1/listings/{slug}/unsubscribe/")
        if resp.status_code == 404:
            raise ListingNotFoundError(f"Listing '{slug}' not found")
        return resp.status_code in (200, 204)

    async def list_subscriptions(self) -> list[dict]:
        self._require_listing_key()
        client = await self._ensure_client()
        resp = await client.get("/api/v1/subscriptions/")
        self._check_response(resp)
        return resp.json().get("results", [])

    # ── Metrics Ingestion ─────────────────────────────────────────

    async def ingest_metrics(self, slug: str, metrics: dict) -> bool:
        self._require_listing_key()
        self._require_ingest_key()
        client = await self._ensure_client()
        resp = await client.post(
            f"/api/v1/listings/{slug}/metrics/",
            json=metrics,
            headers={"X-Ingest-Key": self.cfg.ingest_api_key},
        )
        self._check_response(resp)
        return resp.status_code in (200, 201)

    # ── Response handling ─────────────────────────────────────────

    def _check_response(self, resp: httpx.Response) -> None:
        if resp.status_code == 429:
            retry = int(resp.headers.get("Retry-After", "60"))
            raise RateLimitError(
                f"Marketplace rate limit exceeded (retry after {retry}s)",
                retry_after=retry,
            )
        if resp.status_code >= 400:
            try:
                body = resp.json()
                detail = body.get("detail", body.get("error", resp.text))
            except Exception:
                detail = resp.text
            raise MarketplaceError(
                f"Marketplace API error {resp.status_code}: {detail}",
                details={"status_code": resp.status_code, "body": detail},
            )
