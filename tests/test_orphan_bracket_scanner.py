from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from algochains_mcp.brokers.base import Order, OrderSide, OrderStatus, OrderType, Position
from algochains_mcp.live_bot_intelligence.bot_ops import (
    classify_orphan_bracket_orders,
    futures_market_is_open,
    swing_protect_gate_status,
)
from algochains_mcp.tool_danger_tiers import TIER_ORDER_EXEC, TIER_READ_ONLY, get_danger_tier


def _order(
    order_id: str,
    *,
    contract_id: int = 4327110,
    order_type: OrderType = OrderType.STOP,
    status: OrderStatus = OrderStatus.ACCEPTED,
) -> Order:
    return Order(
        id=order_id,
        broker="tradovate",
        symbol="MNQM6",
        side=OrderSide.SELL,
        order_type=order_type,
        qty=1,
        status=status,
        raw={
            "id": int(order_id),
            "contractId": contract_id,
            "contractName": "MNQM6",
            "ordType": "Stop" if order_type == OrderType.STOP else "Limit",
            "ordStatus": "Working",
        },
    )


def _position(contract_id: int = 4327110) -> Position:
    return Position(
        broker="tradovate",
        symbol="MNQM6",
        qty=1,
        avg_entry_price=29123.25,
        market_value=0,
        unrealized_pnl=0,
        raw={"contractId": contract_id, "contractName": "MNQM6", "netPos": 1},
    )


def _decode_tool_json(result):
    text = result[0].text if hasattr(result[0], "text") else str(result[0])
    return json.loads(text)


def test_orphan_classifier_detects_protective_order_without_position() -> None:
    scan = classify_orphan_bracket_orders([], [_order("316687424456")], symbol="MNQ")

    assert scan["status"] == "ORPHAN_WORKING_ORDERS"
    assert scan["orphan_count"] == 1
    assert scan["orphans"][0]["order_id"] == "316687424456"
    assert scan["orphans"][0]["reason"] == "no_matching_open_position"


def test_orphan_classifier_ignores_order_with_matching_position() -> None:
    scan = classify_orphan_bracket_orders([_position()], [_order("316687424456")], symbol="MNQ")

    assert scan["status"] == "OK"
    assert scan["orphan_count"] == 0
    assert scan["ignored"][0]["reason"] == "matching_open_position"


def test_unlinked_limit_orders_require_explicit_opt_in() -> None:
    limit_order = _order("316687424419", order_type=OrderType.LIMIT)

    default_scan = classify_orphan_bracket_orders([], [limit_order], symbol="MNQ")
    opt_in_scan = classify_orphan_bracket_orders(
        [],
        [limit_order],
        symbol="MNQ",
        include_unlinked_limit_orders=True,
    )

    assert default_scan["orphan_count"] == 0
    assert default_scan["ignored"][0]["reason"] == "not_bracket_like"
    assert opt_in_scan["orphan_count"] == 1


def test_futures_market_hours_exclude_weekend_and_daily_break() -> None:
    assert futures_market_is_open(datetime(2026, 6, 16, 16, 0, tzinfo=timezone.utc)) is True
    assert futures_market_is_open(datetime(2026, 6, 16, 21, 30, tzinfo=timezone.utc)) is False
    assert futures_market_is_open(datetime(2026, 6, 20, 16, 0, tzinfo=timezone.utc)) is False
    assert futures_market_is_open(datetime(2026, 6, 21, 22, 30, tzinfo=timezone.utc)) is True


def test_swing_protect_gate_requires_all_three_inputs(monkeypatch) -> None:
    monkeypatch.setenv("MNQ_SWING_PROTECT", "YES")

    assert swing_protect_gate_status("MNQ", gate="PROCEED", swing=True)["passed"] is True
    assert swing_protect_gate_status("MNQ", gate="STOP", swing=True)["passed"] is False
    assert swing_protect_gate_status("MNQ", gate="PROCEED", swing=False)["passed"] is False


def test_tool_tiers_are_explicit() -> None:
    assert get_danger_tier("check_orphan_bracket_orders") == TIER_READ_ONLY
    assert get_danger_tier("cancel_orphan_bracket_orders") == TIER_ORDER_EXEC


def test_cancel_tool_cancels_only_orphan_candidate(monkeypatch) -> None:
    import algochains_mcp.live_bot_intelligence.bot_ops as bot_ops
    import algochains_mcp.server as srv

    class FakeConnector:
        def __init__(self) -> None:
            self.cancelled: list[str] = []

        async def get_positions(self):
            return [_position(contract_id=999)]

        async def get_orders(self, status):
            assert status == "open"
            return [_order("316687424456"), _order("316687424457", contract_id=999)]

        async def cancel_order(self, order_id: str) -> bool:
            self.cancelled.append(order_id)
            return True

    class FakeRegistry:
        def __init__(self, connector: FakeConnector) -> None:
            self.connector = connector

        def get(self, name: str):
            assert name == "tradovate"
            return self.connector

        def list_configured(self):
            return ["tradovate"]

    connector = FakeConnector()
    monkeypatch.setenv("OWNER_API_TOKEN", "owner-secret")
    monkeypatch.setenv("MNQ_SWING_PROTECT", "YES")
    monkeypatch.setattr(bot_ops, "futures_market_is_open", lambda: True)

    result = asyncio.run(
        srv._dispatch_tool(
            "cancel_orphan_bracket_orders",
            {
                "broker": "tradovate",
                "symbol": "MNQ",
                "owner_token": "owner-secret",
                "confirm": True,
                "gate": "PROCEED",
                "swing": True,
                "market_hours_only": True,
            },
            FakeRegistry(connector),
        )
    )

    data = _decode_tool_json(result)
    assert data["status"] == "CANCELLED_ORPHANS"
    assert [entry["order_id"] for entry in data["cancelled"]] == ["316687424456"]
    assert connector.cancelled == ["316687424456"]
