"""tests/test_http_bridge_server_import_error.py — broken-deploy failure mode.

When the deployed bridge cannot import ``algochains_mcp.server`` (mcp SDK
drift — e.g. ``'Tool' object has no attribute 'inputSchema'``), tool calls
must return a clear operator-facing ServerImportError payload instead of
leaking the raw import-time exception as if the tool itself failed.
"""
from __future__ import annotations

import asyncio
import sys

import algochains_mcp
from algochains_mcp import http_bridge


def _break_server_import(monkeypatch):
    """Make ``from algochains_mcp import server`` raise ImportError."""
    monkeypatch.setitem(sys.modules, "algochains_mcp.server", None)
    monkeypatch.delattr(algochains_mcp, "server", raising=False)


def test_import_helper_returns_error_payload_when_server_unimportable(monkeypatch):
    _break_server_import(monkeypatch)
    module, error = http_bridge._import_server_module()
    assert module is None
    assert error["error_type"] == "ServerImportError"
    assert "failed to import" in error["error"]
    assert "server_import_ok" in error["action"]


def test_import_helper_returns_module_when_import_works():
    module, error = http_bridge._import_server_module()
    assert error is None
    assert hasattr(module, "call_tool")


def test_tool_call_surfaces_server_import_error_not_raw_exception(monkeypatch):
    _break_server_import(monkeypatch)
    result = asyncio.run(
        http_bridge.handle_mcp_request(
            "detect_market_regime",
            {},
            is_owner=True,
            caller_scope="legacy_owner",
        )
    )
    assert result["error_type"] == "ServerImportError"
    assert result["tool"] == "detect_market_regime"
    assert "inputSchema" not in result["error"]
    assert "redeploy" in result["action"]
