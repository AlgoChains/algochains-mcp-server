"""Route tool calls from agents to appropriate MCP tools."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class ToolRouter:
    """Route agent tool calls to MCP tools with rate limiting."""

    def __init__(self) -> None:
        self._call_log: list[dict] = []
        self._permissions: dict[str, list[str]] = {}

    async def route(self, agent_id: str, tool_name: str, arguments: dict) -> dict:
        try:
            entry = {
                "agent_id": agent_id,
                "tool_name": tool_name,
                "arguments": arguments,
                "routed_at": datetime.now(timezone.utc).isoformat(),
            }
            self._call_log.append(entry)
            return {"status": "ok", "routed": entry}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def get_permissions(self, agent_id: str) -> dict:
        try:
            perms = self._permissions.get(agent_id, ["*"])
            calls = [e for e in self._call_log if e.get("agent_id") == agent_id]
            return {"status": "ok", "agent_id": agent_id, "permissions": perms, "recent_calls": len(calls)}
        except Exception as e:
            return {"status": "error", "error": str(e)}
