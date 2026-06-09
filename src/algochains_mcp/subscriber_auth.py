"""
subscriber_auth.py — Subscriber API key resolution for the MCP HTTP bridge.

Looks up an incoming `X-Api-Key: sub_live_…` against `subscriber_api_keys`
in Supabase via the `resolve_subscriber_api_key()` SECURITY DEFINER function
(see migration 20260420_subscriber_copytrade.sql). Caches resolved keys for
60 seconds to avoid hammering the database on each tool call.

Usage from the bridge:

    from algochains_mcp.subscriber_auth import resolve_subscriber_key

    sub = resolve_subscriber_key(provided_key)
    if sub:
        # sub.subscriber_id, sub.scopes available
        ...

NEVER call this with the owner's BRIDGE_API_KEY — only after the bridge has
confirmed the key starts with `sub_` (the subscriber prefix).
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Allow-list of legal subscriber-key prefixes. Production uses sub_live_*;
# sub_test_* is reserved for the dry-run / sandbox flow.
SUBSCRIBER_KEY_PREFIXES = ("sub_live_", "sub_test_")

# Default scope set granted to a subscriber key when the row's `scopes`
# column is NULL or empty. Mirrors the migration default.
DEFAULT_SUBSCRIBER_SCOPES = (
    "signal_stream",
    "my_pnl",
    "my_fills",
    "my_assignments",
    "heartbeat",
    "report_fill",
)

# Cache: key_hash -> (resolved_at, ResolvedSubscriber)
_CACHE: dict[str, tuple[float, "ResolvedSubscriber"]] = {}
_CACHE_TTL_SEC = 60.0
_CACHE_LOCK = threading.Lock()
_NEGATIVE_TTL_SEC = 5.0  # don't re-query a bad key for 5s


@dataclass(frozen=True)
class ResolvedSubscriber:
    subscriber_id: str
    scopes: tuple[str, ...]


def is_subscriber_key(key: str | None) -> bool:
    """True iff the key starts with a known subscriber prefix."""
    if not key:
        return False
    return any(key.startswith(p) for p in SUBSCRIBER_KEY_PREFIXES)


def hash_subscriber_key(key: str) -> str:
    """SHA-256 hex digest used as the lookup column in subscriber_api_keys."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _service_client():
    """Return the singleton Supabase service-role client, or None."""
    try:
        from .marketplace.supabase_tools import _get_sb_client
    except Exception as exc:  # pragma: no cover - import path safety
        log.warning("supabase_tools unavailable: %s", exc)
        return None
    return _get_sb_client(use_service_role=True)


def resolve_subscriber_key(raw_key: str | None) -> ResolvedSubscriber | None:
    """
    Resolve a raw subscriber API key into (subscriber_id, scopes).

    Returns None if:
      - the key is empty / not a subscriber-shaped key
      - Supabase is not reachable / not configured
      - the key is unknown or revoked

    The plaintext key never leaves this process; we hash before any RPC.
    """
    if not is_subscriber_key(raw_key):
        return None

    key_hash = hash_subscriber_key(raw_key)
    now = time.monotonic()

    with _CACHE_LOCK:
        cached = _CACHE.get(key_hash)
        if cached and now - cached[0] < _CACHE_TTL_SEC:
            # Note: a None resolved is stored as a fake entry to negative-cache;
            # we do that by storing ResolvedSubscriber("", ()) with shorter TTL.
            resolved = cached[1]
            if not resolved.subscriber_id:
                if now - cached[0] < _NEGATIVE_TTL_SEC:
                    return None
            else:
                return resolved

    sb = _service_client()
    if sb is None:
        log.warning("subscriber key resolution skipped — Supabase service client unavailable")
        return None

    try:
        resp = sb.rpc("resolve_subscriber_api_key", {"p_key_hash": key_hash}).execute()
        rows = getattr(resp, "data", None) or []
    except Exception as exc:
        log.warning("resolve_subscriber_api_key RPC failed: %s", exc)
        return None

    if not rows:
        with _CACHE_LOCK:
            _CACHE[key_hash] = (now, ResolvedSubscriber(subscriber_id="", scopes=()))
        return None

    row = rows[0]
    raw_scopes = row.get("scopes") or []
    scopes = tuple(s for s in raw_scopes if isinstance(s, str)) or DEFAULT_SUBSCRIBER_SCOPES
    # BUG-19 FIX: str(None) produces the literal string "None", creating a
    # subscriber_id="None" that can match across unrelated rows in authZ checks.
    # Guard explicitly: treat None/falsy subscriber_id as an auth failure.
    _raw_sid = row.get("subscriber_id")
    if not _raw_sid:
        log.warning("subscriber_auth: row has null/empty subscriber_id — treating as unauthenticated")
        with _CACHE_LOCK:
            _CACHE[key_hash] = (now, ResolvedSubscriber(subscriber_id="", scopes=()))
        return None
    resolved = ResolvedSubscriber(
        subscriber_id=str(_raw_sid),
        scopes=scopes,
    )
    with _CACHE_LOCK:
        _CACHE[key_hash] = (now, resolved)
    return resolved


def invalidate_cache() -> None:
    """Drop the entire resolution cache (used by tests / key rotation paths)."""
    with _CACHE_LOCK:
        _CACHE.clear()
