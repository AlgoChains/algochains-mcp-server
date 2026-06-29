"""
platform_auth.py — Programmatic AlgoChains account + MFA + developer key tools.

Provides MCP tool handlers for:
  Account: signup_algochains, verify_email_otp, login_algochains,
           refresh_session, logout_algochains
  MFA:     enroll_mfa, challenge_mfa, verify_mfa, list_mfa_factors,
           remove_mfa_factor
  Keys:    create_developer_key, list_developer_keys, rotate_developer_key,
           revoke_developer_key, get_developer_key_usage, test_bridge_connection

All network calls are to the Supabase Auth REST API using SUPABASE_URL +
SUPABASE_ANON_KEY from environment. Key operations also write to the
developer_api_keys Supabase table via SUPABASE_SERVICE_KEY.

Session persistence: access_token / refresh_token are stored in
state/platform_session.json (gitignored) and loaded on startup.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("algochains_mcp.auth.platform_auth")

# ── Supabase config ────────────────────────────────────────────────────────
_SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
_SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
_SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
_BRIDGE_URL = os.environ.get(
    "ALGOCHAINS_BRIDGE_URL", "https://api.algochains.ai"
).rstrip("/")

# Session file (gitignored)
_SESSION_FILE = Path(__file__).parents[3] / "state" / "platform_session.json"


# ── helpers ────────────────────────────────────────────────────────────────

def _auth_headers(token: str | None = None) -> dict[str, str]:
    key = token or _load_session().get("access_token", "")
    h = {"apikey": _SUPABASE_ANON_KEY, "Content-Type": "application/json"}
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h


def _service_headers() -> dict[str, str]:
    return {
        "apikey": _SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {_SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
    }


def _load_session() -> dict[str, Any]:
    try:
        if _SESSION_FILE.exists():
            return json.loads(_SESSION_FILE.read_text())
    except Exception:
        pass
    return {}


def _save_session(data: dict[str, Any]) -> None:
    try:
        _SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: write to temp file then rename to prevent corrupt reads on crash
        import tempfile
        tmp = _SESSION_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        try:
            tmp.chmod(0o600)
        except Exception:
            pass  # Windows: chmod may not enforce; acceptable
        tmp.replace(_SESSION_FILE)
    except Exception as exc:
        logger.warning("Could not persist session: %s", exc)


def _clear_session() -> None:
    try:
        _SESSION_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def _masked_key(plaintext: str) -> str:
    """Return prefix + *** for display (e.g. 'ac_live_AbCdEfGh***')."""
    return plaintext[:12] + "***" if len(plaintext) > 12 else "***"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _need_supabase() -> dict[str, str] | None:
    """Return error dict if Supabase env is not configured."""
    if not _SUPABASE_URL or not _SUPABASE_ANON_KEY:
        return {
            "error": "Supabase not configured",
            "required_env": ["SUPABASE_URL", "SUPABASE_ANON_KEY"],
            "hint": "Add these to .env and restart the MCP server.",
        }
    return None


def _aal_level(session: dict) -> str:
    """Return AAL level from session JWT claims ('aal1' or 'aal2')."""
    try:
        import base64
        token = session.get("access_token", "")
        payload_b64 = token.split(".")[1]
        padding = 4 - len(payload_b64) % 4
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=" * padding))
        return payload.get("aal", "aal1")
    except Exception:
        return "aal1"


# ══════════════════════════════════════════════════════════════════════════════
# ACCOUNT TOOLS
# ══════════════════════════════════════════════════════════════════════════════

async def signup_algochains(email: str, password: str) -> dict[str, Any]:
    """
    Create a new AlgoChains account via Supabase Auth.
    Returns session on success or requires_email_confirm if email confirmation enabled.
    """
    err = _need_supabase()
    if err:
        return err

    import httpx
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{_SUPABASE_URL}/auth/v1/signup",
                headers=_auth_headers(),
                json={"email": email, "password": password},
            )
        data = resp.json()
        if resp.status_code == 200 and data.get("access_token"):
            session = {
                "access_token": data["access_token"],
                "refresh_token": data.get("refresh_token", ""),
                "user_id": data.get("user", {}).get("id", ""),
                "email": email,
                "expires_at": time.time() + data.get("expires_in", 3600),
            }
            _save_session(session)
            return {
                "status": "ok",
                "message": "Account created and session active.",
                "user_id": session["user_id"],
                "email": email,
                "next_step": "enroll_mfa",
            }
        # Email confirmation required
        if resp.status_code == 200 and data.get("user", {}).get("confirmation_sent_at"):
            return {
                "status": "requires_email_confirm",
                "message": "Check your email for a confirmation link, then call verify_email_otp.",
                "email": email,
                "next_step": "verify_email_otp",
            }
        return {
            "error": data.get("msg") or data.get("message") or "Signup failed",
            "status_code": resp.status_code,
            "details": data,
        }
    except Exception as exc:
        return {"error": f"Signup request failed: {exc}"}


async def verify_email_otp(email: str, token: str) -> dict[str, Any]:
    """Verify email OTP token from confirmation email."""
    err = _need_supabase()
    if err:
        return err

    import httpx
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{_SUPABASE_URL}/auth/v1/verify",
                headers=_auth_headers(),
                json={"email": email, "token": token, "type": "email"},
            )
        data = resp.json()
        if resp.status_code == 200 and data.get("access_token"):
            session = {
                "access_token": data["access_token"],
                "refresh_token": data.get("refresh_token", ""),
                "user_id": data.get("user", {}).get("id", ""),
                "email": email,
                "expires_at": time.time() + data.get("expires_in", 3600),
            }
            _save_session(session)
            return {
                "status": "ok",
                "message": "Email verified. You are now logged in.",
                "next_step": "enroll_mfa",
            }
        return {
            "error": data.get("msg") or data.get("message") or "Verification failed",
            "status_code": resp.status_code,
        }
    except Exception as exc:
        return {"error": f"Verification request failed: {exc}"}


async def login_algochains(email: str, password: str) -> dict[str, Any]:
    """Login to AlgoChains with email + password."""
    err = _need_supabase()
    if err:
        return err

    import httpx
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{_SUPABASE_URL}/auth/v1/token?grant_type=password",
                headers=_auth_headers(),
                json={"email": email, "password": password},
            )
        data = resp.json()
        if resp.status_code == 200 and data.get("access_token"):
            session = {
                "access_token": data["access_token"],
                "refresh_token": data.get("refresh_token", ""),
                "user_id": data.get("user", {}).get("id", ""),
                "email": email,
                "expires_at": time.time() + data.get("expires_in", 3600),
            }
            _save_session(session)
            aal = _aal_level(session)
            return {
                "status": "ok",
                "message": "Logged in successfully.",
                "user_id": session["user_id"],
                "email": email,
                "aal": aal,
                "next_step": "enroll_mfa" if aal == "aal1" else "create_developer_key",
            }
        return {
            "error": data.get("msg") or data.get("error_description") or "Login failed",
            "status_code": resp.status_code,
        }
    except Exception as exc:
        return {"error": f"Login request failed: {exc}"}


async def refresh_session() -> dict[str, Any]:
    """Refresh an expiring JWT session using the stored refresh_token."""
    err = _need_supabase()
    if err:
        return err

    session = _load_session()
    refresh_token = session.get("refresh_token", "")
    if not refresh_token:
        return {"error": "No refresh_token in stored session. Please login_algochains first."}

    import httpx
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{_SUPABASE_URL}/auth/v1/token?grant_type=refresh_token",
                headers=_auth_headers(),
                json={"refresh_token": refresh_token},
            )
        data = resp.json()
        if resp.status_code == 200 and data.get("access_token"):
            session.update({
                "access_token": data["access_token"],
                "refresh_token": data.get("refresh_token", refresh_token),
                "expires_at": time.time() + data.get("expires_in", 3600),
            })
            _save_session(session)
            return {"status": "ok", "message": "Session refreshed.", "aal": _aal_level(session)}
        return {
            "error": data.get("msg") or "Refresh failed — please login again.",
            "status_code": resp.status_code,
        }
    except Exception as exc:
        return {"error": f"Refresh request failed: {exc}"}


async def logout_algochains() -> dict[str, Any]:
    """Revoke current session and clear stored credentials."""
    session = _load_session()
    token = session.get("access_token", "")
    _clear_session()
    if not token or not _SUPABASE_URL:
        return {"status": "ok", "message": "Session cleared locally (no active token to revoke)."}

    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"{_SUPABASE_URL}/auth/v1/logout",
                headers=_auth_headers(token),
            )
    except Exception:
        pass
    return {"status": "ok", "message": "Logged out and session cleared."}


# ══════════════════════════════════════════════════════════════════════════════
# MFA TOOLS
# ══════════════════════════════════════════════════════════════════════════════

async def enroll_mfa(factor_type: str = "totp") -> dict[str, Any]:
    """
    Enroll a new MFA factor (TOTP or SMS).
    Returns QR code URI for TOTP (scan with authenticator app) or sends SMS.
    After scanning, call verify_mfa to complete enrollment and activate AAL2.
    """
    err = _need_supabase()
    if err:
        return err

    session = _load_session()
    if not session.get("access_token"):
        return {"error": "Not logged in. Call login_algochains first."}

    import httpx
    try:
        body: dict[str, Any] = {"factor_type": factor_type}
        if factor_type == "totp":
            body["issuer"] = "AlgoChains"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{_SUPABASE_URL}/auth/v1/factors",
                headers=_auth_headers(session["access_token"]),
                json=body,
            )
        data = resp.json()
        if resp.status_code in (200, 201):
            result: dict[str, Any] = {
                "status": "enrollment_started",
                "factor_id": data.get("id"),
                "factor_type": data.get("type", factor_type),
                "next_step": "verify_mfa",
            }
            if factor_type == "totp" and data.get("totp"):
                result["qr_code_uri"] = data["totp"].get("uri", "")
                result["secret"] = data["totp"].get("secret", "")
                result["message"] = (
                    "Scan the QR code URI with your authenticator app "
                    "(Google Authenticator, Authy, etc.). "
                    "Then call verify_mfa(factor_id=..., code=<6-digit-totp>)."
                )
            return result
        return {
            "error": data.get("msg") or data.get("message") or "MFA enrollment failed",
            "status_code": resp.status_code,
            "details": data,
        }
    except Exception as exc:
        return {"error": f"MFA enrollment request failed: {exc}"}


async def challenge_mfa(factor_id: str) -> dict[str, Any]:
    """Create an MFA challenge for login step-up. Required before verify_mfa in login flow."""
    err = _need_supabase()
    if err:
        return err

    session = _load_session()
    if not session.get("access_token"):
        return {"error": "Not logged in. Call login_algochains first."}

    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{_SUPABASE_URL}/auth/v1/factors/{factor_id}/challenge",
                headers=_auth_headers(session["access_token"]),
                json={},
            )
        data = resp.json()
        if resp.status_code in (200, 201):
            return {
                "status": "challenge_created",
                "challenge_id": data.get("id"),
                "expires_at": data.get("expires_at"),
                "next_step": "verify_mfa",
                "message": "Enter your authenticator code and call verify_mfa(factor_id=..., code=..., challenge_id=...).",
            }
        return {
            "error": data.get("msg") or "Challenge creation failed",
            "status_code": resp.status_code,
        }
    except Exception as exc:
        return {"error": f"Challenge request failed: {exc}"}


async def verify_mfa(
    factor_id: str,
    code: str,
    challenge_id: str | None = None,
) -> dict[str, Any]:
    """
    Verify an MFA code. Upgrades session to AAL2 (required for key creation).
    During enrollment: provide factor_id + code (no challenge_id needed).
    During login step-up: provide factor_id + code + challenge_id.
    """
    err = _need_supabase()
    if err:
        return err

    session = _load_session()
    if not session.get("access_token"):
        return {"error": "Not logged in. Call login_algochains first."}

    import httpx
    body: dict[str, Any] = {"factor_id": factor_id, "code": code}
    if challenge_id:
        body["challenge_id"] = challenge_id

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{_SUPABASE_URL}/auth/v1/factors/{factor_id}/verify",
                headers=_auth_headers(session["access_token"]),
                json=body,
            )
        data = resp.json()
        if resp.status_code == 200 and data.get("access_token"):
            session.update({
                "access_token": data["access_token"],
                "refresh_token": data.get("refresh_token", session.get("refresh_token", "")),
                "expires_at": time.time() + data.get("expires_in", 3600),
            })
            _save_session(session)
            aal = _aal_level(session)
            return {
                "status": "ok",
                "message": "MFA verified. Session is now AAL2.",
                "aal": aal,
                "next_step": "create_developer_key" if aal == "aal2" else "challenge_mfa",
            }
        return {
            "error": data.get("msg") or data.get("message") or "MFA verification failed — wrong code?",
            "status_code": resp.status_code,
        }
    except Exception as exc:
        return {"error": f"MFA verification request failed: {exc}"}


async def list_mfa_factors() -> dict[str, Any]:
    """List enrolled MFA factors for the current session."""
    err = _need_supabase()
    if err:
        return err

    session = _load_session()
    if not session.get("access_token"):
        return {"error": "Not logged in. Call login_algochains first."}

    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{_SUPABASE_URL}/auth/v1/factors",
                headers=_auth_headers(session["access_token"]),
            )
        data = resp.json()
        if resp.status_code == 200:
            factors = data if isinstance(data, list) else data.get("factors", [])
            return {
                "status": "ok",
                "factors": [
                    {
                        "id": f.get("id"),
                        # Supabase returns "type"; factor_type is the enrollment body key
                        "type": f.get("type") or f.get("factor_type"),
                        "friendly_name": f.get("friendly_name"),
                        "status": f.get("status"),
                        "created_at": f.get("created_at"),
                        "updated_at": f.get("updated_at"),
                    }
                    for f in factors
                ],
                "count": len(factors),
            }
        return {"error": data.get("msg") or "Failed to list factors", "status_code": resp.status_code}
    except Exception as exc:
        return {"error": f"Factor list request failed: {exc}"}


async def remove_mfa_factor(factor_id: str, owner_token: str = "") -> dict[str, Any]:
    """
    Remove an enrolled MFA factor. Requires owner_token (ORDER_EXEC tier).
    This is a destructive action — removing all factors downgrades session to AAL1.
    """
    _expected = os.environ.get("OWNER_API_TOKEN", "")
    if not _expected or owner_token != _expected:
        return {
            "error": "remove_mfa_factor requires owner_token authorization.",
            "required_secret": "OWNER_API_TOKEN",
        }

    err = _need_supabase()
    if err:
        return err

    session = _load_session()
    if not session.get("access_token"):
        return {"error": "Not logged in."}

    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.delete(
                f"{_SUPABASE_URL}/auth/v1/factors/{factor_id}",
                headers=_auth_headers(session["access_token"]),
            )
        if resp.status_code in (200, 204):
            return {"status": "ok", "message": f"Factor {factor_id} removed."}
        data = resp.json()
        return {"error": data.get("msg") or "Factor removal failed", "status_code": resp.status_code}
    except Exception as exc:
        return {"error": f"Factor removal request failed: {exc}"}


# ══════════════════════════════════════════════════════════════════════════════
# DEVELOPER KEY TOOLS
# ══════════════════════════════════════════════════════════════════════════════

def _check_aal2(session: dict) -> dict | None:
    """Return error if session is not AAL2 (MFA-verified)."""
    aal = _aal_level(session)
    if aal != "aal2":
        return {
            "error": "requires_mfa_challenge",
            "message": (
                "Developer key operations require an AAL2 (MFA-verified) session. "
                "Call enroll_mfa (if not enrolled) or challenge_mfa + verify_mfa to upgrade."
            ),
            "current_aal": aal,
            "required_aal": "aal2",
        }
    return None


async def create_developer_key(
    name: str = "default",
    scopes: list[str] | None = None,
    env: str = "live",
    tier: str = "developer_pro",
) -> dict[str, Any]:
    """
    Mint a new ac_live_* / ac_test_* developer API key.
    Requires an AAL2 (MFA-verified) session.
    The plaintext key is returned ONCE ONLY — store it immediately.
    Uses key_contract.build_insert_payload() to ensure writer parity.
    """
    from algochains_mcp.auth.key_contract import generate_platform_key, build_insert_payload

    err = _need_supabase()
    if err:
        return err

    if not _SUPABASE_SERVICE_KEY:
        return {
            "error": "SUPABASE_SERVICE_KEY not configured",
            "hint": "Add SUPABASE_SERVICE_KEY to .env to enable key creation.",
        }

    session = _load_session()
    if not session.get("access_token"):
        return {"error": "Not logged in. Call login_algochains first."}

    aal_err = _check_aal2(session)
    if aal_err:
        return aal_err

    if env not in ("live", "test"):
        return {"error": "env must be 'live' or 'test'"}

    # Resolve clerk_user_id: prefer Clerk ID from user metadata, fall back to email
    user_meta = session.get("user_meta", {}) or {}
    clerk_user_id = (
        user_meta.get("clerk_user_id")
        or user_meta.get("clerk_id")
        or session.get("email", "")
        or session.get("user_id", "unknown")
    )

    plaintext = generate_platform_key(env)
    payload = build_insert_payload(
        raw_key=plaintext,
        clerk_user_id=clerk_user_id,
        tier=tier,
        label=name,
        override_scopes=scopes,  # validated against tier max inside build_insert_payload
    )
    # Also store Supabase Auth user_id as secondary link
    supabase_uid = session.get("user_id", "")
    if supabase_uid:
        payload["user_id"] = supabase_uid

    import httpx
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{_SUPABASE_URL}/rest/v1/developer_api_keys",
                headers={**_service_headers(), "Prefer": "return=representation"},
                json=payload,
            )
        if resp.status_code in (200, 201):
            row = resp.json()
            row_id = row[0].get("id") if isinstance(row, list) else row.get("id")
            return {
                "status": "ok",
                "key": plaintext,  # SHOWN ONCE ONLY
                "key_id": row_id,
                "key_prefix": payload["key_prefix"],
                "key_hint": payload["key_hint"],
                "name": name,
                "scopes": payload["scopes"],
                "tier": tier,
                "env": env,
                "clerk_user_id": clerk_user_id,
                "warning": "⚠️  Save this key immediately — it will NOT be shown again.",
                "next_step": "test_bridge_connection",
            }
        return {
            "error": "Key creation failed",
            "status_code": resp.status_code,
            "details": resp.text[:200],
        }
    except Exception as exc:
        return {"error": f"Key creation request failed: {exc}"}


async def list_developer_keys() -> dict[str, Any]:
    """List developer keys for the current user (masked — no plaintext)."""
    err = _need_supabase()
    if err:
        return err

    session = _load_session()
    if not session.get("access_token"):
        return {"error": "Not logged in."}

    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{_SUPABASE_URL}/rest/v1/developer_api_keys?select=id,key_prefix,name,scopes,env,last_used_at,revoked_at,created_at",
                headers=_auth_headers(session["access_token"]),
            )
        if resp.status_code == 200:
            keys = resp.json()
            return {
                "status": "ok",
                "keys": [
                    {
                        "id": k.get("id"),
                        "display": _masked_key(k.get("key_prefix", "")) + "***",
                        "name": k.get("name"),
                        "scopes": k.get("scopes", []),
                        "env": k.get("env"),
                        "active": k.get("revoked_at") is None,
                        "last_used_at": k.get("last_used_at"),
                        "created_at": k.get("created_at"),
                    }
                    for k in keys
                ],
                "count": len(keys),
            }
        return {"error": "Failed to list keys", "status_code": resp.status_code}
    except Exception as exc:
        return {"error": f"Key list request failed: {exc}"}


async def rotate_developer_key(key_id: str, name: str | None = None) -> dict[str, Any]:
    """
    Atomically rotate a developer key. Revokes old key and mints a new one.
    Requires AAL2 session. New plaintext returned ONCE ONLY.
    """
    err = _need_supabase()
    if err:
        return err

    if not _SUPABASE_SERVICE_KEY:
        return {"error": "SUPABASE_SERVICE_KEY required for key rotation."}

    session = _load_session()
    if not session.get("access_token"):
        return {"error": "Not logged in."}

    aal_err = _check_aal2(session)
    if aal_err:
        return aal_err

    caller_user_id = session.get("user_id", "")
    import httpx
    try:
        # 1. Fetch existing key metadata — scope to caller's user_id to prevent IDOR
        async with httpx.AsyncClient(timeout=10) as client:
            fetch_resp = await client.get(
                f"{_SUPABASE_URL}/rest/v1/developer_api_keys"
                f"?id=eq.{key_id}&user_id=eq.{caller_user_id}&select=*",
                headers={**_service_headers()},
            )
        rows = fetch_resp.json()
        if not rows:
            # Row not found OR belongs to a different user — return same error to prevent enumeration
            return {"error": f"Key {key_id} not found or access denied."}
        old_key = rows[0]
        env = old_key.get("env", "live")
        scopes = old_key.get("scopes", ["read:market_data"])
        new_name = name or old_key.get("name", "default")

        # 2. Revoke old key FIRST (fail-safe: if mint fails, old key still valid)
        revoke_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.patch(
                f"{_SUPABASE_URL}/rest/v1/developer_api_keys"
                f"?id=eq.{key_id}&user_id=eq.{caller_user_id}",
                headers=_service_headers(),
                json={"revoked_at": revoke_ts},
            )
        if r.status_code not in (200, 204):
            return {"error": f"Could not revoke old key (HTTP {r.status_code}) — rotation aborted."}

        # 3. Mint new key (after revoke — caller must save; if this fails, key was already revoked)
        new_result = await create_developer_key(name=new_name, scopes=scopes, env=env)
        if new_result.get("status") != "ok":
            return {
                "error": "Old key revoked but new key minting failed. Contact support with old_key_id.",
                "old_key_id": key_id,
                "old_key_revoked": True,
                "details": new_result,
            }

        return {
            "status": "ok",
            "old_key_id": key_id,
            "old_key_revoked": True,
            "new_key": new_result["key"],  # SHOWN ONCE ONLY
            "new_key_id": new_result["key_id"],
            "warning": "⚠️  Save the new key immediately — it will NOT be shown again.",
        }
    except Exception as exc:
        return {"error": f"Key rotation failed: {exc}"}


async def revoke_developer_key(key_id: str) -> dict[str, Any]:
    """Soft-delete (revoke) a developer key. Requires AAL2 session."""
    err = _need_supabase()
    if err:
        return err

    if not _SUPABASE_SERVICE_KEY:
        return {"error": "SUPABASE_SERVICE_KEY required."}

    session = _load_session()
    if not session.get("access_token"):
        return {"error": "Not logged in."}

    aal_err = _check_aal2(session)
    if aal_err:
        return aal_err

    caller_user_id = session.get("user_id", "")
    import httpx
    try:
        # Scope PATCH to caller's user_id to prevent IDOR — other users' keys are invisible
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.patch(
                f"{_SUPABASE_URL}/rest/v1/developer_api_keys"
                f"?id=eq.{key_id}&user_id=eq.{caller_user_id}",
                headers={**_service_headers(), "Prefer": "return=minimal"},
                json={"revoked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
            )
        if resp.status_code in (200, 204):
            return {"status": "ok", "message": f"Key {key_id} revoked."}
        return {"error": "Revocation failed or key not found/access denied.", "status_code": resp.status_code}
    except Exception as exc:
        return {"error": f"Revocation request failed: {exc}"}


async def get_developer_key_usage(key_id: str) -> dict[str, Any]:
    """Get usage metadata for a developer key."""
    err = _need_supabase()
    if err:
        return err

    session = _load_session()
    if not session.get("access_token"):
        return {"error": "Not logged in."}

    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{_SUPABASE_URL}/rest/v1/developer_api_keys?id=eq.{key_id}&select=key_prefix,name,scopes,env,last_used_at,revoked_at,created_at",
                headers=_auth_headers(session["access_token"]),
            )
        rows = resp.json()
        if resp.status_code == 200 and rows:
            k = rows[0]
            return {
                "status": "ok",
                "key_id": key_id,
                "display": _masked_key(k.get("key_prefix", "")) + "***",
                "name": k.get("name"),
                "scopes": k.get("scopes"),
                "env": k.get("env"),
                "active": k.get("revoked_at") is None,
                "last_used_at": k.get("last_used_at"),
                "created_at": k.get("created_at"),
            }
        return {"error": "Key not found or access denied."}
    except Exception as exc:
        return {"error": f"Usage fetch failed: {exc}"}


async def test_bridge_connection(api_key: str | None = None) -> dict[str, Any]:
    """
    Test a developer API key against the hosted HTTP bridge.
    Uses stored key or provided api_key parameter.
    """
    key = api_key or os.environ.get("AC_DEV_KEY", "")
    if not key:
        return {
            "error": "No API key provided. Pass api_key=... or set AC_DEV_KEY env var.",
        }

    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{_BRIDGE_URL}/health",
                headers={"X-Api-Key": key},
            )
        if resp.status_code == 200:
            data = resp.json()
            return {
                "status": "ok",
                "bridge": _BRIDGE_URL,
                "auth_mode": data.get("auth_mode"),
                "scopes": data.get("developer_scopes") or data.get("scopes"),
                "server_version": data.get("version"),
                "message": "Bridge connection successful. Your developer key is valid.",
            }
        return {
            "status": "error",
            "bridge": _BRIDGE_URL,
            "http_status": resp.status_code,
            "message": "Bridge auth failed — check key prefix and env (live vs test).",
        }
    except Exception as exc:
        return {"error": f"Bridge connection test failed: {exc}", "bridge": _BRIDGE_URL}
