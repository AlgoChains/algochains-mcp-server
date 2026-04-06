"""Execution venue registry and management."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


DEFAULT_VENUES = [
    {"id": "nyse", "name": "NYSE", "venue_type": "lit", "avg_latency_ms": 2.5, "maker_fee": -0.0020, "taker_fee": 0.0030},
    {"id": "nasdaq", "name": "NASDAQ", "venue_type": "lit", "avg_latency_ms": 1.8, "maker_fee": -0.0025, "taker_fee": 0.0030},
    {"id": "arca", "name": "NYSE Arca", "venue_type": "ecn", "avg_latency_ms": 2.0, "maker_fee": -0.0021, "taker_fee": 0.0030},
    {"id": "sigmax", "name": "SIGMA-X", "venue_type": "dark", "avg_latency_ms": 5.0, "maker_fee": 0.0, "taker_fee": 0.0015},
    {"id": "posit", "name": "POSIT", "venue_type": "dark", "avg_latency_ms": 8.0, "maker_fee": 0.0, "taker_fee": 0.0018},
]


class VenueManager:
    """Execution venue registry."""

    def __init__(self) -> None:
        self._venues: dict[str, dict] = {v["id"]: {**v, "status": "active", "priority": 1, "supported_assets": ["equity"], "updated_at": datetime.now(timezone.utc).isoformat()} for v in DEFAULT_VENUES}

    async def register(self, name: str, venue_type: str, config: dict | None = None) -> dict:
        try:
            venue_id = uuid.uuid4().hex[:12]
            venue = {
                "id": venue_id,
                "name": name,
                "venue_type": venue_type,
                "config": config or {},
                "status": "active",
                "priority": 1,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._venues[venue_id] = venue
            return {"status": "ok", "venue": venue}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def list_venues(self) -> dict:
        try:
            return {"status": "ok", "venues": list(self._venues.values()), "count": len(self._venues)}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def get_status(self, venue_id: str) -> dict:
        try:
            venue = self._venues.get(venue_id)
            if not venue:
                return {"status": "error", "error": f"Venue {venue_id} not found"}
            return {"status": "ok", "venue": venue}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def set_priority(self, venue_id: str, priority: int) -> dict:
        try:
            venue = self._venues.get(venue_id)
            if not venue:
                return {"status": "error", "error": f"Venue {venue_id} not found"}
            venue["priority"] = priority
            venue["updated_at"] = datetime.now(timezone.utc).isoformat()
            return {"status": "ok", "venue_id": venue_id, "priority": priority}
        except Exception as e:
            return {"status": "error", "error": str(e)}
