"""Strategy marketplace — publish, discover, and subscribe to trading strategies."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


class StrategyMarketplace:
    """Publish, discover, and subscribe to trading strategies."""

    def __init__(self) -> None:
        self._listings: dict[str, dict] = {}
        self._subscriptions: dict[str, dict] = {}

    async def publish_strategy(self, name: str, description: str, author: str, price_monthly: float = 0.0, performance: dict | None = None) -> dict:
        try:
            listing_id = uuid.uuid4().hex[:12]
            listing = {
                "id": listing_id,
                "name": name,
                "description": description,
                "author": author,
                "price_monthly": price_monthly,
                "performance": performance or {},
                "subscribers": 0,
                "status": "active",
                "published_at": datetime.now(timezone.utc).isoformat(),
            }
            self._listings[listing_id] = listing
            return {"status": "ok", "listing": listing}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def search(self, query: str | None = None, min_sharpe: float | None = None) -> dict:
        try:
            results = list(self._listings.values())
            if query:
                results = [r for r in results if query.lower() in r["name"].lower() or query.lower() in r["description"].lower()]
            return {"status": "ok", "results": results, "count": len(results)}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def subscribe(self, listing_id: str, tenant_id: str) -> dict:
        try:
            listing = self._listings.get(listing_id)
            if not listing:
                return {"status": "error", "error": f"Listing {listing_id} not found"}
            sub_id = uuid.uuid4().hex[:12]
            sub = {"id": sub_id, "listing_id": listing_id, "tenant_id": tenant_id, "subscribed_at": datetime.now(timezone.utc).isoformat()}
            self._subscriptions[sub_id] = sub
            listing["subscribers"] += 1
            return {"status": "ok", "subscription": sub}
        except Exception as e:
            return {"status": "error", "error": str(e)}
