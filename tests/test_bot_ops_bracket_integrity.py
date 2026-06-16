import sys
import types

from algochains_mcp.live_bot_intelligence import bot_ops


class FakeTradovateClient:
    positions = []
    working_orders = []

    def __init__(self, cid=None, secret=None, env=None):
        self.cid = cid
        self.secret = secret
        self.env = env

    def authenticate(self):
        return None

    def get_positions(self):
        return self.positions

    def get_working_orders(self):
        return self.working_orders


def _install_fake_tradovate(monkeypatch):
    fake_module = types.SimpleNamespace(TradovateClient=FakeTradovateClient)
    monkeypatch.setitem(sys.modules, "tradovate_client", fake_module)
    monkeypatch.setenv("TRADOVATE_ENV", "demo")
    FakeTradovateClient.positions = []
    FakeTradovateClient.working_orders = []


def test_check_unprotected_positions_flags_stop_only(monkeypatch):
    _install_fake_tradovate(monkeypatch)
    FakeTradovateClient.positions = [
        {"contractId": "123", "contractName": "CLM6", "netPos": 1, "netPrice": 72.5}
    ]
    FakeTradovateClient.working_orders = [
        {"contractId": 123, "orderType": "Stop", "action": "Sell"}
    ]

    result = bot_ops.check_unprotected_positions()

    assert result["status"] == "UNPROTECTED_EXPOSURE"
    assert result["positions_checked"] == 1
    assert result["missing_brackets"][0]["has_stop"] is True
    assert result["missing_brackets"][0]["has_target"] is False


def test_check_unprotected_positions_ignores_wrong_side_target(monkeypatch):
    _install_fake_tradovate(monkeypatch)
    FakeTradovateClient.positions = [
        {"contractId": 123, "contractName": "CLM6", "netPos": 1, "netPrice": 72.5}
    ]
    FakeTradovateClient.working_orders = [
        {"contractId": 123, "orderType": "Stop", "action": "Sell"},
        {"contractId": 123, "orderType": "Limit", "action": "Buy"},
    ]

    result = bot_ops.check_unprotected_positions()

    assert result["status"] == "UNPROTECTED_EXPOSURE"
    assert result["missing_brackets"][0]["has_stop"] is True
    assert result["missing_brackets"][0]["has_target"] is False


def test_check_unprotected_positions_requires_stop_and_target(monkeypatch):
    _install_fake_tradovate(monkeypatch)
    FakeTradovateClient.positions = [
        {"contractId": 123, "contractName": "CLM6", "netPos": 1, "netPrice": 72.5}
    ]
    FakeTradovateClient.working_orders = [
        {"contractId": 123, "orderType": "Stop", "action": "Sell"},
        {"contract": {"id": "123"}, "orderType": "Limit", "action": "Sell"},
    ]

    result = bot_ops.check_unprotected_positions()

    assert result["status"] == "OK"
    assert result["positions_checked"] == 1
    assert result["missing_brackets"] == []
    assert result["protected"][0]["has_stop"] is True
    assert result["protected"][0]["has_target"] is True
