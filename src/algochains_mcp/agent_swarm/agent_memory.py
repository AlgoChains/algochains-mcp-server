"""Shared memory and context for agent swarms."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class AgentMemory:
    """Shared memory store for agent coordination."""

    def __init__(self) -> None:
        self._memory: list[dict] = []

    async def store(self, agent_id: str, memory_type: str, content: Any) -> dict:
        try:
            entry = {"agent_id": agent_id, "memory_type": memory_type, "content": content, "stored_at": datetime.now(timezone.utc).isoformat()}
            self._memory.append(entry)
            return {"status": "ok", "entry": entry}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def query(self, query: str, memory_type: str | None = None, agent_id: str | None = None, limit: int = 10) -> dict:
        try:
            results = self._memory[:]
            if memory_type:
                results = [m for m in results if m.get("memory_type") == memory_type]
            if agent_id:
                results = [m for m in results if m.get("agent_id") == agent_id]
            return {"status": "ok", "results": results[:limit], "count": len(results)}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def get_stats(self, agent_id: str | None = None) -> dict:
        try:
            entries = self._memory
            if agent_id:
                entries = [m for m in entries if m.get("agent_id") == agent_id]
            types = {}
            for m in entries:
                t = m.get("memory_type", "unknown")
                types[t] = types.get(t, 0) + 1
            return {"status": "ok", "total_entries": len(entries), "by_type": types}
        except Exception as e:
            return {"status": "error", "error": str(e)}
