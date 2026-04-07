"""
oauth_manager.py — Generic OAuth 2.0 Manager for Broker Integrations
=====================================================================

Handles the full OAuth 2.0 PKCE flow for broker connections:
  1. generate_auth_url()  — creates authorization URL + PKCE state
  2. exchange_code()       — exchanges auth code for tokens
  3. refresh_token()       — renews access tokens
  4. get_token()           — returns valid token, auto-refreshing if needed
  5. revoke_token()        — disconnects broker

Tokens are stored in Supabase (algochains_oauth_tokens) or locally.

Supported brokers:
  - schwab     (Charles Schwab / TD Ameritrade successor)
  - alpaca     (Alpaca Markets OAuth)
  - tradovate  (Tradovate OAuth)
  - oanda      (OANDA OAuth)
  - ibkr       (Interactive Brokers — uses separate IBKR Portal)

No mock tokens. No synthetic auth flows. Every exchange hits the real broker OAuth endpoint.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

logger = logging.getLogger("algochains_mcp.brokers.oauth")

# ── Config ────────────────────────────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
_STATE_DIR = Path(os.getenv("ALGOCHAINS_STATE_DIR", "state"))
_OAUTH_TOKENS_FILE = _STATE_DIR / "oauth_tokens.json"
_OAUTH_STATES_FILE = _STATE_DIR / "oauth_states.json"
_OAUTH_TABLE = "algochains_oauth_tokens"
_TIMEOUT = httpx.Timeout(20.0, connect=5.0)


# ── Broker OAuth config registry ─────────────────────────────────────────────

@dataclass
class BrokerOAuthConfig:
    broker: str
    client_id_env: str
    client_secret_env: str
    auth_url: str
    token_url: str
    revoke_url: str
    scopes: list[str]
    redirect_uri_env: str = "ALGOCHAINS_OAUTH_REDIRECT_URI"
    pkce_required: bool = True
    token_lifetime_seconds: int = 1800  # default 30min


BROKER_OAUTH_CONFIGS: dict[str, BrokerOAuthConfig] = {
    "schwab": BrokerOAuthConfig(
        broker="schwab",
        client_id_env="SCHWAB_CLIENT_ID",
        client_secret_env="SCHWAB_CLIENT_SECRET",
        auth_url="https://api.schwabapi.com/v1/oauth/authorize",
        token_url="https://api.schwabapi.com/v1/oauth/token",
        revoke_url="https://api.schwabapi.com/v1/oauth/revoke",
        scopes=["PlaceOrders", "Accounts", "Trading"],
        pkce_required=False,  # Schwab uses basic auth for client credentials
        token_lifetime_seconds=1800,
    ),
    "alpaca": BrokerOAuthConfig(
        broker="alpaca",
        client_id_env="ALPACA_OAUTH_CLIENT_ID",
        client_secret_env="ALPACA_OAUTH_CLIENT_SECRET",
        auth_url="https://app.alpaca.markets/oauth/authorize",
        token_url="https://api.alpaca.markets/oauth/token",
        revoke_url="https://api.alpaca.markets/oauth/revoke",
        scopes=["account:write", "trading", "data"],
        token_lifetime_seconds=86400,
    ),
    "oanda": BrokerOAuthConfig(
        broker="oanda",
        client_id_env="OANDA_OAUTH_CLIENT_ID",
        client_secret_env="OANDA_OAUTH_CLIENT_SECRET",
        auth_url="https://www.oanda.com/oauth2/authorize",
        token_url="https://www.oanda.com/oauth2/token",
        revoke_url="https://www.oanda.com/oauth2/revoke",
        scopes=["read_account", "trade"],
        token_lifetime_seconds=3600,
    ),
    "tradovate": BrokerOAuthConfig(
        broker="tradovate",
        client_id_env="TRADOVATE_CID",
        client_secret_env="TRADOVATE_SECRET",
        auth_url="https://trader.tradovate.com/oauth/authorize",
        token_url="https://live.tradovateapi.com/v1/auth/oauthtoken",
        revoke_url="",
        scopes=["trading"],
        pkce_required=False,
        token_lifetime_seconds=4800,
    ),
}


# ── PKCE helpers ─────────────────────────────────────────────────────────────

def _generate_pkce() -> tuple[str, str, str]:
    """Returns (code_verifier, code_challenge, state)."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    return verifier, challenge, state


# ── Local state storage ───────────────────────────────────────────────────────

def _load_tokens() -> dict[str, dict]:
    if _OAUTH_TOKENS_FILE.exists():
        try:
            return json.loads(_OAUTH_TOKENS_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save_tokens(tokens: dict[str, dict]) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _OAUTH_TOKENS_FILE.write_text(json.dumps(tokens, indent=2, default=str))


def _load_states() -> dict[str, dict]:
    if _OAUTH_STATES_FILE.exists():
        try:
            return json.loads(_OAUTH_STATES_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save_states(states: dict[str, dict]) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _OAUTH_STATES_FILE.write_text(json.dumps(states, indent=2, default=str))


# ── Supabase helpers ──────────────────────────────────────────────────────────

def _sb_available() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SERVICE_KEY)


def _sb_headers() -> dict:
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


async def _sb_upsert_token(broker: str, user_id: str, token_data: dict) -> None:
    if not _sb_available():
        return
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            await client.post(
                f"{SUPABASE_URL.rstrip('/')}/rest/v1/{_OAUTH_TABLE}",
                headers={**_sb_headers(), "Prefer": "resolution=merge-duplicates,return=representation"},
                json={"broker": broker, "user_id": user_id, **token_data},
            )
    except Exception as e:
        logger.error("Supabase token upsert failed: %s", e)


async def _sb_get_token(broker: str, user_id: str) -> Optional[dict]:
    if not _sb_available():
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{SUPABASE_URL.rstrip('/')}/rest/v1/{_OAUTH_TABLE}"
                f"?broker=eq.{broker}&user_id=eq.{user_id}&limit=1",
                headers=_sb_headers(),
            )
            if resp.status_code == 200:
                rows = resp.json()
                return rows[0] if rows else None
    except Exception as e:
        logger.error("Supabase token get failed: %s", e)
    return None


# ── Core OAuth functions ──────────────────────────────────────────────────────

async def generate_auth_url(
    broker: str,
    user_id: str,
    redirect_uri: Optional[str] = None,
) -> dict[str, Any]:
    """
    Step 1: Generate the OAuth authorization URL for the user to visit.

    The user opens this URL in their browser, logs into their broker,
    and is redirected back to redirect_uri with a ?code= parameter.

    Returns:
        auth_url: The URL to redirect the user to
        state:    CSRF state token (must be verified on callback)
        expires_at: When this auth URL expires (10 minutes)
    """
    cfg = BROKER_OAUTH_CONFIGS.get(broker)
    if not cfg:
        return {
            "success": False,
            "error": f"Broker '{broker}' not supported. Available: {list(BROKER_OAUTH_CONFIGS.keys())}",
        }

    client_id = os.getenv(cfg.client_id_env, "")
    if not client_id:
        return {
            "success": False,
            "error": f"{cfg.client_id_env} not set. Get your app credentials from the {broker} developer portal.",
        }

    uri = redirect_uri or os.getenv(cfg.redirect_uri_env, f"https://algochains.ai/oauth/callback/{broker}")

    verifier, challenge, state = _generate_pkce()

    params: dict[str, str] = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": uri,
        "scope": " ".join(cfg.scopes),
        "state": state,
    }
    if cfg.pkce_required:
        params["code_challenge"] = challenge
        params["code_challenge_method"] = "S256"

    auth_url = f"{cfg.auth_url}?{urllib.parse.urlencode(params)}"
    expires_at = time.time() + 600  # 10 minute window

    # Store PKCE state for verification on callback
    states = _load_states()
    states[state] = {
        "broker": broker,
        "user_id": user_id,
        "verifier": verifier,
        "redirect_uri": uri,
        "expires_at": expires_at,
    }
    _save_states(states)

    return {
        "success": True,
        "broker": broker,
        "auth_url": auth_url,
        "state": state,
        "expires_at": datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat(),
        "instructions": f"Redirect the user to auth_url. After they authorize, call exchange_code() with the returned code and state.",
    }


async def exchange_code(
    state: str,
    code: str,
    redirect_uri: Optional[str] = None,
) -> dict[str, Any]:
    """
    Step 2: Exchange the authorization code for access/refresh tokens.

    Call this when your OAuth callback endpoint receives the ?code= parameter.

    Args:
        state: The state value returned by generate_auth_url()
        code:  The authorization code from the broker callback
        redirect_uri: Must match the URI used in generate_auth_url()

    Returns:
        access_token, refresh_token, expires_at, broker, user_id
    """
    states = _load_states()
    state_data = states.get(state)
    if not state_data:
        return {"success": False, "error": "Invalid or expired state. Call generate_auth_url() again."}
    if time.time() > state_data.get("expires_at", 0):
        del states[state]
        _save_states(states)
        return {"success": False, "error": "Authorization state expired (10min window). Start over."}

    broker = state_data["broker"]
    user_id = state_data["user_id"]
    verifier = state_data.get("verifier", "")
    uri = redirect_uri or state_data.get("redirect_uri", "")

    cfg = BROKER_OAUTH_CONFIGS[broker]
    client_id = os.getenv(cfg.client_id_env, "")
    client_secret = os.getenv(cfg.client_secret_env, "")

    payload: dict[str, str] = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": uri,
        "client_id": client_id,
    }
    if cfg.pkce_required and verifier:
        payload["code_verifier"] = verifier

    # Schwab and some brokers use HTTP Basic Auth for client credentials
    auth = None
    if client_secret:
        auth = (client_id, client_secret)

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                cfg.token_url,
                data=payload,
                auth=auth,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if resp.status_code not in (200, 201):
                return {
                    "success": False,
                    "error": f"Token exchange failed ({resp.status_code}): {resp.text[:300]}",
                }
            token_data = resp.json()
    except Exception as e:
        return {"success": False, "error": f"Token exchange request failed: {e}"}

    now = time.time()
    expires_in = token_data.get("expires_in", cfg.token_lifetime_seconds)
    record = {
        "broker": broker,
        "user_id": user_id,
        "access_token": token_data.get("access_token", ""),
        "refresh_token": token_data.get("refresh_token", ""),
        "token_type": token_data.get("token_type", "Bearer"),
        "scope": token_data.get("scope", " ".join(cfg.scopes)),
        "issued_at": now,
        "expires_at": now + expires_in,
        "connected_at": datetime.now(timezone.utc).isoformat(),
    }

    # Persist
    tokens = _load_tokens()
    tokens[f"{broker}:{user_id}"] = record
    _save_tokens(tokens)
    await _sb_upsert_token(broker, user_id, record)

    # Clean up state
    del states[state]
    _save_states(states)

    logger.info("OAuth token exchanged for broker=%s user=%s expires_in=%ss", broker, user_id, expires_in)

    return {
        "success": True,
        "broker": broker,
        "user_id": user_id,
        "expires_at": datetime.fromtimestamp(record["expires_at"], tz=timezone.utc).isoformat(),
        "scope": record["scope"],
        "connected": True,
    }


async def refresh_token(broker: str, user_id: str) -> dict[str, Any]:
    """Refresh an expired OAuth access token using the stored refresh token."""
    cfg = BROKER_OAUTH_CONFIGS.get(broker)
    if not cfg:
        return {"success": False, "error": f"Broker '{broker}' not supported."}

    # Get stored token
    tokens = _load_tokens()
    record = tokens.get(f"{broker}:{user_id}")
    if not record:
        record = await _sb_get_token(broker, user_id)
    if not record or not record.get("refresh_token"):
        return {
            "success": False,
            "error": f"No stored token for {broker}/{user_id}. User must re-authorize.",
            "action": "call generate_auth_url() to restart OAuth flow",
        }

    client_id = os.getenv(cfg.client_id_env, "")
    client_secret = os.getenv(cfg.client_secret_env, "")
    auth = (client_id, client_secret) if client_secret else None

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                cfg.token_url,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": record["refresh_token"],
                    "client_id": client_id,
                },
                auth=auth,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if resp.status_code not in (200, 201):
                return {
                    "success": False,
                    "error": f"Token refresh failed ({resp.status_code}). User may need to re-authorize.",
                }
            token_data = resp.json()
    except Exception as e:
        return {"success": False, "error": f"Token refresh request failed: {e}"}

    now = time.time()
    expires_in = token_data.get("expires_in", cfg.token_lifetime_seconds)
    record.update({
        "access_token": token_data.get("access_token", record["access_token"]),
        "refresh_token": token_data.get("refresh_token", record["refresh_token"]),
        "issued_at": now,
        "expires_at": now + expires_in,
    })

    tokens[f"{broker}:{user_id}"] = record
    _save_tokens(tokens)
    await _sb_upsert_token(broker, user_id, record)

    return {
        "success": True,
        "broker": broker,
        "expires_at": datetime.fromtimestamp(record["expires_at"], tz=timezone.utc).isoformat(),
    }


async def get_token(broker: str, user_id: str, auto_refresh: bool = True) -> dict[str, Any]:
    """
    Get the current access token for a broker, auto-refreshing if expired.

    Returns the token info or an error if not connected / refresh failed.
    """
    tokens = _load_tokens()
    record = tokens.get(f"{broker}:{user_id}")
    if not record:
        record = await _sb_get_token(broker, user_id)
    if not record:
        return {
            "success": False,
            "connected": False,
            "error": f"Not connected to {broker}. Call generate_auth_url() to connect.",
        }

    # Check expiry (with 60s buffer)
    if time.time() >= record["expires_at"] - 60:
        if auto_refresh and record.get("refresh_token"):
            refresh_result = await refresh_token(broker, user_id)
            if not refresh_result["success"]:
                return {**refresh_result, "connected": False}
            # Re-load after refresh
            tokens = _load_tokens()
            record = tokens.get(f"{broker}:{user_id}", record)
        else:
            return {
                "success": False,
                "connected": False,
                "error": f"{broker} token expired. Re-authorization required.",
            }

    remaining = int(record["expires_at"] - time.time())
    return {
        "success": True,
        "connected": True,
        "broker": broker,
        "access_token": record["access_token"],
        "token_type": record.get("token_type", "Bearer"),
        "expires_in_seconds": remaining,
        "scope": record.get("scope", ""),
    }


async def revoke_token(broker: str, user_id: str) -> dict[str, Any]:
    """Revoke OAuth access and remove stored tokens."""
    cfg = BROKER_OAUTH_CONFIGS.get(broker)

    tokens = _load_tokens()
    record = tokens.pop(f"{broker}:{user_id}", None)
    _save_tokens(tokens)

    # Try broker revoke endpoint
    if record and cfg and cfg.revoke_url and record.get("access_token"):
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                await client.post(
                    cfg.revoke_url,
                    data={"token": record["access_token"]},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except Exception as e:
            logger.warning("Broker revoke request failed (token already removed locally): %s", e)

    # Remove from Supabase
    if _sb_available():
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                await client.delete(
                    f"{SUPABASE_URL.rstrip('/')}/rest/v1/{_OAUTH_TABLE}"
                    f"?broker=eq.{broker}&user_id=eq.{user_id}",
                    headers=_sb_headers(),
                )
        except Exception as e:
            logger.error("Supabase token delete failed: %s", e)

    return {"success": True, "broker": broker, "user_id": user_id, "connected": False}


async def get_connected_brokers(user_id: str) -> dict[str, Any]:
    """Return all brokers the user has connected via OAuth."""
    tokens = _load_tokens()
    connected = []
    for key, record in tokens.items():
        if record.get("user_id") == user_id or key.endswith(f":{user_id}"):
            broker = record.get("broker", key.split(":")[0])
            remaining = max(0, int(record.get("expires_at", 0) - time.time()))
            connected.append({
                "broker": broker,
                "connected_at": record.get("connected_at", ""),
                "token_expires_in_seconds": remaining,
                "token_valid": remaining > 0,
                "scope": record.get("scope", ""),
            })

    return {
        "success": True,
        "user_id": user_id,
        "connected_brokers": connected,
        "count": len(connected),
        "available_brokers": list(BROKER_OAUTH_CONFIGS.keys()),
    }
