"""StrategyDeployer — deploy validated strategies to paper or live trading."""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .spec import StrategySpec, StrategyStatus

logger = logging.getLogger("algochains_mcp.strategy_builder.deployer")

_STATE_DIR = Path(os.getenv("ALGOCHAINS_STATE_DIR", "state"))
_DEPLOY_FILE = _STATE_DIR / "deployments.json"


def _load_deployments() -> dict[str, dict[str, Any]]:
    if _DEPLOY_FILE.exists():
        try:
            return json.loads(_DEPLOY_FILE.read_text())
        except Exception as e:
            logger.warning("Could not load deployments file: %s", e)
    return {}


def _save_deployments(deploys: dict[str, dict[str, Any]]) -> None:
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        _DEPLOY_FILE.write_text(json.dumps(deploys, indent=2, default=str))
    except Exception as e:
        logger.error("Could not persist deployments: %s", e)


class StrategyDeployer:
    """
    Deploy a validated StrategySpec to paper or live trading on a connected broker.

    Deployments are persisted to state/deployments.json and survive server restarts.
    Live mode requires the strategy to be in VALIDATED or BACKTESTED status.
    """

    def __init__(self) -> None:
        self._deployments: dict[str, dict[str, Any]] = _load_deployments()

    async def deploy(
        self,
        spec: StrategySpec,
        broker: str,
        mode: str = "paper",
        capital: float = 10_000.0,
    ) -> dict[str, Any]:
        if mode == "live" and spec.status not in (
            StrategyStatus.VALIDATED.value,
            StrategyStatus.BACKTESTED.value,
            "validated",
            "backtested",
        ):
            return {
                "success": False,
                "error": (
                    f"Strategy must be validated before live deployment. "
                    f"Current status: {spec.status}. Run validate_strategy() first."
                ),
                "spec_id": spec.id,
            }

        deployment_id = f"dep_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()

        deployment: dict[str, Any] = {
            "deployment_id": deployment_id,
            "spec_id": spec.id,
            "spec_name": spec.name,
            "broker": broker,
            "mode": mode,
            "capital": capital,
            "symbols": spec.symbols,
            "timeframe": spec.timeframe,
            "status": "active",
            "created_at": now,
            "updated_at": now,
            "position_sizing": spec.position_sizing,
            "exit_rules": spec.exit_rules,
        }

        self._deployments[deployment_id] = deployment
        _save_deployments(self._deployments)
        spec.status = StrategyStatus.DEPLOYED.value

        logger.info(
            "Strategy '%s' deployed (id=%s): broker=%s mode=%s capital=$%.2f",
            spec.name, deployment_id, broker, mode, capital,
        )

        return {
            "success": True,
            "deployment": deployment,
            "next_steps": (
                f"Strategy deployed to {broker} in {mode} mode with ${capital:,.2f} capital. "
                "Deployment is persisted and survives server restarts. "
                "Monitor via list_deployments() or stop_deployment(deployment_id=...)."
            ),
        }

    async def list_deployments(
        self,
        status_filter: str | None = None,
        broker_filter: str | None = None,
    ) -> dict[str, Any]:
        deploys = list(self._deployments.values())
        if status_filter:
            deploys = [d for d in deploys if d.get("status") == status_filter]
        if broker_filter:
            deploys = [d for d in deploys if d.get("broker") == broker_filter]
        return {
            "count": len(deploys),
            "deployments": deploys,
            "persisted_to": str(_DEPLOY_FILE),
        }

    async def get_deployment(self, deployment_id: str) -> dict[str, Any]:
        dep = self._deployments.get(deployment_id)
        if not dep:
            return {"success": False, "error": f"Deployment '{deployment_id}' not found."}
        return {"success": True, "deployment": dep}

    async def stop_deployment(self, deployment_id: str) -> dict[str, Any]:
        dep = self._deployments.get(deployment_id)
        if not dep:
            return {"success": False, "error": f"Deployment '{deployment_id}' not found."}
        dep["status"] = "stopped"
        dep["stopped_at"] = datetime.now(timezone.utc).isoformat()
        dep["updated_at"] = dep["stopped_at"]
        _save_deployments(self._deployments)
        logger.info("Deployment %s stopped", deployment_id)
        return {"success": True, "deployment": dep}

    async def update_deployment_status(
        self, deployment_id: str, status: str, notes: str = ""
    ) -> dict[str, Any]:
        """Update deployment status (e.g. active → paused → stopped)."""
        valid = {"active", "paused", "stopped", "error"}
        if status not in valid:
            return {"success": False, "error": f"status must be one of: {valid}"}
        dep = self._deployments.get(deployment_id)
        if not dep:
            return {"success": False, "error": f"Deployment '{deployment_id}' not found."}
        dep["status"] = status
        dep["updated_at"] = datetime.now(timezone.utc).isoformat()
        if notes:
            dep["notes"] = notes
        _save_deployments(self._deployments)
        return {"success": True, "deployment": dep}
