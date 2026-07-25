"""ASSP Phase 5 AppSec sprint regression tests (2026-07-25)."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from algochains_mcp.subscriber_tools import report_fill


def _decode(result: list[Any]) -> dict[str, Any]:
    item = result[0]
    return json.loads(item.text if hasattr(item, "text") else str(item))


@pytest.fixture(autouse=True)
def _full_mode_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure direct handler gates are reachable regardless of test order."""
    monkeypatch.setenv("OWNER_API_TOKEN", "assp-owner-secret")
    monkeypatch.setenv("ALGOCHAINS_TOOL_MODE", "full")
    import algochains_mcp.server as srv
    from algochains_mcp.config import load_config

    monkeypatch.setattr(srv, "_config", load_config())
    from algochains_mcp.trading_guardrails import get_guardrails

    g = get_guardrails()
    g._loop_detector._call_log.clear()
    g._loop_detector._hash_counts.clear()
    g._cb.clear()


class TestSseFailClosed:
    def test_rejects_without_key_when_unauth_not_allowed(self, monkeypatch):
        import algochains_mcp.sse_server as sse_mod

        monkeypatch.setattr(sse_mod, "SSE_API_KEY", "")
        monkeypatch.delenv("ALGOCHAINS_SSE_ALLOW_UNAUTH", raising=False)
        monkeypatch.setattr(sse_mod, "SSE_HOST", "127.0.0.1")

        class _Req:
            headers: dict[str, str] = {}
            query_params: dict[str, str] = {}

        assert sse_mod._validate_api_key(_Req()) is False

    def test_allows_localhost_only_when_unauth_opt_in(self, monkeypatch):
        import algochains_mcp.sse_server as sse_mod

        monkeypatch.setattr(sse_mod, "SSE_API_KEY", "")
        monkeypatch.setenv("ALGOCHAINS_SSE_ALLOW_UNAUTH", "1")
        monkeypatch.setattr(sse_mod, "SSE_HOST", "127.0.0.1")

        class _Req:
            headers: dict[str, str] = {}
            query_params: dict[str, str] = {}

        assert sse_mod._validate_api_key(_Req()) is True

    def test_still_rejects_unauth_on_non_localhost_bind(self, monkeypatch):
        import algochains_mcp.sse_server as sse_mod

        monkeypatch.delenv("ALGOCHAINS_SSE_KEY", raising=False)
        monkeypatch.setenv("ALGOCHAINS_SSE_ALLOW_UNAUTH", "1")
        monkeypatch.setattr(sse_mod, "SSE_HOST", "0.0.0.0")
        monkeypatch.setattr(sse_mod, "SSE_API_KEY", "")

        class _Req:
            headers: dict[str, str] = {}
            query_params: dict[str, str] = {}

        assert sse_mod._validate_api_key(_Req()) is False


class TestRunMcptPipelineDryRunDefault:
    @patch("subprocess.run")
    def test_defaults_dry_run_true(self, mock_run, monkeypatch, tmp_path):
        import algochains_mcp.server as srv

        monkeypatch.setattr(srv, "_default_control_tower", lambda: str(tmp_path))
        mock_run.return_value = MagicMock(returncode=0, stdout='{"ok": true}', stderr="")

        result = asyncio.run(srv.call_tool("run_mcpt_pipeline", {}))
        payload = _decode(result)

        assert "error" not in payload or "owner_token" not in str(payload.get("error", ""))
        cmd = mock_run.call_args[0][0]
        assert "--dry-run" in cmd

    @patch("subprocess.run")
    def test_live_run_requires_owner_token(self, mock_run, monkeypatch, tmp_path):
        import algochains_mcp.server as srv

        monkeypatch.setattr(srv, "_default_control_tower", lambda: str(tmp_path))

        result = asyncio.run(
            srv.call_tool("run_mcpt_pipeline", {"dry_run": False})
        )
        payload = _decode(result)

        assert payload.get("assp_rule") == "AC-MCP-008"
        assert "owner_token" in payload.get("error", "")
        mock_run.assert_not_called()


class TestDispatchTowerJobOwnerGate:
    def test_rejects_without_owner_token(self):
        import algochains_mcp.server as srv

        result = asyncio.run(
            srv.call_tool("dispatch_tower_job", {"job_type": "ml_retrain"})
        )
        payload = _decode(result)

        assert payload.get("assp_rule") == "AC-MCP-007"
        assert "owner_token" in payload.get("error", "")


class TestGetAllBotOpsStatusTier1:
    def test_not_in_tier1(self):
        import algochains_mcp.server as srv

        tier1 = {tool.name for tool in srv.TOOLS_TIER1}
        assert "get_all_bot_ops_status" not in tier1

    def test_redacts_without_owner_token(self):
        import algochains_mcp.server as srv

        result = asyncio.run(srv.call_tool("get_all_bot_ops_status", {}))
        payload = _decode(result)

        assert payload.get("redacted") is True
        assert payload.get("assp_rule") == "AC-MCP-010"


class TestRunOnyxIngestOwnerGate:
    def test_not_in_tier1(self):
        import algochains_mcp.server as srv

        tier1 = {tool.name for tool in srv.TOOLS_TIER1}
        assert "run_onyx_ingest" not in tier1

    def test_rejects_without_owner_token(self):
        import algochains_mcp.server as srv

        result = asyncio.run(srv.call_tool("run_onyx_ingest", {}))
        payload = _decode(result)

        assert payload.get("assp_rule") == "AC-MCP-009"
        assert "owner_token" in payload.get("error", "")


class TestConnectOnyxDocsOwnerGate:
    def test_rejects_without_owner_token(self):
        import algochains_mcp.server as srv

        result = asyncio.run(
            srv.call_tool(
                "connect_onyx_docs",
                {"doc_paths": ["/tmp/x.md"], "doc_type": "general"},
            )
        )
        payload = _decode(result)

        assert payload.get("assp_rule") == "AC-MCP-009"
        assert "owner_token" in payload.get("error", "")


class TestReportFillSignalIdRequired:
    def test_subscriber_cannot_forge_entry_without_signal_id(self):
        sb = MagicMock()
        with patch("algochains_mcp.subscriber_tools._service_client", return_value=sb):
            out = report_fill(
                "sub-1",
                bot="MNQ",
                symbol="MNQ",
                side="BUY",
                qty=1,
                fill_kind="entry",
                daemon_authorized=False,
            )
        assert out.get("error") == "signal_id_required"
