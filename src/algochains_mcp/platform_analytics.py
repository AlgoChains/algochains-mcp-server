"""
platform_analytics.py — Minimal Platform Analytics
====================================================

Tracks page views, signups, conversions, and funnel events for
the AlgoChains soft-launch. Stored in Supabase
(algochains_analytics_events table) or local JSON fallback.

Events follow a simple schema:
  event_type: page_view | signup | email_verified | broker_connected |
               bot_started | purchase | waitlist_join | conversion
  session_id: anonymous session identifier
  user_id:    optional Supabase user ID
  properties: JSON bag of event-specific data

This is intentionally lightweight — not a replacement for PostHog/Mixpanel
in production, but covers the soft-launch analytics requirement.

No synthetic events. No mock user counts. All data comes from real events
being triggered via the MCP tool layer.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

logger = logging.getLogger("algochains_mcp.platform_analytics")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
_TABLE = "algochains_analytics_events"
_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

_STATE_DIR = Path(os.getenv("ALGOCHAINS_STATE_DIR", "state"))
_LOCAL_FILE = _STATE_DIR / "analytics_events.json"

# Valid event types for the soft-launch funnel
FUNNEL_EVENTS = {
    "page_view",
    "signup",
    "email_verified",
    "waitlist_join",
    "broker_connected",
    "broker_disconnected",
    "bot_started",
    "bot_stopped",
    "purchase",
    "subscription_started",
    "subscription_cancelled",
    "support_ticket_created",
    "invite_accepted",
    "conversion",
    "session_start",
    "session_end",
    "error",
}


def _sb_available() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SERVICE_KEY)


def _sb_headers() -> dict:
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
    }


def _load_local() -> list[dict]:
    if _LOCAL_FILE.exists():
        try:
            return json.loads(_LOCAL_FILE.read_text())
        except Exception:
            return []
    return []


def _save_local(events: list[dict]) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    # Keep max 50k events locally
    if len(events) > 50000:
        events = events[-50000:]
    _LOCAL_FILE.write_text(json.dumps(events, indent=2, default=str))


async def track_event(
    event_type: str,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    page: Optional[str] = None,
    referrer: Optional[str] = None,
    properties: Optional[dict] = None,
    ip_country: Optional[str] = None,
    device: Optional[str] = None,
) -> dict[str, Any]:
    """
    Track a platform analytics event.

    Args:
        event_type:  Type of event (page_view, signup, broker_connected, etc.)
        session_id:  Anonymous session ID (frontend generates this)
        user_id:     Supabase user ID if logged in
        page:        Page path (e.g., /marketplace, /onboarding)
        referrer:    Referring URL or source (e.g., twitter, direct)
        properties:  Additional event properties dict
        ip_country:  ISO country code derived from IP (handled by edge function)
        device:      desktop | mobile | tablet

    Returns:
        event_id, tracked_at
    """
    if event_type not in FUNNEL_EVENTS:
        logger.warning("Unknown event_type '%s' — tracking anyway", event_type)

    event: dict[str, Any] = {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "session_id": session_id or str(uuid.uuid4()),
        "user_id": user_id,
        "page": page,
        "referrer": referrer,
        "properties": properties or {},
        "ip_country": ip_country,
        "device": device,
        "tracked_at": datetime.now(timezone.utc).isoformat(),
    }

    if _sb_available():
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    f"{SUPABASE_URL.rstrip('/')}/rest/v1/{_TABLE}",
                    headers=_sb_headers(),
                    json=event,
                )
                if resp.status_code not in (200, 201):
                    logger.debug("Analytics insert failed %s — buffering locally", resp.status_code)
                    events = _load_local()
                    events.append(event)
                    _save_local(events)
        except Exception as e:
            logger.debug("Analytics Supabase error: %s — buffering locally", e)
            events = _load_local()
            events.append(event)
            _save_local(events)
    else:
        events = _load_local()
        events.append(event)
        _save_local(events)

    return {"event_id": event["event_id"], "tracked": True}


async def track_page_view(
    page: str,
    session_id: str,
    user_id: Optional[str] = None,
    referrer: Optional[str] = None,
    device: Optional[str] = None,
) -> dict[str, Any]:
    """Convenience wrapper for page view tracking."""
    return await track_event(
        event_type="page_view",
        session_id=session_id,
        user_id=user_id,
        page=page,
        referrer=referrer,
        device=device,
    )


async def track_signup(
    user_id: str,
    email: str,
    source: str = "direct",
    invite_code: Optional[str] = None,
) -> dict[str, Any]:
    """Track a new user signup."""
    return await track_event(
        event_type="signup",
        user_id=user_id,
        properties={
            "email_domain": email.split("@")[-1] if "@" in email else "unknown",
            "source": source,
            "invite_code": invite_code,
        },
    )


async def track_conversion(
    user_id: str,
    conversion_type: str,
    value_usd: Optional[float] = None,
    product: Optional[str] = None,
) -> dict[str, Any]:
    """Track a conversion event (purchase, subscription, etc.)."""
    return await track_event(
        event_type="conversion",
        user_id=user_id,
        properties={
            "conversion_type": conversion_type,
            "value_usd": value_usd,
            "product": product,
        },
    )


async def get_analytics_summary(
    days: int = 7,
    event_type: Optional[str] = None,
) -> dict[str, Any]:
    """
    Get analytics summary for the last N days.

    Returns:
        Total events, unique sessions, unique users, by-day breakdown,
        top pages, top event types, conversion funnel.
    """
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    if _sb_available():
        try:
            params = f"tracked_at=gte.{cutoff}"
            if event_type:
                params += f"&event_type=eq.{event_type}"
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    f"{SUPABASE_URL.rstrip('/')}/rest/v1/{_TABLE}"
                    f"?{params}&select=event_type,session_id,user_id,page,tracked_at",
                    headers=_sb_headers(),
                )
                if resp.status_code == 200:
                    rows = resp.json()
                    return _compute_summary(rows, days)
        except Exception as e:
            logger.error("Analytics summary Supabase error: %s", e)

    # Local fallback
    import time as _time
    cutoff_ts = _time.time() - days * 86400
    events = _load_local()

    def _parse_ts(s: str) -> float:
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        except Exception:
            return 0.0

    filtered = [e for e in events if _parse_ts(e.get("tracked_at", "")) >= cutoff_ts]
    if event_type:
        filtered = [e for e in filtered if e.get("event_type") == event_type]
    return _compute_summary(filtered, days)


def _compute_summary(rows: list[dict], days: int) -> dict[str, Any]:
    total = len(rows)
    unique_sessions = len({r.get("session_id") for r in rows if r.get("session_id")})
    unique_users = len({r.get("user_id") for r in rows if r.get("user_id")})

    by_type: dict[str, int] = {}
    by_page: dict[str, int] = {}
    by_day: dict[str, int] = {}

    for r in rows:
        et = r.get("event_type", "unknown")
        by_type[et] = by_type.get(et, 0) + 1

        pg = r.get("page") or "/"
        by_page[pg] = by_page.get(pg, 0) + 1

        ts = r.get("tracked_at", "")
        day = ts[:10] if ts else "unknown"
        by_day[day] = by_day.get(day, 0) + 1

    # Funnel: signup → email_verified → broker_connected → bot_started → purchase
    funnel_keys = ["signup", "email_verified", "broker_connected", "bot_started", "purchase"]
    funnel = {k: by_type.get(k, 0) for k in funnel_keys}

    top_pages = sorted(by_page.items(), key=lambda x: x[1], reverse=True)[:10]

    return {
        "success": True,
        "period_days": days,
        "total_events": total,
        "unique_sessions": unique_sessions,
        "unique_users": unique_users,
        "by_event_type": by_type,
        "top_pages": [{"page": p, "views": c} for p, c in top_pages],
        "by_day": by_day,
        "conversion_funnel": funnel,
        "conversion_rate_pct": round(funnel["purchase"] / funnel["signup"] * 100, 1) if funnel["signup"] > 0 else 0.0,
    }
