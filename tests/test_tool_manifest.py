"""Tests for MCP tool manifest builder."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from algochains_mcp.tool_manifest import build_manifest, SCHEMA_VERSION


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
    assert names["massive_call_api"]["implementation_status"] == "full"
    assert names["unknown_v99_tool"]["implementation_status"] == "stub"


def test_skip_flag_reported(monkeypatch):
    monkeypatch.setenv("ALGOCHAINS_SKIP_MARKETPLACE_KEY_CHECK", "1")
    m = build_manifest(tool_names=["x"], tier1_names=set(), tool_mode="full")
    assert m["marketplace_key_check"] == "skipped"


if __name__ == "__main__":
    test_manifest_shape()
    import os
    os.environ["ALGOCHAINS_SKIP_MARKETPLACE_KEY_CHECK"] = "1"
    m = build_manifest(tool_names=["x"], tier1_names=set(), tool_mode="full")
    assert m["marketplace_key_check"] == "skipped"
    os.environ.pop("ALGOCHAINS_SKIP_MARKETPLACE_KEY_CHECK", None)
    print("tool_manifest tests ok")
