"""Non-production agent-swarm tool router.

Decision from Agentic Stack Gap V2: keep these swarm helpers explicitly
non-production until they are wired through the shared MCP tool policy and real
dispatch path. This module may record proposed routes for experiments, but it
must not execute tools or mutate broker/account state.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


PRODUCTION_ENABLED = False
SECRET_KEY_PARTS = ("token", "secret", "password", "api_key", "key", "credential")


def _redacted_argument_hash(arguments: dict[str, Any]) -> str:
    def redact(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: "[REDACTED]" if any(part in str(key).lower() for part in SECRET_KEY_PARTS) else redact(inner)
                for key, inner in value.items()
            }
        if isinstance(value, list):
            return [redact(item) for item in value]
        return value

    canonical = json.dumps(redact(arguments or {}), sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ToolRouter:
    """Record proposed agent tool routes without executing tools."""

    def __init__(self) -> None:
        self._call_log: list[dict] = []
        self._permissions: dict[str, list[str]] = {}

    async def route(self, agent_id: str, tool_name: str, arguments: dict) -> dict:
        try:
            entry = {
                "agent_id": agent_id,
                "tool_name": tool_name,
                "argument_hash": _redacted_argument_hash(arguments),
                "routed_at": datetime.now(timezone.utc).isoformat(),
                "production_enabled": PRODUCTION_ENABLED,
                "decision": "record_only_non_production",
                "reason": "agent_swarm tool routing is not wired to shared ToolPolicyDecision dispatch",
            }
            self._call_log.append(entry)
            return {
                "status": "blocked",
                "routed": entry,
                "message": "agent_swarm ToolRouter is record-only until policy-backed dispatch is implemented",
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def get_permissions(self, agent_id: str) -> dict:
        try:
            perms = self._permissions.get(agent_id, ["*"])
            calls = [e for e in self._call_log if e.get("agent_id") == agent_id]
            return {"status": "ok", "agent_id": agent_id, "permissions": perms, "recent_calls": len(calls)}
        except Exception as e:
            return {"status": "error", "error": str(e)}
