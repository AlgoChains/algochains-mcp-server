"""
waitlist.py — Join Waitlist with Live Email System
====================================================

Manages AlgoChains waitlist signups. Stores in Supabase
(algochains_waitlist table) and sends welcome + confirmation
emails via Resend.

Waitlist positions are assigned by signup time.
On invite, generates a unique invite code and sends it via email.

Required env vars:
  SUPABASE_URL           — Supabase project URL
  SUPABASE_SERVICE_ROLE_KEY   — Service role key
  RESEND_API_KEY         — Resend API key for transactional email
  WAITLIST_FROM_EMAIL    — From address (default: waitlist@algochains.ai)

No synthetic users. No mock data.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

logger = logging.getLogger("algochains_mcp.waitlist")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
WAITLIST_FROM_EMAIL = os.getenv("WAITLIST_FROM_EMAIL", "waitlist@algochains.ai")
_TABLE = "algochains_waitlist"
_TIMEOUT = httpx.Timeout(15.0, connect=5.0)

_STATE_DIR = Path(os.getenv("ALGOCHAINS_STATE_DIR", "state"))
_LOCAL_FILE = _STATE_DIR / "waitlist.json"


def _sb_available() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)


def _sb_headers() -> dict:
    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _load_local() -> list[dict]:
    if _LOCAL_FILE.exists():
        try:
            return json.loads(_LOCAL_FILE.read_text())
        except Exception:
            return []
    return []


def _save_local(entries: list[dict]) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _LOCAL_FILE.write_text(json.dumps(entries, indent=2, default=str))


async def _send_waitlist_email(email: str, first_name: str, position: int) -> bool:
    if not RESEND_API_KEY:
        return False
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
                json={
                    "from": WAITLIST_FROM_EMAIL,
                    "to": [email],
                    "subject": "You're on the AlgoChains Waitlist!",
                    "html": f"""
<h2>Welcome to AlgoChains, {first_name}!</h2>
<p>You're <strong>#{position}</strong> on the waitlist.</p>
<p>AlgoChains connects your AI assistant to your trading accounts — run backtests,
analyze strategies, and execute trades through natural language.</p>
<p>We'll email you when your spot opens up. Early access is rolling out now.</p>
<br>
<p>In the meantime, explore our marketplace at
<a href="https://algochains.ai/marketplace">algochains.ai/marketplace</a>.</p>
<hr>
<small>AlgoChains · <a href="https://algochains.ai/unsubscribe?email={email}">Unsubscribe</a></small>
""",
                },
            )
            return resp.status_code in (200, 201)
    except Exception as e:
        logger.error("Waitlist email send failed: %s", e)
        return False


async def join_waitlist(
    email: str,
    first_name: str = "",
    last_name: str = "",
    broker: str = "",
    use_case: str = "",
    referral_code: Optional[str] = None,
) -> dict[str, Any]:
    """
    Add an email to the AlgoChains waitlist.

    Args:
        email:        User's email address
        first_name:   Optional first name for personalized email
        last_name:    Optional last name
        broker:       Which broker they use (alpaca, schwab, tradovate, etc.)
        use_case:     What they plan to use AlgoChains for
        referral_code: Optional referral code from an existing user

    Returns:
        position: Their position in the waitlist
        already_joined: True if email was already registered
    """
    if not email or "@" not in email:
        return {"success": False, "error": "Valid email address required"}

    email = email.lower().strip()
    now = datetime.now(timezone.utc).isoformat()

    # Check if already registered
    if _sb_available():
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    f"{SUPABASE_URL.rstrip('/')}/rest/v1/{_TABLE}?email=eq.{email}&limit=1",
                    headers=_sb_headers(),
                )
                if resp.status_code == 200 and resp.json():
                    existing = resp.json()[0]
                    return {
                        "success": True,
                        "already_joined": True,
                        "position": existing.get("position", "?"),
                        "joined_at": existing.get("created_at", ""),
                    }
        except Exception as e:
            logger.error("Supabase waitlist check error: %s", e)
    else:
        entries = _load_local()
        for e in entries:
            if e.get("email") == email:
                return {
                    "success": True,
                    "already_joined": True,
                    "position": e.get("position", len(entries)),
                    "joined_at": e.get("created_at", ""),
                }

    # Get current count for position
    position = 1
    if _sb_available():
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    f"{SUPABASE_URL.rstrip('/')}/rest/v1/{_TABLE}?select=id",
                    headers={**_sb_headers(), "Prefer": "count=exact"},
                )
                if resp.status_code == 200:
                    count_header = resp.headers.get("content-range", "0-0/0")
                    position = int(count_header.split("/")[-1]) + 1
        except Exception:
            pass
    else:
        position = len(_load_local()) + 1

    entry = {
        "id": str(uuid.uuid4()),
        "email": email,
        "first_name": first_name.strip(),
        "last_name": last_name.strip(),
        "broker_interest": broker,
        "use_case": use_case[:500],
        "referral_code": referral_code,
        "position": position,
        "status": "waiting",
        "invite_code": None,
        "invited_at": None,
        "created_at": now,
    }

    if _sb_available():
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    f"{SUPABASE_URL.rstrip('/')}/rest/v1/{_TABLE}",
                    headers=_sb_headers(),
                    json=entry,
                )
                if resp.status_code not in (200, 201):
                    logger.warning("Supabase waitlist insert failed: %s", resp.text[:200])
                    entries = _load_local()
                    entries.append(entry)
                    _save_local(entries)
        except Exception as e:
            logger.error("Supabase waitlist error: %s", e)
            entries = _load_local()
            entries.append(entry)
            _save_local(entries)
    else:
        entries = _load_local()
        entries.append(entry)
        _save_local(entries)

    # Send welcome email
    name_display = first_name or email.split("@")[0]
    email_sent = await _send_waitlist_email(email, name_display, position)

    return {
        "success": True,
        "already_joined": False,
        "position": position,
        "email_confirmation_sent": email_sent,
        "message": f"You're #{position} on the waitlist! Check your email for confirmation.",
    }


async def get_waitlist_stats() -> dict[str, Any]:
    """Get waitlist aggregate stats."""
    if _sb_available():
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    f"{SUPABASE_URL.rstrip('/')}/rest/v1/{_TABLE}?select=status,broker_interest",
                    headers={**_sb_headers(), "Prefer": "count=exact"},
                )
                if resp.status_code == 200:
                    rows = resp.json()
                    count_hdr = resp.headers.get("content-range", "0-0/0")
                    total = int(count_hdr.split("/")[-1]) if "/" in count_hdr else len(rows)
                    by_status: dict[str, int] = {}
                    by_broker: dict[str, int] = {}
                    for r in rows:
                        s = r.get("status", "waiting")
                        by_status[s] = by_status.get(s, 0) + 1
                        b = r.get("broker_interest", "unknown") or "unknown"
                        by_broker[b] = by_broker.get(b, 0) + 1
                    return {
                        "success": True,
                        "total": total,
                        "by_status": by_status,
                        "by_broker": by_broker,
                    }
        except Exception as e:
            logger.error("Supabase waitlist stats error: %s", e)

    entries = _load_local()
    by_status: dict[str, int] = {}
    by_broker: dict[str, int] = {}
    for e in entries:
        s = e.get("status", "waiting")
        by_status[s] = by_status.get(s, 0) + 1
        b = e.get("broker_interest", "unknown") or "unknown"
        by_broker[b] = by_broker.get(b, 0) + 1
    return {
        "success": True,
        "total": len(entries),
        "by_status": by_status,
        "by_broker": by_broker,
        "storage": "local",
    }


async def send_invite(email: str) -> dict[str, Any]:
    """
    Send an invite to a waitlist user. Generates a unique invite code
    and emails it. Updates their status to 'invited'.
    """
    email = email.lower().strip()
    invite_code = secrets.token_urlsafe(12)
    now = datetime.now(timezone.utc).isoformat()

    if _sb_available():
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.patch(
                    f"{SUPABASE_URL.rstrip('/')}/rest/v1/{_TABLE}?email=eq.{email}",
                    headers=_sb_headers(),
                    json={"status": "invited", "invite_code": invite_code, "invited_at": now},
                )
                if resp.status_code not in (200, 204):
                    return {"success": False, "error": "Email not found in waitlist"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    else:
        entries = _load_local()
        found = False
        for e in entries:
            if e.get("email") == email:
                e["status"] = "invited"
                e["invite_code"] = invite_code
                e["invited_at"] = now
                found = True
                break
        if not found:
            return {"success": False, "error": f"{email} not found in waitlist"}
        _save_local(entries)

    # Send invite email
    if RESEND_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                await client.post(
                    "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
                    json={
                        "from": WAITLIST_FROM_EMAIL,
                        "to": [email],
                        "subject": "Your AlgoChains Invite is Ready!",
                        "html": f"""
<h2>Your AlgoChains invite is here!</h2>
<p>You're in! Use the code below to create your account:</p>
<h1 style="font-family:monospace;letter-spacing:4px">{invite_code}</h1>
<p><a href="https://algochains.ai/signup?invite={invite_code}" style="background:#2563eb;color:white;padding:12px 24px;text-decoration:none;border-radius:6px">Claim Your Spot</a></p>
<p>This invite expires in 7 days.</p>
<hr><small>AlgoChains — AI-powered trading platform</small>
""",
                    },
                )
        except Exception as e:
            logger.error("Invite email send failed: %s", e)

    return {
        "success": True,
        "email": email,
        "invite_code": invite_code,
        "status": "invited",
        "invite_url": f"https://algochains.ai/signup?invite={invite_code}",
    }
