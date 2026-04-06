"""Transaction Cost Analysis engine."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class TCAEngine:
    """Transaction Cost Analysis."""

    def __init__(self) -> None:
        self._records: list[dict] = []

    async def analyze(self, trades: list[dict], benchmark: str = "vwap") -> dict:
        try:
            results = []
            for trade in trades:
                fill = trade.get("fill_price", 0)
                arrival = trade.get("arrival_price", fill)
                slippage_bps = ((fill - arrival) / arrival * 10000) if arrival else 0
                results.append({
                    "symbol": trade.get("symbol", ""),
                    "side": trade.get("side", ""),
                    "qty": trade.get("qty", 0),
                    "fill_price": fill,
                    "arrival_price": arrival,
                    "slippage_bps": round(slippage_bps, 2),
                    "benchmark": benchmark,
                })
            self._records.extend(results)
            avg_slippage = sum(r["slippage_bps"] for r in results) / max(len(results), 1)
            return {
                "status": "ok",
                "trade_count": len(results),
                "avg_slippage_bps": round(avg_slippage, 2),
                "details": results,
                "analyzed_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def get_report(self, start_date: str, end_date: str, account_id: str | None = None) -> dict:
        try:
            return {
                "status": "ok",
                "start_date": start_date,
                "end_date": end_date,
                "account_id": account_id,
                "total_records": len(self._records),
                "report": self._records[-100:],
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def implementation_shortfall(self, orders: list[dict]) -> dict:
        try:
            results = []
            for order in orders:
                decision_price = order.get("decision_price", 0)
                fill_price = order.get("fill_price", 0)
                shortfall_bps = ((fill_price - decision_price) / decision_price * 10000) if decision_price else 0
                results.append({
                    "symbol": order.get("symbol", ""),
                    "shortfall_bps": round(shortfall_bps, 2),
                    "decision_price": decision_price,
                    "fill_price": fill_price,
                })
            avg_shortfall = sum(r["shortfall_bps"] for r in results) / max(len(results), 1)
            return {
                "status": "ok",
                "order_count": len(results),
                "avg_shortfall_bps": round(avg_shortfall, 2),
                "details": results,
                "analyzed_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}
