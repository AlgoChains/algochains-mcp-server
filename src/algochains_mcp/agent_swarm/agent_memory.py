"""Shared memory and context for agent swarms."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class AgentMemory:
    """Shared memory store for agent coordination."""

    def __init__(self) -> None:
        self._memory: dict[str, Any] = {}

    async def store(self, key: str, value: Any, agent_id: str | None = None) -> dict:
        try:
            self._memory[key] = {"value": value, "agent_id": agent_id, "stored_at": datetime.now(timezone.utc).isoformat()}
            return {"status": "ok", "key": key}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def retrieve(self, key: str) -> dict:
        try:
            entry = self._memory.get(key)
            if not entry:
                return {"status": "error", "error": f"Key {key} not found"}
            return {"status": "ok", "key": key, "data": entry}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def list_keys(self, prefix: str | None = None) -> dict:
        try:
            keys = list(self._memory.keys())
            if prefix:
                keys = [k for k in keys if k.startswith(prefix)]
            return {"status": "ok", "keys": keys, "count": len(keys)}
        except Exception as e:
            return {"status": "error", "error": str(e)}
