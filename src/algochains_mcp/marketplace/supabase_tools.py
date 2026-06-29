"""
supabase_tools.py — Supabase-backed marketplace and metrics tools for AlgoChains MCP Server.

Implements three tools:
  get_marketplace_listings  — marketplace_listing rows with RLS (approved only)
  get_live_bot_metrics      — bot_metrics_live rows (all bots or one)
  get_subscriber_bots       — subscriber_bot_assignments rows for a given subscriber

The Supabase client uses SUPABASE_URL + SUPABASE_ANON_KEY from env.
RLS on the tables controls what the anon key can see:
  - marketplace_listing: only status IN ('approved', 'validated', 'live') AND lifecycle_status = 'PUBLISHED'
  - bot_metrics_live:    public SELECT (owner-readable, anon read)
  - subscriber_bot_assignments: requires service_role or auth.uid() match

All three functions fall back gracefully (empty list + error key) if Supabase is
unreachable or credentials are missing — the MCP server stays operational.
"""
from __future__ import annotations

import os
import logging
import threading
from typing import Any

log = logging.getLogger(__name__)

# ── Supabase singletons ───────────────────────────────────────────────────────
# One client per key type, created on first call and reused for every
# subsequent tool invocation — avoids HTTP pool teardown on every tool call.
_SB_ANON: Any = None
_SB_SERVICE: Any = None
_SB_LOCK = threading.Lock()


def _get_sb_client(use_service_role: bool = False):
    """Return the module-level Supabase client (singleton), or None if not configured."""
    global _SB_ANON, _SB_SERVICE
    target = "_SB_SERVICE" if use_service_role else "_SB_ANON"
    existing = _SB_SERVICE if use_service_role else _SB_ANON
    if existing is not None:
        return existing

    with _SB_LOCK:
        existing = _SB_SERVICE if use_service_role else _SB_ANON
        if existing is not None:
            return existing
        try:
            from supabase import create_client
        except ImportError:
            log.warning("supabase package not installed — pip install supabase>=2.0.0")
            return None

        url = os.getenv("SUPABASE_URL", "")
        if use_service_role:
            # Accept both naming conventions: CT uses SUPABASE_SERVICE_ROLE_KEY,
            # mcp-server .env uses SUPABASE_SERVICE_KEY (per config.py).
            key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "") or os.getenv("SUPABASE_SERVICE_KEY", "")
        else:
            key = os.getenv("SUPABASE_ANON_KEY", "") or os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY", "")

        if not url or not key:
            return None
        client = create_client(url, key)
        if use_service_role:
            _SB_SERVICE = client
        else:
            _SB_ANON = client
        log.info("Supabase %s client initialised (singleton)", target)
        return client


def get_marketplace_listings(
    status: str = "all",
    asset_class: str = "all",
    limit: int = 50,
) -> dict[str, Any]:
    """
    Fetch marketplace bot listings from Supabase (RLS enforces approved-only for anon key).

    Args:
        status:      Filter by status: 'all', 'live', 'validated', 'approved', 'paper'
        asset_class: Filter by asset class: 'all', 'futures', 'equities', 'stocks', 'crypto'
        limit:       Max rows to return (default 50)

    Returns:
        Dict with keys: total, live, validated, paper, subscribable, owner_only, listings
    """
    sb = _get_sb_client()
    if sb is None:
        return {"error": "Supabase not configured (SUPABASE_URL + SUPABASE_ANON_KEY)", "listings": []}

    try:
        q = (
            sb.table("marketplace_listing")
            .select(
                "id,strategy_title,symbol,asset_class,strategy_type,status,"
                "sharpe,win_rate,max_drawdown,price,subscribable,max_subscribers,"
                "access_level,paper_only,futures_locked,lifecycle_status,"
                "supports_live_trading,avg_monthly_return,total_trades"
            )
            .in_("status", ["approved", "validated", "live"])
            .eq("lifecycle_status", "PUBLISHED")
            .order("sharpe", desc=True)
            .limit(limit)
        )

        # Client-side filters on top of RLS
        if asset_class != "all":
            # Django uses 'stocks' not 'equities'
            ac = "stocks" if asset_class == "equities" else asset_class
            q = q.eq("asset_class", ac)

        result = q.execute()
        rows = result.data or []

        # Map Django rows to normalised listing shape
        listings = []
        for row in rows:
            s = str(row.get("status", "paper"))
            is_live = row.get("supports_live_trading") is True and s == "approved"
            derived_status = "live" if is_live else ("validated" if s == "approved" else s)

            if status != "all" and derived_status != status and s != status:
                continue

            listings.append({
                "id": str(row.get("id")),
                "name": row.get("strategy_title"),
                "symbol": row.get("symbol"),
                "asset_class": "equities" if row.get("asset_class") == "stocks" else row.get("asset_class"),
                "strategy": row.get("strategy_type"),
                "status": derived_status,
                "oos_sharpe": row.get("sharpe"),
                "win_rate": row.get("win_rate"),
                "max_dd": row.get("max_drawdown"),
                "subscription_price": row.get("price"),
                "subscribable": row.get("subscribable", False),
                "max_subscribers": row.get("max_subscribers"),
                "access_level": row.get("access_level", "subscriber"),
                "paper_only": row.get("paper_only", False),
                "futures_locked": row.get("futures_locked", False),
                "avg_monthly_return": row.get("avg_monthly_return"),
                "total_trades": row.get("total_trades"),
            })

        return {
            "total": len(listings),
            "live": sum(1 for b in listings if b["status"] == "live"),
            "validated": sum(1 for b in listings if b["status"] == "validated"),
            "paper": sum(1 for b in listings if b["status"] == "paper"),
            "subscribable": sum(1 for b in listings if b["subscribable"]),
            "owner_only": sum(1 for b in listings if b["futures_locked"]),
            "listings": listings,
            "source": "supabase",
        }
    except Exception as exc:
        log.error("get_marketplace_listings failed: %s", exc)
        return {"error": str(exc), "listings": [], "source": "supabase_error"}


def get_live_bot_metrics(bot_id: str | None = None) -> dict[str, Any]:
    """
    Fetch live bot operational metrics from Supabase bot_metrics_live table.

    Args:
        bot_id: Optional bot ID to filter (e.g. 'mnq', 'cl'). None returns all bots.

    Returns:
        Dict with keys: bots (list), total, running, source
    """
    sb = _get_sb_client()
    if sb is None:
        return {"error": "Supabase not configured", "bots": []}

    try:
        q = sb.table("bot_metrics_live").select("*").order("updated_at", desc=True)
        if bot_id:
            q = q.eq("bot_id", bot_id.lower())

        result = q.execute()
        bots = result.data or []

        return {
            "bots": bots,
            "total": len(bots),
            "running": sum(1 for b in bots if b.get("is_running")),
            "source": "supabase",
        }
    except Exception as exc:
        log.error("get_live_bot_metrics failed: %s", exc)
        return {"error": str(exc), "bots": [], "source": "supabase_error"}


def get_subscriber_bots(user_id: str) -> dict[str, Any]:
    """
    Fetch active bot assignments for a given subscriber.

    Uses service_role key to bypass RLS — this function must only be called
    server-side with a verified user_id (never expose to untrusted clients).

    Args:
        user_id: Subscriber ID or email to look up

    Returns:
        Dict with keys: subscriptions (list), total, active, source
    """
    sb = _get_sb_client(use_service_role=True)
    if sb is None:
        return {"error": "Supabase service_role not configured", "subscriptions": []}

    try:
        q = (
            sb.table("subscriber_bot_assignments")
            .select(
                "bot,mode,paused,size_multiplier,max_contracts,daily_loss_cap_usd,created_at,updated_at"
            )
            .eq("subscriber_id", user_id)
            .order("bot")
        )

        result = q.execute()
        subs = result.data or []

        return {
            "subscriptions": subs,
            "total": len(subs),
            "active": sum(1 for s in subs if not s.get("paused")),
            "source": "supabase",
        }
    except Exception as exc:
        log.error("get_subscriber_bots failed: %s", exc)
        return {"error": str(exc), "subscriptions": [], "source": "supabase_error"}


_PRIVATE_NETWORK_PREFIXES = (
    "http://localhost",
    "http://127.",
    "http://10.",
    "http://192.168.",
    "http://172.16.",
    "http://172.17.",
    "http://172.18.",
    "http://172.19.",
    "http://172.2",
    "http://172.3",
    "https://localhost",
    "https://127.",
    "https://10.",
    "https://192.168.",
    "https://172.16.",
    "https://172.17.",
    "https://172.18.",
    "https://172.19.",
    "https://172.2",
    "https://172.3",
    "file://",
    "ftp://",
    "http://0.",
    "http://169.254.",  # link-local (AWS metadata)
)


def _is_ssrf_target(url: str) -> bool:
    """Return True if the URL targets a private/link-local/loopback address."""
    lower = (url or "").lower()
    return any(lower.startswith(prefix) for prefix in _PRIVATE_NETWORK_PREFIXES)


def deliver_strategy_to_subscriber(
    subscriber_id: str,
    strategy_id: str,
    webhook_url: str | None = None,
    token_ttl_seconds: int = 86400,
) -> dict[str, Any]:
    """
    Deliver an approved marketplace strategy config to a subscriber's bot endpoint.

    Flow:
      1. Verify caller has an active subscription for (subscriber_id, strategy_id).
      2. Read approved listing from Supabase marketplace_listing (service_role).
      3. Build a time-limited, HMAC-signed config token containing strategy params.
      4. POST the signed payload to the subscriber's webhook URL (from subscription record
         — caller-supplied webhook_url override is SSRF-checked against private ranges).
      5. Log the delivery event to Supabase marketplace_deliveries table.

    Security:
      - Active subscription ownership is verified before any config data is accessed.
      - Caller-supplied webhook_url is blocked if it targets private/link-local ranges.
      - signed_token is NOT returned in the tool response — delivery receipt only.

    Args:
        subscriber_id: Subscriber's Supabase user ID (auth.uid())
        strategy_id:   Supabase marketplace_listing.id to deliver
        webhook_url:   Optional override; if omitted, subscription record URL is used.
                       Private/link-local targets are blocked (SSRF guard).
        token_ttl_seconds: Config token lifetime in seconds (default 24h)

    Returns delivery receipt (no signed token in response).
    """
    import hashlib
    import hmac as _hmac
    import json as _json
    import time as _time
    import uuid as _uuid

    # ── Step 0: SSRF guard on caller-supplied webhook URL ─────────────────────
    if webhook_url and _is_ssrf_target(webhook_url):
        return {
            "error": f"Blocked: webhook_url targets a private or link-local address. "
                     f"Provide an externally reachable HTTPS endpoint.",
        }

    sb = _get_sb_client(use_service_role=True)
    if sb is None:
        return {"error": "Supabase service_role client not available — check SUPABASE_SERVICE_ROLE_KEY"}

    _signal_secret = os.getenv("ALGOCHAINS_SIGNAL_SECRET", "")
    if not _signal_secret:
        return {"error": "ALGOCHAINS_SIGNAL_SECRET not set — cannot sign delivery token"}

    # ── Step 1: Verify active subscription ownership ───────────────────────────
    try:
        sub_check = (
            sb.table("marketplace_botsubscription")
            .select("id,status,webhook_url")
            .eq("subscriber_id", subscriber_id)
            .eq("listing_id_id", strategy_id)
            .limit(1)
            .execute()
        )
        sub_rows = sub_check.data or []
        if not sub_rows:
            # Also try algochains_subscriptions table
            sub_check2 = (
                sb.table("algochains_subscriptions")
                .select("id,status,webhook_url")
                .eq("subscriber_id", subscriber_id)
                .eq("strategy_id", strategy_id)
                .limit(1)
                .execute()
            )
            sub_rows = sub_check2.data or []
        if not sub_rows:
            return {
                "error": f"No active subscription found for subscriber={subscriber_id!r} "
                         f"strategy={strategy_id!r}. Cannot deliver strategy config.",
            }
        sub_row = sub_rows[0]
        if sub_row.get("status") not in ("active", "trialing", "past_due"):
            return {
                "error": f"Subscription status={sub_row.get('status')!r} is not active. "
                         "Cannot deliver strategy config.",
            }
        # Prefer subscription-record webhook URL over caller override
        trusted_webhook_url = sub_row.get("webhook_url") or webhook_url
        # SSRF guard on subscription-record URL too (defence-in-depth)
        if trusted_webhook_url and _is_ssrf_target(trusted_webhook_url):
            trusted_webhook_url = None
    except Exception as exc:
        return {"error": f"Subscription verification failed: {exc}"}

    # ── Step 2: Load approved listing ──────────────────────────────────────────
    try:
        listing_resp = (
            sb.table("marketplace_listing")
            .select("*")
            .eq("id", strategy_id)
            .in_("status", ["approved", "validated", "live"])
            .limit(1)
            .execute()
        )
        rows = listing_resp.data or []
        if not rows:
            return {
                "error": f"Strategy {strategy_id!r} not found or not in approved/validated/live status"
            }
        listing = rows[0]
    except Exception as exc:
        return {"error": f"Failed to fetch listing: {exc}"}

    # ── Step 3: Build time-limited config token ────────────────────────────────
    delivery_id = _uuid.uuid4().hex[:16]
    issued_at = int(_time.time())
    expires_at = issued_at + token_ttl_seconds

    config_payload = {
        "delivery_id": delivery_id,
        "subscriber_id": subscriber_id,
        "strategy_id": strategy_id,
        "strategy_title": listing.get("strategy_title", ""),
        "asset_class": listing.get("asset_class", ""),
        "config": listing.get("config", {}),
        "issued_at": issued_at,
        "expires_at": expires_at,
    }
    payload_str = _json.dumps(config_payload, sort_keys=True)

    # HMAC-SHA256 signature for webhook verification
    signature = _hmac.new(
        _signal_secret.encode(),
        payload_str.encode(),
        digestmod=hashlib.sha256,
    ).hexdigest()

    signed_token = {
        "payload": config_payload,
        "signature": f"hmac-sha256={signature}",
        "token_version": "1",
    }

    # ── Step 4: POST to webhook (subscription-record URL preferred) ───────────
    target_url = trusted_webhook_url
    webhook_status: str
    webhook_code: int | None = None

    if target_url:
        try:
            import httpx as _httpx
            resp = _httpx.post(
                target_url,
                json=signed_token,
                headers={
                    "Content-Type": "application/json",
                    "X-AlgoChains-Signature": f"hmac-sha256={signature}",
                    "X-AlgoChains-Delivery-ID": delivery_id,
                },
                timeout=10.0,
            )
            webhook_code = resp.status_code
            webhook_status = "delivered" if resp.status_code < 300 else f"failed_{resp.status_code}"
        except Exception as _wh_exc:
            webhook_status = f"error: {_wh_exc}"
    else:
        webhook_status = "no_webhook_url"

    # ── Step 5: Log delivery to Supabase ──────────────────────────────────────
    try:
        sb.table("marketplace_deliveries").upsert({
            "delivery_id": delivery_id,
            "subscriber_id": subscriber_id,
            "strategy_id": strategy_id,
            "webhook_url": target_url or "",
            "webhook_status": webhook_status,
            "webhook_http_code": webhook_code,
            "delivered_at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(issued_at)),
            "expires_at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(expires_at)),
        }).execute()
    except Exception as _log_exc:
        log.warning("deliver_strategy: could not log delivery to Supabase: %s", _log_exc)

    # SEC-2026-C2 FIX: signed_token is NOT returned to the caller.
    # The signed config is posted to the subscriber's verified webhook only.
    return {
        "status": webhook_status,
        "delivery_id": delivery_id,
        "subscriber_id": subscriber_id,
        "strategy_id": strategy_id,
        "strategy_title": listing.get("strategy_title"),
        "webhook_url": target_url,
        "webhook_http_code": webhook_code,
        "token_expires_at": expires_at,
    }
