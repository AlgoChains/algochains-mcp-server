"""Signed developer auth context for HTTP-bridge → server tool dispatch."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from typing import Any

log = logging.getLogger(__name__)

INTERNAL_AUTH_KEYS = ("_developer_scopes", "_clerk_user_id", "_auth_context_sig")


def _signing_secret() -> str:
    return (
        os.getenv("ALGOCHAINS_BRIDGE_API_KEY", "")
        or os.getenv("BRIDGE_API_KEY", "")
        or os.getenv("OWNER_API_TOKEN", "")
        or os.getenv("ALGOCHAINS_BRIDGE_AUTH_SECRET", "")
    )


def _signature(scopes: tuple[str, ...], clerk_user_id: str, secret: str) -> str:
    payload = json.dumps(
        {"scopes": list(scopes), "clerk_user_id": clerk_user_id},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def attach_trusted_developer_context(
    arguments: dict[str, Any],
    *,
    scopes: tuple[str, ...],
    clerk_user_id: str,
) -> dict[str, Any]:
    """Attach HMAC-signed developer scopes for trusted bridge dispatch."""
    args = dict(arguments or {})
    secret = _signing_secret()
    if not secret:
        log.warning("internal_auth_context: no signing secret — developer context omitted")
        return args
    args["_developer_scopes"] = list(scopes)
    args["_clerk_user_id"] = clerk_user_id or ""
    args["_auth_context_sig"] = _signature(scopes, clerk_user_id or "", secret)
    return args


def strip_untrusted_internal_auth(arguments: dict[str, Any]) -> dict[str, Any]:
    """Remove spoofed internal auth keys; restore only when signature validates."""
    args = dict(arguments or {})
    raw_scopes = args.pop("_developer_scopes", None)
    clerk_user_id = str(args.pop("_clerk_user_id", "") or "")
    sig = args.pop("_auth_context_sig", None)

    if raw_scopes is None and not clerk_user_id:
        return args

    scopes = tuple(s for s in (raw_scopes or ()) if isinstance(s, str))
    secret = _signing_secret()
    if not secret or not sig or not scopes:
        if raw_scopes is not None or clerk_user_id:
            log.warning("internal_auth_context: rejected unsigned developer auth context")
        return args

    expected = _signature(scopes, clerk_user_id, secret)
    if not hmac.compare_digest(str(sig), expected):
        log.warning("internal_auth_context: rejected invalid developer auth signature")
        return args

    args["_developer_scopes"] = list(scopes)
    args["_clerk_user_id"] = clerk_user_id
    return args
