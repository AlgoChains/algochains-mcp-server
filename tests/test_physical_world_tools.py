from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


PHYSICAL_TOOLS = {
    "get_physical_event_sources",
    "map_physical_event_assets",
    "score_physical_event_alpha",
    "get_sonia_air_heartbeat",
}


@pytest.fixture(autouse=True)
def _full_mode(monkeypatch):
    """Exercise the handlers directly: smart mode is now an execution boundary, so
    force a full-mode config object (call_tool caches the module-global `_config`,
    which a prior smart-mode call in the suite can otherwise pin to smart)."""
    monkeypatch.setenv("ALGOCHAINS_TOOL_MODE", "full")
    import algochains_mcp.server as srv
    from algochains_mcp.config import load_config

    monkeypatch.setattr(srv, "_config", load_config())


def _text(result):
    item = result[0]
    return item.text if hasattr(item, "text") else str(item)


def test_physical_world_tools_registered_once():
    import algochains_mcp.server as srv

    names = [tool.name for tool in srv.TOOLS_ANNOTATED]
    for tool in PHYSICAL_TOOLS:
        assert names.count(tool) == 1
        assert tool in srv._HANDLER_REGISTRY


def test_physical_world_tools_read_only_and_rate_limited():
    from algochains_mcp.middleware import get_tool_category
    from algochains_mcp.tool_danger_tiers import TIER_READ_ONLY, get_tool_tier

    for tool in PHYSICAL_TOOLS:
        assert get_tool_tier(tool) == TIER_READ_ONLY
        assert get_tool_category(tool) == "v17_physical_events"


def test_physical_world_manifest_partial():
    import algochains_mcp.server as srv
    from algochains_mcp.tool_manifest import build_manifest

    manifest = build_manifest(
        tool_names=[tool.name for tool in srv.TOOLS_ANNOTATED],
        tier1_names=set(srv.TIER1_TOOL_NAMES),
        tool_mode="full",
    )
    by_name = {entry["name"]: entry for entry in manifest["tools"]}
    for tool in PHYSICAL_TOOLS:
        assert by_name[tool]["implementation_status"] == "partial"


def test_score_physical_event_alpha_is_advisory():
    import algochains_mcp.server as srv

    async def _call():
        return await srv.call_tool(
            "score_physical_event_alpha",
            {
                "symbol": "CL",
                "event_type": "energy_inventory",
                "severity": 8,
                "freshness_minutes": 15,
                "liquidity_proxy": 250000,
            },
        )

    data = json.loads(_text(asyncio.run(_call())))
    assert data["status"] == "ok"
    assert data["mapped_to_asset"] is True
    assert data["broker_truth"] is False
    assert data["decision_use"] == "research_queue_only"


def test_sonia_air_heartbeat_fallback_when_missing(tmp_path, monkeypatch):
    import algochains_mcp.server as srv

    monkeypatch.setenv("ALGOCHAINS_CONTROL_TOWER", str(tmp_path))

    async def _call():
        return await srv.call_tool("get_sonia_air_heartbeat", {})

    data = json.loads(_text(asyncio.run(_call())))
    assert data["status"] == "offline_or_not_bootstrapped"
    assert data["node_id"] == "sonia_air"
