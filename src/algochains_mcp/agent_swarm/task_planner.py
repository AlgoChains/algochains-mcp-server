"""Task planning and decomposition for agent swarms."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


class TaskPlanner:
    """Plan and decompose tasks for agent execution."""

    def __init__(self) -> None:
        self._plans: dict[str, dict] = {}

    async def create_plan(self, goal: str, constraints: dict | None = None) -> dict:
        try:
            plan_id = uuid.uuid4().hex[:12]
            plan = {
                "id": plan_id,
                "goal": goal,
                "constraints": constraints or {},
                "steps": [],
                "status": "pending",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._plans[plan_id] = plan
            return {"status": "ok", "plan": plan}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def get_plan(self, plan_id: str) -> dict:
        try:
            plan = self._plans.get(plan_id)
            if not plan:
                return {"status": "error", "error": f"Plan {plan_id} not found"}
            return {"status": "ok", "plan": plan}
        except Exception as e:
            return {"status": "error", "error": str(e)}
