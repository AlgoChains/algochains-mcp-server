"""Daemon callback credential — separate from subscriber read/report keys."""
from __future__ import annotations

import hmac
import os

DAEMON_KEY_PREFIX = "sub_daemon_"


def is_daemon_callback_key(key: str | None) -> bool:
    if not key:
        return False
    return key.startswith(DAEMON_KEY_PREFIX)


def verify_daemon_callback_key(provided: str | None) -> bool:
    """True when *provided* matches ALGOCHAINS_DAEMON_CALLBACK_TOKEN."""
    expected = os.getenv("ALGOCHAINS_DAEMON_CALLBACK_TOKEN", "")
    if not expected or not provided:
        return False
    if provided.startswith(DAEMON_KEY_PREFIX):
        provided = provided[len(DAEMON_KEY_PREFIX) :]
    return hmac.compare_digest(provided, expected)
