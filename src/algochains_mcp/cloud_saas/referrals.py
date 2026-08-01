"""
referrals.py — Affiliate / referral system (subscriber-scoped tools).

Each public function takes the resolved `subscriber_id` (from
`subscriber_auth.resolve_subscriber_key`) as its first argument — the bridge
resolves identity server-side and this module never trusts a caller-supplied id.
All access goes through the Supabase service-role client.

Commission policy
-----------------
A referrer earns COMMISSION_RATE (20%) of a referred subscriber's subscription
revenue for the first COMMISSION_MONTHS (3) months only. THIS MODULE DOES NOT
ACCRUE COMMISSIONS — that happens in the billing webhook, which writes rows into
`referral_commissions`. Here we only:
  - mint / fetch a subscriber's shareable code   (create_referral_code)
  - summarise their referral activity             (get_my_referrals)
  - total their earnings (with compliance note)   (get_referral_earnings)
  - record a first-touch attribution at signup    (record_referral_attribution)

Fail closed: if the service client is unavailable every function returns an
error dict rather than raising.
"""
from __future__ import annotations

import logging
import secrets
from typing import Any

log = logging.getLogger(__name__)

# ─── Commission policy constants (documentation + share-link copy) ────────────
# Accrual itself lives in the billing webhook; these are the canonical values.
COMMISSION_RATE = 0.20      # 20% of the referred subscriber's subscription revenue
COMMISSION_MONTHS = 3       # ... for the first 3 months only

# Human-friendly code: 'AC-' + 6 url-safe upper-case characters.
_CODE_PREFIX = "AC-"
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no ambiguous 0/O/1/I
_CODE_BODY_LEN = 6

_SHARE_URL_TMPL = "https://algochains.ai/r/{code}"


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


def _gen_code() -> str:
    body = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_BODY_LEN))
    return f"{_CODE_PREFIX}{body}"


def _share_url(code: str) -> str:
    return _SHARE_URL_TMPL.format(code=code)


def _existing_code(sb, subscriber_id: str) -> str | None:
    """Return this subscriber's active referral code, or None."""
    try:
        resp = (
            sb.table("referral_codes")
            .select("code")
            .eq("owner_subscriber_id", subscriber_id)
            .eq("active", True)
            .limit(1)
            .execute()
        )
        rows = getattr(resp, "data", None) or []
        if rows:
            return rows[0].get("code")
    except Exception as exc:
        log.warning("existing code lookup failed for %s: %s", subscriber_id, exc)
    return None


# ─── tools ────────────────────────────────────────────────────────────────────

def create_referral_code(subscriber_id: str) -> dict[str, Any]:
    """
    Return this subscriber's shareable referral code, minting one on first call.

    One active code per owner — if a code already exists it is returned as-is
    (idempotent). Code collisions on the UNIQUE constraint are retried with a
    fresh code.
    """
    sb = _service_client()
    if sb is None:
        return _err("supabase_unavailable")

    existing = _existing_code(sb, subscriber_id)
    if existing:
        return {
            "code": existing,
            "share_url": _share_url(existing),
            "note": (
                f"Existing referral code. Earn {int(COMMISSION_RATE * 100)}% of each "
                f"referral's subscription for their first {COMMISSION_MONTHS} months."
            ),
        }

    last_exc: str | None = None
    for _ in range(5):  # retry on UNIQUE collision
        code = _gen_code()
        try:
            sb.table("referral_codes").insert({
                "code": code,
                "owner_subscriber_id": subscriber_id,
                "active": True,
            }).execute()
        except Exception as exc:
            msg = str(exc).lower()
            last_exc = str(exc)
            if "23505" in msg or "duplicate key" in msg or "unique" in msg:
                # Could be a code collision OR a concurrent insert for this owner.
                concurrent = _existing_code(sb, subscriber_id)
                if concurrent:
                    return {
                        "code": concurrent,
                        "share_url": _share_url(concurrent),
                        "note": (
                            f"Existing referral code. Earn {int(COMMISSION_RATE * 100)}% of each "
                            f"referral's subscription for their first {COMMISSION_MONTHS} months."
                        ),
                    }
                continue  # plain code collision — retry with a new code
            return _err("insert_failed", detail=str(exc))
        return {
            "code": code,
            "share_url": _share_url(code),
            "note": (
                f"Share this link to earn {int(COMMISSION_RATE * 100)}% of each referral's "
                f"subscription for their first {COMMISSION_MONTHS} months."
            ),
        }

    return _err("code_generation_failed", detail=last_exc)


def get_my_referrals(subscriber_id: str) -> dict[str, Any]:
    """
    Summarise the subscriber's referral activity: their code, how many
    subscribers they've attributed, and commission counts + sums by status.
    """
    sb = _service_client()
    if sb is None:
        return _err("supabase_unavailable")

    code = _existing_code(sb, subscriber_id)

    # Attribution count (subscribers referred by this owner's code).
    attribution_count = 0
    if code:
        try:
            attr_resp = (
                sb.table("referral_attributions")
                .select("referred_subscriber_id", count="exact")
                .eq("code", code)
                .execute()
            )
            attribution_count = getattr(attr_resp, "count", None)
            if attribution_count is None:
                attribution_count = len(list(getattr(attr_resp, "data", None) or []))
        except Exception as exc:
            return _err("query_failed", detail=str(exc))

    # Commissions grouped by status (count + sum).
    by_status: dict[str, dict[str, float]] = {
        "pending": {"count": 0, "total_usd": 0.0},
        "paid": {"count": 0, "total_usd": 0.0},
        "reversed": {"count": 0, "total_usd": 0.0},
    }
    try:
        comm_resp = (
            sb.table("referral_commissions")
            .select("commission_usd,status")
            .eq("owner_subscriber_id", subscriber_id)
            .execute()
        )
        comm_rows = list(getattr(comm_resp, "data", None) or [])
    except Exception as exc:
        return _err("query_failed", detail=str(exc))

    for row in comm_rows:
        status = row.get("status") or "pending"
        bucket = by_status.setdefault(status, {"count": 0, "total_usd": 0.0})
        bucket["count"] += 1
        bucket["total_usd"] += float(row.get("commission_usd") or 0)

    for bucket in by_status.values():
        bucket["total_usd"] = round(bucket["total_usd"], 2)

    return {
        "subscriber_id": subscriber_id,
        "code": code,
        "share_url": _share_url(code) if code else None,
        "referrals_count": attribution_count,
        "commissions_by_status": by_status,
    }


def get_referral_earnings(subscriber_id: str) -> dict[str, Any]:
    """
    Total pending + paid commission earnings for the subscriber. Reversed
    commissions are excluded from totals. Wrapped with the standard compliance
    disclaimer because it surfaces dollar figures.
    """
    sb = _service_client()
    if sb is None:
        return _err("supabase_unavailable")

    try:
        resp = (
            sb.table("referral_commissions")
            .select("commission_usd,status")
            .eq("owner_subscriber_id", subscriber_id)
            .in_("status", ["pending", "paid"])
            .execute()
        )
        rows = list(getattr(resp, "data", None) or [])
    except Exception as exc:
        return _err("query_failed", detail=str(exc))

    pending_usd = 0.0
    paid_usd = 0.0
    for row in rows:
        amount = float(row.get("commission_usd") or 0)
        if row.get("status") == "paid":
            paid_usd += amount
        else:
            pending_usd += amount

    pending_usd = round(pending_usd, 2)
    paid_usd = round(paid_usd, 2)

    from ..compliance.disclosures import with_disclaimer
    return with_disclaimer({
        "subscriber_id": subscriber_id,
        "pending_usd": pending_usd,
        "paid_usd": paid_usd,
        "total_usd": round(pending_usd + paid_usd, 2),
        "commission_rate": COMMISSION_RATE,
        "commission_months": COMMISSION_MONTHS,
    })


def record_referral_attribution(referred_subscriber_id: str, code: str) -> dict[str, Any]:
    """
    Record a first-touch referral attribution at signup. NOT a user tool —
    called by the signup / checkout flow. Self-referral and unknown / inactive
    codes return clean error dicts. Idempotent: re-recording a subscriber that
    already has an attribution returns the existing (first-touch) code.
    """
    sb = _service_client()
    if sb is None:
        return _err("supabase_unavailable")

    if not referred_subscriber_id or not code:
        return _err("missing_arguments")

    try:
        resp = sb.rpc(
            "record_referral_attribution",
            {
                "p_referred_subscriber_id": referred_subscriber_id,
                "p_code": code,
            },
        ).execute()
    except Exception as exc:
        msg = str(exc).lower()
        if "self_referral_not_allowed" in msg:
            return _err("self_referral_not_allowed")
        if "unknown_or_inactive_referral_code" in msg:
            return _err("unknown_referral_code", code=code)
        return _err("attribution_failed", detail=str(exc))

    attributed = getattr(resp, "data", None)
    if not attributed:
        # RPC returned NULL — code didn't validate.
        return _err("unknown_referral_code", code=code)

    return {
        "ok": True,
        "referred_subscriber_id": referred_subscriber_id,
        "attributed_code": attributed,
    }
