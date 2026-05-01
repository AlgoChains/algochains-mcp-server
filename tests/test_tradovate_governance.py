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
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _make_connector(access_token: str = ""):
    """Create a TradovateConnector with a fake config (no live calls)."""
    from algochains_mcp.brokers.tradovate import TradovateConnector, TradovateConfig
    cfg = TradovateConfig(
        access_token=access_token,
        username="testuser",
        password="testpass",  # noqa: secret-scan-skip — test fixture only
        env="demo",
    )
    return TradovateConnector(cfg)


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
    original_post = conn._http.post

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
    _expected = "guardian-token-abc123"  # noqa: secret-scan-skip — test fixture value
    assert conn._access_token == _expected, (
        f"Expected _access_token={_expected!r}, got: {conn._access_token!r}"
    )


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
