"""
verification.py — Email & SMS Code Verification for Purchases/Signups
=======================================================================

Generates and validates one-time codes (OTC) for:
  - Purchase confirmation (email + optional SMS)
  - Account email verification
  - High-value action confirmation (broker connect, withdrawal)

Code format: 6-digit numeric, valid for 10 minutes.
Stored in Supabase (algochains_verification_codes) or locally.
Rate-limited to 3 sends per email per hour.

Required env vars:
  SUPABASE_URL           — Supabase project URL
  SUPABASE_SERVICE_KEY   — Service role key
  RESEND_API_KEY         — Email delivery via Resend
  TWILIO_ACCOUNT_SID     — Twilio SID for SMS (optional)
  TWILIO_AUTH_TOKEN      — Twilio auth token (optional)
  TWILIO_FROM_NUMBER     — Twilio sending number, e.g. +15551234567 (optional)
  VERIFICATION_FROM_EMAIL — From address (default: noreply@algochains.ai)

No mock codes. Codes are cryptographically random.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

logger = logging.getLogger("algochains_mcp.verification")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")
VERIFICATION_FROM_EMAIL = os.getenv("VERIFICATION_FROM_EMAIL", "noreply@algochains.ai")

_TABLE = "algochains_verification_codes"
_CODE_TTL_SECONDS = 600  # 10 minutes
_MAX_ATTEMPTS = 5        # lock after 5 wrong attempts
_RATE_LIMIT_WINDOW = 3600  # 1 hour
_RATE_LIMIT_MAX = 3        # 3 sends per hour per destination
_TIMEOUT = httpx.Timeout(15.0, connect=5.0)

_STATE_DIR = Path(os.getenv("ALGOCHAINS_STATE_DIR", "state"))
_LOCAL_FILE = _STATE_DIR / "verification_codes.json"


class VerificationError(Exception):
    pass


def _generate_code() -> str:
    """Generate a cryptographically secure 6-digit numeric code."""
    return str(secrets.randbelow(900000) + 100000)


def _hash_code(code: str) -> str:
    """One-way hash the code so we don't store plaintext."""
    return hashlib.sha256(code.encode()).hexdigest()


def _sb_available() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SERVICE_KEY)


def _sb_headers() -> dict:
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _load_local() -> dict[str, dict]:
    if _LOCAL_FILE.exists():
        try:
            return json.loads(_LOCAL_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save_local(codes: dict[str, dict]) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _LOCAL_FILE.write_text(json.dumps(codes, indent=2, default=str))


def _make_key(destination: str, purpose: str) -> str:
    return hashlib.sha256(f"{destination.lower()}:{purpose}".encode()).hexdigest()[:32]


async def _check_rate_limit(destination: str) -> bool:
    """Returns True if under rate limit (sending allowed)."""
    key = f"rl:{_make_key(destination, 'any')}"
    now = time.time()
    window_start = now - _RATE_LIMIT_WINDOW

    if _sb_available():
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    f"{SUPABASE_URL.rstrip('/')}/rest/v1/{_TABLE}"
                    f"?destination=eq.{destination.lower()}"
                    f"&created_at=gte.{datetime.fromtimestamp(window_start, tz=timezone.utc).isoformat()}"
                    f"&select=id",
                    headers=_sb_headers(),
                )
                if resp.status_code == 200:
                    return len(resp.json()) < _RATE_LIMIT_MAX
        except Exception:
            pass

    codes = _load_local()
    recent = sum(
        1 for v in codes.values()
        if v.get("destination", "").lower() == destination.lower()
        and v.get("created_at", 0) > window_start
    )
    return recent < _RATE_LIMIT_MAX


async def _store_code(destination: str, purpose: str, code_hash: str) -> None:
    now = time.time()
    record = {
        "destination": destination.lower(),
        "purpose": purpose,
        "code_hash": code_hash,
        "attempts": 0,
        "used": False,
        "created_at": now,
        "expires_at": now + _CODE_TTL_SECONDS,
    }

    if _sb_available():
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                # Invalidate any existing codes for this destination+purpose
                await client.delete(
                    f"{SUPABASE_URL.rstrip('/')}/rest/v1/{_TABLE}"
                    f"?destination=eq.{destination.lower()}&purpose=eq.{purpose}&used=eq.false",
                    headers=_sb_headers(),
                )
                await client.post(
                    f"{SUPABASE_URL.rstrip('/')}/rest/v1/{_TABLE}",
                    headers=_sb_headers(),
                    json=record,
                )
                return
        except Exception as e:
            logger.error("Supabase store_code error: %s", e)

    codes = _load_local()
    key = _make_key(destination, purpose)
    codes[key] = record
    _save_local(codes)


async def _get_code_record(destination: str, purpose: str) -> Optional[dict]:
    if _sb_available():
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    f"{SUPABASE_URL.rstrip('/')}/rest/v1/{_TABLE}"
                    f"?destination=eq.{destination.lower()}&purpose=eq.{purpose}&used=eq.false"
                    f"&order=created_at.desc&limit=1",
                    headers=_sb_headers(),
                )
                if resp.status_code == 200 and resp.json():
                    return resp.json()[0]
        except Exception as e:
            logger.error("Supabase get_code error: %s", e)

    codes = _load_local()
    key = _make_key(destination, purpose)
    return codes.get(key)


async def _mark_used(destination: str, purpose: str) -> None:
    if _sb_available():
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                await client.patch(
                    f"{SUPABASE_URL.rstrip('/')}/rest/v1/{_TABLE}"
                    f"?destination=eq.{destination.lower()}&purpose=eq.{purpose}&used=eq.false",
                    headers=_sb_headers(),
                    json={"used": True},
                )
                return
        except Exception:
            pass
    codes = _load_local()
    key = _make_key(destination, purpose)
    if key in codes:
        codes[key]["used"] = True
        _save_local(codes)


async def _increment_attempts(destination: str, purpose: str) -> None:
    if _sb_available():
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                rec = await _get_code_record(destination, purpose)
                if rec and rec.get("id"):
                    await client.patch(
                        f"{SUPABASE_URL.rstrip('/')}/rest/v1/{_TABLE}?id=eq.{rec['id']}",
                        headers=_sb_headers(),
                        json={"attempts": rec.get("attempts", 0) + 1},
                    )
                return
        except Exception:
            pass
    codes = _load_local()
    key = _make_key(destination, purpose)
    if key in codes:
        codes[key]["attempts"] = codes[key].get("attempts", 0) + 1
        _save_local(codes)


# ── Public API ────────────────────────────────────────────────────────────────

async def send_email_code(
    email: str,
    purpose: str = "email_verification",
    context: Optional[str] = None,
) -> dict[str, Any]:
    """
    Send a 6-digit verification code to an email address.

    Args:
        email:   Target email address
        purpose: Why we're verifying (email_verification, purchase, broker_connect)
        context: Optional context shown in the email (e.g., "connecting your Schwab account")

    Returns:
        success: bool
        expires_in: seconds until code expires
    """
    if not email or "@" not in email:
        return {"success": False, "error": "Valid email address required"}
    if not RESEND_API_KEY:
        return {
            "success": False,
            "error": "RESEND_API_KEY not configured. Cannot send verification email.",
        }

    email = email.lower().strip()
    allowed = await _check_rate_limit(email)
    if not allowed:
        return {
            "success": False,
            "error": f"Too many codes sent. Please wait before requesting a new code.",
        }

    code = _generate_code()
    code_hash = _hash_code(code)
    await _store_code(email, purpose, code_hash)

    purpose_labels = {
        "email_verification": "verify your email address",
        "purchase": "confirm your purchase",
        "broker_connect": "connect your broker account",
        "password_reset": "reset your password",
        "account_recovery": "recover your account",
    }
    action_label = purpose_labels.get(purpose, purpose.replace("_", " "))
    ctx_text = f"<p>Context: {context}</p>" if context else ""

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
                json={
                    "from": VERIFICATION_FROM_EMAIL,
                    "to": [email],
                    "subject": f"AlgoChains Verification Code: {code}",
                    "html": f"""
<h2>Your verification code</h2>
<p>Use this code to {action_label}:</p>
{ctx_text}
<h1 style="font-size:48px;font-family:monospace;letter-spacing:8px;color:#2563eb">{code}</h1>
<p>This code expires in <strong>10 minutes</strong>.</p>
<p>If you didn't request this, ignore this email.</p>
<hr><small>AlgoChains · noreply</small>
""",
                },
            )
            if resp.status_code not in (200, 201):
                return {"success": False, "error": f"Email delivery failed: {resp.text[:200]}"}
    except Exception as e:
        return {"success": False, "error": f"Email send failed: {e}"}

    return {
        "success": True,
        "sent_to": email,
        "expires_in_seconds": _CODE_TTL_SECONDS,
        "purpose": purpose,
    }


async def send_sms_code(
    phone: str,
    purpose: str = "purchase",
    context: Optional[str] = None,
) -> dict[str, Any]:
    """
    Send a 6-digit verification code via SMS using Twilio.

    Args:
        phone:   E.164 format phone number (+15551234567)
        purpose: Why we're verifying
        context: Optional context shown in the SMS

    Returns:
        success: bool
        message_sid: Twilio message SID if successful
    """
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN or not TWILIO_FROM_NUMBER:
        return {
            "success": False,
            "error": (
                "Twilio not configured. Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, "
                "TWILIO_FROM_NUMBER to enable SMS verification."
            ),
        }

    phone = phone.strip()
    if not phone.startswith("+"):
        return {"success": False, "error": "Phone number must be in E.164 format: +15551234567"}

    allowed = await _check_rate_limit(phone)
    if not allowed:
        return {"success": False, "error": "Too many codes sent. Wait before requesting another."}

    code = _generate_code()
    code_hash = _hash_code(code)
    await _store_code(phone, purpose, code_hash)

    ctx_text = f" ({context})" if context else ""
    body = f"AlgoChains: Your verification code{ctx_text} is {code}. Expires in 10 min. Do not share."

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json",
                auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
                data={"From": TWILIO_FROM_NUMBER, "To": phone, "Body": body},
            )
            if resp.status_code not in (200, 201):
                return {"success": False, "error": f"SMS delivery failed: {resp.text[:200]}"}
            return {
                "success": True,
                "sent_to": phone,
                "message_sid": resp.json().get("sid", ""),
                "expires_in_seconds": _CODE_TTL_SECONDS,
                "purpose": purpose,
            }
    except Exception as e:
        return {"success": False, "error": f"SMS send failed: {e}"}


async def verify_code(
    destination: str,
    code: str,
    purpose: str = "email_verification",
) -> dict[str, Any]:
    """
    Verify a code that was previously sent to an email or phone number.

    Args:
        destination: Email or phone number the code was sent to
        code:        The 6-digit code the user entered
        purpose:     Must match the purpose used in send_email_code / send_sms_code

    Returns:
        valid:   True if code is correct and not expired
        error:   Description if invalid
    """
    if not destination or not code:
        return {"valid": False, "error": "destination and code are required"}

    destination = destination.lower().strip()
    record = await _get_code_record(destination, purpose)

    if not record:
        return {"valid": False, "error": "No verification code found. Request a new one."}

    if record.get("used"):
        return {"valid": False, "error": "Code already used. Request a new one."}

    expires_at = record.get("expires_at", 0)
    if time.time() > expires_at:
        return {"valid": False, "error": f"Code expired. Request a new one."}

    attempts = record.get("attempts", 0)
    if attempts >= _MAX_ATTEMPTS:
        return {"valid": False, "error": "Too many failed attempts. Request a new code."}

    # Constant-time comparison
    expected_hash = record.get("code_hash", "")
    submitted_hash = _hash_code(code)
    if not hmac.compare_digest(expected_hash, submitted_hash):
        await _increment_attempts(destination, purpose)
        remaining = _MAX_ATTEMPTS - attempts - 1
        return {"valid": False, "error": f"Invalid code. {remaining} attempt(s) remaining."}

    await _mark_used(destination, purpose)
    return {
        "valid": True,
        "destination": destination,
        "purpose": purpose,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }
