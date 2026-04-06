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

    async def publish(self, strategy_id: str, pricing: dict, description: str | None = None, tags: list[str] | None = None) -> dict:
        try:
            listing_id = uuid.uuid4().hex[:12]
            listing = {
                "id": listing_id,
                "strategy_id": strategy_id,
                "description": description or "",
                "pricing": pricing,
                "tags": tags or [],
                "subscribers": 0,
                "status": "active",
                "published_at": datetime.now(timezone.utc).isoformat(),
            }
            self._listings[listing_id] = listing
            return {"status": "ok", "listing": listing}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def browse(self, category: str | None = None, min_sharpe: float | None = None, max_price: float | None = None, sort_by: str = "sharpe") -> dict:
        try:
            results = list(self._listings.values())
            if category:
                results = [r for r in results if category in r.get("tags", [])]
            return {"status": "ok", "results": results, "count": len(results), "sort_by": sort_by}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def subscribe(self, tenant_id: str, strategy_id: str, allocation: float | None = None) -> dict:
        try:
            listing = None
            for l in self._listings.values():
                if l.get("strategy_id") == strategy_id:
                    listing = l
                    break
            if not listing:
                return {"status": "error", "error": f"Strategy {strategy_id} not found"}
            sub_id = uuid.uuid4().hex[:12]
            sub = {"id": sub_id, "strategy_id": strategy_id, "tenant_id": tenant_id, "allocation": allocation, "subscribed_at": datetime.now(timezone.utc).isoformat()}
            self._subscriptions[sub_id] = sub
            listing["subscribers"] += 1
            return {"status": "ok", "subscription": sub}
        except Exception as e:
            return {"status": "error", "error": str(e)}
