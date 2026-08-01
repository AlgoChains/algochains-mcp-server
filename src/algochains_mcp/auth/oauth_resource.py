"""
OAuth 2.1 Resource-Server token validation (MCP authorization spec 2025-06-18).

This server is an OAuth 2.0 **Resource Server**, not an Authorization Server.
The AS (/authorize, /token, PKCE, DCR) is delegated to an external IdP
(Supabase Auth / WorkOS / Auth0). Here we only **validate** the bearer access
token presented on the /mcp endpoint and map it to an identity + tenant.

Validation performed (all mandatory per RFC 8414/9728/8707 + MCP spec):
  1. Signature against the AS JWKS (RS256/ES256).
  2. iss == configured issuer.
  3. aud == this server's canonical resource URI (anti-confused-deputy).
  4. exp / nbf valid.
  5. required scope(s) present.

Identity mapping:
  - claims["sub"]                      -> stable user/subscriber id
  - claims["app_metadata"]["tenant_id"] (immutable, server-set) -> tenant id
    (NEVER user_metadata — that is user-editable; OWASP BOLA footgun).

Configuration (env):
  ALGOCHAINS_OAUTH_ISSUER     issuer URL (default https://auth.algochains.ai)
  ALGOCHAINS_MCP_RESOURCE     canonical resource URI == expected aud
  ALGOCHAINS_OAUTH_JWKS_URI   JWKS endpoint (default: <issuer>/.well-known/jwks.json)
  ALGOCHAINS_OAUTH_REQUIRED_SCOPE  space-separated required scopes (default "")

If no issuer/JWKS is configured, OAuth validation is OFF and this module's
validate() returns None (the caller falls back to the static-secret path).
Fail closed: any validation error returns None.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass

log = logging.getLogger("algochains.oauth_resource")

# Lazy, cached JWKS client (PyJWKClient caches keys internally + refreshes).
_JWKS_LOCK = threading.Lock()
_JWKS_CLIENT = None
_JWKS_URI_CACHED: str | None = None


@dataclass(frozen=True)
class OAuthPrincipal:
    """Resolved OAuth identity from a validated access token."""
    subject: str
    tenant_id: str | None
    scopes: tuple[str, ...]
    issuer: str


def _issuer() -> str:
    return os.environ.get("ALGOCHAINS_OAUTH_ISSUER", "https://auth.algochains.ai")


def _resource() -> str:
    return os.environ.get("ALGOCHAINS_MCP_RESOURCE", "https://mcp.algochains.ai")


def _jwks_uri() -> str:
    explicit = os.environ.get("ALGOCHAINS_OAUTH_JWKS_URI", "").strip()
    if explicit:
        return explicit
    return f"{_issuer().rstrip('/')}/.well-known/jwks.json"


def oauth_enabled() -> bool:
    """True only when an explicit OAuth issuer or JWKS URI is configured."""
    return bool(
        os.environ.get("ALGOCHAINS_OAUTH_JWKS_URI", "").strip()
        or os.environ.get("ALGOCHAINS_OAUTH_ENABLED", "").strip().lower() in ("1", "true", "yes")
    )


def _get_jwks_client():
    global _JWKS_CLIENT, _JWKS_URI_CACHED
    uri = _jwks_uri()
    with _JWKS_LOCK:
        if _JWKS_CLIENT is None or _JWKS_URI_CACHED != uri:
            from jwt import PyJWKClient
            _JWKS_CLIENT = PyJWKClient(uri)
            _JWKS_URI_CACHED = uri
        return _JWKS_CLIENT


def validate_oauth_token(token: str | None) -> OAuthPrincipal | None:
    """Validate a bearer JWT access token. Returns the principal or None.

    Fail closed: any signature/claim error, missing config, or library issue
    returns None. Callers treat None as "not authenticated via OAuth."
    """
    if not token or not oauth_enabled():
        return None
    try:
        import jwt  # PyJWT

        signing_key = _get_jwks_client().get_signing_key_from_jwt(token).key
        required = ["exp", "aud", "iss", "sub"]
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256", "ES256"],
            audience=_resource(),     # mandatory aud check (RFC 8707)
            issuer=_issuer(),         # mandatory iss check
            options={"require": required},
        )
    except Exception as exc:  # fail closed
        log.info("OAuth token rejected: %s", exc)
        return None

    # Scope check
    token_scopes = tuple((claims.get("scope") or "").split())
    required_scope = os.environ.get("ALGOCHAINS_OAUTH_REQUIRED_SCOPE", "").split()
    if required_scope and not set(required_scope).issubset(set(token_scopes)):
        log.info("OAuth token missing required scope(s): %s", required_scope)
        return None

    # Tenant from app_metadata (immutable, server-set). Never user_metadata.
    tenant_id = None
    app_md = claims.get("app_metadata")
    if isinstance(app_md, dict):
        tenant_id = app_md.get("tenant_id")

    return OAuthPrincipal(
        subject=str(claims.get("sub")),
        tenant_id=str(tenant_id) if tenant_id else None,
        scopes=token_scopes,
        issuer=str(claims.get("iss")),
    )


__all__ = ["OAuthPrincipal", "oauth_enabled", "validate_oauth_token"]
