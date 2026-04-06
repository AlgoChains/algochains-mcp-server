"""Usage-based billing and subscription management."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


class BillingEngine:
    """Usage-based billing and subscription management."""

    def __init__(self) -> None:
        self._invoices: dict[str, dict] = {}
        self._usage: dict[str, list[dict]] = {}
        self._payments: dict[str, dict] = {}

    async def get_usage(self, tenant_id: str, period: str | None = None) -> dict:
        try:
            usage = self._usage.get(tenant_id, [])
            return {"status": "ok", "tenant_id": tenant_id, "period": period or "current_month", "usage": usage, "count": len(usage)}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def get_invoice(self, tenant_id: str, invoice_id: str | None = None) -> dict:
        try:
            if invoice_id:
                inv = self._invoices.get(invoice_id)
                if not inv or inv.get("tenant_id") != tenant_id:
                    return {"status": "error", "error": f"Invoice {invoice_id} not found"}
                return {"status": "ok", "invoice": inv}
            invoices = [i for i in self._invoices.values() if i.get("tenant_id") == tenant_id]
            if not invoices:
                invoice_id = uuid.uuid4().hex[:12]
                usage = self._usage.get(tenant_id, [])
                total = sum(u.get("quantity", 0) for u in usage)
                inv = {"id": invoice_id, "tenant_id": tenant_id, "line_items": len(usage), "total_amount": round(total * 0.01, 2), "currency": "USD", "status": "draft", "generated_at": datetime.now(timezone.utc).isoformat()}
                self._invoices[invoice_id] = inv
                return {"status": "ok", "invoice": inv}
            return {"status": "ok", "invoice": invoices[-1]}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def list_invoices(self, tenant_id: str, status: str | None = None) -> dict:
        try:
            invoices = [i for i in self._invoices.values() if i.get("tenant_id") == tenant_id]
            if status:
                invoices = [i for i in invoices if i.get("status") == status]
            return {"status": "ok", "invoices": invoices, "count": len(invoices)}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def update_payment(self, tenant_id: str, payment_method: dict) -> dict:
        try:
            self._payments[tenant_id] = {"payment_method": payment_method, "updated_at": datetime.now(timezone.utc).isoformat()}
            return {"status": "ok", "tenant_id": tenant_id, "payment_method_type": payment_method.get("type", "card")}
        except Exception as e:
            return {"status": "error", "error": str(e)}
