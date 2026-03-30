"""MultiTenantEngine — tenant registry, sub-accounts, RLS, billing, branding, broker routing."""
from __future__ import annotations
import logging, uuid
from datetime import datetime
from typing import Any

logger = logging.getLogger("algochains_mcp.multi_tenant")

TIERS = {
    "starter": {"price": 49, "max_sub_accounts": 5, "max_bots": 10, "features": ["basic_branding"]},
    "growth": {"price": 199, "max_sub_accounts": 25, "max_bots": 50, "features": ["basic_branding", "custom_domain"]},
    "professional": {"price": 499, "max_sub_accounts": 100, "max_bots": 200, "features": ["basic_branding", "custom_domain", "api_access", "white_label"]},
    "enterprise": {"price": 0, "max_sub_accounts": -1, "max_bots": -1, "features": ["basic_branding", "custom_domain", "api_access", "white_label", "dedicated_support", "sla"]},
}


class MultiTenantEngine:
    def __init__(self):
        self._tenants: dict[str, dict] = {}
        self._sub_accounts: dict[str, list[dict]] = {}
        self._broker_routes: dict[str, dict] = {}

    async def create_tenant(self, name: str, admin_email: str, tier: str = "starter",
                            branding: dict | None = None) -> dict:
        if tier not in TIERS:
            return {"success": False, "error": f"Invalid tier. Use: {list(TIERS.keys())}"}
        tid = f"tenant_{uuid.uuid4().hex[:12]}"
        tenant = {
            "tenant_id": tid, "name": name, "admin_email": admin_email, "tier": tier,
            "tier_config": TIERS[tier], "status": "active",
            "branding": branding or {"logo_url": "", "primary_color": "#2563EB", "app_name": name},
            "api_key": f"ak_{uuid.uuid4().hex}", "created_at": datetime.utcnow().isoformat(),
            "sub_account_count": 0, "bot_count": 0,
        }
        self._tenants[tid] = tenant
        self._sub_accounts[tid] = []
        return {"success": True, "tenant": tenant}

    async def get_tenant(self, tenant_id: str) -> dict:
        t = self._tenants.get(tenant_id)
        if not t:
            return {"success": False, "error": f"Tenant '{tenant_id}' not found."}
        return {"success": True, "tenant": t, "sub_accounts": len(self._sub_accounts.get(tenant_id, []))}

    async def update_tenant(self, tenant_id: str, updates: dict) -> dict:
        t = self._tenants.get(tenant_id)
        if not t:
            return {"success": False, "error": f"Tenant '{tenant_id}' not found."}
        allowed = {"name", "branding", "tier", "status"}
        for k, v in updates.items():
            if k in allowed:
                t[k] = v
                if k == "tier" and v in TIERS:
                    t["tier_config"] = TIERS[v]
        t["updated_at"] = datetime.utcnow().isoformat()
        return {"success": True, "tenant": t}

    async def create_sub_account(self, tenant_id: str, user_id: str, name: str,
                                  permissions: list[str] | None = None) -> dict:
        t = self._tenants.get(tenant_id)
        if not t:
            return {"success": False, "error": f"Tenant '{tenant_id}' not found."}
        max_sa = t["tier_config"]["max_sub_accounts"]
        if max_sa > 0 and len(self._sub_accounts[tenant_id]) >= max_sa:
            return {"success": False, "error": f"Sub-account limit ({max_sa}) reached for {t['tier']} tier."}
        sa = {
            "sub_account_id": f"sa_{uuid.uuid4().hex[:12]}", "tenant_id": tenant_id,
            "user_id": user_id, "name": name, "status": "active",
            "permissions": permissions or ["read", "trade"],
            "created_at": datetime.utcnow().isoformat(),
        }
        self._sub_accounts[tenant_id].append(sa)
        t["sub_account_count"] = len(self._sub_accounts[tenant_id])
        return {"success": True, "sub_account": sa}

    async def list_sub_accounts(self, tenant_id: str) -> dict:
        t = self._tenants.get(tenant_id)
        if not t:
            return {"success": False, "error": f"Tenant '{tenant_id}' not found."}
        sas = self._sub_accounts.get(tenant_id, [])
        return {"success": True, "tenant_id": tenant_id, "count": len(sas), "sub_accounts": sas}

    async def configure_broker_routing(self, tenant_id: str, broker_config: dict) -> dict:
        t = self._tenants.get(tenant_id)
        if not t:
            return {"success": False, "error": f"Tenant '{tenant_id}' not found."}
        self._broker_routes[tenant_id] = {
            "tenant_id": tenant_id, "brokers": broker_config,
            "updated_at": datetime.utcnow().isoformat(),
        }
        return {"success": True, "routing": self._broker_routes[tenant_id]}

    async def get_billing_summary(self, tenant_id: str) -> dict:
        t = self._tenants.get(tenant_id)
        if not t:
            return {"success": False, "error": f"Tenant '{tenant_id}' not found."}
        tier_cfg = t["tier_config"]
        return {
            "success": True, "tenant_id": tenant_id, "tier": t["tier"],
            "monthly_base": tier_cfg["price"],
            "sub_accounts": t["sub_account_count"], "bots": t["bot_count"],
            "estimated_monthly": tier_cfg["price"],
            "billing_period": datetime.utcnow().strftime("%Y-%m"),
        }

    async def set_branding(self, tenant_id: str, branding: dict) -> dict:
        t = self._tenants.get(tenant_id)
        if not t:
            return {"success": False, "error": f"Tenant '{tenant_id}' not found."}
        allowed = {"logo_url", "primary_color", "secondary_color", "app_name", "favicon_url", "custom_domain", "custom_css"}
        for k, v in branding.items():
            if k in allowed:
                t["branding"][k] = v
        t["branding"]["updated_at"] = datetime.utcnow().isoformat()
        return {"success": True, "branding": t["branding"]}

    # ── Tenant Dashboard ──────────────────────────────────────────

    async def get_tenant_dashboard(self, tenant_id: str) -> dict:
        t = self._tenants.get(tenant_id)
        if not t:
            return {"success": False, "error": f"Tenant '{tenant_id}' not found."}
        sas = self._sub_accounts.get(tenant_id, [])
        active = [s for s in sas if s.get("status") == "active"]
        return {
            "success": True, "tenant_id": tenant_id, "name": t["name"], "tier": t["tier"],
            "total_sub_accounts": len(sas), "active_sub_accounts": len(active),
            "total_aum": sum(s.get("aum", 0) for s in sas),
            "daily_pnl": sum(s.get("daily_pnl", 0) for s in sas),
            "total_trades_today": sum(s.get("trades_today", 0) for s in sas),
            "api_calls_today": t.get("api_calls_today", 0),
            "status": t["status"],
            "broker_routing": self._broker_routes.get(tenant_id, {}),
        }

    # ── Sub-Account Status ────────────────────────────────────────

    async def get_sub_account_status(self, tenant_id: str, sub_account_id: str) -> dict:
        t = self._tenants.get(tenant_id)
        if not t:
            return {"success": False, "error": f"Tenant '{tenant_id}' not found."}
        sas = self._sub_accounts.get(tenant_id, [])
        sa = next((s for s in sas if s["sub_account_id"] == sub_account_id), None)
        if not sa:
            return {"success": False, "error": f"Sub-account '{sub_account_id}' not found."}
        return {
            "success": True, "sub_account": sa,
            "positions": sa.get("positions", []),
            "daily_pnl": sa.get("daily_pnl", 0),
            "compliance_status": sa.get("compliance_status", "healthy"),
            "recent_trades": sa.get("recent_trades", []),
        }

    # ── Sub-Account Permissions ───────────────────────────────────

    async def set_sub_account_permissions(self, tenant_id: str, sub_account_id: str,
                                           permissions: dict) -> dict:
        t = self._tenants.get(tenant_id)
        if not t:
            return {"success": False, "error": f"Tenant '{tenant_id}' not found."}
        sas = self._sub_accounts.get(tenant_id, [])
        sa = next((s for s in sas if s["sub_account_id"] == sub_account_id), None)
        if not sa:
            return {"success": False, "error": f"Sub-account '{sub_account_id}' not found."}
        allowed = {"can_trade", "can_use_marketplace", "can_copy_trade", "max_daily_trades",
                   "max_position_size_usd", "allowed_asset_classes", "read", "trade", "admin"}
        updated = {}
        for k, v in permissions.items():
            if k in allowed:
                updated[k] = v
        if isinstance(sa["permissions"], list):
            sa["permissions"] = {p: True for p in sa["permissions"]}
        sa["permissions"].update(updated)
        sa["permissions_updated_at"] = datetime.utcnow().isoformat()
        return {"success": True, "sub_account_id": sub_account_id, "permissions": sa["permissions"]}
