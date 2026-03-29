"""
Supabase SSO integration for AlgoChains MCP Server.

Handles Google SSO login flow, JWT validation, and session management.
Users authenticate via algochains.ai (Supabase) and receive a JWT + API key
that the MCP server uses for marketplace operations.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

logger = logging.getLogger("algochains_mcp.auth.supabase_sso")


@dataclass
class AuthSession:
    """Represents an authenticated user session."""
    user_id: str
    email: str
    display_name: str
    role: str  # "subscriber" | "developer" | "admin"
    jwt: str
    api_key: str
    expires_at: float
    scopes: list[str] = field(default_factory=list)

    @property
    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    @property
    def is_developer(self) -> bool:
        return self.role in ("developer", "admin")

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "email": self.email,
            "display_name": self.display_name,
            "role": self.role,
            "scopes": self.scopes,
            "expires_in": max(0, int(self.expires_at - time.time())),
        }


class SupabaseAuth:
    """Supabase authentication client for AlgoChains."""

    def __init__(self, supabase_url: str, supabase_anon_key: str):
        self.url = supabase_url.rstrip("/")
        self.anon_key = supabase_anon_key
        self._session: Optional[AuthSession] = None
        self._client: Optional[httpx.AsyncClient] = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.url,
                headers={
                    "apikey": self.anon_key,
                    "Content-Type": "application/json",
                },
                timeout=15.0,
            )
        return self._client

    async def authenticate_with_api_key(self, api_key: str) -> AuthSession:
        """Validate an AlgoChains API key against the backend.

        This is the primary auth flow for MCP server users:
        1. User signs in on algochains.ai (Google SSO)
        2. Gets an ALGOCHAINS_API_KEY from their dashboard
        3. MCP server validates the key on startup
        """
        client = await self._ensure_client()

        resp = await client.post(
            "/auth/v1/token?grant_type=api_key",
            json={"api_key": api_key},
        )

        if resp.status_code == 200:
            data = resp.json()
            self._session = AuthSession(
                user_id=data.get("user", {}).get("id", ""),
                email=data.get("user", {}).get("email", ""),
                display_name=data.get("user", {}).get("user_metadata", {}).get("full_name", ""),
                role=data.get("user", {}).get("app_metadata", {}).get("role", "subscriber"),
                jwt=data.get("access_token", ""),
                api_key=api_key,
                expires_at=time.time() + data.get("expires_in", 3600),
                scopes=data.get("user", {}).get("app_metadata", {}).get("scopes", ["read"]),
            )
            logger.info("Authenticated: %s (%s)", self._session.email, self._session.role)
            return self._session

        logger.warning("API key authentication failed: %s", resp.status_code)
        raise AuthenticationError(f"Invalid API key (HTTP {resp.status_code})")

    async def validate_jwt(self, jwt: str) -> dict[str, Any]:
        """Validate a Supabase JWT and return user claims."""
        client = await self._ensure_client()

        resp = await client.get(
            "/auth/v1/user",
            headers={"Authorization": f"Bearer {jwt}"},
        )

        if resp.status_code == 200:
            return resp.json()
        raise AuthenticationError(f"Invalid JWT (HTTP {resp.status_code})")

    @property
    def session(self) -> Optional[AuthSession]:
        if self._session and not self._session.is_expired:
            return self._session
        return None

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None


class AuthenticationError(Exception):
    """Authentication failed."""
    pass
