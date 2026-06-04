"""
developer_tools.py — Developer-tier MCP tool surface and scope gating.

Developer API keys (ac_live_* / ac_test_*) see a curated subset of tools:
  - All public tools (read-only market data, marketplace browse, etc.)
  - Developer-specific tools (strategy publishing, backtest validation,
    data warehouse reads, Onyx search, validation gates)

Hard constraints enforced here and in http_bridge:
  - Danger tier ceiling: WRITE_LOCAL (tier 1) — no ORDER_EXEC or DESTRUCTIVE
  - No dynamic/meta escalation via execute_dynamic_tool to hidden tools
  - No owner-only tools (bot health, live positions, order placement, etc.)
  - No subscriber-only tools (copy-trade signals, fills, assignments)

The scope system mirrors subscriber_auth scopes but uses colon-namespaced
strings defined in the Supabase developer_api_keys.scopes column.
"""
from __future__ import annotations

# ─── Developer-tier allowlist ────────────────────────────────────────────────
# All tools here must be at danger tier 0 (READ_ONLY) or 1 (WRITE_LOCAL).
# Any tier-2+ tool added here MUST have an explicit review comment and test.

DEVELOPER_TOOLS: frozenset[str] = frozenset({
    # Market data and regime (public, READ_ONLY)
    "detect_market_regime",
    "get_macro_signals",
    "get_vix_term_structure",
    "get_earnings_catalyst",
    "get_latency_profile",

    # Marketplace discovery (public, READ_ONLY)
    "get_marketplace_listings",
    "browse_strategy_marketplace",
    "get_bot_card_data",
    "list_bot_research_attachments",
    "get_strategy_academic_citations",

    # Tool discovery (READ_ONLY) — developers may explore the surface
    "discover_tools",
    "get_tool_details",
    "mcp_tool_manifest",

    # Onyx AI semantic search (READ_ONLY)
    "onyx_search",
    "onyx_ask",

    # Validation and backtesting (READ_ONLY or WRITE_LOCAL — dry-run only)
    "get_validation_gates",
    "validate_strategy_metrics",
    "run_builder_backtest",
    "get_backtest_results",
    "get_monte_carlo_result",

    # Strategy publishing (WRITE_LOCAL — writes to marketplace pipeline, not broker)
    "submit_to_marketplace",

    # Historical data reads (READ_ONLY)
    "query_data_warehouse",
    "get_historical_bars",
    "get_tick_data_summary",

    # Factor model and volatility surface reads (READ_ONLY)
    "get_volatility_surface",
    "get_factor_model",

    # Regime and ML signal reads (READ_ONLY — no live bot context)
    "run_hmm_regime_detection",
    "get_signal_health_summary",
})

# ─── Scope requirements per tool ─────────────────────────────────────────────
# A tool with a scope requirement here can only be called when the resolved
# developer key has that scope in its `scopes` column. Tools with no entry
# here are accessible to any valid developer key.

DEVELOPER_TOOL_SCOPES: dict[str, str] = {
    "run_builder_backtest": "write:backtest",
    "get_backtest_results": "read:backtest",
    "get_monte_carlo_result": "read:backtest",
    "submit_to_marketplace": "publish:listing",
    "query_data_warehouse": "read:data_warehouse",
    "get_historical_bars": "read:market_data",
    "get_tick_data_summary": "read:market_data",
    "get_volatility_surface": "read:market_data",
    "get_factor_model": "read:market_data",
    "run_hmm_regime_detection": "read:signals",
    "get_signal_health_summary": "read:signals",
}

# ─── Hard-blocked tools (never allowed for developer tier) ───────────────────
# These are explicitly forbidden even if inadvertently added to DEVELOPER_TOOLS.
# The bridge checks this list AFTER the allowlist to catch misconfigurations.

DEVELOPER_BLOCKED_TOOLS: frozenset[str] = frozenset({
    # Owner execution paths
    "place_order",
    "cancel_order",
    "close_position",
    "flatten_position",
    "restart_trading_bot",
    "execute_dynamic_tool",  # Meta-tool that could escalate to hidden ORDER_EXEC tools

    # Live system state (owner-only)
    "get_bot_health",
    "get_live_bot_metrics",
    "get_all_bot_metrics",
    "get_system_heartbeat",
    "get_account",
    "get_positions",
    "get_orders",
    "portfolio_summary",

    # Marketplace autopilot (owner-only destructive writes)
    "run_marketplace_autopilot",
    "run_onyx_ingest",
    "archive_non_mnq_listings",

    # Tournament / live upload
    "numerai_upload_predictions",
    "numerai_dry_run_submit",

    # Subscriber copy-trade surface (separate auth path)
    "get_signal_stream",
    "get_my_pnl",
    "get_my_fills",
    "get_my_assignments",
    "report_fill",
    "heartbeat",
    "ack_signal",
})


def check_developer_tool_access(
    tool_name: str,
    developer_scopes: tuple[str, ...],
) -> tuple[bool, str | None]:
    """
    Check whether a developer key is allowed to call `tool_name`.

    Returns (allowed: bool, denial_reason: str | None).
    """
    # Hard block takes precedence over everything.
    if tool_name in DEVELOPER_BLOCKED_TOOLS:
        return False, "tool_not_available_for_developer_tier"

    # Must be in the developer allowlist.
    if tool_name not in DEVELOPER_TOOLS:
        return False, "tool_not_in_developer_allowlist"

    # Check scope if required.
    required_scope = DEVELOPER_TOOL_SCOPES.get(tool_name)
    if required_scope and required_scope not in developer_scopes:
        return False, f"missing_scope:{required_scope}"

    return True, None
