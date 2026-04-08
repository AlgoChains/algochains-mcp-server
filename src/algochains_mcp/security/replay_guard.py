"""Replay attack protection for signed MCP requests.

Addresses SAFE-MCP technique T051 and GUARDRAIL LAP pattern.
Every signed request (Tradovate WebSocket auth, Kalshi RSA-PSS, HMAC signal propagation)
must include X-Timestamp + X-Nonce headers. This middleware rejects:
  - Requests with timestamp older than MAX_AGE_SECONDS (default 300s = 5 min)
  - Requests whose nonce was already seen within the TTL window

Storage: in-memory dict with periodic cleanup (suitable for single-process MCP server).
For multi-process: replace with Redis SETNX or SQLite with WAL.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time
from threading import Lock

logger = logging.getLogger("algochains_mcp.security.replay_guard")

MAX_AGE_SECONDS = int(os.environ.get("REPLAY_GUARD_MAX_AGE", "300"))
NONCE_TTL_SECONDS = MAX_AGE_SECONDS + 60  # Keep nonces slightly longer than max age


class ReplayGuard:
    """Thread-safe nonce + timestamp replay protection.

    Usage:
        guard = ReplayGuard()

        # When receiving a request:
        result = guard.validate(timestamp_str, nonce_str)
        if not result["valid"]:
            raise SecurityError(result["reason"])

        # When sending a request:
        ts, nonce = guard.generate_headers()
    """

    def __init__(self, max_age_seconds: int = MAX_AGE_SECONDS, nonce_ttl: int = NONCE_TTL_SECONDS):
        self._max_age = max_age_seconds
        self._nonce_ttl = nonce_ttl
        self._seen_nonces: dict[str, float] = {}  # nonce -> seen_at_unix
        self._lock = Lock()

    def generate_headers(self) -> tuple[str, str]:
        """Generate (timestamp_str, nonce_str) for outgoing signed requests."""
        ts = str(int(time.time()))
        nonce = secrets.token_hex(16)
        return ts, nonce

    def validate(self, timestamp_str: str, nonce: str) -> dict:
        """Validate a request's timestamp and nonce.

        Returns:
            dict with keys: valid (bool), reason (str if invalid)
        """
        # Parse timestamp
        try:
            ts = float(timestamp_str)
        except (TypeError, ValueError):
            return {"valid": False, "reason": "invalid_timestamp_format"}

        now = time.time()
        age = now - ts

        # Reject stale requests
        if age > self._max_age:
            return {"valid": False, "reason": f"request_expired: age={age:.0f}s > max={self._max_age}s"}

        # Reject future requests (clock skew > 30s)
        if ts > now + 30:
            return {"valid": False, "reason": f"request_from_future: skew={ts-now:.0f}s"}

        # Nonce uniqueness check
        with self._lock:
            self._cleanup_expired()
            if nonce in self._seen_nonces:
                return {"valid": False, "reason": "replay_detected: nonce_already_seen"}
            self._seen_nonces[nonce] = now

        return {"valid": True, "age_seconds": round(age, 2)}

    def _cleanup_expired(self) -> None:
        """Remove nonces older than TTL (called under lock)."""
        cutoff = time.time() - self._nonce_ttl
        expired = [n for n, seen_at in self._seen_nonces.items() if seen_at < cutoff]
        for n in expired:
            del self._seen_nonces[n]

    @property
    def nonce_count(self) -> int:
        with self._lock:
            return len(self._seen_nonces)


def generate_hmac_signature(payload: str, secret: str, algorithm: str = "sha256") -> dict:
    """Generate HMAC signature for request signing.

    Returns headers dict ready to include in requests.
    """
    ts, nonce = _GLOBAL_GUARD.generate_headers()
    message = f"{ts}.{nonce}.{payload}"
    sig = hmac.new(
        secret.encode(),
        message.encode(),
        digestmod=getattr(hashlib, algorithm)
    ).hexdigest()

    return {
        "X-Timestamp": ts,
        "X-Nonce": nonce,
        "X-Signature": f"hmac-{algorithm}={sig}",
    }


def verify_hmac_signature(
    payload: str,
    secret: str,
    timestamp: str,
    nonce: str,
    signature: str,
    algorithm: str = "sha256",
) -> dict:
    """Verify HMAC signature + replay protection in one call."""
    # First check replay
    replay_result = _GLOBAL_GUARD.validate(timestamp, nonce)
    if not replay_result["valid"]:
        return {"valid": False, "reason": replay_result["reason"]}

    # Then verify signature
    message = f"{timestamp}.{nonce}.{payload}"
    expected = hmac.new(
        secret.encode(),
        message.encode(),
        digestmod=getattr(hashlib, algorithm)
    ).hexdigest()

    # Extract raw sig from "hmac-sha256=<hex>" format
    raw_sig = signature.split("=", 1)[-1] if "=" in signature else signature

    if not hmac.compare_digest(expected, raw_sig):
        return {"valid": False, "reason": "signature_mismatch"}

    return {"valid": True, "age_seconds": replay_result.get("age_seconds")}


# Singleton for module-level use
_GLOBAL_GUARD = ReplayGuard()
