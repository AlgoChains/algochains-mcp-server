"""
prediction_market_metrics.py — Prediction Market Bot Metrics (Marketplace Path)
================================================================================

Append-only JSONL log for **real** bot performance snapshots (Polymarket / Kalshi).
Used for marketplace validation: latency vs reference feed, edge vs entry price,
YES vs NO positioning, and audit trail before promotion.

**No synthetic metrics** — every row is written only when a bot (or agent) calls
``record_bot_metric_snapshot`` with observed values from live APIs or live orders.

SEC-2026-C10. That paragraph was a promise with nothing behind it. Any smart-mode
MCP caller could append arbitrary rows — `bot_id` and `market_id` are
caller-supplied strings, and the only validation was that `platform` is one of two
literals. A caller could invent a bot that never traded and give it a flattering
latency and edge history, and the file is what "audit trail before promotion"
means here.

Two changes, and the second matters more than the first:

1. The MCP writer is now ORDER_EXEC (see `tool_danger_tiers.py`), so it demands
   `owner_token` + `confirm`, matching SEC-2026-C4's handling of
   `upsert_bot_performance`. No new shared secret is introduced — a secret this
   module invented would have to be placed by the operator before the gate did
   anything, and an unplaced secret is not a gate.

2. Rows carry provenance, and reads report how many rows lack it. Gating the
   writer protects rows written from now on; it does nothing for the rows already
   in the file, which remain indistinguishable from authentic ones. A reader that
   returns those silently is the actual defect — it reports an audit trail over
   data whose origin nobody established. `read_recent_metrics` now returns
   `attested` / `unattested` counts so a promotion decision can see what it is
   standing on.

Provenance is an *origin marker*, not a signature. It records which path wrote a
row; it does not prove the values were observed from a live exchange. Requiring
that proof means a signed bot identity and an exchange-side cross-check, which is
a larger build — recorded as an open item rather than implied by this field.

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
    written_by: str = "unattested",
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
        # Which path wrote this row. Defaults to "unattested" so a caller that
        # forgets to pass it does not silently inherit credibility.
        "written_by": (written_by or "unattested").strip() or "unattested",
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
    """Read the last N JSONL rows for a bot_id (newest last).

    Reports `attested` / `unattested` alongside the rows. Every row written before
    SEC-2026-C10 predates the owner gate and counts as unattested — it may be
    perfectly genuine, but nothing recorded where it came from, and a promotion
    decision should see that rather than infer trust from the file's name.
    """
    bid = (bot_id or "").strip()
    if not bid:
        return {"success": False, "error": "bot_id required"}
    if not _METRICS_PATH.exists():
        return {
            "success": True, "bot_id": bid, "entries": [], "count": 0,
            "attested": 0, "unattested": 0,
        }

    lines = _METRICS_PATH.read_text(encoding="utf-8").strip().splitlines()
    selected: list[dict[str, Any]] = []
    for line in lines[-max_lines:]:
        try:
            obj = json.loads(line)
            if obj.get("bot_id") == bid:
                # Absent field == written before provenance existed.
                obj.setdefault("written_by", "unattested")
                selected.append(obj)
        except json.JSONDecodeError:
            continue

    unattested = sum(1 for r in selected if r.get("written_by") == "unattested")
    out: dict[str, Any] = {
        "success": True,
        "bot_id": bid,
        "entries": selected,
        "count": len(selected),
        "attested": len(selected) - unattested,
        "unattested": unattested,
    }
    if unattested:
        out["provenance_warning"] = (
            f"{unattested} of {len(selected)} rows carry no provenance — written before "
            "SEC-2026-C10 gated this log, or by an unauthenticated caller. Origin is "
            "unestablished; do not treat these as verified promotion evidence."
        )
    return out
