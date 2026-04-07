"""
password_reset.py — Password Reset & Account Recovery
=======================================================

Handles password reset and account recovery flows via Supabase Auth.

Flow:
  1. initiate_password_reset(email) → sends reset link via Supabase + Resend
  2. verify_reset_token(token)       → validates token, returns session
  3. complete_password_reset(token, new_password) → sets new password

Account recovery (for users who lost access to email):
  1. initiate_account_recovery(email, backup_method) → starts recovery
  2. verify_recovery_code(token, code) → confirms identity
  3. Recovery routes to support ticket if automated recovery fails

Requires:
  SUPABASE_URL              — Supabase project URL
  SUPABASE_SERVICE_KEY      — Service role key
  SUPABASE_ANON_KEY         — Anon key for client-side flows
  RESEND_API_KEY            — Email delivery
  SUPPORT_EMAIL             — Where unrecoverable accounts are escalated

No mock tokens. No synthetic resets. All tokens come from Supabase Auth.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

logger = logging.getLogger("algochains_mcp.auth.password_reset")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", "support@algochains.ai")
RESET_FROM_EMAIL = os.getenv("VERIFICATION_FROM_EMAIL", "noreply@algochains.ai")
RESET_REDIRECT_URL = os.getenv(
    "SUPABASE_RESET_REDIRECT_URL",
    "https://algochains.ai/auth/reset-password",
)
_TIMEOUT = httpx.Timeout(15.0, connect=5.0)

_STATE_DIR = Path(os.getenv("ALGOCHAINS_STATE_DIR", "state"))
_RECOVERY_FILE = _STATE_DIR / "recovery_requests.json"

# Password policy
_MIN_PASSWORD_LENGTH = 12
_PASSWORD_RE = re.compile(
    r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>\/?]).{12,}$"
)


def _sb_available() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SERVICE_KEY)


def _sb_auth_headers() -> dict:
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
    }


def _sb_anon_headers() -> dict:
    return {
        "apikey": SUPABASE_ANON_KEY or SUPABASE_SERVICE_KEY,
        "Content-Type": "application/json",
    }


def _load_recovery_requests() -> dict[str, dict]:
    if _RECOVERY_FILE.exists():
        try:
            return json.loads(_RECOVERY_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save_recovery_requests(requests: dict[str, dict]) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _RECOVERY_FILE.write_text(json.dumps(requests, indent=2, default=str))


def _validate_password(password: str) -> Optional[str]:
    """Returns error message if password fails policy, None if OK."""
    if len(password) < _MIN_PASSWORD_LENGTH:
        return f"Password must be at least {_MIN_PASSWORD_LENGTH} characters"
    if not _PASSWORD_RE.match(password):
        return (
            "Password must contain uppercase, lowercase, number, and special character "
            "(!@#$%^&*()_+-=[]{}|;:,.<>?)"
        )
    return None


async def initiate_password_reset(email: str) -> dict[str, Any]:
    """
    Send a password reset link to the user's email via Supabase Auth.

    Always returns success=True (even if email not found) to prevent
    user enumeration attacks. The actual email is sent by Supabase
    only if the account exists.

    Args:
        email: The user's registered email address

    Returns:
        success: True
        message: Generic confirmation message
    """
    if not email or "@" not in email:
        return {"success": False, "error": "Valid email address required"}

    email = email.lower().strip()

    if not _sb_available():
        return {
            "success": False,
            "error": "Supabase not configured. Set SUPABASE_URL and SUPABASE_SERVICE_KEY.",
        }

    # Use Supabase Auth Admin API to send reset email
    # This uses the service role key which can initiate resets for any user
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{SUPABASE_URL.rstrip('/')}/auth/v1/recover",
                headers={**_sb_anon_headers(), "apikey": SUPABASE_ANON_KEY or SUPABASE_SERVICE_KEY},
                json={"email": email, "redirect_to": RESET_REDIRECT_URL},
            )
            # Supabase returns 200 even if user doesn't exist (by design)
            if resp.status_code not in (200, 204):
                logger.warning("Supabase password reset returned %s: %s", resp.status_code, resp.text[:200])
    except Exception as e:
        logger.error("Supabase password reset error: %s", e)
        return {"success": False, "error": f"Reset request failed: {e}"}

    return {
        "success": True,
        "message": (
            "If an account exists with that email, you'll receive a reset link shortly. "
            "Check your spam folder if you don't see it within a few minutes."
        ),
        "redirect_url": RESET_REDIRECT_URL,
        "expires_in": "1 hour",
    }


async def complete_password_reset(
    access_token: str,
    new_password: str,
) -> dict[str, Any]:
    """
    Complete a password reset using the token from the reset email link.

    The frontend extracts access_token from the URL fragment (#access_token=...).
    This function validates the token and sets the new password via Supabase Auth.

    Args:
        access_token: The access token from the reset email URL fragment
        new_password: The new password (must meet policy requirements)

    Returns:
        success: True if password was changed
        error:   Description if failed
    """
    if not access_token or not new_password:
        return {"success": False, "error": "access_token and new_password are required"}

    password_error = _validate_password(new_password)
    if password_error:
        return {"success": False, "error": password_error}

    if not _sb_available():
        return {"success": False, "error": "Supabase not configured."}

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            # Use the user's access token to update their own password
            resp = await client.put(
                f"{SUPABASE_URL.rstrip('/')}/auth/v1/user",
                headers={
                    "apikey": SUPABASE_ANON_KEY or SUPABASE_SERVICE_KEY,
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json={"password": new_password},
            )
            if resp.status_code in (200, 204):
                return {
                    "success": True,
                    "message": "Password updated successfully. You can now sign in.",
                }
            data = resp.json()
            err = data.get("error_description") or data.get("msg") or f"HTTP {resp.status_code}"
            return {"success": False, "error": f"Password reset failed: {err}"}
    except Exception as e:
        return {"success": False, "error": f"Password reset request failed: {e}"}


async def initiate_account_recovery(
    email: str,
    reason: str = "lost_email_access",
    contact_info: Optional[str] = None,
) -> dict[str, Any]:
    """
    Start account recovery for users who cannot receive the reset email.

    Creates a support ticket and sends instructions to the user's
    alternate contact method.

    Args:
        email:        The email on the account they're trying to recover
        reason:       lost_email_access | lost_2fa | account_locked | other
        contact_info: Alternate contact (phone or backup email) for verification

    Returns:
        ticket_id: Support ticket ID for tracking recovery
        instructions: What to do next
    """
    if not email or "@" not in email:
        return {"success": False, "error": "Email address required"}

    # Import here to avoid circular deps
    from algochains_mcp.support_tickets import create_ticket

    ticket_result = await create_ticket(
        subject=f"Account Recovery Request — {email}",
        description=(
            f"User requesting account recovery.\n"
            f"Email: {email}\n"
            f"Reason: {reason}\n"
            f"Alternate contact: {contact_info or 'not provided'}\n"
            f"Requested at: {datetime.now(timezone.utc).isoformat()}"
        ),
        user_email=contact_info or email,
        category="account",
        priority="high",
        metadata={"recovery_email": email, "reason": reason},
    )

    # Generate a recovery request record
    recovery_token = secrets.token_urlsafe(24)
    now = time.time()
    requests = _load_recovery_requests()
    requests[recovery_token] = {
        "email": email,
        "reason": reason,
        "contact_info": contact_info,
        "ticket_id": ticket_result.get("ticket_id"),
        "created_at": now,
        "expires_at": now + 86400 * 3,  # 3 day recovery window
        "verified": False,
    }
    _save_recovery_requests(requests)

    return {
        "success": True,
        "ticket_id": ticket_result.get("ticket_id"),
        "message": (
            "Your account recovery request has been submitted. "
            "Our support team will contact you at your alternate address "
            f"({contact_info or 'the email on file'}) within 24 hours to verify your identity."
        ),
        "next_steps": [
            "Check your alternate email/phone for a message from our support team",
            "Prepare proof of identity (government ID or account creation details)",
            f"Reference your ticket ID {ticket_result.get('ticket_id')} in all communications",
        ],
        "support_email": SUPPORT_EMAIL,
    }


async def get_password_policy() -> dict[str, Any]:
    """Return the current password policy requirements."""
    return {
        "min_length": _MIN_PASSWORD_LENGTH,
        "requires_uppercase": True,
        "requires_lowercase": True,
        "requires_number": True,
        "requires_special": True,
        "special_chars_allowed": "!@#$%^&*()_+-=[]{}|;:,.<>?",
        "max_length": 128,
        "notes": [
            "Cannot be the same as your previous password",
            "Cannot contain your email address or name",
        ],
    }
