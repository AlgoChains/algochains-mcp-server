"""Alternative data marketplace — browse, subscribe, publish datasets."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


class AltDataMarketplace:
    """Browse, subscribe, and publish alternative data datasets."""

    def __init__(self) -> None:
        self._datasets: dict[str, dict] = {}
        self._subscriptions: dict[str, dict] = {}

    async def browse(self, category: str | None = None, data_type: str | None = None, min_quality: float | None = None) -> dict:
        try:
            datasets = list(self._datasets.values())
            if category:
                datasets = [d for d in datasets if d.get("category") == category]
            if data_type:
                datasets = [d for d in datasets if d.get("data_type") == data_type]
            return {"status": "ok", "datasets": datasets, "count": len(datasets)}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def subscribe(self, dataset_id: str, delivery_method: str | None = None) -> dict:
        try:
            ds = self._datasets.get(dataset_id)
            if not ds:
                return {"status": "error", "error": f"Dataset {dataset_id} not found"}
            sub_id = uuid.uuid4().hex[:12]
            sub = {"id": sub_id, "dataset_id": dataset_id, "delivery_method": delivery_method or "api", "subscribed_at": datetime.now(timezone.utc).isoformat()}
            self._subscriptions[sub_id] = sub
            return {"status": "ok", "subscription": sub}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def get_catalog(self) -> dict:
        try:
            return {
                "status": "ok",
                "datasets": list(self._datasets.values()),
                "total": len(self._datasets),
                "categories": list({d.get("category", "unknown") for d in self._datasets.values()}),
                "as_of": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}
