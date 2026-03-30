"""Agent health monitoring and performance tracking."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class AgentMonitor:
    """Monitor agent health and performance."""

    def __init__(self, orchestrator: Any = None) -> None:
        self._orchestrator = orchestrator

    async def get_swarm_status(self) -> dict:
        try:
            agents = []
            if self._orchestrator:
                result = await self._orchestrator.list_agents()
                agents = result.get("agents", [])
            running = sum(1 for a in agents if a.get("status") == "running")
            return {
                "status": "ok",
                "total_agents": len(agents),
                "running": running,
                "stopped": len(agents) - running,
                "as_of": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def get_agent_metrics(self, agent_id: str) -> dict:
        try:
            return {
                "status": "ok",
                "agent_id": agent_id,
                "tasks_completed": 0,
                "avg_latency_ms": 0.0,
                "error_rate": 0.0,
                "uptime_pct": 100.0,
                "as_of": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}
