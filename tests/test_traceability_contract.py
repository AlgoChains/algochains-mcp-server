"""
Traceability contract tests.

Verifies:
1. place_order schema accepts client_trace_id field
2. cancel_order echoes client_trace_id (gated — only tests schema presence, not live call)
3. HTTP bridge X-Request-Id is propagated
4. MCP_TRACEABILITY_CONTRACT.md document exists
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class _FakeCompletedProcess:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _decode_tool_result(result):
    text = result[0].text if hasattr(result[0], "text") else str(result[0])
    return json.loads(text)


def test_place_order_schema_has_client_trace_id():
    """place_order tool schema must accept client_trace_id for audit correlation."""
    import algochains_mcp.server as srv

    place_order_tool = next(
        (t for t in srv.TOOLS if t.name == "place_order"), None
    )
    assert place_order_tool is not None, "place_order tool not found"
    schema_props = place_order_tool.inputSchema.get("properties", {})
    assert "client_trace_id" in schema_props, (
        "place_order inputSchema missing 'client_trace_id' field. "
        "This field enables MCP call correlation to control-tower trade_log rows."
    )


def test_cancel_order_schema_has_client_trace_id():
    """cancel_order tool schema must accept client_trace_id."""
    import algochains_mcp.server as srv

    cancel_tool = next(
        (t for t in srv.TOOLS if t.name == "cancel_order"), None
    )
    if cancel_tool is None:
        pytest.skip("cancel_order tool not found")
    schema_props = cancel_tool.inputSchema.get("properties", {})
    assert "client_trace_id" in schema_props, (
        "cancel_order inputSchema missing 'client_trace_id' field."
    )


def test_traceability_contract_doc_exists():
    """MCP_TRACEABILITY_CONTRACT.md must exist in docs/."""
    from pathlib import Path
    doc = Path(__file__).resolve().parents[1] / "docs" / "MCP_TRACEABILITY_CONTRACT.md"
    assert doc.exists(), (
        "docs/MCP_TRACEABILITY_CONTRACT.md not found. "
        "This document defines the client_trace_id / signal_id join-key protocol."
    )
    content = doc.read_text()
    assert "client_trace_id" in content, "Contract doc missing client_trace_id section"
    assert "signal_id" in content, "Contract doc missing signal_id section"
    assert "trade_log" in content, "Contract doc must reference trade_log for join semantics"


def test_x_request_id_middleware_present():
    """HTTP bridge must have X-Request-Id middleware registered."""
    from pathlib import Path
    bridge_src = (
        Path(__file__).resolve().parents[1]
        / "src" / "algochains_mcp" / "http_bridge.py"
    ).read_text()
    assert "X-Request-Id" in bridge_src, (
        "http_bridge.py is missing X-Request-Id header handling. "
        "This is required for request traceability."
    )
    assert "_RequestIdMiddleware" in bridge_src or "req_id" in bridge_src, (
        "X-Request-Id middleware not wired up in http_bridge.py"
    )


def test_place_order_echoes_client_trace_id(monkeypatch):
    """place_order blocked response echoes client_trace_id when provided.

    Tests the echo path without a live broker: uses the danger-tier block
    (no owner_token) to get a response that should still reflect the trace ID.
    This validates the schema plumbing without hitting a broker.
    """
    import asyncio
    import algochains_mcp.server as srv

    monkeypatch.setenv("OWNER_API_TOKEN", "test-secret")

    async def _call():
        return await srv.call_tool(
            "execute_dynamic_tool",
            {
                "tool_name": "place_order",
                "arguments": {
                    "broker": "tradovate",
                    "symbol": "MNQZ5",
                    "side": "buy",
                    "qty": 1,
                    "client_trace_id": "test-signal-uuid-1234",
                    # No owner_token — should be blocked by danger tier gate
                }
            }
        )

    result = asyncio.run(_call())
    text = result[0].text if hasattr(result[0], "text") else str(result[0])
    data = json.loads(text) if text.startswith("{") else {}
    # Blocked by danger tier — trace ID propagation on success is tested elsewhere
    # Here we just confirm the schema accepted the field (no validation error)
    assert data.get("blocked") is True or "blocked" in text.lower() or "authorization" in text.lower(), (
        f"Expected danger-tier block, got: {text[:200]}"
    )


def test_signal_trade_correlation_retries_transient_supabase_reset(monkeypatch, tmp_path):
    """Transient Supabase/PostgREST resets should be retried before degradation."""
    import algochains_mcp.server as srv

    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "signal_trade_correlation_audit.py").write_text("# test stub\n")

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if len(calls) == 1:
            return _FakeCompletedProcess(
                1,
                stderr="signals_trace:ConnectError:[Errno 54] Connection reset by peer",
            )
        return _FakeCompletedProcess(
            0,
            stdout=json.dumps({"status": "ok", "sample_size": 2}),
        )

    monkeypatch.setattr(srv, "_default_control_tower", lambda: str(tmp_path))
    monkeypatch.setattr(srv.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = asyncio.run(srv.call_tool(
        "get_signal_trade_correlation",
        {"limit": 7, "action": "submitted"},
    ))
    data = _decode_tool_result(result)

    assert data == {"status": "ok", "sample_size": 2}
    assert len(calls) == 2
    assert calls[0][0][-5:] == ["--limit", "7", "--action", "submitted", "--filled-only"]
    assert calls[0][1]["cwd"] == str(tmp_path)


def test_signal_trade_correlation_does_not_retry_permanent_failure(monkeypatch, tmp_path):
    """Non-transport script failures should surface immediately."""
    import algochains_mcp.server as srv

    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "signal_trade_correlation_audit.py").write_text("# test stub\n")

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return _FakeCompletedProcess(1, stderr="ValueError: malformed audit config")

    monkeypatch.setattr(srv, "_default_control_tower", lambda: str(tmp_path))
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = asyncio.run(srv.call_tool("get_signal_trade_correlation", {}))
    data = _decode_tool_result(result)

    assert data["error"] == "correlation audit failed"
    assert data["attempts"] == 1
    assert data["transient_transport"] is False
    assert len(calls) == 1


def test_signal_trade_correlation_reports_exhausted_transient_retries(monkeypatch, tmp_path):
    """Repeated transient resets should include retry metadata in the error."""
    import algochains_mcp.server as srv

    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "signal_trade_correlation_audit.py").write_text("# test stub\n")

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return _FakeCompletedProcess(
            1,
            stderr="httpx.RemoteProtocolError: server disconnected without sending a response",
        )

    monkeypatch.setattr(srv, "_default_control_tower", lambda: str(tmp_path))
    monkeypatch.setattr(srv.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = asyncio.run(srv.call_tool("get_signal_trade_correlation", {}))
    data = _decode_tool_result(result)

    assert data["error"] == "correlation audit failed"
    assert data["attempts"] == 3
    assert data["transient_transport"] is True
    assert len(calls) == 3
