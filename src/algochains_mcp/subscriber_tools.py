"""
subscriber_tools.py — Subscriber-scoped MCP tools (HTTP bridge).

Each function takes the resolved `subscriber_id` (from
`subscriber_auth.resolve_subscriber_key`) plus tool arguments, performs the
Supabase query / mutation under the service-role client, and returns a JSON-
serialisable dict.

These tools are deliberately small and self-contained:
  - get_signal_stream      → unread copy_trade_signals filtered by assignments
  - get_my_pnl             → today / week PnL from subscriber_fills
  - get_my_fills           → recent fills (paginated)
  - get_my_assignments     → bots + risk caps the subscriber follows
  - report_fill            → daemon writes its fill back to subscriber_fills
  - heartbeat              → daemon liveness ping (upserts subscriber_heartbeats)
  - ack_signal             → subscriber acknowledges a signal (audit trail)

A subscriber can ONLY ever see / write their own rows. The bridge resolves
their `subscriber_id` from the API key; this module never trusts a
subscriber-supplied id.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

log = logging.getLogger(__name__)

PAPER_CONTRACT_VERSION = "paper-subscriber.v1"
PAPER_ENVIRONMENT = "paper"
PAPER_SOURCE = "supabase"
PAPER_STARTING_BALANCE_USD = 50_000.0
PAPER_EXECUTOR_SLA_SECONDS = 120


def _service_client():
    try:
        from .marketplace.supabase_tools import _get_sb_client
    except Exception as exc:  # pragma: no cover
        log.warning("supabase_tools unavailable: %s", exc)
        return None
    return _get_sb_client(use_service_role=True)


def _err(msg: str, **extra: Any) -> dict[str, Any]:
    out = {
        "ok": False,
        "error": msg,
        "error_code": msg,
        "contract_version": PAPER_CONTRACT_VERSION,
        "environment": PAPER_ENVIRONMENT,
        "source": PAPER_SOURCE,
    }
    out.update(extra)
    return out


def _contract_meta(*, now: datetime | None = None) -> dict[str, Any]:
    return {
        "ok": True,
        "contract_version": PAPER_CONTRACT_VERSION,
        "environment": PAPER_ENVIRONMENT,
        "source": PAPER_SOURCE,
        "as_of": (now or datetime.now(timezone.utc)).isoformat(),
    }


class PaperDataUnavailable(RuntimeError):
    """Raised when a required paper data dependency cannot be queried."""


# ─── helpers ────────────────────────────────────────────────────────────────

PAPER_ACCOUNT_SELECT = (
    "starting_balance_usd,current_balance_usd,realized_pnl_usd,fills_count,last_reset_at,updated_at"
)


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _round_cents(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _get_paper_account(sb, subscriber_id: str) -> dict[str, Any] | None:
    try:
        resp = (
            sb.table("subscriber_paper_accounts")
            .select(PAPER_ACCOUNT_SELECT)
            .eq("subscriber_id", subscriber_id)
            .maybe_single()
            .execute()
        )
        account = getattr(resp, "data", None)
        return account if isinstance(account, dict) else None
    except Exception as exc:
        log.warning("paper account lookup failed: %s", exc)
        return None


def _paper_account_pnl_usd(paper_account: dict[str, Any] | None) -> float | None:
    if not paper_account:
        return None

    realized = _decimal_or_none(paper_account.get("realized_pnl_usd"))
    if realized is not None:
        return _round_cents(realized)

    current = _decimal_or_none(paper_account.get("current_balance_usd"))
    starting = _decimal_or_none(paper_account.get("starting_balance_usd"))
    if current is None or starting is None:
        return None
    return _round_cents(current - starting)


def _paper_pnl_aliases(paper_account: dict[str, Any] | None) -> dict[str, float | None]:
    paper_pnl = _paper_account_pnl_usd(paper_account)
    return {
        "paper_pnl_usd": paper_pnl,
        "paper_pnl": paper_pnl,
        "paper_pnl_rollup_usd": paper_pnl,
    }


def _list_active_assignments(sb, subscriber_id: str) -> list[dict[str, Any]]:
    """Return non-paused subscriber_bot_assignments for this subscriber."""
    try:
        resp = (
            sb.table("subscriber_bot_assignments")
            .select("bot,size_multiplier,max_contracts,daily_loss_cap_usd,paused")
            .eq("subscriber_id", subscriber_id)
            .execute()
        )
        return list(getattr(resp, "data", None) or [])
    except Exception as exc:
        log.warning("list assignments failed: %s", exc)
        raise PaperDataUnavailable("assignments_unavailable") from exc


# ─── tools ──────────────────────────────────────────────────────────────────


def get_signal_stream(
    subscriber_id: str,
    *,
    since: str | None = None,
    bots: list[str] | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """
    Return active (non-expired) copy_trade_signals for the bots this
    subscriber follows. Stale signals are filtered out so a daemon coming
    online late doesn't fire historical orders.
    """
    sb = _service_client()
    if sb is None:
        return _err("supabase_unavailable")

    try:
        assignments = _list_active_assignments(sb, subscriber_id)
    except PaperDataUnavailable:
        return _err("assignments_unavailable")
    if not assignments:
        return {**_contract_meta(), "signals": [], "assignments": []}
    allowed_bots = {a["bot"] for a in assignments if not a.get("paused")}
    if bots:
        allowed_bots = allowed_bots.intersection({b.upper() for b in bots})
    if not allowed_bots:
        return {
            **_contract_meta(),
            "signals": [],
            "assignments": assignments,
            "note": "all_paused_or_filtered",
        }

    try:
        q = (
            sb.table("copy_trade_signals")
            .select("*")
            .in_("bot", list(allowed_bots))
            .gt("expires_at", datetime.now(timezone.utc).isoformat())
            .order("emitted_at", desc=True)
            .limit(min(max(limit, 1), 500))
        )
        if since:
            q = q.gte("emitted_at", since)
        resp = q.execute()
        signals = list(getattr(resp, "data", None) or [])
    except Exception as exc:
        return _err("query_failed", detail=str(exc))

    return {
        **_contract_meta(),
        "signals": signals,
        "assignments": assignments,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def get_my_pnl(subscriber_id: str) -> dict[str, Any]:
    """Daily fill PnL plus stable account-level paper PnL aliases."""
    sb = _service_client()
    if sb is None:
        return _err("supabase_unavailable")
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=7)
    try:
        today_resp = (
            sb.table("subscriber_fills")
            .select("pnl_usd,bot,fill_kind")
            .eq("subscriber_id", subscriber_id)
            .gte("filled_at", today_start.isoformat())
            .execute()
        )
        week_resp = (
            sb.table("subscriber_fills")
            .select("pnl_usd")
            .eq("subscriber_id", subscriber_id)
            .gte("filled_at", week_start.isoformat())
            .execute()
        )
    except Exception as exc:
        return _err("query_failed", detail=str(exc))

    today_rows = getattr(today_resp, "data", None) or []
    week_rows = getattr(week_resp, "data", None) or []

    pnl_today = sum(float(r.get("pnl_usd") or 0) for r in today_rows)
    pnl_week = sum(float(r.get("pnl_usd") or 0) for r in week_rows)
    by_bot: dict[str, float] = {}
    fills_today = 0
    for r in today_rows:
        if r.get("fill_kind") in ("entry", "exit"):
            fills_today += 1
        bot = r.get("bot") or "unknown"
        by_bot[bot] = by_bot.get(bot, 0.0) + float(r.get("pnl_usd") or 0)

    paper_account = _get_paper_account(sb, subscriber_id)

    return {
        **_contract_meta(now=now),
        "subscriber_id": subscriber_id,
        "pnl_today_usd": round(pnl_today, 2),
        "pnl_7d_usd": round(pnl_week, 2),
        "fills_today": fills_today,
        "pnl_today_by_bot": {k: round(v, 2) for k, v in by_bot.items()},
        **_paper_pnl_aliases(paper_account),
    }


def get_my_fills(
    subscriber_id: str,
    *,
    limit: int = 50,
    bot: str | None = None,
) -> dict[str, Any]:
    sb = _service_client()
    if sb is None:
        return _err("supabase_unavailable")
    try:
        q = (
            sb.table("subscriber_fills")
            .select(
                "id,bot,symbol,side,qty,fill_price,pnl_usd,fill_kind,tradovate_order_id,filled_at,signal_id,error_msg"
            )
            .eq("subscriber_id", subscriber_id)
            .order("filled_at", desc=True)
            .limit(min(max(limit, 1), 500))
        )
        if bot:
            q = q.eq("bot", bot.upper())
        resp = q.execute()
    except Exception as exc:
        return _err("query_failed", detail=str(exc))
    return {
        **_contract_meta(),
        "subscriber_id": subscriber_id,
        "fills": list(getattr(resp, "data", None) or []),
    }


def get_my_portfolio(subscriber_id: str) -> dict[str, Any]:
    """Paper balance, bot assignments, open entries, and 7-day P&L in one call."""
    sb = _service_client()
    if sb is None:
        return _err("supabase_unavailable")

    pnl = get_my_pnl(subscriber_id)
    if pnl.get("error"):
        return pnl

    assignments_payload = get_my_assignments(subscriber_id)
    assignments = assignments_payload.get("assignments") or []

    paper_account = _get_paper_account(sb, subscriber_id)
    paper_pnl = pnl.get("paper_pnl_usd")

    open_entries: list[dict[str, Any]] = []
    bots = [a["bot"] for a in assignments if not a.get("paused")]
    if bots:
        try:
            sig = (
                sb.table("copy_trade_signals")
                .select("id,bot,symbol,side,qty,entry_price,stop_price,tp_price,emitted_at")
                .in_("bot", bots)
                .gt("expires_at", datetime.now(timezone.utc).isoformat())
                .order("emitted_at", desc=True)
                .limit(5)
                .execute()
            )
            open_entries = list(getattr(sig, "data", None) or [])
        except Exception as exc:
            log.warning("get_my_portfolio open entries: %s", exc)

    return {
        **_contract_meta(),
        "subscriber_id": subscriber_id,
        "paper_account": paper_account,
        "assignments": assignments,
        "open_signals": open_entries,
        "pnl_today_usd": pnl.get("pnl_today_usd"),
        "pnl_7d_usd": pnl.get("pnl_7d_usd"),
        "paper_pnl_usd": paper_pnl,
        "paper_pnl": paper_pnl,
        "paper_pnl_rollup_usd": paper_pnl,
        "fills_today": pnl.get("fills_today"),
        **_paper_pnl_aliases(paper_account),
    }


def place_paper_order(
    subscriber_id: str,
    *,
    symbol: str,
    side: str,
    qty: int,
    order_type: str = "market",
    limit_price: float | None = None,
) -> dict[str, Any]:
    """Queue a self-directed paper order (filled at real quotes by paper_trade_executor)."""
    sb = _service_client()
    if sb is None:
        return _err("supabase_unavailable")
    sym = (symbol or "").strip().upper()
    sd = (side or "").strip().upper()
    if sd not in ("BUY", "SELL"):
        return _err("invalid_side", got=side)
    if int(qty) <= 0:
        return _err("invalid_qty", got=qty)
    ot = (order_type or "market").strip().lower()
    if ot not in ("market", "limit"):
        return _err("invalid_order_type", got=order_type)
    if ot == "limit" and limit_price is None:
        return _err("limit_price_required")
    try:
        acct = (
            sb.table("subscriber_paper_accounts")
            .select("subscriber_id")
            .eq("subscriber_id", subscriber_id)
            .maybe_single()
            .execute()
        )
        if not getattr(acct, "data", None):
            return _err("paper_account_missing", hint="Activate AlgoChains Paper first")
    except Exception as exc:
        return _err("account_lookup_failed", detail=str(exc))
    payload = {
        "subscriber_id": subscriber_id,
        "symbol": sym,
        "side": sd,
        "qty": int(qty),
        "order_type": ot,
        "limit_price": limit_price,
        "status": "pending",
    }
    try:
        resp = sb.table("subscriber_paper_orders").insert(payload).execute()
    except Exception as exc:
        return _err("insert_failed", detail=str(exc))
    rows = list(getattr(resp, "data", None) or [])
    return {**_contract_meta(), "subscriber_id": subscriber_id, "order": rows[0] if rows else None}


def cancel_paper_order(subscriber_id: str, *, order_id: str) -> dict[str, Any]:
    """Cancel a pending self-directed paper order."""
    sb = _service_client()
    if sb is None:
        return _err("supabase_unavailable")
    if not order_id:
        return _err("order_id_required")
    try:
        existing = (
            sb.table("subscriber_paper_orders")
            .select("id,status")
            .eq("id", order_id)
            .eq("subscriber_id", subscriber_id)
            .maybe_single()
            .execute()
        )
        row = getattr(existing, "data", None)
        if not row:
            return _err("order_not_found", order_id=order_id)
        if row.get("status") != "pending":
            return _err("not_cancellable", status=row.get("status"))
        sb.table("subscriber_paper_orders").update(
            {"status": "cancelled", "updated_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", order_id).eq("subscriber_id", subscriber_id).execute()
    except Exception as exc:
        return _err("cancel_failed", detail=str(exc))
    return {
        **_contract_meta(),
        "subscriber_id": subscriber_id,
        "order_id": order_id,
        "status": "cancelled",
    }


def get_my_paper_positions(subscriber_id: str) -> dict[str, Any]:
    """Pending self-directed orders and recent filled paper orders."""
    sb = _service_client()
    if sb is None:
        return _err("supabase_unavailable")
    pending: list[dict[str, Any]] = []
    recent: list[dict[str, Any]] = []
    try:
        pend = (
            sb.table("subscriber_paper_orders")
            .select("*")
            .eq("subscriber_id", subscriber_id)
            .in_("status", ["pending", "filled"])
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )
        rows = list(getattr(pend, "data", None) or [])
        pending = [r for r in rows if r.get("status") == "pending"]
        recent = [r for r in rows if r.get("status") == "filled"][:20]
    except Exception as exc:
        return _err("query_failed", detail=str(exc))
    return {
        **_contract_meta(),
        "pending_orders": pending,
        "recent_filled_orders": recent,
    }


def _age_seconds(value: Any, now: datetime) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (now - parsed.astimezone(timezone.utc)).total_seconds())


def get_paper_route_health(subscriber_id: str) -> dict[str, Any]:
    """Report executor heartbeat and pending-order SLA without inventing health."""
    sb = _service_client()
    if sb is None:
        return _err("supabase_unavailable")

    now = datetime.now(timezone.utc)
    account = _get_paper_account(sb, subscriber_id)
    if account is None:
        return _err("paper_account_missing")

    try:
        heartbeat_resp = (
            sb.table("subscriber_heartbeats")
            .select("last_seen,daemon_version,fills_today,pnl_today_usd,notes")
            .eq("subscriber_id", subscriber_id)
            .maybe_single()
            .execute()
        )
        heartbeat_row = getattr(heartbeat_resp, "data", None)
        pending_resp = (
            sb.table("subscriber_paper_orders")
            .select("id,status,created_at,updated_at")
            .eq("subscriber_id", subscriber_id)
            .eq("status", "pending")
            .order("created_at")
            .limit(100)
            .execute()
        )
        pending_orders = list(getattr(pending_resp, "data", None) or [])
    except Exception as exc:
        return _err("paper_route_health_query_failed", detail=str(exc))

    heartbeat_age = _age_seconds(
        heartbeat_row.get("last_seen") if isinstance(heartbeat_row, dict) else None,
        now,
    )
    pending_ages = [
        age
        for age in (_age_seconds(order.get("created_at"), now) for order in pending_orders)
        if age is not None
    ]
    oldest_pending_age = max(pending_ages) if pending_ages else None

    if heartbeat_age is None:
        health = "unavailable"
        reason = "executor_heartbeat_missing"
    elif heartbeat_age > PAPER_EXECUTOR_SLA_SECONDS:
        health = "stale"
        reason = "executor_heartbeat_stale"
    elif oldest_pending_age is not None and oldest_pending_age > PAPER_EXECUTOR_SLA_SECONDS:
        health = "degraded"
        reason = "pending_order_sla_breached"
    else:
        health = "healthy"
        reason = None

    return {
        **_contract_meta(now=now),
        "subscriber_id": subscriber_id,
        "health": health,
        "reason": reason,
        "executor_sla_seconds": PAPER_EXECUTOR_SLA_SECONDS,
        "heartbeat": heartbeat_row if isinstance(heartbeat_row, dict) else None,
        "heartbeat_age_seconds": round(heartbeat_age, 3) if heartbeat_age is not None else None,
        "pending_order_count": len(pending_orders),
        "oldest_pending_age_seconds": (
            round(oldest_pending_age, 3) if oldest_pending_age is not None else None
        ),
    }


def get_marketplace_listings(
    subscriber_id: str,
    *,
    asset_class: str = "all",
    status: str = "all",
    limit: int = 50,
) -> dict[str, Any]:
    """Approved marketplace listings (public data) for subscriber discovery."""
    del subscriber_id  # scope enforced by bridge; listings are not per-subscriber
    try:
        from .marketplace.supabase_tools import get_marketplace_listings as _listings
    except Exception as exc:
        return _err("marketplace_unavailable", detail=str(exc))
    return _listings(status=status, asset_class=asset_class, limit=limit)


def get_my_assignments(subscriber_id: str) -> dict[str, Any]:
    sb = _service_client()
    if sb is None:
        return _err("supabase_unavailable")
    try:
        resp = (
            sb.table("subscriber_bot_assignments")
            .select("bot,size_multiplier,max_contracts,daily_loss_cap_usd,paused,updated_at")
            .eq("subscriber_id", subscriber_id)
            .order("bot")
            .execute()
        )
    except Exception as exc:
        return _err("query_failed", detail=str(exc))
    return {
        **_contract_meta(),
        "subscriber_id": subscriber_id,
        "assignments": list(getattr(resp, "data", None) or []),
    }


def report_fill(
    subscriber_id: str,
    *,
    signal_id: str | None = None,
    bot: str,
    symbol: str,
    side: str,
    qty: int,
    fill_price: float | None = None,
    tradovate_order_id: str | None = None,
    pnl_usd: float | None = None,
    fill_kind: str = "entry",
    error_msg: str | None = None,
    bracket_id: str | None = None,
    is_paper: bool = False,
) -> dict[str, Any]:
    """
    Daemon callback: persist a fill the local copy-trader executed.
    All identity comes from the resolved subscriber_id; the daemon may not
    forge a different one.

    `bracket_id` is forwarded so the owner audit can pair entries with exits
    without having to chain through signal_id. `is_paper` defaults to False —
    the paper_trade_executor on the owner host is the only writer that sets
    it true. The unique index on (subscriber_id, signal_id, fill_kind) for
    entry/exit/modify means a replayed insert returns 23505, which we surface
    as ok with duplicate=True so the daemon's retry loop exits cleanly.
    """
    sb = _service_client()
    if sb is None:
        return _err("supabase_unavailable")
    if fill_kind not in ("entry", "exit", "modify", "reject"):
        return _err("invalid_fill_kind", got=fill_kind)
    payload = {
        "subscriber_id": subscriber_id,
        "signal_id": signal_id,
        "bot": (bot or "").upper(),
        "symbol": symbol,
        "side": (side or "").upper(),
        "qty": int(qty),
        "fill_price": fill_price,
        "tradovate_order_id": tradovate_order_id,
        "pnl_usd": pnl_usd,
        "fill_kind": fill_kind,
        "error_msg": error_msg,
        "bracket_id": bracket_id,
        "is_paper": bool(is_paper),
    }
    try:
        resp = sb.table("subscriber_fills").insert(payload).execute()
    except Exception as exc:
        msg = str(exc).lower()
        if "23505" in msg or "duplicate key" in msg or "uniq_sf_subscriber_signal_kind" in msg:
            return {
                **_contract_meta(),
                "subscriber_id": subscriber_id,
                "fill_id": None,
                "duplicate": True,
            }
        return _err("insert_failed", detail=str(exc))
    rows = getattr(resp, "data", None) or []
    return {
        **_contract_meta(),
        "subscriber_id": subscriber_id,
        "fill_id": (rows[0].get("id") if rows else None),
        "duplicate": False,
    }


def heartbeat(
    subscriber_id: str,
    *,
    daemon_version: str | None = None,
    tradovate_linked: bool | None = None,
    fills_today: int | None = None,
    pnl_today_usd: float | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Upsert one row in subscriber_heartbeats (PK = subscriber_id)."""
    sb = _service_client()
    if sb is None:
        return _err("supabase_unavailable")
    payload: dict[str, Any] = {
        "subscriber_id": subscriber_id,
        "last_seen": datetime.now(timezone.utc).isoformat(),
    }
    if daemon_version is not None:
        payload["daemon_version"] = daemon_version
    if tradovate_linked is not None:
        payload["tradovate_linked"] = bool(tradovate_linked)
    if fills_today is not None:
        payload["fills_today"] = int(fills_today)
    if pnl_today_usd is not None:
        payload["pnl_today_usd"] = float(pnl_today_usd)
    if notes is not None:
        payload["notes"] = notes
    try:
        resp = (
            sb.table("subscriber_heartbeats").upsert(payload, on_conflict="subscriber_id").execute()
        )
    except Exception as exc:
        return _err("upsert_failed", detail=str(exc))
    rows = getattr(resp, "data", None) or []
    return {**_contract_meta(), "subscriber_id": subscriber_id, "row": rows[0] if rows else None}


def ack_signal(subscriber_id: str, *, signal_id: str) -> dict[str, Any]:
    """
    Lightweight acknowledgement: writes a 'reject' or 'entry' decision shell
    into subscriber_fills with no broker fields. Used by the daemon to record
    that it considered a signal but chose not to act (e.g. paused, cap hit).
    """
    sb = _service_client()
    if sb is None:
        return _err("supabase_unavailable")
    try:
        sig = (
            sb.table("copy_trade_signals")
            .select("bot,symbol,side,qty")
            .eq("id", signal_id)
            .maybe_single()
            .execute()
        )
        sig_row = getattr(sig, "data", None) or {}
    except Exception as exc:
        return _err("signal_lookup_failed", detail=str(exc))
    if not sig_row:
        return _err("signal_not_found", signal_id=signal_id)
    payload = {
        "subscriber_id": subscriber_id,
        "signal_id": signal_id,
        "bot": sig_row.get("bot"),
        "symbol": sig_row.get("symbol"),
        "side": sig_row.get("side"),
        "qty": int(sig_row.get("qty") or 0),
        "fill_kind": "reject",
        "error_msg": "ack_only",
    }
    try:
        sb.table("subscriber_fills").insert(payload).execute()
    except Exception as exc:
        return _err("insert_failed", detail=str(exc))
    return {**_contract_meta(), "subscriber_id": subscriber_id}


# ─── dispatcher ─────────────────────────────────────────────────────────────

SUBSCRIBER_TOOL_HANDLERS = {
    "get_signal_stream": get_signal_stream,
    "get_my_pnl": get_my_pnl,
    "get_my_fills": get_my_fills,
    "get_my_assignments": get_my_assignments,
    "get_my_portfolio": get_my_portfolio,
    "get_marketplace_listings": get_marketplace_listings,
    "place_paper_order": place_paper_order,
    "cancel_paper_order": cancel_paper_order,
    "get_my_paper_positions": get_my_paper_positions,
    "get_paper_route_health": get_paper_route_health,
    "report_fill": report_fill,
    "heartbeat": heartbeat,
    "ack_signal": ack_signal,
}

# Required scope per tool. The bridge enforces that the resolved key has
# the relevant scope before dispatching.
SUBSCRIBER_TOOL_SCOPES = {
    "get_signal_stream": "signal_stream",
    "get_my_pnl": "my_pnl",
    "get_my_fills": "my_fills",
    "get_my_assignments": "my_assignments",
    "get_my_portfolio": "my_pnl",
    "get_marketplace_listings": "my_assignments",
    "place_paper_order": "paper_trade",
    "cancel_paper_order": "paper_trade",
    "get_my_paper_positions": "paper_trade",
    "get_paper_route_health": "paper_trade",
    "report_fill": "report_fill",
    "heartbeat": "heartbeat",
    "ack_signal": "report_fill",
}

SUBSCRIBER_TOOLS = frozenset(SUBSCRIBER_TOOL_HANDLERS.keys())


def call_subscriber_tool(
    name: str,
    subscriber_id: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Route a subscriber-scoped tool call. The bridge has already validated scope."""
    handler = SUBSCRIBER_TOOL_HANDLERS.get(name)
    if handler is None:
        return _err("unknown_subscriber_tool", tool=name)
    args = dict(arguments or {})
    args.pop("subscriber_id", None)  # never trust caller-supplied id
    try:
        return handler(subscriber_id, **args)
    except TypeError as exc:
        return _err("bad_arguments", tool=name, detail=str(exc))
    except Exception as exc:
        log.exception("subscriber tool %s failed", name)
        return _err("handler_failed", tool=name, detail=str(exc))
