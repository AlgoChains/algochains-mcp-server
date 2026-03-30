"""Multi-agent orchestration — spawn, manage, and coordinate trading agents."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


class AgentOrchestrator:
    """Spawn, manage, and coordinate trading agents."""

    def __init__(self) -> None:
        self._agents: dict[str, dict] = {}

    async def spawn_agent(self, agent_type: str, config: dict, name: str | None = None) -> dict:
        try:
            agent_id = uuid.uuid4().hex[:12]
            agent = {
                "id": agent_id,
                "name": name or f"agent_{agent_id}",
                "type": agent_type,
                "config": config,
                "status": "running",
                "tasks_completed": 0,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._agents[agent_id] = agent
            return {"status": "ok", "agent": agent}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def stop_agent(self, agent_id: str) -> dict:
        try:
            agent = self._agents.get(agent_id)
            if not agent:
                return {"status": "error", "error": f"Agent {agent_id} not found"}
            agent["status"] = "stopped"
            return {"status": "ok", "agent_id": agent_id, "new_status": "stopped"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def list_agents(self, status: str | None = None) -> dict:
        try:
            agents = list(self._agents.values())
            if status:
                agents = [a for a in agents if a["status"] == status]
            return {"status": "ok", "agents": agents, "count": len(agents)}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def get_agent(self, agent_id: str) -> dict:
        try:
            agent = self._agents.get(agent_id)
            if not agent:
                return {"status": "error", "error": f"Agent {agent_id} not found"}
            return {"status": "ok", "agent": agent}
        except Exception as e:
            return {"status": "error", "error": str(e)}
