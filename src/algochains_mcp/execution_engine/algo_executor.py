"""Algorithmic execution strategies — TWAP, VWAP, Iceberg, Sniper."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


class AlgoExecutor:
    """Algorithmic execution strategies."""

    ALGO_TYPES = ("twap", "vwap", "iceberg", "sniper")

    def __init__(self) -> None:
        self._executions: dict[str, dict] = {}

    async def start(self, algo_type: str, order: dict, parameters: dict | None = None) -> dict:
        try:
            if algo_type not in self.ALGO_TYPES:
                return {"status": "error", "error": f"Invalid algo_type: {algo_type}. Must be one of {self.ALGO_TYPES}"}
            execution_id = uuid.uuid4().hex[:12]
            execution = {
                "id": execution_id,
                "algo_type": algo_type,
                "symbol": order.get("symbol", ""),
                "side": order.get("side", ""),
                "total_qty": order.get("qty", 0),
                "filled_qty": 0,
                "avg_fill_price": None,
                "status": "active",
                "parameters": parameters or {},
                "child_orders": [],
                "started_at": datetime.now(timezone.utc).isoformat(),
                "completed_at": None,
            }
            self._executions[execution_id] = execution
            return {"status": "ok", "execution": execution}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def stop(self, execution_id: str) -> dict:
        try:
            execution = self._executions.get(execution_id)
            if not execution:
                return {"status": "error", "error": f"Execution {execution_id} not found"}
            execution["status"] = "stopped"
            execution["completed_at"] = datetime.now(timezone.utc).isoformat()
            return {"status": "ok", "execution_id": execution_id, "new_status": "stopped"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def get_status(self, execution_id: str) -> dict:
        try:
            execution = self._executions.get(execution_id)
            if not execution:
                return {"status": "error", "error": f"Execution {execution_id} not found"}
            pct = (execution["filled_qty"] / execution["total_qty"] * 100) if execution["total_qty"] > 0 else 0
            return {"status": "ok", "execution": execution, "fill_pct": round(pct, 2)}
        except Exception as e:
            return {"status": "error", "error": str(e)}
