"""Multi-tenant management for Cloud SaaS."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


class TenantManager:
    """Multi-tenant management."""

    def __init__(self) -> None:
        self._tenants: dict[str, dict] = {}

    async def create_tenant(self, company_name: str, admin_email: str, plan: str = "free", config: dict | None = None) -> dict:
        try:
            tenant_id = uuid.uuid4().hex[:12]
            tenant = {
                "id": tenant_id,
                "company_name": company_name,
                "admin_email": admin_email,
                "plan": plan,
                "config": config or {},
                "status": "active",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._tenants[tenant_id] = tenant
            return {"status": "ok", "tenant": tenant}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def get_tenant(self, tenant_id: str) -> dict:
        try:
            tenant = self._tenants.get(tenant_id)
            if not tenant:
                return {"status": "error", "error": f"Tenant {tenant_id} not found"}
            return {"status": "ok", "tenant": tenant}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def update_tenant(self, tenant_id: str, updates: dict) -> dict:
        try:
            tenant = self._tenants.get(tenant_id)
            if not tenant:
                return {"status": "error", "error": f"Tenant {tenant_id} not found"}
            for k, v in updates.items():
                if k != "id":
                    tenant[k] = v
            tenant["updated_at"] = datetime.now(timezone.utc).isoformat()
            return {"status": "ok", "tenant": tenant}
        except Exception as e:
            return {"status": "error", "error": str(e)}
