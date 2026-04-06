"""Multi-agent orchestration — spawn, manage, and coordinate trading agents."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


class AgentOrchestrator:
    """Spawn, manage, and coordinate trading agents."""

    def __init__(self) -> None:
        self._agents: dict[str, dict] = {}

    async def spawn(self, name: str, role: str, strategy: str | None = None, capital_allocation: float | None = None) -> dict:
        try:
            agent_id = uuid.uuid4().hex[:12]
            agent = {
                "id": agent_id,
                "name": name,
                "role": role,
                "strategy": strategy,
                "capital_allocation": capital_allocation,
                "status": "running",
                "tasks_completed": 0,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._agents[agent_id] = agent
            return {"status": "ok", "agent": agent}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def list_agents(self, role_filter: str | None = None) -> dict:
        try:
            agents = list(self._agents.values())
            if role_filter:
                agents = [a for a in agents if a.get("role") == role_filter]
            return {"status": "ok", "agents": agents, "count": len(agents)}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def get_detail(self, agent_id: str) -> dict:
        try:
            agent = self._agents.get(agent_id)
            if not agent:
                return {"status": "error", "error": f"Agent {agent_id} not found"}
            return {"status": "ok", "agent": agent}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def terminate(self, agent_id: str, reason: str | None = None) -> dict:
        try:
            agent = self._agents.get(agent_id)
            if not agent:
                return {"status": "error", "error": f"Agent {agent_id} not found"}
            agent["status"] = "terminated"
            agent["termination_reason"] = reason
            return {"status": "ok", "agent_id": agent_id, "new_status": "terminated", "reason": reason}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def set_parameters(self, agent_id: str, parameters: dict) -> dict:
        try:
            agent = self._agents.get(agent_id)
            if not agent:
                return {"status": "error", "error": f"Agent {agent_id} not found"}
            agent.setdefault("parameters", {}).update(parameters)
            return {"status": "ok", "agent_id": agent_id, "parameters": agent["parameters"]}
        except Exception as e:
            return {"status": "error", "error": str(e)}
