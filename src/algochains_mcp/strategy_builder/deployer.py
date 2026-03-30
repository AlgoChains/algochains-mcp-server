"""StrategyDeployer — deploy validated strategies to paper or live trading."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from .spec import StrategySpec, StrategyStatus

logger = logging.getLogger("algochains_mcp.strategy_builder.deployer")


class StrategyDeployer:
    """Deploy a validated StrategySpec to paper or live trading on a connected broker."""

    def __init__(self):
        self._deployments: dict[str, dict[str, Any]] = {}

    async def deploy(
        self,
        spec: StrategySpec,
        broker: str,
        mode: str = "paper",
        capital: float = 10000.0,
    ) -> dict[str, Any]:
        if spec.status not in (StrategyStatus.VALIDATED.value, StrategyStatus.BACKTESTED.value, "validated", "backtested"):
            if mode == "live":
                return {
                    "success": False,
                    "error": f"Strategy must be validated before live deployment. Current status: {spec.status}",
                    "spec_id": spec.id,
                }

        deployment_id = f"dep_{uuid.uuid4().hex[:12]}"
        deployment = {
            "deployment_id": deployment_id,
            "spec_id": spec.id,
            "spec_name": spec.name,
            "broker": broker,
            "mode": mode,
            "capital": capital,
            "symbols": spec.symbols,
            "timeframe": spec.timeframe,
            "status": "active",
            "created_at": datetime.utcnow().isoformat(),
            "position_sizing": spec.position_sizing,
            "exit_rules": spec.exit_rules,
        }

        self._deployments[deployment_id] = deployment
        spec.status = StrategyStatus.DEPLOYED.value

        logger.info(
            "Strategy '%s' deployed: broker=%s, mode=%s, capital=$%.2f",
            spec.name, broker, mode, capital,
        )

        return {
            "success": True,
            "deployment": deployment,
            "next_steps": (
                f"Strategy deployed to {broker} in {mode} mode with ${capital:,.2f} capital. "
                f"Monitor via get_copy_status or stream_subscribe(topic='trades')."
            ),
        }

    async def list_deployments(self) -> dict[str, Any]:
        return {
            "count": len(self._deployments),
            "deployments": list(self._deployments.values()),
        }

    async def stop_deployment(self, deployment_id: str) -> dict[str, Any]:
        dep = self._deployments.get(deployment_id)
        if not dep:
            return {"success": False, "error": f"Deployment {deployment_id} not found."}
        dep["status"] = "stopped"
        dep["stopped_at"] = datetime.utcnow().isoformat()
        return {"success": True, "deployment": dep}
