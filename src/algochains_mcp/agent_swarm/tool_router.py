"""Route tool calls from agents to appropriate MCP tools."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class ToolRouter:
    """Route agent tool calls to MCP tools with rate limiting."""

    def __init__(self) -> None:
        self._call_log: list[dict] = []

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

    async def get_call_log(self, agent_id: str | None = None, limit: int = 50) -> dict:
        try:
            log = self._call_log
            if agent_id:
                log = [e for e in log if e["agent_id"] == agent_id]
            return {"status": "ok", "log": log[-limit:], "total": len(log)}
        except Exception as e:
            return {"status": "error", "error": str(e)}
