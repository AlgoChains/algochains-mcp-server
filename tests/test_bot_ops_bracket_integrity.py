from __future__ import annotations

import os
import sys
import types
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from algochains_mcp.live_bot_intelligence import bot_ops


def _install_tradovate_client(
    monkeypatch,
    positions: list[dict[str, Any]],
    working_orders: list[dict[str, Any]],
) -> None:
    module = types.ModuleType("tradovate_client")

    class FakeTradovateClient:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def authenticate(self) -> None:
            pass

        def get_positions(self) -> list[dict[str, Any]]:
            return positions

        def get_working_orders(self) -> list[dict[str, Any]]:
            return working_orders

    module.TradovateClient = FakeTradovateClient
    monkeypatch.setitem(sys.modules, "tradovate_client", module)


def test_check_unprotected_positions_requires_target_leg(tmp_path, monkeypatch):
    monkeypatch.setattr(bot_ops, "CONTROL_TOWER", tmp_path)
    monkeypatch.setenv("TRADOVATE_ENV", "demo")
    _install_tradovate_client(
        monkeypatch,
        positions=[
            {"contractId": "123", "contractName": "MNQM6", "netPos": 1, "netPrice": 100.0},
        ],
        working_orders=[
            {"contractId": 123, "orderType": "Stop", "action": "Sell"},
        ],
    )

    result = bot_ops.check_unprotected_positions()

    assert result["status"] == "UNPROTECTED_EXPOSURE"
    assert result["positions_checked"] == 1
    assert result["protected"] == []
    assert result["unprotected"][0]["has_stop"] is True
    assert result["unprotected"][0]["has_target"] is False
    assert result["unprotected"][0]["missing"] == ["target"]


def test_check_unprotected_positions_requires_opposite_side_target(tmp_path, monkeypatch):
    monkeypatch.setattr(bot_ops, "CONTROL_TOWER", tmp_path)
    monkeypatch.setenv("TRADOVATE_ENV", "demo")
    _install_tradovate_client(
        monkeypatch,
        positions=[
            {"contractId": 123, "contractName": "MNQM6", "netPos": 1, "netPrice": 100.0},
        ],
        working_orders=[
            {"contractId": 123, "orderType": "Stop", "action": "Sell"},
            {"contractId": 123, "orderType": "Limit", "action": "Buy"},
        ],
    )

    result = bot_ops.check_unprotected_positions()

    assert result["status"] == "UNPROTECTED_EXPOSURE"
    assert result["unprotected"][0]["has_stop"] is True
    assert result["unprotected"][0]["has_target"] is False
    assert result["unprotected"][0]["missing"] == ["target"]


def test_check_unprotected_positions_accepts_stop_and_target(tmp_path, monkeypatch):
    monkeypatch.setattr(bot_ops, "CONTROL_TOWER", tmp_path)
    monkeypatch.setenv("TRADOVATE_ENV", "demo")
    _install_tradovate_client(
        monkeypatch,
        positions=[
            {"contractId": 123, "contractName": "MNQM6", "netPos": -1, "netPrice": 100.0},
        ],
        working_orders=[
            {"contractId": "123", "orderType": "Stop", "action": "Buy"},
            {"contract": {"id": "123"}, "orderType": "Limit", "action": "Buy"},
        ],
    )

    result = bot_ops.check_unprotected_positions()

    assert result["status"] == "OK"
    assert result["positions_checked"] == 1
    assert result["unprotected"] == []
    assert result["protected"][0]["has_stop"] is True
    assert result["protected"][0]["has_target"] is True
    assert result["protected"][0]["missing"] == []
