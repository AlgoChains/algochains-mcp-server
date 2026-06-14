"""Tenant-context propagation helper (dependency-light).

Carries the active ``tenant_id`` through async call chains via a
:class:`contextvars.ContextVar` so server-side code never re-derives the tenant
from caller-supplied input.

SECURITY — OWASP API1:2023 (Broken Object Level Authorization / BOLA)
─────────────────────────────────────────────────────────────────────────
The ``tenant_id`` set here MUST come from the *validated* token claim
(JWT ``app_metadata.tenant_id``), NEVER from a request body, query parameter,
header, or any other caller-controlled field. Trusting caller input for tenant
selection is a textbook BOLA vulnerability — a caller could read or write
another tenant's data simply by asserting a different id.

This module pairs with the Postgres ``public.current_tenant_id()`` RLS building
block: the database enforces isolation via Row Level Security, while this
contextvar makes the validated tenant available to application code (logging,
client selection, scoped queries) within the same request.
"""
from __future__ import annotations

import contextvars

_tenant_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "tenant_id", default=None
)


def set_tenant(tenant_id: str | None) -> contextvars.Token:
    """Set the active tenant for the current context.

    The ``tenant_id`` MUST originate from the validated token claim, never from
    caller input. Returns the reset token (use with :meth:`ContextVar.reset`).
    """
    return _tenant_ctx.set(tenant_id)


def get_tenant() -> str | None:
    """Return the active tenant id, or ``None`` if unset."""
    return _tenant_ctx.get()


def require_tenant() -> str:
    """Return the active tenant id, raising if it is unset.

    Use on code paths that must be tenant-scoped to fail closed rather than
    silently operating without a tenant boundary.
    """
    tenant_id = _tenant_ctx.get()
    if not tenant_id:
        raise ValueError(
            "No tenant in context. A validated tenant_id (from the token claim) "
            "is required for this operation. Use set_tenant()/TenantContext with "
            "the JWT app_metadata.tenant_id — never caller input (OWASP API1:2023 BOLA)."
        )
    return tenant_id


class TenantContext:
    """Context manager that sets the active tenant and resets it on exit.

    Example::

        with TenantContext(validated_tenant_id):
            ...  # all code here sees get_tenant() == validated_tenant_id
    """

    def __init__(self, tenant_id: str | None) -> None:
        self._tenant_id = tenant_id
        self._token: contextvars.Token | None = None

    def __enter__(self) -> "TenantContext":
        self._token = _tenant_ctx.set(self._tenant_id)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._token is not None:
            _tenant_ctx.reset(self._token)
            self._token = None
