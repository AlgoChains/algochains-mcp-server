"""
prediction_market_metrics.py — Prediction Market Bot Metrics (Marketplace Path)
================================================================================

Append-only JSONL log for **real** bot performance snapshots (Polymarket / Kalshi).
Used for marketplace validation: latency vs reference feed, edge vs entry price,
YES vs NO positioning, and audit trail before promotion.

**No synthetic metrics** — every row is written only when a bot (or agent) calls
``record_bot_metric_snapshot`` with observed values from live APIs or live orders.

File: ``state/prediction_market_bot_metrics.jsonl`` (override via
``ALGOCHAINS_PM_METRICS_PATH``).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("algochains_mcp.prediction_market_metrics")

_STATE_DIR = Path(os.getenv("ALGOCHAINS_STATE_DIR", "state"))
_METRICS_PATH = Path(
    os.getenv("ALGOCHAINS_PM_METRICS_PATH", str(_STATE_DIR / "prediction_market_bot_metrics.jsonl"))
)


def record_bot_metric_snapshot(
    bot_id: str,
    platform: str,
    market_id: str,
    yes_probability: Optional[float] = None,
    edge_vs_entry: Optional[float] = None,
    latency_ms_observed: Optional[float] = None,
    action: str = "",
    notes: str = "",
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Record one observable snapshot for a prediction-market bot run.

    Typical fields (all optional except identifiers):
      yes_probability:    Current YES price 0–1 from exchange
      edge_vs_entry:      Model-estimated edge vs entry (platform-specific units)
      latency_ms_observed: Delay vs faster reference (e.g. chainlink vs CEX)
      action:               BUY_YES | BUY_NO | SELL | HOLD | ARB | ...
    """
    bid = (bot_id or "").strip()
    plat = (platform or "").strip().lower()
    mid = (market_id or "").strip()

    if not bid or plat not in ("polymarket", "kalshi"):
        return {
            "success": False,
            "error": "bot_id and platform ('polymarket'|'kalshi') and market_id are required",
        }
    if not mid:
        return {"success": False, "error": "market_id is required"}

    row: dict[str, Any] = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "bot_id": bid,
        "platform": plat,
        "market_id": mid,
        "yes_probability": yes_probability,
        "edge_vs_entry": edge_vs_entry,
        "latency_ms_observed": latency_ms_observed,
        "action": (action or "").strip(),
        "notes": (notes or "").strip()[:2000],
        "metadata": extra or {},
    }

    _METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _METRICS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")

    logger.debug("Recorded PM bot metric bot_id=%s platform=%s", bid, plat)
    return {
        "success": True,
        "path": str(_METRICS_PATH),
        "bot_id": bid,
        "platform": plat,
        "market_id": mid,
    }


def read_recent_metrics(bot_id: str, max_lines: int = 500) -> dict[str, Any]:
    """Read the last N JSONL rows for a bot_id (newest last)."""
    bid = (bot_id or "").strip()
    if not bid:
        return {"success": False, "error": "bot_id required"}
    if not _METRICS_PATH.exists():
        return {"success": True, "bot_id": bid, "entries": [], "count": 0}

    lines = _METRICS_PATH.read_text(encoding="utf-8").strip().splitlines()
    selected: list[dict[str, Any]] = []
    for line in lines[-max_lines:]:
        try:
            obj = json.loads(line)
            if obj.get("bot_id") == bid:
                selected.append(obj)
        except json.JSONDecodeError:
            continue
    return {"success": True, "bot_id": bid, "entries": selected, "count": len(selected)}
