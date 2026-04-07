"""Multi-tenant management for Cloud SaaS. State persisted to state/tenants.json."""
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
_TENANTS_FILE = _STATE_DIR / "tenants.json"


def _load_tenants() -> dict[str, dict]:
    if _TENANTS_FILE.exists():
        try:
            return json.loads(_TENANTS_FILE.read_text())
        except Exception as e:
            logger.warning("Could not load tenants file: %s", e)
    return {}


def _save_tenants(tenants: dict[str, dict]) -> None:
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        _TENANTS_FILE.write_text(json.dumps(tenants, indent=2, default=str))
    except Exception as e:
        logger.error("Could not persist tenants: %s", e)


class TenantManager:
    """Multi-tenant management. Tenants persist to state/tenants.json across restarts."""

    def __init__(self) -> None:
        self._tenants: dict[str, dict] = _load_tenants()

    async def create_tenant(
        self,
        company_name: str,
        admin_email: str,
        plan: str = "free",
        config: dict | None = None,
    ) -> dict:
        try:
            # Prevent duplicate admin emails
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
            _save_tenants(self._tenants)
            return {"status": "ok", "tenant": tenant}
        except Exception as e:
            logger.error("create_tenant failed: %s", e)
            return {"status": "error", "error": str(e)}

    async def get_tenant(self, tenant_id: str) -> dict:
        try:
            tenant = self._tenants.get(tenant_id)
            if not tenant:
                return {"status": "error", "error": f"Tenant '{tenant_id}' not found"}
            return {"status": "ok", "tenant": tenant}
        except Exception as e:
            logger.error("get_tenant failed: %s", e)
            return {"status": "error", "error": str(e)}

    async def list_tenants(self, plan_filter: str | None = None, status_filter: str | None = None) -> dict:
        tenants = list(self._tenants.values())
        if plan_filter:
            tenants = [t for t in tenants if t.get("plan") == plan_filter]
        if status_filter:
            tenants = [t for t in tenants if t.get("status") == status_filter]
        return {"status": "ok", "tenants": tenants, "count": len(tenants)}

    async def update_tenant(self, tenant_id: str, updates: dict) -> dict:
        try:
            tenant = self._tenants.get(tenant_id)
            if not tenant:
                return {"status": "error", "error": f"Tenant '{tenant_id}' not found"}
            for k, v in updates.items():
                if k not in ("id", "created_at"):
                    tenant[k] = v
            tenant["updated_at"] = datetime.now(timezone.utc).isoformat()
            _save_tenants(self._tenants)
            return {"status": "ok", "tenant": tenant}
        except Exception as e:
            logger.error("update_tenant failed: %s", e)
            return {"status": "error", "error": str(e)}

    async def suspend_tenant(self, tenant_id: str, reason: str = "") -> dict:
        return await self.update_tenant(tenant_id, {"status": "suspended", "suspend_reason": reason})

    async def delete_tenant(self, tenant_id: str) -> dict:
        if tenant_id not in self._tenants:
            return {"status": "error", "error": f"Tenant '{tenant_id}' not found"}
        del self._tenants[tenant_id]
        _save_tenants(self._tenants)
        return {"status": "ok", "deleted_tenant_id": tenant_id}
