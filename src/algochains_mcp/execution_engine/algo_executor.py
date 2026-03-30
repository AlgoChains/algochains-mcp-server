"""Algorithmic execution strategies — TWAP, VWAP, Iceberg, Sniper."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


class AlgoExecutor:
    """Algorithmic execution strategies."""

    ALGO_TYPES = ("twap", "vwap", "iceberg", "sniper")

    def __init__(self) -> None:
        self._algo_orders: dict[str, dict] = {}

    async def execute_twap(self, order: dict, duration_minutes: int = 30, slice_count: int = 10) -> dict:
        return await self._create_algo_order("twap", order, {"duration_minutes": duration_minutes, "slice_count": slice_count})

    async def execute_vwap(self, order: dict, participation_rate: float = 0.1, max_duration: int = 60) -> dict:
        return await self._create_algo_order("vwap", order, {"participation_rate": participation_rate, "max_duration": max_duration})

    async def execute_iceberg(self, order: dict, visible_qty: float = 100, variance_pct: float = 10) -> dict:
        return await self._create_algo_order("iceberg", order, {"visible_qty": visible_qty, "variance_pct": variance_pct})

    async def execute_sniper(self, order: dict, target_price: float = 0.0, urgency: str = "medium") -> dict:
        return await self._create_algo_order("sniper", order, {"target_price": target_price, "urgency": urgency})

    async def _create_algo_order(self, algo_type: str, order: dict, params: dict) -> dict:
        try:
            algo_id = uuid.uuid4().hex[:12]
            algo_order = {
                "id": algo_id,
                "algo_type": algo_type,
                "symbol": order.get("symbol", ""),
                "side": order.get("side", ""),
                "total_qty": order.get("qty", 0),
                "filled_qty": 0,
                "avg_fill_price": None,
                "status": "active",
                "params": params,
                "child_orders": [],
                "started_at": datetime.now(timezone.utc).isoformat(),
                "completed_at": None,
            }
            self._algo_orders[algo_id] = algo_order
            return {"status": "ok", "algo_order": algo_order}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def get_status(self, algo_order_id: str) -> dict:
        try:
            order = self._algo_orders.get(algo_order_id)
            if not order:
                return {"status": "error", "error": f"Algo order {algo_order_id} not found"}
            pct = (order["filled_qty"] / order["total_qty"] * 100) if order["total_qty"] > 0 else 0
            return {"status": "ok", "algo_order": order, "fill_pct": round(pct, 2)}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def cancel(self, algo_order_id: str) -> dict:
        try:
            order = self._algo_orders.get(algo_order_id)
            if not order:
                return {"status": "error", "error": f"Algo order {algo_order_id} not found"}
            order["status"] = "cancelled"
            order["completed_at"] = datetime.now(timezone.utc).isoformat()
            return {"status": "ok", "algo_order_id": algo_order_id, "new_status": "cancelled"}
        except Exception as e:
            return {"status": "error", "error": str(e)}
