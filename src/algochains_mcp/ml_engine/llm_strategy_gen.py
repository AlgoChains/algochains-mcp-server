"""Use LLMs to generate StrategySpec from natural language."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


class LLMStrategyGenerator:
    """Use LLMs to generate StrategySpec from natural language."""

    def __init__(self) -> None:
        self._generated: dict[str, dict] = {}

    async def generate(
        self,
        description: str,
        asset_class: str = "equity",
        risk_tolerance: str = "medium",
        constraints: dict | None = None,
    ) -> dict:
        try:
            if risk_tolerance not in ("low", "medium", "high"):
                return {"status": "error", "error": f"Invalid risk_tolerance: {risk_tolerance}"}
            spec_id = uuid.uuid4().hex[:12]
            spec = {
                "id": spec_id,
                "description": description,
                "asset_class": asset_class,
                "risk_tolerance": risk_tolerance,
                "constraints": constraints,
                "generated_spec": {
                    "entry_rules": [],
                    "exit_rules": [],
                    "position_sizing": {"method": "fixed_fractional", "risk_per_trade": 0.02 if risk_tolerance == "medium" else 0.01 if risk_tolerance == "low" else 0.03},
                    "indicators": [],
                },
                "llm_model": "pending",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._generated[spec_id] = spec
            return {"status": "ok", "strategy_spec": spec}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def refine(self, spec_id: str, feedback: str) -> dict:
        try:
            spec = self._generated.get(spec_id)
            if not spec:
                return {"status": "error", "error": f"Spec {spec_id} not found"}
            spec["feedback_history"] = spec.get("feedback_history", [])
            spec["feedback_history"].append({"feedback": feedback, "at": datetime.now(timezone.utc).isoformat()})
            return {"status": "ok", "spec_id": spec_id, "feedback_applied": feedback, "refined_at": datetime.now(timezone.utc).isoformat()}
        except Exception as e:
            return {"status": "error", "error": str(e)}
