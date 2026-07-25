"""ASSP M1 — mcp-server audit emit regression."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from algochains_mcp.assp_mcp_audit import emit_mcp_audit_denial


@pytest.fixture
def audit_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    ct = tmp_path / "control-tower"
    ct.mkdir()
    ct_audit = ct / "artifacts" / "assp" / "intent_bus" / "mcp_audit.jsonl"
    home_audit = tmp_path / "home" / "assp_mcp_audit.jsonl"
    monkeypatch.setenv("ALGOCHAINS_CONTROL_TOWER", str(ct))
    monkeypatch.setattr(
        "algochains_mcp.assp_mcp_audit._audit_paths",
        lambda: (ct_audit, home_audit),
    )
    import algochains_mcp.assp_mcp_audit as mod

    mod._import_attempted = True
    mod._delegate = None
    return ct_audit, home_audit


def test_emit_denial_required_fields(audit_paths):
    ct_audit, home_audit = audit_paths
    evt = emit_mcp_audit_denial(
        rule_id="AC-MCP-007",
        tool_name="dispatch_tower_job",
        deny_reason="owner_token_required",
    )
    assert evt is not None
    assert evt["sourcetype"] == "algochains:mcp_audit"
    assert evt["rule_id"] == "AC-MCP-007"
    assert evt["intent_allowed"] is False
    assert evt["correlation_id"]
    assert evt["authority"] == "agent_memory"
    assert evt["provenance"] == "direct_mirror"
    assert ct_audit.exists() or home_audit.exists()
    path = ct_audit if ct_audit.exists() else home_audit
    row = json.loads(path.read_text().strip().splitlines()[-1])
    assert row["event"] == "owner_gate_denied"


def test_emit_never_raises_on_bad_paths(monkeypatch):
    monkeypatch.setattr(
        "algochains_mcp.assp_mcp_audit._append_jsonl",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("fail")),
    )
    import algochains_mcp.assp_mcp_audit as mod

    mod._import_attempted = True
    mod._delegate = None
    assert emit_mcp_audit_denial(rule_id="AC-MCP-010", tool_name="get_all_bot_ops_status") is None
