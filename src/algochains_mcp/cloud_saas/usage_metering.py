"""
usage_metering.py — Usage-based metered-billing ledger for the MCP server.

This module owns the LOCAL usage ledger only:
  - record_usage()       → bump the (key_hash, period_month) counter + audit row
  - get_usage_summary()  → read the current-month rollup with cost projection

Design principles:
  - record_usage() is **fail-open**: metering must never block or break a tool
    call. Any exception is swallowed and returned as {"recorded": False, ...}.
  - get_usage_summary() is **fail-closed**: a read that cannot reach Supabase
    returns an error dict (callers should surface, not fabricate, usage).
  - Identity is a stable per-subscriber string passed as `key_hash`. It can be
    the SHA-256 of the raw subscriber key (when the raw key is in scope, e.g.
    middleware) OR the resolved `subscriber_id` (when only that is in scope,
    e.g. subscriber tool dispatch). The SAME identifier MUST be used on both the
    write (record_usage) and read (get_usage_summary) sides or counts won't match.

Stripe note (out of scope here):
  Real Stripe meter reporting is wired in the billing webhook / a separate
  reporter, NOT this module. Stripe 2026 removed legacy usage records
  (API >= 2025-03-31.basil); the reporter must use Billing Meters + Meter
  Events v2 via client.v2.billing.meter_events.create(event_name=...,
  payload={stripe_customer_id, value}, identifier=<dedupe id>), where the
  identifier enforces 24h uniqueness. Hybrid pricing = a licensed base price
  item + a tiered metered price item (first N units unit_amount 0, then
  overage). Current usage is read via the Meter Event Summary API. This module
  is the local source of truth the reporter sums from; it never calls Stripe.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

# Per-tier monthly included call quota. `0` is the unlimited sentinel (live
# subscribers are not metered on call volume). Anything > 0 meters overage.
INCLUDED_QUOTA_BY_TIER: dict[str, int] = {
    "paper": 1000,
    "live": 0,              # unlimited sentinel — no per-call metering
    "developer-paper": 2000,
    "developer-live": 10000,
}

# USD billed per call beyond the included quota (the tiered overage unit_amount
# mirrored to the Stripe metered price item).
OVERAGE_PER_CALL_USD = 0.01

# Module note: real Stripe meter reporting (Meter Events v2 + Meter Event
# Summary reads) lives in the billing webhook / a separate reporter, NOT here.
_STRIPE_REPORTING_IS_OUT_OF_SCOPE = True


def _service_client():
    """Singleton Supabase service-role client, or None if unavailable."""
    try:
        from ..marketplace.supabase_tools import _get_sb_client
    except Exception as exc:  # pragma: no cover - import path safety
        log.warning("supabase_tools unavailable: %s", exc)
        return None
    return _get_sb_client(use_service_role=True)


def _err(msg: str, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"error": msg}
    out.update(extra)
    return out


def _current_period_month(now: datetime | None = None) -> str:
    """UTC 'YYYY-MM' billing period key."""
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m")


def included_quota_for_tier(tier: str | None) -> int:
    """Resolve the monthly included quota for a subscription tier (default paper)."""
    if not tier:
        return INCLUDED_QUOTA_BY_TIER["paper"]
    return INCLUDED_QUOTA_BY_TIER.get(tier, INCLUDED_QUOTA_BY_TIER["paper"])


def _event_identifier(key_hash: str, tool_name: str, now: datetime) -> str:
    """Deterministic, minute-bucketed dedup id.

    Retries of the same (key, tool) within the same UTC minute collapse to one
    audit row via the usage_events.event_identifier UNIQUE constraint — the same
    posture Stripe Meter Events v2 enforces with its 24h `identifier` uniqueness.
    """
    minute_bucket = now.strftime("%Y%m%dT%H%M")
    raw = f"{key_hash}:{tool_name}:{minute_bucket}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def record_usage(
    key_hash: str,
    tool_name: str,
    *,
    included_quota: int = 1000,
) -> dict[str, Any]:
    """Record one metered tool call. FAIL-OPEN — never raises.

    Atomically bumps the (key_hash, period_month) counter via the
    increment_usage RPC, then best-effort writes a sampled audit row whose
    event_identifier dedups retries within the same minute. Any failure returns
    {"recorded": False, "error": ...} so the caller is never blocked by metering.
    """
    try:
        if included_quota == 0:
            # Unlimited (live) tier — nothing to meter; treat as a no-op success.
            return {"recorded": True, "calls": 0, "overage_calls": 0, "metered": False}

        sb = _service_client()
        if sb is None:
            return {"recorded": False, "error": "supabase_unavailable"}

        now = datetime.now(timezone.utc)
        period_month = _current_period_month(now)

        resp = sb.rpc(
            "increment_usage",
            {
                "p_key_hash": key_hash,
                "p_period_month": period_month,
                "p_included_quota": int(included_quota),
            },
        ).execute()
        rows = getattr(resp, "data", None) or []
        row = rows[0] if rows else {}
        calls = int(row.get("calls") or 0)
        overage_calls = int(row.get("overage_calls") or 0)

        # Best-effort sampled audit — failure here must NOT fail the call.
        try:
            event_id = _event_identifier(key_hash, tool_name, now)
            sb.table("usage_events").insert(
                {
                    "key_hash": key_hash,
                    "tool_name": tool_name,
                    "event_identifier": event_id,
                    "occurred_at": now.isoformat(),
                }
            ).execute()
        except Exception as audit_exc:  # pragma: no cover - dedup/transient
            # 23505 (duplicate event_identifier) is the expected dedup outcome.
            log.debug("usage_events audit insert skipped: %s", audit_exc)

        return {"recorded": True, "calls": calls, "overage_calls": overage_calls}
    except Exception as exc:  # pragma: no cover - fail open, never raise
        log.warning("record_usage failed (fail-open) for tool %s: %s", tool_name, exc)
        return {"recorded": False, "error": str(exc)}


def get_usage_summary(
    key_hash: str,
    *,
    included_quota: int = 1000,
) -> dict[str, Any]:
    """Current-month usage + projected month-end cost. FAIL-CLOSED on error.

    Returns the rollup for the current UTC period plus a simple linear cost
    projection (scale overage by days_in_month / day_of_month). If Supabase is
    unavailable or the query fails, returns an error dict rather than guessing.
    """
    sb = _service_client()
    if sb is None:
        return _err("supabase_unavailable")

    now = datetime.now(timezone.utc)
    period_month = _current_period_month(now)

    try:
        resp = (
            sb.table("usage_counters")
            .select("calls,overage_calls,included_quota")
            .eq("key_hash", key_hash)
            .eq("period_month", period_month)
            .maybe_single()
            .execute()
        )
        row = getattr(resp, "data", None) or {}
    except Exception as exc:
        return _err("query_failed", detail=str(exc))

    calls = int(row.get("calls") or 0)
    # Prefer the quota snapshot stored on the row; fall back to the caller's.
    effective_quota = int(row.get("included_quota") or included_quota)
    overage_calls = int(row.get("overage_calls") or max(0, calls - effective_quota))
    overage_cost_usd = round(overage_calls * OVERAGE_PER_CALL_USD, 2)

    # Simple linear projection: scale current overage by the fraction of the
    # month elapsed. day_of_month is 1-based so the denominator is never zero.
    day_of_month = now.day
    days_in_month = _days_in_month(now)
    if day_of_month > 0:
        projected_overage = overage_calls * (days_in_month / day_of_month)
    else:  # pragma: no cover - guard only
        projected_overage = float(overage_calls)
    projected_month_end_cost_usd = round(projected_overage * OVERAGE_PER_CALL_USD, 2)

    return {
        "period_month": period_month,
        "calls": calls,
        "included_quota": effective_quota,
        "overage_calls": overage_calls,
        "overage_cost_usd": overage_cost_usd,
        "projected_month_end_cost_usd": projected_month_end_cost_usd,
        "as_of": now.isoformat(),
    }


def _days_in_month(now: datetime) -> int:
    """Number of days in the month of `now` (UTC)."""
    if now.month == 12:
        nxt = now.replace(year=now.year + 1, month=1, day=1)
    else:
        nxt = now.replace(month=now.month + 1, day=1)
    first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return (nxt.replace(hour=0, minute=0, second=0, microsecond=0) - first).days
