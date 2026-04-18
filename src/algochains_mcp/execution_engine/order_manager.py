"""Institutional order management with pre-trade checks.

SIMULATION MODE (default): submit_order returns synthetic in-memory UUIDs and
does NOT route to any broker.  Set ``ALGOCHAINS_EXECUTION_ENGINE=live`` to
signal that a real broker backend is configured (execution then flows through
the broker connector, NOT through this manager's in-memory store).

Agents MUST check ``result["status"]`` — ``"simulation"`` means no real order
was placed.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any

_EXEC_LIVE = os.getenv("ALGOCHAINS_EXECUTION_ENGINE", "").lower() == "live"
_SIM_BANNER = (
    "SIMULATION — ALGOCHAINS_EXECUTION_ENGINE is not 'live'. "
    "Order was recorded in-memory only. No broker API was called."
)


class InstitutionalOrderManager:
    """Validates and manages institutional-grade orders.

    All ``submit_order`` responses include ``"status": "simulation"`` until
    ``ALGOCHAINS_EXECUTION_ENGINE=live`` is set.
    """

    def __init__(self) -> None:
        self._orders: dict[str, dict] = {}

    async def validate_order(self, order: dict, account_id: str | None = None) -> dict:
        try:
            errors = []
            if not order.get("symbol"):
                errors.append("symbol is required")
            if not order.get("side"):
                errors.append("side is required")
            if not order.get("qty") or order["qty"] <= 0:
                errors.append("qty must be positive")
            if errors:
                return {"status": "error", "errors": errors}
            return {"status": "ok", "valid": True, "account_id": account_id}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def submit_order(self, order: dict, account_id: str | None = None, compliance_override: bool = False) -> dict:
        try:
            if not compliance_override:
                validation = await self.validate_order(order, account_id)
                if validation.get("status") == "error":
                    return validation
            order_id = uuid.uuid4().hex[:12]
            record = {
                "id": order_id,
                **order,
                "account_id": account_id,
                "compliance_override": compliance_override,
                "status": "pending",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._orders[order_id] = record
            status = "ok" if _EXEC_LIVE else "simulation"
            result: dict = {"status": status, "order": record}
            if not _EXEC_LIVE:
                result["simulation_warning"] = _SIM_BANNER
            return result
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def get_order_status(self, order_id: str) -> dict:
        try:
            order = self._orders.get(order_id)
            if not order:
                return {"status": "error", "error": f"Order {order_id} not found"}
            return {"status": "ok", "order": order}
        except Exception as e:
            return {"status": "error", "error": str(e)}
