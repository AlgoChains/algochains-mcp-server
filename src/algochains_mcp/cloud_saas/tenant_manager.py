"""Multi-tenant management for Cloud SaaS.

State is persisted to the Supabase `algochains_subscriptions` table (primary),
with a local JSON file fallback for single-machine dev when Supabase is not
configured.  RLS on the Supabase table provides per-user row isolation.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("algochains_mcp.cloud_saas.tenants")

_STATE_DIR = Path(os.getenv("ALGOCHAINS_STATE_DIR", "state"))
_TENANTS_FILE = _STATE_DIR / "tenants.json"  # local dev fallback

_SUPABASE_URL = os.getenv("SUPABASE_URL", "")
_SUPABASE_SERVICE_KEY = (
    os.getenv("SUPABASE_SERVICE_ROLE_KEY", "") or os.getenv("SUPABASE_SERVICE_KEY", "")
)
_TENANTS_TABLE = "algochains_tenants"  # dedicated tenants table (HA-safe, RLS isolated)
_FALLBACK_TABLE = "algochains_subscriptions"  # fallback if tenants table not yet migrated

_SUPABASE_ENABLED = bool(_SUPABASE_URL and _SUPABASE_SERVICE_KEY)


def _sb_headers() -> dict[str, str]:
    return {
        "apikey": _SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {_SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


# ── Local JSON fallback (single-machine dev only) ─────────────────────────────

def _load_local() -> dict[str, dict]:
    if _TENANTS_FILE.exists():
        try:
            return json.loads(_TENANTS_FILE.read_text())
        except Exception as e:
            logger.warning("Could not load tenants file: %s", e)
    return {}


def _save_local(tenants: dict[str, dict]) -> None:
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        _TENANTS_FILE.write_text(json.dumps(tenants, indent=2, default=str))
    except Exception as e:
        logger.error("Could not persist tenants locally: %s", e)


# ── Supabase helpers ──────────────────────────────────────────────────────────

async def _sb_upsert(tenant: dict[str, Any]) -> bool:
    """Upsert one tenant row to Supabase. Returns True on success."""
    if not _SUPABASE_ENABLED:
        return False
    try:
        import httpx as _httpx
        url = f"{_SUPABASE_URL}/rest/v1/{_TENANTS_TABLE}"
        headers = {**_sb_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"}
        async with _httpx.AsyncClient(timeout=5.0) as c:
            r = await c.post(url, headers=headers, json=tenant)
        if r.status_code in (200, 201):
            return True
        logger.warning("tenant_manager: Supabase upsert %s", r.status_code)
        return False
    except Exception as e:
        logger.warning("tenant_manager: Supabase upsert error: %s", e)
        return False


async def _sb_delete(tenant_id: str) -> bool:
    if not _SUPABASE_ENABLED:
        return False
    try:
        import httpx as _httpx
        url = f"{_SUPABASE_URL}/rest/v1/{_TENANTS_TABLE}?id=eq.{tenant_id}"
        async with _httpx.AsyncClient(timeout=5.0) as c:
            r = await c.delete(url, headers=_sb_headers())
        return r.status_code in (200, 204)
    except Exception as e:
        logger.warning("tenant_manager: Supabase delete error: %s", e)
        return False


async def _sb_load_all() -> list[dict]:
    if not _SUPABASE_ENABLED:
        return []
    try:
        import httpx as _httpx
        url = f"{_SUPABASE_URL}/rest/v1/{_TENANTS_TABLE}?select=*&limit=1000"
        async with _httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(url, headers=_sb_headers())
        if r.status_code == 200:
            return r.json()
        if r.status_code == 404:
            logger.info(
                "tenant_manager: %s table not found in Supabase — "
                "run migration or keep using local JSON.", _TENANTS_TABLE
            )
        else:
            logger.warning("tenant_manager: Supabase load returned %s", r.status_code)
        return []
    except Exception as e:
        logger.warning("tenant_manager: Supabase load error: %s", e)
        return []


# ── TenantManager ─────────────────────────────────────────────────────────────

class TenantManager:
    """
    Multi-tenant management.

    Write path: Supabase first, local JSON as shadow / fallback.
    Read path: in-memory cache (seeded at startup from Supabase or local JSON).
    """

    def __init__(self) -> None:
        # In-memory cache, seeded lazily on first operation
        self._tenants: dict[str, dict] = {}
        self._seeded = False

    async def _ensure_seeded(self) -> None:
        if self._seeded:
            return
        rows = await _sb_load_all()
        if rows:
            self._tenants = {r["id"]: r for r in rows}
            logger.info("tenant_manager: loaded %d tenants from Supabase", len(self._tenants))
        else:
            self._tenants = _load_local()
            logger.info(
                "tenant_manager: Supabase unavailable — loaded %d tenants from local JSON",
                len(self._tenants),
            )
        self._seeded = True

    async def create_tenant(
        self,
        company_name: str,
        admin_email: str,
        plan: str = "free",
        config: dict | None = None,
    ) -> dict:
        try:
            await self._ensure_seeded()
            existing = next(
                (t for t in self._tenants.values() if t.get("admin_email") == admin_email),
                None,
            )
            if existing:
                return {
                    "status": "error",
                    "error": f"Tenant with admin_email '{admin_email}' already exists (id={existing['id']})",
                }

            tenant_id = uuid.uuid4().hex[:12]
            now = datetime.now(timezone.utc).isoformat()
            tenant: dict[str, Any] = {
                "id": tenant_id,
                "company_name": company_name,
                "admin_email": admin_email,
                "plan": plan,
                "config": config or {},
                "status": "active",
                "created_at": now,
                "updated_at": now,
            }
            self._tenants[tenant_id] = tenant
            # Write-through: Supabase + local fallback
            await _sb_upsert(tenant)
            _save_local(self._tenants)
            return {"status": "ok", "tenant": tenant}
        except Exception as e:
            logger.error("create_tenant failed: %s", e)
            return {"status": "error", "error": str(e)}

    async def get_tenant(self, tenant_id: str) -> dict:
        try:
            await self._ensure_seeded()
            tenant = self._tenants.get(tenant_id)
            if not tenant:
                return {"status": "error", "error": f"Tenant '{tenant_id}' not found"}
            return {"status": "ok", "tenant": tenant}
        except Exception as e:
            logger.error("get_tenant failed: %s", e)
            return {"status": "error", "error": str(e)}

    async def list_tenants(self, plan_filter: str | None = None, status_filter: str | None = None) -> dict:
        await self._ensure_seeded()
        tenants = list(self._tenants.values())
        if plan_filter:
            tenants = [t for t in tenants if t.get("plan") == plan_filter]
        if status_filter:
            tenants = [t for t in tenants if t.get("status") == status_filter]
        return {"status": "ok", "tenants": tenants, "count": len(tenants)}

    async def update_tenant(self, tenant_id: str, updates: dict) -> dict:
        try:
            await self._ensure_seeded()
            tenant = self._tenants.get(tenant_id)
            if not tenant:
                return {"status": "error", "error": f"Tenant '{tenant_id}' not found"}
            for k, v in updates.items():
                if k not in ("id", "created_at"):
                    tenant[k] = v
            tenant["updated_at"] = datetime.now(timezone.utc).isoformat()
            await _sb_upsert(tenant)
            _save_local(self._tenants)
            return {"status": "ok", "tenant": tenant}
        except Exception as e:
            logger.error("update_tenant failed: %s", e)
            return {"status": "error", "error": str(e)}

    async def suspend_tenant(self, tenant_id: str, reason: str = "") -> dict:
        return await self.update_tenant(tenant_id, {"status": "suspended", "suspend_reason": reason})

    async def delete_tenant(self, tenant_id: str) -> dict:
        await self._ensure_seeded()
        if tenant_id not in self._tenants:
            return {"status": "error", "error": f"Tenant '{tenant_id}' not found"}
        del self._tenants[tenant_id]
        await _sb_delete(tenant_id)
        _save_local(self._tenants)
        return {"status": "ok", "deleted_tenant_id": tenant_id}
