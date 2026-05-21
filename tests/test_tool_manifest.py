"""Tests for MCP tool manifest builder."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from algochains_mcp.tool_manifest import build_manifest, SCHEMA_VERSION
from algochains_mcp.cli_contract import filter_manifest_tools, command_contract_summary


def test_manifest_shape():
    m = build_manifest(
        tool_names=["place_order", "massive_call_api", "unknown_v99_tool"],
        tier1_names={"place_order", "massive_call_api"},
        tool_mode="smart",
    )
    assert m["schema_version"] == SCHEMA_VERSION
    assert m["tool_mode"] == "smart"
    assert m["total_tools"] == 3
    assert m["summary_by_status"]["full"] >= 2
    names = {t["name"]: t for t in m["tools"]}
    assert names["place_order"]["implementation_status"] == "full"
    assert names["place_order"]["tier1"] is True
    assert names["place_order"]["danger_label"] == "ORDER_EXEC"
    assert names["place_order"]["approval"]["canonical_arg"] == "confirm=true"
    assert names["place_order"]["transports"]["stdio_direct"] is True
    assert names["massive_call_api"]["implementation_status"] == "full"
    assert names["massive_call_api"]["danger_label"] == "READ_ONLY"
    assert names["unknown_v99_tool"]["implementation_status"] == "stub"
    assert names["unknown_v99_tool"]["tier_source"] == "default"


def test_skip_flag_reported(monkeypatch):
    monkeypatch.setenv("ALGOCHAINS_SKIP_MARKETPLACE_KEY_CHECK", "1")
    m = build_manifest(tool_names=["x"], tier1_names=set(), tool_mode="full")
    assert m["marketplace_key_check"] == "skipped"


def test_cli_contract_filters_tools_and_preserves_stub_status():
    m = build_manifest(
        tool_names=["get_positions", "deploy_strategy", "place_order"],
        tier1_names={"get_positions"},
        tool_mode="smart",
    )
    read_only = filter_manifest_tools(m, max_danger_tier=0)
    names = {tool["name"] for tool in read_only}
    summary = command_contract_summary(m)

    assert "get_positions" in names
    assert "place_order" not in names
    assert any(tool["name"] == "deploy_strategy" and tool["implementation_status"] == "stub" for tool in m["tools"])
    assert summary["schema_version"] == SCHEMA_VERSION
    assert summary["total_tools"] == 3


def test_live_manifest_has_policy_contract_fields():
    import algochains_mcp.server as srv

    m = build_manifest(
        tool_names=[t.name for t in srv.TOOLS],
        tier1_names=set(srv.TIER1_TOOL_NAMES),
        tool_mode="smart",
    )
    missing = []
    for tool in m["tools"]:
        for key in ("danger_tier", "danger_label", "tier_source", "approval", "transports", "visibility"):
            if key not in tool:
                missing.append((tool["name"], key))
    assert not missing


if __name__ == "__main__":
    test_manifest_shape()
    import os
    os.environ["ALGOCHAINS_SKIP_MARKETPLACE_KEY_CHECK"] = "1"
    m = build_manifest(tool_names=["x"], tier1_names=set(), tool_mode="full")
    assert m["marketplace_key_check"] == "skipped"
    os.environ.pop("ALGOCHAINS_SKIP_MARKETPLACE_KEY_CHECK", None)
    print("tool_manifest tests ok")
