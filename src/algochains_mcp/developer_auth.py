"""
developer_auth.py — Developer API key resolution for the MCP HTTP bridge.

Resolves incoming developer keys (ac_live_… / ac_test_… prefixes) against the
`developer_api_keys` table in Supabase via the
`resolve_developer_api_key()` SECURITY DEFINER function (20260521_developer_api_keys.sql).

Key design choices (mirrors subscriber_auth.py):
  - Plaintext key never stored; SHA-256 hash used for lookup.
  - 60s positive cache, 5s negative cache per key hash.
  - Fails closed: any Supabase unavailability returns None.
  - Auth is key-only — no email or scope elevation from request body.

Usage in http_bridge._resolve_auth:

    from algochains_mcp.developer_auth import is_developer_key, resolve_developer_key

    if is_developer_key(provided_key):
        dev = resolve_developer_key(provided_key)
        if dev:
            # dev.clerk_user_id, dev.scopes, dev.env available
            ...
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Developer key prefixes issued by the Django web app.
DEVELOPER_KEY_PREFIXES = ("ac_live_", "ac_test_")

# Default scope set when the DB row has no scopes (safe minimum).
DEFAULT_DEVELOPER_SCOPES = ("read:market_data", "read:signals")

# Cache: key_hash → (resolved_at_monotonic, ResolvedDeveloper)
_CACHE: dict[str, tuple[float, "ResolvedDeveloper"]] = {}
_CACHE_TTL_SEC = 60.0
_NEGATIVE_TTL_SEC = 5.0
_CACHE_LOCK = threading.Lock()


@dataclass(frozen=True)
class ResolvedDeveloper:
    clerk_user_id: str
    scopes: tuple[str, ...]
    env: str  # 'live' | 'test'


def is_developer_key(key: str | None) -> bool:
    """True iff the key starts with a known developer prefix."""
    if not key:
        return False
    return any(key.startswith(p) for p in DEVELOPER_KEY_PREFIXES)


def hash_developer_key(key: str) -> str:
    """SHA-256 hex digest used for Supabase lookup."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _service_client():
    """Return the singleton Supabase service-role client, or None."""
    try:
        from .marketplace.supabase_tools import _get_sb_client
    except Exception as exc:
        log.warning("developer_auth: supabase_tools unavailable — %s", exc)
        return None
    return _get_sb_client(use_service_role=True)


def resolve_developer_key(raw_key: str | None) -> ResolvedDeveloper | None:
    """
    Resolve a raw developer API key (ac_live_* / ac_test_*) to (clerk_user_id, scopes, env).

    Returns None if:
      - Key is empty / not a developer-shaped key.
      - Supabase is unreachable or not configured.
      - Key is unknown, inactive, or revoked.

    The plaintext key never leaves this process; we hash before the RPC call.
    """
    if not is_developer_key(raw_key):
        return None

    key_hash = hash_developer_key(raw_key)
    now = time.monotonic()

    with _CACHE_LOCK:
        cached = _CACHE.get(key_hash)
        if cached is not None:
            ts, resolved = cached
            if not resolved.clerk_user_id:
                # Negative cache entry
                if now - ts < _NEGATIVE_TTL_SEC:
                    return None
            elif now - ts < _CACHE_TTL_SEC:
                return resolved

    sb = _service_client()
    if sb is None:
        log.warning("developer_auth: Supabase service client unavailable — failing closed")
        return None

    try:
        resp = sb.rpc("resolve_developer_api_key", {"p_key_hash": key_hash}).execute()
        rows = getattr(resp, "data", None) or []
    except Exception as exc:
        log.warning("developer_auth: resolve_developer_api_key RPC failed — %s", exc)
        return None

    if not rows:
        with _CACHE_LOCK:
            _CACHE[key_hash] = (now, ResolvedDeveloper(clerk_user_id="", scopes=(), env="live"))
        return None

    row = rows[0]
    raw_scopes = row.get("scopes") or []
    scopes = tuple(s for s in raw_scopes if isinstance(s, str)) or DEFAULT_DEVELOPER_SCOPES

    clerk_user_id = row.get("clerk_user_id")
    if not clerk_user_id:
        log.warning("developer_auth: row returned null clerk_user_id — failing closed")
        with _CACHE_LOCK:
            _CACHE[key_hash] = (now, ResolvedDeveloper(clerk_user_id="", scopes=(), env="live"))
        return None

    env = row.get("env", "live")
    if env not in ("live", "test"):
        env = "live"

    resolved = ResolvedDeveloper(
        clerk_user_id=str(clerk_user_id),
        scopes=scopes,
        env=env,
    )
    with _CACHE_LOCK:
        _CACHE[key_hash] = (now, resolved)
    return resolved


def invalidate_cache() -> None:
    """Drop the entire resolution cache (used by tests and key rotation paths)."""
    with _CACHE_LOCK:
        _CACHE.clear()
