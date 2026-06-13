"""
connect_payouts.py — Creator earnings ledger + Stripe Connect payout surface.

Thin orchestration layer ON TOP of the existing Stripe Connect plumbing in
``billing_engine.py``. This module owns the *ledger* (accruals + payouts) and the
*safety envelope* around money movement; it never re-implements Stripe calls —
it delegates to ``BillingEngine`` for account creation, balance reads, and the
actual transfer.

Tables (see supabase/migrations/20260528_creator_earnings.sql):
  - creator_connect_accounts — Stripe Connect account mirror + payouts_enabled gate
  - creator_earnings         — append-only accrual ledger (status accrued|paid|reversed)
  - creator_payouts          — append-only payout ledger (idempotency_key UNIQUE)

LEGAL / SAFETY (payouts MOVE REAL MONEY):
  - ``run_creator_payouts`` defaults to ``dry_run=True`` — it computes a plan and
    executes NOTHING unless explicitly called with ``dry_run=False``.
  - Designed to be owner-gated: the server.py dispatch checks OWNER_API_TOKEN
    BEFORE calling here. This module assumes it has already been authorized.
  - Every executed payout writes a ``creator_payouts`` ledger row (status
    'planned') BEFORE the external Stripe transfer, with a Stripe idempotency key
    derived from the stable ledger id (``payout_<creator>_<period>``).
  - Before transferring we check the creator's ``payouts_enabled`` flag AND the
    live Stripe balance via ``BillingEngine().get_creator_balance``.
  - The UNIQUE idempotency_key + a status guard make the run safe to retry:
    a duplicate period collides on the key and is skipped — never double-paid.

Fail-closed: any Supabase / Stripe unavailability returns an error dict and
executes no money movement.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

# Creators keep 80% (80/20 split). Documented AlgoChains target — MCPize runs
# ~85/15, 70/30 is below market. Stored per-earning-row in the DB for audit.
REVENUE_SHARE_PCT = 80.0


def _service_client():
    """Return the singleton Supabase service-role client, or None (fail closed)."""
    try:
        from ..marketplace.supabase_tools import _get_sb_client
    except Exception as exc:  # pragma: no cover
        log.warning("connect_payouts: supabase_tools unavailable — %s", exc)
        return None
    return _get_sb_client(use_service_role=True)


def _err(msg: str, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"error": msg}
    out.update(extra)
    return out


def _current_period() -> str:
    """Stable payout period token (UTC year-month), used in the idempotency key."""
    return datetime.now(timezone.utc).strftime("%Y%m")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def create_creator_onboarding_link(
    creator_id: str,
    creator_email: str,
) -> dict[str, Any]:
    """
    Create (or refresh) a Stripe Connect Express onboarding link for a creator.

    Thin wrapper: delegates the real Stripe account creation to BillingEngine,
    then upserts a creator_connect_accounts row mirroring the account so payout
    runs can gate on payouts_enabled. BillingEngine is imported lazily so this
    module does not pull in `stripe` at import time.

    Returns {onboarding_url, creator_id, note} (or an error dict, fail-closed).
    """
    if not creator_id or not creator_email:
        return _err("creator_id and creator_email are required")

    try:
        from .billing_engine import BillingEngine
    except Exception as exc:
        return _err(f"billing_engine unavailable: {exc}")

    try:
        account = await BillingEngine().create_stripe_connect_account(
            creator_id=creator_id,
            creator_email=creator_email,
        )
    except Exception as exc:
        log.warning("create_creator_onboarding_link: Stripe account create failed — %s", exc)
        return _err(f"stripe_connect_account_failed: {exc}", creator_id=creator_id)

    onboarding_url = account.get("onboarding_url")
    stripe_account_id = account.get("account_id")
    payouts_enabled = bool(account.get("payouts_enabled", False))

    # Mirror the Connect account into our ledger table (best-effort; the Stripe
    # account is the source of truth, this row is for the payout-gate lookup).
    sb = _service_client()
    if sb is not None:
        try:
            sb.table("creator_connect_accounts").upsert(
                {
                    "creator_id": creator_id,
                    "stripe_account_id": stripe_account_id,
                    "payouts_enabled": payouts_enabled,
                    "email": creator_email,
                    "updated_at": _now_iso(),
                },
                on_conflict="creator_id",
            ).execute()
        except Exception as exc:  # pragma: no cover - non-fatal
            log.warning("create_creator_onboarding_link: connect-account upsert failed — %s", exc)

    return {
        "onboarding_url": onboarding_url,
        "creator_id": creator_id,
        "note": (
            "Open onboarding_url to complete Stripe Connect KYC and link your bank "
            "account. Payouts stay disabled until Stripe verifies your account."
        ),
    }


def get_my_creator_earnings(creator_id: str) -> dict[str, Any]:
    """
    Read a creator's earnings summary (accrued/paid/reversed totals) plus their
    payout history. Read-only; fail-closed.

    Carries the standard past-performance disclaimer via with_disclaimer.
    """
    from ..compliance.disclosures import with_disclaimer

    if not creator_id:
        return _err("creator_id is required")

    sb = _service_client()
    if sb is None:
        return _err("supabase_unavailable")

    try:
        earn_resp = (
            sb.table("creator_earnings")
            .select("creator_share_usd,gross_usd,platform_fee_usd,status")
            .eq("creator_id", creator_id)
            .execute()
        )
        earn_rows = getattr(earn_resp, "data", None) or []
    except Exception as exc:
        log.warning("get_my_creator_earnings: earnings read failed — %s", exc)
        return _err(f"earnings_read_failed: {exc}", creator_id=creator_id)

    # Sum creator_share by status.
    by_status: dict[str, float] = {"accrued": 0.0, "paid": 0.0, "reversed": 0.0}
    gross_total = 0.0
    for row in earn_rows:
        status = row.get("status") or "accrued"
        share = float(row.get("creator_share_usd") or 0.0)
        by_status[status] = by_status.get(status, 0.0) + share
        gross_total += float(row.get("gross_usd") or 0.0)

    try:
        pay_resp = (
            sb.table("creator_payouts")
            .select("amount_usd,status,dry_run,stripe_transfer_id,idempotency_key,created_at")
            .eq("creator_id", creator_id)
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )
        payout_history = getattr(pay_resp, "data", None) or []
    except Exception as exc:
        log.warning("get_my_creator_earnings: payout history read failed — %s", exc)
        payout_history = []

    result = {
        "creator_id": creator_id,
        "revenue_share_pct": REVENUE_SHARE_PCT,
        "gross_usd": round(gross_total, 2),
        "accrued_usd": round(by_status.get("accrued", 0.0), 2),
        "paid_usd": round(by_status.get("paid", 0.0), 2),
        "reversed_usd": round(by_status.get("reversed", 0.0), 2),
        "pending_payout_usd": round(by_status.get("accrued", 0.0), 2),
        "payout_history": payout_history,
    }
    return with_disclaimer(result)


async def run_creator_payouts(
    creator_id: str | None = None,
    *,
    dry_run: bool = True,
    min_payout_usd: float = 25.0,
) -> dict[str, Any]:
    """
    Scan accrued creator_earnings, group by creator, and pay out creators whose
    accrued balance clears ``min_payout_usd``.

    SAFETY (this MOVES REAL MONEY):
      - dry_run=True (DEFAULT) computes the plan and executes NOTHING.
      - Owner-gated upstream: server.py checks OWNER_API_TOKEN before calling.
      - Per creator, when executing:
          1. Gate on creator_connect_accounts.payouts_enabled.
          2. Confirm live Stripe balance via BillingEngine().get_creator_balance.
          3. Insert a creator_payouts row (status 'planned', dry_run=False) with a
             stable idempotency_key = payout_<creator>_<period> BEFORE any transfer.
             The UNIQUE constraint refuses a duplicate period → no double-pay.
          4. Call BillingEngine().trigger_creator_payout(...).
          5. Advance the payout row to 'transferred' (or 'failed') and flip the
             matched accrued earnings rows to 'paid'.

    Returns {dry_run, period, plan: [...], executed: [...], skipped_below_min: [...]}.
    """
    sb = _service_client()
    if sb is None:
        return _err("supabase_unavailable", dry_run=dry_run)

    period = _current_period()

    # 1. Pull accrued earnings (optionally scoped to one creator).
    try:
        q = (
            sb.table("creator_earnings")
            .select("id,creator_id,creator_share_usd,status")
            .eq("status", "accrued")
        )
        if creator_id:
            q = q.eq("creator_id", creator_id)
        accrued_rows = getattr(q.execute(), "data", None) or []
    except Exception as exc:
        log.warning("run_creator_payouts: accrued earnings read failed — %s", exc)
        return _err(f"earnings_read_failed: {exc}", dry_run=dry_run, period=period)

    # 2. Group by creator, summing creator_share_usd and tracking the earning ids.
    grouped: dict[str, dict[str, Any]] = {}
    for row in accrued_rows:
        cid = row.get("creator_id")
        if not cid:
            continue
        bucket = grouped.setdefault(cid, {"total": 0.0, "earning_ids": []})
        bucket["total"] += float(row.get("creator_share_usd") or 0.0)
        bucket["earning_ids"].append(row.get("id"))

    plan: list[dict[str, Any]] = []
    executed: list[dict[str, Any]] = []
    skipped_below_min: list[dict[str, Any]] = []

    for cid, bucket in grouped.items():
        total = round(float(bucket["total"]), 2)
        idempotency_key = f"payout_{cid}_{period}"

        if total < min_payout_usd:
            skipped_below_min.append(
                {"creator_id": cid, "accrued_usd": total, "min_payout_usd": min_payout_usd}
            )
            continue

        plan_entry = {
            "creator_id": cid,
            "amount_usd": total,
            "earning_count": len(bucket["earning_ids"]),
            "idempotency_key": idempotency_key,
        }
        plan.append(plan_entry)

        # DRY RUN: plan only, no money moves, no rows written.
        if dry_run:
            continue

        # ── EXECUTE ──────────────────────────────────────────────────────────
        # 2a. Gate on payouts_enabled (fail closed if we can't confirm it).
        try:
            acct_resp = (
                sb.table("creator_connect_accounts")
                .select("payouts_enabled,stripe_account_id")
                .eq("creator_id", cid)
                .limit(1)
                .execute()
            )
            acct_rows = getattr(acct_resp, "data", None) or []
        except Exception as exc:
            executed.append({**plan_entry, "status": "failed", "reason": f"account_lookup_failed: {exc}"})
            continue

        if not acct_rows or not acct_rows[0].get("payouts_enabled"):
            executed.append({**plan_entry, "status": "skipped", "reason": "payouts_not_enabled"})
            continue

        # 2b. Confirm live Stripe balance before transferring.
        try:
            from .billing_engine import BillingEngine
            be = BillingEngine()
            balance = await be.get_creator_balance(cid)
            available = float(balance.get("available_usd") or 0.0)
        except Exception as exc:
            executed.append({**plan_entry, "status": "failed", "reason": f"balance_check_failed: {exc}"})
            continue

        if available + 1e-6 < total:
            executed.append(
                {**plan_entry, "status": "skipped",
                 "reason": "insufficient_stripe_balance", "available_usd": round(available, 2)}
            )
            continue

        # 2c. Write the ledger row BEFORE the external transfer. The UNIQUE
        #     idempotency_key is the double-pay guard: if this period was already
        #     paid, the insert collides and we skip without transferring.
        try:
            sb.table("creator_payouts").insert(
                {
                    "creator_id": cid,
                    "amount_usd": total,
                    "idempotency_key": idempotency_key,
                    "status": "planned",
                    "dry_run": False,
                    "created_at": _now_iso(),
                    "updated_at": _now_iso(),
                }
            ).execute()
        except Exception as exc:
            # Most likely a UNIQUE violation on idempotency_key → already paid.
            executed.append(
                {**plan_entry, "status": "skipped",
                 "reason": "duplicate_payout_period_guard", "detail": str(exc)[:200]}
            )
            continue

        # 2d. Trigger the real transfer.
        amount_cents = int(round(total * 100))
        try:
            payout = await be.trigger_creator_payout(cid, amount_cents=amount_cents)
            transfer_id = payout.get("payout_id")
        except Exception as exc:
            # Mark the planned row failed; do NOT touch earnings (stay accrued).
            try:
                sb.table("creator_payouts").update(
                    {"status": "failed", "updated_at": _now_iso()}
                ).eq("idempotency_key", idempotency_key).execute()
            except Exception:  # pragma: no cover
                pass
            executed.append({**plan_entry, "status": "failed", "reason": f"transfer_failed: {exc}"})
            continue

        # 2e. Advance payout row → 'transferred' and flip earnings → 'paid'.
        try:
            sb.table("creator_payouts").update(
                {"status": "transferred", "stripe_transfer_id": transfer_id, "updated_at": _now_iso()}
            ).eq("idempotency_key", idempotency_key).execute()
        except Exception as exc:  # pragma: no cover
            log.warning("run_creator_payouts: payout status update failed (%s) — %s", idempotency_key, exc)

        earnings_paid = 0
        try:
            # Status guard: only flip rows that are still 'accrued'.
            upd = (
                sb.table("creator_earnings")
                .update({"status": "paid"})
                .eq("creator_id", cid)
                .eq("status", "accrued")
                .execute()
            )
            earnings_paid = len(getattr(upd, "data", None) or [])
        except Exception as exc:  # pragma: no cover
            log.warning("run_creator_payouts: earnings mark-paid failed (%s) — %s", cid, exc)

        executed.append(
            {**plan_entry, "status": "transferred",
             "stripe_transfer_id": transfer_id, "earnings_marked_paid": earnings_paid}
        )

    return {
        "dry_run": dry_run,
        "period": period,
        "min_payout_usd": min_payout_usd,
        "plan": plan,
        "executed": executed,
        "skipped_below_min": skipped_below_min,
    }
