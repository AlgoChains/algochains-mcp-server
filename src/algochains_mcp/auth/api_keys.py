"""
API Key validation and scope enforcement for AlgoChains MCP Server.

Three key types:
  - ALGOCHAINS_API_KEY:     Read marketplace, subscribe, deploy (all users)
  - LISTING_API_KEY:        Create/update listings (developers only)
  - METRICS_INGEST_API_KEY: Push live metrics (developers only)
"""
from __future__ import annotations

import hashlib
import logging
from enum import Enum

logger = logging.getLogger("algochains_mcp.auth.api_keys")


class KeyScope(str, Enum):
    READ = "read"
    SUBSCRIBE = "subscribe"
    DEPLOY = "deploy"
    PUBLISH = "publish"
    METRICS = "metrics"
    ADMIN = "admin"


# Scope mapping per key type
KEY_SCOPES: dict[str, list[KeyScope]] = {
    "ALGOCHAINS_API_KEY": [KeyScope.READ, KeyScope.SUBSCRIBE, KeyScope.DEPLOY],
    "LISTING_API_KEY": [KeyScope.READ, KeyScope.PUBLISH],
    "METRICS_INGEST_API_KEY": [KeyScope.METRICS],
}


def validate_key_format(key: str) -> bool:
    """Check that an API key has valid format (non-empty, reasonable length)."""
    if not key or not isinstance(key, str):
        return False
    stripped = key.strip()
    return 16 <= len(stripped) <= 256


def hash_key(key: str) -> str:
    """SHA-256 hash of an API key for safe logging/storage."""
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def get_scopes_for_key(key_type: str) -> list[KeyScope]:
    """Return the allowed scopes for a given key type."""
    return KEY_SCOPES.get(key_type, [])


def check_scope(required: KeyScope, available: list[KeyScope]) -> bool:
    """Check if a required scope is available."""
    if KeyScope.ADMIN in available:
        return True
    return required in available


class APIKeyValidator:
    """Validates API keys and enforces scope restrictions."""

    def __init__(self):
        self._keys: dict[str, dict] = {}

    def register_key(self, key: str, key_type: str, user_id: str = "") -> None:
        """Register a key with its type and owner."""
        if not validate_key_format(key):
            logger.warning("Invalid key format for %s", key_type)
            return
        self._keys[hash_key(key)] = {
            "type": key_type,
            "user_id": user_id,
            "scopes": get_scopes_for_key(key_type),
        }
        logger.info("Registered %s key (%s...)", key_type, hash_key(key))

    def authorize(self, key: str, required_scope: KeyScope) -> bool:
        """Check if a key is authorized for a specific scope."""
        hashed = hash_key(key)
        key_info = self._keys.get(hashed)
        if not key_info:
            logger.warning("Unknown API key: %s...", hashed)
            return False
        return check_scope(required_scope, key_info["scopes"])
