"""Strategy marketplace — publish, discover, and subscribe to trading strategies."""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("algochains_mcp.cloud_saas.marketplace")

_STATE_DIR = Path(os.getenv("ALGOCHAINS_STATE_DIR", "state"))
_MARKETPLACE_FILE = _STATE_DIR / "marketplace_listings.json"
_SUBSCRIPTIONS_FILE = _STATE_DIR / "marketplace_subscriptions.json"


def _load(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception as e:
            logger.warning("Could not load %s: %s", path, e)
    return {}


def _save(path: Path, data: dict) -> None:
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, default=str))
    except Exception as e:
        logger.error("Could not persist %s: %s", path, e)


class StrategyMarketplace:
    """Publish, discover, and subscribe to trading strategies. State is persisted across restarts."""

    def __init__(self) -> None:
        self._listings: dict[str, dict] = _load(_MARKETPLACE_FILE)
        self._subscriptions: dict[str, dict] = _load(_SUBSCRIPTIONS_FILE)

    async def publish(
        self,
        strategy_id: str,
        pricing: dict,
        description: str | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        try:
            listing_id = uuid.uuid4().hex[:12]
            now = datetime.now(timezone.utc).isoformat()
            listing: dict[str, Any] = {
                "id": listing_id,
                "strategy_id": strategy_id,
                "description": description or "",
                "pricing": pricing,
                "tags": tags or [],
                "subscribers": 0,
                "status": "active",
                "published_at": now,
                "updated_at": now,
            }
            self._listings[listing_id] = listing
            _save(_MARKETPLACE_FILE, self._listings)
            return {"status": "ok", "listing": listing}
        except Exception as e:
            logger.error("publish failed: %s", e)
            return {"status": "error", "error": str(e)}

    async def browse(
        self,
        category: str | None = None,
        min_sharpe: float | None = None,
        max_price: float | None = None,
        sort_by: str = "published_at",
    ) -> dict:
        try:
            results = list(self._listings.values())
            if category:
                results = [r for r in results if category in r.get("tags", [])]
            if max_price is not None:
                results = [r for r in results if r.get("pricing", {}).get("price", 0) <= max_price]
            results.sort(key=lambda r: r.get(sort_by, ""), reverse=True)
            return {"status": "ok", "results": results, "count": len(results), "sort_by": sort_by}
        except Exception as e:
            logger.error("browse failed: %s", e)
            return {"status": "error", "error": str(e)}

    async def get_listing(self, listing_id: str) -> dict:
        listing = self._listings.get(listing_id)
        if not listing:
            return {"status": "error", "error": f"Listing '{listing_id}' not found"}
        return {"status": "ok", "listing": listing}

    async def subscribe(
        self,
        tenant_id: str,
        strategy_id: str,
        allocation: float | None = None,
    ) -> dict:
        try:
            listing = next(
                (l for l in self._listings.values() if l.get("strategy_id") == strategy_id),
                None,
            )
            if not listing:
                return {"status": "error", "error": f"Strategy '{strategy_id}' not listed in marketplace"}
            sub_id = uuid.uuid4().hex[:12]
            now = datetime.now(timezone.utc).isoformat()
            sub: dict[str, Any] = {
                "id": sub_id,
                "strategy_id": strategy_id,
                "listing_id": listing["id"],
                "tenant_id": tenant_id,
                "allocation": allocation,
                "status": "active",
                "subscribed_at": now,
            }
            self._subscriptions[sub_id] = sub
            listing["subscribers"] = listing.get("subscribers", 0) + 1
            listing["updated_at"] = now
            _save(_MARKETPLACE_FILE, self._listings)
            _save(_SUBSCRIPTIONS_FILE, self._subscriptions)
            return {"status": "ok", "subscription": sub}
        except Exception as e:
            logger.error("subscribe failed: %s", e)
            return {"status": "error", "error": str(e)}

    async def list_subscriptions(self, tenant_id: str | None = None) -> dict:
        subs = list(self._subscriptions.values())
        if tenant_id:
            subs = [s for s in subs if s.get("tenant_id") == tenant_id]
        return {"status": "ok", "subscriptions": subs, "count": len(subs)}

    async def delist(self, listing_id: str) -> dict:
        listing = self._listings.get(listing_id)
        if not listing:
            return {"status": "error", "error": f"Listing '{listing_id}' not found"}
        listing["status"] = "delisted"
        listing["updated_at"] = datetime.now(timezone.utc).isoformat()
        _save(_MARKETPLACE_FILE, self._listings)
        return {"status": "ok", "listing": listing}
