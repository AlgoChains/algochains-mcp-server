"""
MCP 2025-11-25 Elicitation support.

Allows the server to request structured user input mid-conversation before
executing destructive or high-value trade actions. Implements the
elicitation/create request pattern from the MCP spec.

Usage (in _dispatch_tool):
    from .spec_compliance.elicitation import ElicitationManager, ElicitRequest
    mgr = get_elicitation_manager()
    result = await mgr.request_confirmation(
        title="Confirm Order",
        fields={"symbol": "NVDA", "qty": 100, "estimated_notional": 15000},
        threshold_label="$10,000 notional",
    )
    if not result.confirmed:
        return _text({"error": "Order cancelled by user", "reason": result.cancel_reason})
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ElicitOutcome(str, Enum):
    CONFIRMED = "confirmed"
    DECLINED = "declined"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


@dataclass
class ElicitRequest:
    request_id: str
    title: str
    description: str
    schema: dict[str, Any]
    created_at: float = field(default_factory=time.time)
    timeout_seconds: float = 120.0


@dataclass
class ElicitResult:
    request_id: str
    outcome: ElicitOutcome
    confirmed: bool
    values: dict[str, Any] = field(default_factory=dict)
    cancel_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "outcome": self.outcome.value,
            "confirmed": self.confirmed,
            "values": self.values,
            "cancel_reason": self.cancel_reason,
        }


class ElicitationManager:
    """
    Manages elicitation requests for the MCP server.

    In the stdio transport, we cannot literally pause and wait for a form —
    instead we encode the elicitation as a structured tool response that
    instructs the AI client to present the confirmation to the user before
    proceeding. The client calls confirm_elicitation with the result.

    In the Streamable HTTP transport (Phase 5), real elicitation/create
    requests are emitted over the SSE stream.
    """

    NOTIONAL_THRESHOLD = 10_000  # USD — confirm orders above this

    def __init__(self) -> None:
        self._pending: dict[str, ElicitRequest] = {}
        self._results: dict[str, ElicitResult] = {}

    # ── Core elicitation primitives ─────────────────────────────────

    def create_request(
        self,
        title: str,
        description: str,
        schema: dict[str, Any],
        timeout_seconds: float = 120.0,
    ) -> ElicitRequest:
        req = ElicitRequest(
            request_id=str(uuid.uuid4()),
            title=title,
            description=description,
            schema=schema,
            timeout_seconds=timeout_seconds,
        )
        self._pending[req.request_id] = req
        return req

    def submit_result(self, request_id: str, confirmed: bool, values: dict[str, Any] = {}, cancel_reason: str = "") -> ElicitResult:
        if request_id not in self._pending:
            raise ValueError(f"No pending elicitation request: {request_id}")
        del self._pending[request_id]
        outcome = ElicitOutcome.CONFIRMED if confirmed else ElicitOutcome.DECLINED
        result = ElicitResult(
            request_id=request_id,
            outcome=outcome,
            confirmed=confirmed,
            values=values,
            cancel_reason=cancel_reason,
        )
        self._results[request_id] = result
        return result

    def get_result(self, request_id: str) -> ElicitResult | None:
        return self._results.get(request_id)

    def list_pending(self) -> list[dict[str, Any]]:
        now = time.time()
        expired = [rid for rid, r in self._pending.items() if now - r.created_at > r.timeout_seconds]
        for rid in expired:
            del self._pending[rid]
        return [
            {"request_id": r.request_id, "title": r.title, "description": r.description, "age_seconds": round(now - r.created_at, 1)}
            for r in self._pending.values()
        ]

    # ── High-level helpers for common trade confirmations ────────────

    def build_order_confirmation(
        self,
        symbol: str,
        side: str,
        qty: float,
        estimated_notional: float,
        broker: str,
        account: str = "default",
    ) -> dict[str, Any]:
        """
        Build the elicitation payload for a high-notional order.
        Returns a dict suitable for embedding in a _text() response that
        tells the agent to elicit user confirmation before placing the order.
        """
        req = self.create_request(
            title=f"Confirm {side.upper()} Order — ${estimated_notional:,.0f}",
            description=(
                f"This order exceeds the ${self.NOTIONAL_THRESHOLD:,} confirmation threshold. "
                f"Review before executing."
            ),
            schema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "title": "Symbol", "default": symbol},
                    "side": {"type": "string", "title": "Side", "default": side},
                    "qty": {"type": "number", "title": "Quantity", "default": qty},
                    "estimated_notional": {"type": "number", "title": "Est. Notional ($)", "default": estimated_notional},
                    "broker": {"type": "string", "title": "Broker", "default": broker},
                    "account": {"type": "string", "title": "Account", "default": account},
                    "confirmed": {"type": "boolean", "title": "I confirm this order", "default": False},
                },
                "required": ["confirmed"],
            },
        )
        return {
            "elicitation_required": True,
            "request_id": req.request_id,
            "title": req.title,
            "description": req.description,
            "schema": req.schema,
            "instruction": (
                "IMPORTANT: Present this confirmation form to the user before placing the order. "
                "Call confirm_elicitation with the request_id and confirmed=true to proceed."
            ),
        }

    def build_close_all_confirmation(self, estimated_pnl: float, position_count: int) -> dict[str, Any]:
        req = self.create_request(
            title="Confirm Close ALL Positions",
            description=f"This will close {position_count} positions. Estimated P&L impact: ${estimated_pnl:+,.2f}",
            schema={
                "type": "object",
                "properties": {
                    "position_count": {"type": "integer", "title": "Positions to close", "default": position_count},
                    "estimated_pnl": {"type": "number", "title": "Estimated P&L ($)", "default": estimated_pnl},
                    "confirmed": {"type": "boolean", "title": "I confirm closing all positions", "default": False},
                },
                "required": ["confirmed"],
            },
        )
        return {
            "elicitation_required": True,
            "request_id": req.request_id,
            "title": req.title,
            "description": req.description,
            "schema": req.schema,
            "instruction": "Present this form to the user. Call confirm_elicitation to proceed or cancel.",
        }

    def build_live_deploy_confirmation(self, strategy_name: str, broker: str, capital: float) -> dict[str, Any]:
        req = self.create_request(
            title=f"Confirm LIVE Deployment — {strategy_name}",
            description=(
                f"You are about to deploy '{strategy_name}' to LIVE trading on {broker} "
                f"with ${capital:,.0f} capital. This will execute REAL orders."
            ),
            schema={
                "type": "object",
                "properties": {
                    "strategy_name": {"type": "string", "title": "Strategy", "default": strategy_name},
                    "broker": {"type": "string", "title": "Broker", "default": broker},
                    "capital": {"type": "number", "title": "Capital ($)", "default": capital},
                    "i_understand_live_risk": {"type": "boolean", "title": "I understand this is LIVE trading", "default": False},
                    "confirmed": {"type": "boolean", "title": "Deploy to live", "default": False},
                },
                "required": ["i_understand_live_risk", "confirmed"],
            },
        )
        return {
            "elicitation_required": True,
            "request_id": req.request_id,
            "title": req.title,
            "description": req.description,
            "schema": req.schema,
            "instruction": "Present this confirmation to the user. BOTH fields must be true to proceed.",
        }


_elicitation_manager: ElicitationManager | None = None


def get_elicitation_manager() -> ElicitationManager:
    global _elicitation_manager
    if _elicitation_manager is None:
        _elicitation_manager = ElicitationManager()
    return _elicitation_manager
