"""
WS4 — Realized-P&L attribution + high-water-mark performance-fee engine.

Two concerns, strictly separated:

1. SUBSCRIBER realized P&L (`get_my_realized_pnl`) — segregates LIVE fills
   (subscriber_fills.is_live = TRUE, real broker) from PAPER fills (simulated).
   Paper results carry the CFTC Reg. 4.41(b) hypothetical-performance disclaimer;
   live results carry an actual-results note. They are NEVER co-mingled.

2. CREATOR P&L reconciliation (`reconcile_creator_pnl`, owner-gated) — attributes
   each subscriber's OWN net realized P&L to the strategy's creator, per the 2026
   copy-trading standard (per-subscriber, then summed; net of fees; closed
   positions only; one source of truth = the copiers' realized fills). Writes the
   creator_strategy_pnl ledger with a period-scoped idempotency guard.

### High-water-mark performance fee — DISABLED BY DEFAULT

Per researched legal precedent (CFTC/NFA), charging a **performance/incentive
fee on auto-copied/directed trading looks like discretionary CTA activity** and
likely requires CTA registration + NFA membership + mandatory incentive-fee
conflict disclosure. We therefore keep the platform revenue model on the
DEFENSIBLE flat-subscription + usage basis: `PERFORMANCE_FEE_RATE = 0.0`. The
HWM math exists as infrastructure but does not charge anything until an owner
explicitly enables it AND counsel approves. See docs/LEGAL_COMPLIANCE_AUDIT.md.

The HWM formula (per (subscriber, strategy) account, crystallized on REALIZED
P&L to avoid clawback):

    fee_base = max(realized_pnl_to_date - prior_hwm, 0)
    perf_fee = fee_base * rate
    new_hwm  = max(prior_hwm, realized_pnl_to_date - perf_fee)

Drawdowns are recovered for free (the HWM is never lowered).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("algochains.realized_pnl")

# Performance fee is OFF by default for the regulatory reasons above. An owner
# may set ALGOCHAINS_PERFORMANCE_FEE_RATE (e.g. "0.20") to enable it — but only
# after legal sign-off and the required CTA/NFA disclosures are in place.
DEFAULT_PERFORMANCE_FEE_RATE = 0.0
REVENUE_SHARE_PCT = 80.0


def _service_client():
    try:
        from ..marketplace.supabase_tools import _get_sb_client
    except Exception as exc:  # pragma: no cover
        log.warning("supabase_tools unavailable: %s", exc)
        return None
    return _get_sb_client(use_service_role=True)


def _err(msg: str, **extra: Any) -> dict[str, Any]:
    out = {"error": msg}
    out.update(extra)
    return out


def performance_fee_rate() -> float:
    """Effective performance-fee rate. 0.0 unless explicitly enabled by an owner."""
    try:
        return float(os.environ.get("ALGOCHAINS_PERFORMANCE_FEE_RATE", str(DEFAULT_PERFORMANCE_FEE_RATE)))
    except (TypeError, ValueError):
        return DEFAULT_PERFORMANCE_FEE_RATE


def compute_hwm_performance_fee(
    prior_hwm: float,
    realized_pnl_to_date: float,
    rate: float | None = None,
) -> dict[str, float]:
    """Pure HWM performance-fee calculation. No I/O, no side effects.

    Charges only on NEW profit above the prior high-water mark; drawdowns are
    recovered free (HWM never lowered). Returns fee_base, perf_fee, new_hwm.
    """
    r = performance_fee_rate() if rate is None else float(rate)
    fee_base = max(realized_pnl_to_date - prior_hwm, 0.0)
    perf_fee = round(fee_base * r, 2)
    new_hwm = max(prior_hwm, realized_pnl_to_date - perf_fee)
    return {
        "fee_base_usd": round(fee_base, 2),
        "perf_fee_usd": perf_fee,
        "new_hwm_usd": round(new_hwm, 2),
        "rate": r,
    }


def get_my_realized_pnl(subscriber_id: str) -> dict[str, Any]:
    """Subscriber realized P&L, LIVE and PAPER strictly segregated.

    Live = real broker fills (is_live=TRUE). Paper = simulated (CFTC 4.41(b)).
    """
    sb = _service_client()
    if sb is None:
        return _err("supabase_unavailable")

    try:
        resp = (
            sb.table("subscriber_fills")
            .select("pnl_usd,is_live,bot,fill_kind,filled_at")
            .eq("subscriber_id", subscriber_id)
            .order("filled_at", desc=True)
            .limit(1000)
            .execute()
        )
        rows = list(getattr(resp, "data", None) or [])
    except Exception as exc:
        return _err("query_failed", detail=str(exc))

    live_pnl = sum(float(r.get("pnl_usd") or 0) for r in rows if r.get("is_live"))
    paper_pnl = sum(float(r.get("pnl_usd") or 0) for r in rows if not r.get("is_live"))
    live_count = sum(1 for r in rows if r.get("is_live"))
    paper_count = sum(1 for r in rows if not r.get("is_live"))

    from ..compliance.disclosures import (
        HYPOTHETICAL_PERFORMANCE_DISCLAIMER,
        PAST_PERFORMANCE_DISCLAIMER,
    )

    return {
        "subscriber_id": subscriber_id,
        "live": {
            "realized_pnl_usd": round(live_pnl, 2),
            "fills": live_count,
            "note": "Actual realized results from real broker fills.",
        },
        "paper": {
            "realized_pnl_usd": round(paper_pnl, 2),
            "fills": paper_count,
            "hypothetical_performance_disclaimer": HYPOTHETICAL_PERFORMANCE_DISCLAIMER,
        },
        "disclaimer": PAST_PERFORMANCE_DISCLAIMER,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


async def reconcile_creator_pnl(
    period_start: str,
    period_end: str,
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Attribute subscribers' LIVE net realized P&L to strategy creators.

    Per the 2026 copy-trading standard: compute each subscriber's own net
    realized P&L (closed/live fills only), then SUM over distinct subscribers per
    strategy → creator. One source of truth (copiers' fills). Period-scoped
    idempotency key prevents double-counting. dry_run=True returns the plan only.

    NOTE: this is an OWNER-gated reconciliation. It records the creator_strategy_pnl
    ledger but does NOT itself move money — payout is run separately via
    connect_payouts.run_creator_payouts after counsel-approved review.
    """
    sb = _service_client()
    if sb is None:
        return _err("supabase_unavailable")

    # 1. Pull LIVE fills in the period that carry a signal_id (copy-trade origin).
    try:
        fills_resp = (
            sb.table("subscriber_fills")
            .select("subscriber_id,signal_id,pnl_usd,filled_at")
            .eq("is_live", True)
            .gte("filled_at", period_start)
            .lt("filled_at", period_end)
            .not_.is_("signal_id", "null")
            .limit(10000)
            .execute()
        )
        fills = list(getattr(fills_resp, "data", None) or [])
    except Exception as exc:
        return _err("fills_query_failed", detail=str(exc))

    if not fills:
        return {"dry_run": dry_run, "period_start": period_start, "period_end": period_end,
                "creators": [], "note": "No live copy-trade fills in period."}

    # 2. Map signal_id -> strategy_id via copy_trade_signals.
    signal_ids = sorted({f["signal_id"] for f in fills if f.get("signal_id")})
    sig_to_strategy: dict[str, str] = {}
    try:
        sig_resp = (
            sb.table("copy_trade_signals")
            .select("id,strategy_id")
            .in_("id", signal_ids)
            .execute()
        )
        for s in (getattr(sig_resp, "data", None) or []):
            if s.get("strategy_id"):
                sig_to_strategy[s["id"]] = s["strategy_id"]
    except Exception as exc:
        return _err("signal_join_failed", detail=str(exc))

    # 3. Per (strategy, subscriber) net realized P&L; only positive contributes
    #    (loss periods cost nothing — drawdown recovered free, per HWM logic).
    per_strategy_sub: dict[tuple[str, str], float] = {}
    for f in fills:
        strat = sig_to_strategy.get(f.get("signal_id"))
        if not strat:
            continue
        key = (strat, f["subscriber_id"])
        per_strategy_sub[key] = per_strategy_sub.get(key, 0.0) + float(f.get("pnl_usd") or 0)

    # 4. Map strategy -> creator via marketplace_listing.
    strategies = sorted({k[0] for k in per_strategy_sub})
    strat_to_creator: dict[str, str] = {}
    try:
        ml_resp = (
            sb.table("marketplace_listing")
            .select("id,creator_id")
            .in_("id", strategies)
            .execute()
        )
        for m in (getattr(ml_resp, "data", None) or []):
            if m.get("creator_id"):
                strat_to_creator[m["id"]] = m["creator_id"]
    except Exception as exc:
        log.warning("creator map lookup failed: %s", exc)

    # 5. Aggregate to creator: sum each subscriber's POSITIVE net realized P&L.
    share_pct = REVENUE_SHARE_PCT / 100.0
    creator_rollup: dict[str, dict[str, float]] = {}
    for (strat, _sub), net in per_strategy_sub.items():
        creator = strat_to_creator.get(strat)
        if not creator:
            continue
        bucket = creator_rollup.setdefault(creator, {"gross_realized_pnl_usd": 0.0, "creator_share_usd": 0.0})
        contrib = max(net, 0.0)  # loss periods cost nothing
        bucket["gross_realized_pnl_usd"] += contrib
        bucket["creator_share_usd"] += contrib * share_pct

    plan = [
        {
            "creator_id": cid,
            "gross_realized_pnl_usd": round(v["gross_realized_pnl_usd"], 2),
            "creator_share_usd": round(v["creator_share_usd"], 2),
            "revenue_share_pct": REVENUE_SHARE_PCT,
        }
        for cid, v in sorted(creator_rollup.items())
    ]

    if dry_run:
        return {"dry_run": True, "period_start": period_start, "period_end": period_end,
                "creators": plan, "note": "Plan only — no ledger rows written."}

    # 6. Write creator_strategy_pnl rows (period-scoped; idempotency via the
    #    unique-ish (creator, period) tuple — caller should not re-run a locked period).
    written = []
    for row in plan:
        try:
            sb.table("creator_strategy_pnl").insert({
                "creator_id": row["creator_id"],
                "period_start": period_start,
                "period_end": period_end,
                "gross_realized_pnl_usd": row["gross_realized_pnl_usd"],
                "creator_share_usd": row["creator_share_usd"],
                "revenue_share_pct": REVENUE_SHARE_PCT,
                "triggered_payout": False,
            }).execute()
            written.append(row["creator_id"])
        except Exception as exc:
            log.error("creator_strategy_pnl insert failed for %s: %s", row["creator_id"], exc)

    return {"dry_run": False, "period_start": period_start, "period_end": period_end,
            "creators": plan, "written": written}


__all__ = [
    "compute_hwm_performance_fee",
    "performance_fee_rate",
    "get_my_realized_pnl",
    "reconcile_creator_pnl",
    "REVENUE_SHARE_PCT",
]
