"""Multi-tenant management for Cloud SaaS."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


class TenantManager:
    """Multi-tenant management."""

    def __init__(self) -> None:
        self._tenants: dict[str, dict] = {}

    async def create_tenant(self, name: str, plan: str = "starter", config: dict | None = None) -> dict:
        try:
            tenant_id = uuid.uuid4().hex[:12]
            tenant = {
                "id": tenant_id,
                "name": name,
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

    async def list_tenants(self, status: str | None = None) -> dict:
        try:
            tenants = list(self._tenants.values())
            if status:
                tenants = [t for t in tenants if t["status"] == status]
            return {"status": "ok", "tenants": tenants, "count": len(tenants)}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def update_plan(self, tenant_id: str, new_plan: str) -> dict:
        try:
            tenant = self._tenants.get(tenant_id)
            if not tenant:
                return {"status": "error", "error": f"Tenant {tenant_id} not found"}
            old_plan = tenant["plan"]
            tenant["plan"] = new_plan
            return {"status": "ok", "tenant_id": tenant_id, "old_plan": old_plan, "new_plan": new_plan}
        except Exception as e:
            return {"status": "error", "error": str(e)}
