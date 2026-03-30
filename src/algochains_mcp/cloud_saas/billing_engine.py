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

    async def record_usage(self, tenant_id: str, metric: str, quantity: float, unit: str = "count") -> dict:
        try:
            entry = {
                "tenant_id": tenant_id,
                "metric": metric,
                "quantity": quantity,
                "unit": unit,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            }
            self._usage.setdefault(tenant_id, []).append(entry)
            return {"status": "ok", "usage": entry}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def get_usage(self, tenant_id: str, period: str | None = None) -> dict:
        try:
            usage = self._usage.get(tenant_id, [])
            return {"status": "ok", "tenant_id": tenant_id, "usage": usage, "count": len(usage)}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def generate_invoice(self, tenant_id: str) -> dict:
        try:
            invoice_id = uuid.uuid4().hex[:12]
            usage = self._usage.get(tenant_id, [])
            total = sum(u.get("quantity", 0) for u in usage)
            invoice = {
                "id": invoice_id,
                "tenant_id": tenant_id,
                "line_items": len(usage),
                "total_amount": round(total * 0.01, 2),
                "currency": "USD",
                "status": "draft",
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }
            self._invoices[invoice_id] = invoice
            return {"status": "ok", "invoice": invoice}
        except Exception as e:
            return {"status": "error", "error": str(e)}
