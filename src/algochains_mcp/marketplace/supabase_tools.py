"""
supabase_tools.py — Supabase-backed marketplace and metrics tools for AlgoChains MCP Server.

Implements three tools:
  get_marketplace_listings  — marketplace_listing rows with RLS (approved only)
  get_live_bot_metrics      — bot_metrics_live rows (all bots or one)
  get_subscriber_bots       — marketplace_botsubscription rows for a given subscriber

The Supabase client uses SUPABASE_URL + SUPABASE_ANON_KEY from env.
RLS on the tables controls what the anon key can see:
  - marketplace_listing: only status IN ('approved', 'validated', 'live') AND lifecycle_status = 'PUBLISHED'
  - bot_metrics_live:    public SELECT (owner-readable, anon read)
  - marketplace_botsubscription: requires service_role or auth.uid() match

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
            key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
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
    Fetch active bot subscriptions for a given subscriber.

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
        # Query by subscriber_email (Django auth model) or subscriber_id (UUID)
        q = sb.table("marketplace_botsubscription").select(
            "id,listing_id_id,status,created_at,subscriber_email,requester_slack_id,"
            "marketplace_listing(strategy_title,symbol,sharpe,win_rate,asset_class)"
        )

        if "@" in user_id:
            q = q.eq("subscriber_email", user_id)
        else:
            q = q.eq("subscriber_id", user_id)

        result = q.execute()
        subs = result.data or []

        return {
            "subscriptions": subs,
            "total": len(subs),
            "active": sum(1 for s in subs if s.get("status") == "active"),
            "source": "supabase",
        }
    except Exception as exc:
        log.error("get_subscriber_bots failed: %s", exc)
        return {"error": str(exc), "subscriptions": [], "source": "supabase_error"}
