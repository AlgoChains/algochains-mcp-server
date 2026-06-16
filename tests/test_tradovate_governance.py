"""
Tradovate connector governance tests.

Verifies:
1. capabilities["streaming"] is False — MCP does not own the WS connection
2. stream_quotes() raises NotImplementedError, not a silent no-op
3. connect() uses pre-existing TRADOVATE_ACCESS_TOKEN first (no second OAuth storm)
4. No second WebSocket path is exposed through the MCP broker connector
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _make_connector(access_token: str = ""):
    """Create a TradovateConnector with a fake config (no live calls)."""
    from algochains_mcp.brokers.tradovate import TradovateConnector, TradovateConfig
    cfg = TradovateConfig(
        access_token=access_token,
        username="testuser",
        password="testpass",  # secret-scan-skip — test fixture only
        env="demo",
    )
    return TradovateConnector(cfg)


def _jwt_with_exp(exp: int) -> str:
    """Build an unsigned JWT-shaped test token with an exp claim."""
    header = {"alg": "none", "typ": "JWT"}
    payload = {"exp": exp}

    def _segment(data: dict) -> str:
        raw = json.dumps(data, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{_segment(header)}.{_segment(payload)}."


def test_streaming_capability_is_false():
    """TradovateConnector.capabilities['streaming'] must be False.

    The MCP connector is REST-only.  Live bots own the WebSocket connection
    via the control tower.  This test catches any future reactivation of WS.
    """
    conn = _make_connector()
    caps = conn.capabilities
    assert caps.get("streaming") is False, (
        f"TradovateConnector.capabilities['streaming'] must be False, got: {caps['streaming']}. "
        "MCP connector must not own a WebSocket — bots own the WS connection."
    )


def test_stream_quotes_raises_not_implemented():
    """stream_quotes() must raise NotImplementedError, not silently return."""
    conn = _make_connector()

    async def _try():
        await conn.stream_quotes(["MNQZ5"])

    with pytest.raises(NotImplementedError, match="WebSocket streaming"):
        asyncio.run(_try())


def test_capabilities_futures_true():
    """Futures capability is expected True — sanity check."""
    conn = _make_connector()
    assert conn.capabilities.get("futures") is True


def test_connect_prefers_preexisting_token(monkeypatch):
    """connect() must use TRADOVATE_ACCESS_TOKEN without making OAuth requests.

    When the Token Guardian has written a valid access token, connect() must
    consume it and skip the username/password OAuth flow.  This prevents a
    second OAuth session from racing with the guardian token renewal.
    """
    monkeypatch.setenv("TRADOVATE_ACCESS_TOKEN", "guardian-token-abc123")
    # Override cfg.access_token to simulate the env var being picked up by config
    from algochains_mcp.brokers.tradovate import TradovateConnector, TradovateConfig
    cfg = TradovateConfig(
        access_token=os.environ.get("TRADOVATE_ACCESS_TOKEN", ""),
        username="user",
        password="pass",
        env="demo",
    )
    conn = TradovateConnector(cfg)

    # Track HTTP calls — should be 0 if pre-existing token is used
    http_calls: list[str] = []

    async def _mock_post(url, *a, **kw):
        http_calls.append(url)
        raise RuntimeError("Should not reach HTTP — token should be pre-loaded")

    conn._http.post = _mock_post  # type: ignore

    async def _run():
        # Patch account lookup to avoid real HTTP
        async def _mock_get(url, *a, **kw):
            from unittest.mock import MagicMock
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = [{"id": 99, "spec": "TEST123"}]
            return resp
        conn._http.get = _mock_get  # type: ignore
        result = await conn.connect()
        return result

    asyncio.run(_run())
    assert not http_calls, (
        f"connect() made unexpected OAuth HTTP calls when TRADOVATE_ACCESS_TOKEN "
        f"was present: {http_calls}"
    )
    _expected = "guardian-token-abc123"  # secret-scan-skip — test fixture value
    assert conn._access_token == _expected, (
        f"Expected _access_token={_expected!r}, got: {conn._access_token!r}"
    )


def test_connect_uses_preexisting_jwt_expiry(monkeypatch):
    """A Guardian JWT with >60 min remaining must not reconnect immediately."""
    expires_at = int(time.time() + (76 * 60))
    access_token = _jwt_with_exp(expires_at)
    monkeypatch.setenv("TRADOVATE_ACCESS_TOKEN", access_token)

    from algochains_mcp.brokers.tradovate import TradovateConnector, TradovateConfig
    cfg = TradovateConfig(
        access_token=os.environ.get("TRADOVATE_ACCESS_TOKEN", ""),
        username="user",
        password="pass",
        env="demo",
    )
    conn = TradovateConnector(cfg)

    async def _mock_get(url, *a, **kw):
        from unittest.mock import MagicMock
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = [{"id": 99, "name": "TEST123"}]
        return resp

    async def _run():
        conn._http.get = _mock_get  # type: ignore
        assert await conn.connect() is True

        reconnects = 0

        async def _unexpected_reconnect():
            nonlocal reconnects
            reconnects += 1
            return True

        conn.connect = _unexpected_reconnect  # type: ignore
        await conn._ensure_token()
        return reconnects

    reconnects = asyncio.run(_run())
    assert conn._token_expires_at == expires_at
    assert reconnects == 0


def test_connect_falls_back_for_opaque_preexisting_token(monkeypatch):
    """Non-JWT Guardian tokens retain the conservative 60-minute fallback."""
    monkeypatch.setenv("TRADOVATE_ACCESS_TOKEN", "opaque-guardian-token")

    from algochains_mcp.brokers.tradovate import TradovateConnector, TradovateConfig
    cfg = TradovateConfig(
        access_token=os.environ.get("TRADOVATE_ACCESS_TOKEN", ""),
        username="user",
        password="pass",
        env="demo",
    )
    conn = TradovateConnector(cfg)

    async def _mock_get(url, *a, **kw):
        from unittest.mock import MagicMock
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = [{"id": 99, "name": "TEST123"}]
        return resp

    async def _run():
        conn._http.get = _mock_get  # type: ignore
        before = time.time()
        assert await conn.connect() is True
        after = time.time()
        return before, after

    before, after = asyncio.run(_run())
    assert before + 3600 <= conn._token_expires_at <= after + 3600


def test_no_ws_import_in_connector():
    """Tradovate connector must not import a WebSocket library at module level.

    This catches accidental re-introduction of a WS client in the MCP connector.
    """
    from pathlib import Path
    connector_path = (
        Path(__file__).resolve().parents[1]
        / "src" / "algochains_mcp" / "brokers" / "tradovate.py"
    )
    src = connector_path.read_text()
    # These would indicate a live WS client — not allowed in MCP connector
    forbidden = ["import websockets", "import websocket", "websocket.WebSocket(", "ws.connect("]
    found = [f for f in forbidden if f in src]
    assert not found, (
        f"WebSocket import/usage found in tradovate.py connector: {found}. "
        "MCP connector must be REST-only; WS belongs to the control tower bots."
    )


def test_get_account_uses_cash_balance_when_total_cash_value_is_null():
    """Nullable Tradovate fields must not leak as JSON null account balances."""
    conn = _make_connector()

    async def _mock_get(path, *args, **kwargs):
        if path == "/account/list":
            return [{"id": 99, "name": "TEST123"}]
        if path == "/cashBalance/getCashBalanceSnapshot":
            return {"totalCashValue": None, "cashBalance": "12500.75"}
        raise AssertionError(f"Unexpected Tradovate path: {path}")

    conn._get = _mock_get  # type: ignore

    async def _run():
        return await conn.get_account()

    account = asyncio.run(_run())
    payload = account.to_dict()
    assert payload["equity"] == 12500.75
    assert payload["cash"] == 12500.75
    assert payload["balance"] == 12500.75
    assert payload["account_balance"] == 12500.75


def test_get_account_raises_when_snapshot_has_no_numeric_balance():
    """A missing live balance is a degraded broker read, not a zero/null balance."""
    from algochains_mcp.errors import BrokerConnectionError

    conn = _make_connector()

    async def _mock_get(path, *args, **kwargs):
        if path == "/account/list":
            return [{"id": 99, "name": "TEST123"}]
        if path == "/cashBalance/getCashBalanceSnapshot":
            return {"totalCashValue": None, "cashBalance": None}
        raise AssertionError(f"Unexpected Tradovate path: {path}")

    conn._get = _mock_get  # type: ignore

    async def _run():
        return await conn.get_account()

    with pytest.raises(BrokerConnectionError, match="numeric balance"):
        asyncio.run(_run())


def test_get_quote_raises_when_entries_are_empty():
    """Missing REST market data must not be normalized into a zero quote."""
    from algochains_mcp.errors import BrokerQuoteError

    conn = _make_connector()

    async def _mock_find_contract(symbol):
        assert symbol == "MNQ"
        return {"id": 12345, "name": "MNQM6"}

    async def _mock_get(path, params):
        assert path == "/md/getQuote"
        assert params == {"symbol": "MNQM6"}
        return {"entries": {}}

    conn._find_contract = _mock_find_contract  # type: ignore
    conn._get = _mock_get  # type: ignore

    async def _run():
        return await conn.get_quote("MNQ")

    with pytest.raises(BrokerQuoteError, match="Quote unavailable"):
        asyncio.run(_run())


def test_get_quote_parses_wrapped_tradovate_quote_payload():
    """Tradovate /md/getQuote may wrap entries under d.quotes[]."""
    conn = _make_connector()

    async def _mock_find_contract(symbol):
        assert symbol == "MNQ"
        return {"id": 12345, "name": "MNQM6"}

    async def _mock_get(path, params):
        assert path == "/md/getQuote"
        assert params == {"symbol": "MNQM6"}
        return {
            "e": "md",
            "d": {
                "quotes": [
                    {
                        "contractId": 12345,
                        "entries": {
                            "Bid": {"price": "20125.25"},
                            "Offer": {"price": "20125.75"},
                            "Trade": {"price": "20125.50", "size": "7"},
                        },
                    }
                ]
            },
        }

    conn._find_contract = _mock_find_contract  # type: ignore
    conn._get = _mock_get  # type: ignore

    async def _run():
        return await conn.get_quote("MNQ")

    quote = asyncio.run(_run())
    assert quote.symbol == "MNQ"
    assert quote.bid == 20125.25
    assert quote.ask == 20125.75
    assert quote.last == 20125.50
    assert quote.volume == 7
