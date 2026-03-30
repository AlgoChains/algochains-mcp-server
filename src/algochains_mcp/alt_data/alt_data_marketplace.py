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

    async def list_datasets(self, category: str | None = None) -> dict:
        try:
            datasets = list(self._datasets.values())
            if category:
                datasets = [d for d in datasets if d.get("category") == category]
            return {"status": "ok", "datasets": datasets, "count": len(datasets)}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def subscribe(self, dataset_id: str) -> dict:
        try:
            ds = self._datasets.get(dataset_id)
            if not ds:
                return {"status": "error", "error": f"Dataset {dataset_id} not found"}
            sub_id = uuid.uuid4().hex[:12]
            sub = {"id": sub_id, "dataset_id": dataset_id, "subscribed_at": datetime.now(timezone.utc).isoformat()}
            self._subscriptions[sub_id] = sub
            return {"status": "ok", "subscription": sub}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def publish(self, name: str, category: str, schema: dict, pricing: dict) -> dict:
        try:
            ds_id = uuid.uuid4().hex[:12]
            ds = {
                "id": ds_id, "name": name, "category": category, "schema": schema,
                "pricing": pricing, "published_at": datetime.now(timezone.utc).isoformat(),
            }
            self._datasets[ds_id] = ds
            return {"status": "ok", "dataset": ds}
        except Exception as e:
            return {"status": "error", "error": str(e)}
