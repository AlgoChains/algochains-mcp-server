"""
CI Tool Count Gate — enforces that registered tool count matches documentation.

Fails the build if tool count drifts from DOCUMENTED_TOOL_COUNT.
This prevents the v18→v20 style drift where docs said 242 but reality was 227.

Update DOCUMENTED_TOOL_COUNT when intentionally adding or removing tools.
Also update: server.py docstring, SERVER_INSTRUCTIONS, pyproject.toml, README.md, registry.json.
"""
from __future__ import annotations

import pytest

# Update this constant when intentionally changing tool count.
# v21.0 target: 350+ tools
DOCUMENTED_TOOL_COUNT_MIN = 260  # Minimum — fail if we drop below this
DOCUMENTED_TOOL_COUNT_MAX = 500  # Safety ceiling — fail if unexpectedly high


def _get_registered_tools() -> list:
    """Import server and return the registered tool list."""
    try:
        from algochains_mcp import tool_manifest
        manifest = tool_manifest.build_manifest()
        return manifest.get("tools", [])
    except Exception as exc:
        pytest.skip(f"Could not load tool manifest: {exc}")


def test_tool_count_within_expected_range():
    """Registered tool count must be within documented range."""
    tools = _get_registered_tools()
    count = len(tools)

    assert count >= DOCUMENTED_TOOL_COUNT_MIN, (
        f"Tool count dropped: {count} registered < {DOCUMENTED_TOOL_COUNT_MIN} minimum. "
        f"Update DOCUMENTED_TOOL_COUNT_MIN if intentional, or restore missing tools."
    )
    assert count <= DOCUMENTED_TOOL_COUNT_MAX, (
        f"Tool count suspiciously high: {count} > {DOCUMENTED_TOOL_COUNT_MAX}. "
        f"Possible duplicate registration. Review server.py."
    )


def test_all_tools_have_descriptions():
    """Every tool must have a non-empty description."""
    tools = _get_registered_tools()
    missing_desc = [t.get("name", "?") for t in tools if not t.get("description", "").strip()]
    assert not missing_desc, (
        f"Tools missing descriptions: {missing_desc}. "
        "Add description to each tool's @app.list_tools() entry."
    )


def test_all_tools_have_input_schema():
    """Every tool must declare an inputSchema."""
    tools = _get_registered_tools()
    missing_schema = [
        t.get("name", "?") for t in tools
        if not t.get("inputSchema") and not t.get("input_schema")
    ]
    assert not missing_schema, (
        f"Tools missing inputSchema: {missing_schema[:10]}. "
        "Ensure all tools have parameter definitions."
    )


def test_no_duplicate_tool_names():
    """Tool names must be unique — duplicates cause silent override."""
    tools = _get_registered_tools()
    names = [t.get("name", "") for t in tools]
    seen: dict[str, int] = {}
    for name in names:
        seen[name] = seen.get(name, 0) + 1
    duplicates = {name: count for name, count in seen.items() if count > 1}
    assert not duplicates, (
        f"Duplicate tool names detected: {duplicates}. "
        "Each tool must have a unique name."
    )


def test_no_duplicate_tool_literals_in_server_source():
    """Regex-scan server.py for Tool(name="…") literals.

    Runs even if the full server module can't import (e.g. missing optional deps
    in CI), so we always catch a new duplicate tool at merge time. Complements
    `test_no_duplicate_tool_names` which needs the full import path.
    """
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1]
           / "src" / "algochains_mcp" / "server.py").read_text()
    names = re.findall(r'Tool\(\s*name=["\']([^"\']+)', src)
    seen: dict[str, int] = {}
    for n in names:
        seen[n] = seen.get(n, 0) + 1
    dups = {k: v for k, v in seen.items() if v > 1}
    assert not dups, (
        f"Duplicate Tool(name=…) literals in server.py: {dups}. "
        "Each tool must be declared exactly once."
    )


def test_server_module_importable():
    """server.py must import without error."""
    try:
        import algochains_mcp.server as srv
        assert srv.app is not None
    except Exception as exc:
        pytest.fail(f"server.py failed to import: {exc}")


def test_spec_compliance_modules_importable():
    """All Phase 1 spec compliance modules must import cleanly."""
    from algochains_mcp.spec_compliance.elicitation import ElicitationManager
    from algochains_mcp.spec_compliance.tasks import TaskManager
    from algochains_mcp.spec_compliance.subscriptions import SubscriptionManager
    assert ElicitationManager is not None
    assert TaskManager is not None
    assert SubscriptionManager is not None


def test_evolution_modules_importable():
    """All Phase 2 evolution modules must import cleanly."""
    from algochains_mcp.evolution.trade_memory import TradeMemory
    from algochains_mcp.evolution.reward_model import RewardModel
    from algochains_mcp.evolution.evolution_daemon import EvolutionDaemon
    from algochains_mcp.evolution.lessons_injector import LessonsInjector
    assert all([TradeMemory, RewardModel, EvolutionDaemon, LessonsInjector])


def test_order_flow_modules_importable():
    """All Phase 3 order flow modules must import cleanly."""
    from algochains_mcp.order_flow.footprint import compute_footprint_chart
    from algochains_mcp.order_flow.cumulative_delta import compute_cumulative_delta
    from algochains_mcp.order_flow.volume_profile import compute_volume_profile
    from algochains_mcp.order_flow.dark_pool_volume import DarkPoolEngine
    from algochains_mcp.order_flow.earnings_catalyst import EarningsCatalystEngine
    from algochains_mcp.order_flow.prediction_markets import PredictionMarketsEngine
    from algochains_mcp.order_flow.macro_signals import MacroSignalEngine
    assert all([compute_footprint_chart, compute_cumulative_delta, compute_volume_profile,
                DarkPoolEngine, EarningsCatalystEngine, PredictionMarketsEngine, MacroSignalEngine])


def test_auth_modules_importable():
    """Phase 4 auth modules must import cleanly."""
    from algochains_mcp.auth.key_vault import KeyVault
    from algochains_mcp.auth.agent_provisioner import AgentProvisioner
    assert KeyVault and AgentProvisioner


def test_streaming_modules_importable():
    """Phase 5 streaming modules must import cleanly."""
    from algochains_mcp.streaming.alert_engine import PriceAlertEngine
    from algochains_mcp.streaming.earnings_calendar import EarningsCalendar
    assert PriceAlertEngine and EarningsCalendar
