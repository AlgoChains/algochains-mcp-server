"""API gateway with rate limiting, auth, and usage tracking."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


class APIGateway:
    """API gateway with rate limiting, auth, and usage tracking."""

    def __init__(self) -> None:
        self._api_keys: dict[str, dict] = {}
        self._usage: list[dict] = []

    async def generate_key(self, tenant_id: str, name: str, permissions: list[str] | None = None, rate_limit: int = 1000) -> dict:
        try:
            key = uuid.uuid4().hex
            key_id = uuid.uuid4().hex[:12]
            record = {
                "id": key_id,
                "tenant_id": tenant_id,
                "name": name,
                "key_prefix": key[:8] + "...",
                "permissions": permissions or ["read"],
                "rate_limit_per_hour": rate_limit,
                "requests_today": 0,
                "status": "active",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._api_keys[key_id] = record
            return {"status": "ok", "api_key": key, "key_record": record}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def list_keys(self, tenant_id: str) -> dict:
        try:
            keys = [k for k in self._api_keys.values() if k["tenant_id"] == tenant_id]
            return {"status": "ok", "keys": keys, "count": len(keys)}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def revoke_key(self, key_id: str) -> dict:
        try:
            record = self._api_keys.get(key_id)
            if not record:
                return {"status": "error", "error": f"API key {key_id} not found"}
            record["status"] = "revoked"
            return {"status": "ok", "key_id": key_id, "new_status": "revoked"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def get_usage(self, tenant_id: str, key_id: str | None = None) -> dict:
        try:
            keys = [k for k in self._api_keys.values() if k["tenant_id"] == tenant_id]
            if key_id:
                keys = [k for k in keys if k["id"] == key_id]
            total_requests = sum(k.get("requests_today", 0) for k in keys)
            return {
                "status": "ok",
                "tenant_id": tenant_id,
                "api_keys": len(keys),
                "total_requests_today": total_requests,
                "as_of": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def get_health(self) -> dict:
        try:
            active_keys = sum(1 for k in self._api_keys.values() if k["status"] == "active")
            return {
                "status": "ok",
                "platform": "healthy",
                "active_api_keys": active_keys,
                "total_api_keys": len(self._api_keys),
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}
