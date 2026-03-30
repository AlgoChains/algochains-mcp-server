"""Transaction Cost Analysis engine."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class TCAEngine:
    """Transaction Cost Analysis."""

    def __init__(self) -> None:
        self._records: list[dict] = []

    async def analyze(self, trades: list[dict], benchmark: str = "arrival_price") -> dict:
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

    async def get_report(self, date_range: dict | None = None, groupby: str = "venue") -> dict:
        try:
            return {
                "status": "ok",
                "date_range": date_range,
                "groupby": groupby,
                "total_records": len(self._records),
                "report": self._records[-100:],
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def get_analytics(self, lookback_days: int = 30) -> dict:
        try:
            total = len(self._records)
            avg_slip = sum(r.get("slippage_bps", 0) for r in self._records) / max(total, 1)
            return {
                "status": "ok",
                "lookback_days": lookback_days,
                "total_trades_analyzed": total,
                "avg_slippage_bps": round(avg_slip, 2),
                "fill_rate_pct": 100.0,
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}
