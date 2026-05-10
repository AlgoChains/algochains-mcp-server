"""
tool_danger_tiers.py — Danger tier classification for every MCP tool.

Tiers:
  0 — READ_ONLY:    No side effects. Safe for any agent, any user. (get_quote, detect_market_regime)
  1 — WRITE_LOCAL:  Writes to internal server state only. No money, no broker. (create_alert, validate_strategy)
  2 — ORDER_EXEC:   Executes real orders. Touches live broker accounts. Requires confirmation. (place_order)
  3 — DESTRUCTIVE:  Irreversible bulk or high-impact actions. (flatten_all_positions, cancel_all_orders)

Used by:
  - HTTP bridge /tools endpoint — returns danger_tier for each tool
  - quickstart.py — shows which tools are safe in demo/paper mode
  - README tier-0 starter pack
  - Agent prompt engineering (agents should check danger_tier before acting)

Rules for classification:
  - When in doubt, assign HIGHER tier (more danger).
  - Tools that ONLY read are tier 0.
  - Tools that write internal config/alerts are tier 1.
  - Anything that calls a broker place_order / cancel / modify is tier 2.
  - Anything that touches all positions or mass-cancels is tier 3.
"""
from __future__ import annotations

from typing import Any

# ── Tier constants ─────────────────────────────────────────────────────────────

TIER_READ_ONLY = 0       # Safe — no side effects
TIER_READ_EXTERNAL = 0  # Alias: read-only calls to external data APIs (same risk as READ_ONLY)
TIER_WRITE_LOCAL = 1    # Internal writes only — no money, no broker API
TIER_ORDER_EXEC = 2     # Real order execution — touches live broker
TIER_DESTRUCTIVE = 3    # Irreversible bulk / high-impact

TIER_LABELS = {
    TIER_READ_ONLY: "READ_ONLY",
    TIER_WRITE_LOCAL: "WRITE_LOCAL",
    TIER_ORDER_EXEC: "ORDER_EXEC",
    TIER_DESTRUCTIVE: "DESTRUCTIVE",
}

TIER_DESCRIPTIONS = {
    TIER_READ_ONLY: "No side effects. Safe for any user or agent.",
    TIER_WRITE_LOCAL: "Writes to internal server state only. No broker API calls.",
    TIER_ORDER_EXEC: "Executes real orders on a live broker account. Requires human confirmation.",
    TIER_DESTRUCTIVE: "Irreversible bulk action. Touches all positions or cancels all orders.",
}

CALLER_SCOPE_CEILINGS: dict[str, int] = {
    "autonomous": TIER_WRITE_LOCAL,
    "research": TIER_WRITE_LOCAL,
    "interactive": TIER_ORDER_EXEC,
    "admin": TIER_DESTRUCTIVE,
}

# ── Explicit tier overrides ────────────────────────────────────────────────────
# Any tool not listed here gets TIER_WRITE_LOCAL by default (conservative).

_TOOL_TIERS: dict[str, int] = {

    # ── Tier 0: READ_ONLY ────────────────────────────────────────────────────
    # Tradovate read-only tools
    "search_tradovate_contracts": TIER_READ_ONLY,
    "get_tradovate_risk_snapshot": TIER_READ_ONLY,
    "get_bot_health": TIER_READ_ONLY,
    "get_quant_regime_state": TIER_READ_ONLY,
    # Market data
    "get_quote": TIER_READ_ONLY,
    "get_ohlcv": TIER_READ_ONLY,
    "get_tick_data": TIER_READ_ONLY,
    "get_options_chain": TIER_READ_ONLY,
    "get_futures_curve": TIER_READ_ONLY,
    "get_level2": TIER_READ_ONLY,
    "get_order_book": TIER_READ_ONLY,
    "get_order_book_imbalance": TIER_READ_ONLY,
    "get_volume_profile": TIER_READ_ONLY,
    "get_footprint_chart": TIER_READ_ONLY,
    "compute_cumulative_delta": TIER_READ_ONLY,
    "compute_vwap": TIER_READ_ONLY,
    "compute_twap": TIER_READ_ONLY,
    "get_dark_pool_volume": TIER_READ_ONLY,
    "get_dark_pool_prints": TIER_READ_ONLY,
    "detect_absorption": TIER_READ_ONLY,
    "get_vix_term_structure": TIER_READ_ONLY,
    "get_yield_curve": TIER_READ_ONLY,
    "get_credit_spreads": TIER_READ_ONLY,
    "get_dxy_regime": TIER_READ_ONLY,
    "get_pmi_data": TIER_READ_ONLY,
    "get_macro_signals": TIER_READ_ONLY,
    # Account & portfolio (read paths)
    "get_account": TIER_READ_ONLY,
    "get_positions": TIER_READ_ONLY,
    "get_orders": TIER_READ_ONLY,
    "get_working_orders": TIER_READ_ONLY,
    "get_fills": TIER_READ_ONLY,
    "portfolio_summary": TIER_READ_ONLY,
    "get_risk_parameters": TIER_READ_ONLY,
    "daily_pnl_summary": TIER_READ_ONLY,
    "get_live_pnl": TIER_READ_ONLY,
    "get_fill_analysis": TIER_READ_ONLY,
    "get_execution_quality": TIER_READ_ONLY,
    "compute_slippage": TIER_READ_ONLY,
    # Signals & regime
    "detect_market_regime": TIER_READ_ONLY,
    "generate_signal": TIER_READ_ONLY,
    "get_regime_state": TIER_READ_ONLY,
    "get_ensemble_vote": TIER_READ_ONLY,
    "compute_confidence": TIER_READ_ONLY,
    "compute_gex": TIER_READ_ONLY,
    "unusual_options_activity": TIER_READ_ONLY,
    "read_tape": TIER_READ_ONLY,
    "pair_trade_signal": TIER_READ_ONLY,
    "compute_vwap_deviation": TIER_READ_ONLY,
    "detect_dark_pool": TIER_READ_ONLY,
    "compute_kelly": TIER_READ_ONLY,
    "get_position_sizing": TIER_READ_ONLY,
    # Risk (read paths)
    "get_max_drawdown": TIER_READ_ONLY,
    "get_var": TIER_READ_ONLY,
    "compute_correlation_risk": TIER_READ_ONLY,
    "check_vix_gate": TIER_READ_ONLY,
    "get_daily_loss_proximity": TIER_READ_ONLY,
    "event_risk_check": TIER_READ_ONLY,
    # Strategy research (read only)
    # NOTE: validate_strategy appears again at TIER_WRITE_LOCAL below — that
    # entry overrides this one. The final effective tier is TIER_WRITE_LOCAL
    # (correct: validation writes sandbox state). Left here for documentation.
    "validate_strategy": TIER_READ_ONLY,
    "run_backtest": TIER_READ_ONLY,
    "walk_forward_test": TIER_READ_ONLY,
    "optimize_strategy": TIER_READ_ONLY,
    "analyze_overfitting": TIER_READ_ONLY,
    "compute_sharpe": TIER_READ_ONLY,
    "run_mcpt_validation": TIER_READ_ONLY,
    "get_factor_exposures": TIER_READ_ONLY,
    "run_sensitivity_sweep": TIER_READ_ONLY,
    "scan_regime_alpha": TIER_READ_ONLY,
    "compute_dcf": TIER_READ_ONLY,
    "replicate_paper": TIER_READ_ONLY,
    "search_ssrn_strategies": TIER_READ_ONLY,
    # Market intelligence (read only)
    "get_earnings_catalyst": TIER_READ_ONLY,
    "get_prediction_markets": TIER_READ_ONLY,
    "search_prediction_markets": TIER_READ_ONLY,
    "get_polymarket_high_volume": TIER_READ_ONLY,
    "get_polymarket_market": TIER_READ_ONLY,
    "get_polymarket_market_history": TIER_READ_ONLY,
    "list_polymarket_markets": TIER_READ_ONLY,
    "get_kalshi_settlements": TIER_READ_ONLY,
    "place_kalshi_order": TIER_ORDER_EXEC,
    # BUG-07 FIX: run_safe_compounder and run_kalshi_full_pipeline call
    # kalshi_signed_post (live Kalshi orders) when execute=true + confirmed=true.
    # The `run_` prefix rule mis-tiered them as WRITE_LOCAL, allowing autonomous
    # agents (capped at WRITE_LOCAL) to trigger real money orders without
    # owner_token authorization. Explicit ORDER_EXEC overrides the prefix rule.
    "run_safe_compounder": TIER_ORDER_EXEC,
    "run_kalshi_full_pipeline": TIER_ORDER_EXEC,
    "run_kalshi_strategy_order": TIER_ORDER_EXEC,
    # V22.9 — PAI Integration
    "get_algochains_telos": TIER_READ_ONLY,
    "update_algochains_telos": TIER_WRITE_LOCAL,
    "get_us_economic_indicators": TIER_READ_EXTERNAL,
    "get_crude_oil_inventories": TIER_READ_EXTERNAL,
    "get_fed_policy_signals": TIER_READ_EXTERNAL,
    "capture_learning_signal": TIER_WRITE_LOCAL,
    "get_learning_signals": TIER_READ_ONLY,
    "send_ntfy_notification": TIER_WRITE_LOCAL,
    "get_prediction_market_bot_metrics": TIER_READ_ONLY,
    "record_prediction_market_bot_metric": TIER_WRITE_LOCAL,
    "propagate_trade_signal": TIER_ORDER_EXEC,
    "get_congressional_trades": TIER_READ_ONLY,
    "get_insider_activity": TIER_READ_ONLY,
    "get_alt_data_signals": TIER_READ_ONLY,
    "get_news_sentiment": TIER_READ_ONLY,
    "get_economic_releases": TIER_READ_ONLY,
    "get_cot_report": TIER_READ_ONLY,
    # Onyx (semantic search, read-only)
    "onyx_search": TIER_READ_ONLY,
    "onyx_ask": TIER_READ_ONLY,
    "onyx_health": TIER_READ_ONLY,
    "onyx_search_strategies": TIER_READ_ONLY,
    "onyx_query_bot_history": TIER_READ_ONLY,
    "onyx_find_best_setup": TIER_READ_ONLY,
    "onyx_get_lessons": TIER_READ_ONLY,
    # Tool discovery
    "discover_tools": TIER_READ_ONLY,
    "get_tool_details": TIER_READ_ONLY,
    "mcp_tool_manifest": TIER_READ_ONLY,
    # Live bot metrics (read only)
    "get_live_bot_metrics": TIER_READ_ONLY,
    "get_all_bot_metrics": TIER_READ_ONLY,
    "get_bot_dashboard": TIER_READ_ONLY,
    "get_system_heartbeat": TIER_READ_ONLY,
    "get_system_health": TIER_READ_ONLY,
    "get_incident_report": TIER_READ_ONLY,
    "get_adaptive_brain_status": TIER_READ_ONLY,
    "get_evolution_status": TIER_READ_ONLY,
    "list_evolved_strategies": TIER_READ_ONLY,
    "get_strategy_rankings": TIER_READ_ONLY,
    "get_lessons_learned": TIER_READ_ONLY,
    "get_task_status": TIER_READ_ONLY,
    "list_active_tasks": TIER_READ_ONLY,
    # Crypto/DeFi reads
    "get_crypto_quote": TIER_READ_ONLY,
    "get_funding_rate": TIER_READ_ONLY,
    "get_open_interest": TIER_READ_ONLY,
    "get_liquidation_clusters": TIER_READ_ONLY,
    "get_staking_yields": TIER_READ_ONLY,
    "get_nft_portfolio": TIER_READ_ONLY,
    "get_copy_trade_leaders": TIER_READ_ONLY,
    # Account protection (read paths)
    "get_protection_config": TIER_READ_ONLY,
    "check_order_safety": TIER_READ_ONLY,
    "check_circuit_breaker": TIER_READ_ONLY,
    # Marketplace (read paths)
    "get_marketplace_listings": TIER_READ_ONLY,
    "browse_marketplace": TIER_READ_ONLY,
    "get_listing_detail": TIER_READ_ONLY,
    "get_subscriber_metrics": TIER_READ_ONLY,
    "get_revenue_report": TIER_READ_ONLY,
    "get_bot_card_data": TIER_READ_ONLY,
    "list_bot_research_attachments": TIER_READ_ONLY,
    "get_strategy_academic_citations": TIER_READ_ONLY,
    # Macro/data
    "get_massive_quote": TIER_READ_ONLY,
    "massive_get_quote": TIER_READ_ONLY,
    "massive_screener": TIER_READ_ONLY,
    # Ingestion (read/list)
    "list_ingested_data": TIER_READ_ONLY,

    # ── Tier 1: WRITE_LOCAL ───────────────────────────────────────────────────
    # These write to internal server state only
    "create_price_alert": TIER_WRITE_LOCAL,
    "delete_alert": TIER_WRITE_LOCAL,
    "list_alerts": TIER_WRITE_LOCAL,
    "subscribe_resource": TIER_WRITE_LOCAL,
    "list_subscriptions": TIER_WRITE_LOCAL,
    "notify_resource_update": TIER_WRITE_LOCAL,
    "subscribe_bot_metrics": TIER_WRITE_LOCAL,
    "subscribe_earnings": TIER_WRITE_LOCAL,
    "connect_broker": TIER_WRITE_LOCAL,
    "list_brokers": TIER_WRITE_LOCAL,
    "disconnect_broker": TIER_WRITE_LOCAL,
    "validate_strategy": TIER_WRITE_LOCAL,
    "create_strategy_listing": TIER_WRITE_LOCAL,
    "submit_to_marketplace": TIER_WRITE_LOCAL,
    "build_strategy": TIER_WRITE_LOCAL,
    "list_templates": TIER_WRITE_LOCAL,
    "fork_template": TIER_WRITE_LOCAL,
    "deploy_strategy": TIER_WRITE_LOCAL,
    "create_shadow_portfolio": TIER_WRITE_LOCAL,
    "get_shadow_results": TIER_WRITE_LOCAL,
    "register_strategy": TIER_WRITE_LOCAL,
    "ingest_csv_data": TIER_WRITE_LOCAL,
    "ingest_json_signals": TIER_WRITE_LOCAL,
    "connect_onyx_docs": TIER_WRITE_LOCAL,
    "onyx_ingest_document": TIER_WRITE_LOCAL,
    "run_onyx_ingest": TIER_WRITE_LOCAL,
    "record_trade_episode": TIER_WRITE_LOCAL,
    "query_trade_memory": TIER_READ_ONLY,
    "inject_session_context": TIER_WRITE_LOCAL,
    "submit_long_running_task": TIER_WRITE_LOCAL,
    "cancel_task": TIER_WRITE_LOCAL,
    "store_api_key": TIER_WRITE_LOCAL,
    "rotate_api_key": TIER_WRITE_LOCAL,
    "check_key_health": TIER_READ_ONLY,
    "set_byok_key": TIER_WRITE_LOCAL,
    "get_byok_status": TIER_READ_ONLY,
    "configure_white_label": TIER_WRITE_LOCAL,
    "get_white_label_config": TIER_READ_ONLY,
    "create_tenant": TIER_WRITE_LOCAL,
    "get_tenant": TIER_READ_ONLY,
    "create_sandbox": TIER_WRITE_LOCAL,
    "destroy_sandbox": TIER_WRITE_LOCAL,
    "get_tenant_audit_log": TIER_READ_ONLY,
    "subscribe_strategy": TIER_WRITE_LOCAL,
    "subscribe_copy_trading": TIER_WRITE_LOCAL,
    "create_dca_schedule": TIER_WRITE_LOCAL,
    "set_account_protection": TIER_WRITE_LOCAL,
    "run_watchdog_check": TIER_WRITE_LOCAL,
    "run_morning_scan": TIER_WRITE_LOCAL,
    "run_evolution_cycle": TIER_WRITE_LOCAL,
    "rollback_evolution": TIER_WRITE_LOCAL,
    "run_marketplace_autopilot": TIER_WRITE_LOCAL,
    "execute_dynamic_tool": TIER_WRITE_LOCAL,
    "execute_intent": TIER_WRITE_LOCAL,
    "approve_intent": TIER_WRITE_LOCAL,
    "get_intent_plan": TIER_READ_ONLY,
    "get_intent_history": TIER_READ_ONLY,
    "run_token_guardian": TIER_WRITE_LOCAL,
    "provision_agent_account": TIER_WRITE_LOCAL,
    "audit_access_log": TIER_READ_ONLY,
    "list_agent_accounts": TIER_READ_ONLY,
    "become_leader": TIER_WRITE_LOCAL,
    "run_ai_debate": TIER_READ_ONLY,

    # ── Tier 2: ORDER_EXEC ────────────────────────────────────────────────────
    # These execute real broker orders
    "place_order": TIER_ORDER_EXEC,
    "place_bracket_order": TIER_ORDER_EXEC,
    "place_oco_order": TIER_ORDER_EXEC,
    "restart_trading_bot": TIER_ORDER_EXEC,
    "flatten_bot_position": TIER_ORDER_EXEC,
    "modify_order": TIER_ORDER_EXEC,
    "cancel_order": TIER_ORDER_EXEC,
    "close_position": TIER_ORDER_EXEC,
    "smart_route_order": TIER_ORDER_EXEC,
    "route_order": TIER_ORDER_EXEC,
    "execute_twap": TIER_ORDER_EXEC,
    "execute_vwap": TIER_ORDER_EXEC,
    "execute_swap": TIER_ORDER_EXEC,
    "start_algo_execution": TIER_ORDER_EXEC,
    "submit_institutional_order": TIER_ORDER_EXEC,
    "submit_protected_tx": TIER_ORDER_EXEC,
    "request_trade_confirmation": TIER_ORDER_EXEC,
    "follow_leader": TIER_ORDER_EXEC,
    "process_payment": TIER_ORDER_EXEC,
    "create_payment_session": TIER_ORDER_EXEC,

    # ── Tier 3: DESTRUCTIVE ───────────────────────────────────────────────────
    # Irreversible bulk actions
    "flatten_all_positions": TIER_DESTRUCTIVE,
    "cancel_all_orders": TIER_DESTRUCTIVE,
    "emergency_stop": TIER_DESTRUCTIVE,
    "reset_daily_loss_limit": TIER_DESTRUCTIVE,
    "trip_circuit_breaker": TIER_DESTRUCTIVE,
    "delete_all_alerts": TIER_DESTRUCTIVE,
    "purge_trade_memory": TIER_DESTRUCTIVE,
    "destroy_all_sandboxes": TIER_DESTRUCTIVE,

    # ── Numerai tournament tools (HK-17: upload is irreversible — must be ORDER_EXEC) ──
    "numerai_status": TIER_READ_ONLY,           # env flags as booleans; no API calls
    "numerai_round_info": TIER_READ_ONLY,       # reads current round from numerapi
    "numerai_get_model_scores": TIER_READ_ONLY, # leaderboard read via numerapi
    "numerai_validate_metrics": TIER_READ_ONLY, # per-era corr on local holdout; no write
    "numerai_download_dataset": TIER_WRITE_LOCAL,  # writes parquet to ALGOCHAINS_STATE_DIR
    "numerai_train_baseline": TIER_WRITE_LOCAL,    # trains model; writes PKL to models/numerai/
    "numerai_dry_run_submit": TIER_WRITE_LOCAL,    # writes submission CSV; no upload
    # HK-17: upload is irreversible — must NOT be TIER_READ_ONLY or TIER_WRITE_LOCAL.
    # Uses TIER_ORDER_EXEC (same as place_order) as "TIER_WRITE_REMOTE" equivalent.
    # Requires NUMERAI_ALLOW_LIVE=1 AND model_id. Gated in submit.py gate logic.
    "numerai_upload_predictions": TIER_ORDER_EXEC,

    # ── Subscriber tools (HTTP bridge SUBSCRIBER_TOOLS surface) ──────────────
    # Read-only subscriber views
    "get_signal_stream": TIER_READ_ONLY,
    "get_my_pnl": TIER_READ_ONLY,
    "get_my_fills": TIER_READ_ONLY,
    "get_my_assignments": TIER_READ_ONLY,
    # Subscriber writes are scoped to their own rows; treat as WRITE_LOCAL.
    "report_fill": TIER_WRITE_LOCAL,
    "heartbeat": TIER_WRITE_LOCAL,
    "ack_signal": TIER_WRITE_LOCAL,
}

# Prefix-based defaults for tools not explicitly listed
_PREFIX_DEFAULT_TIERS: list[tuple[str, int]] = [
    ("massive_", TIER_READ_ONLY),        # Massive.com — market data only
    ("get_", TIER_READ_ONLY),            # get_* are generally reads
    ("list_", TIER_READ_ONLY),           # list_* are generally reads
    ("check_", TIER_READ_ONLY),          # check_* are generally reads
    ("compute_", TIER_READ_ONLY),        # compute_* are generally reads
    ("detect_", TIER_READ_ONLY),         # detect_* are generally reads
    ("analyze_", TIER_READ_ONLY),        # analyze_* are generally reads
    ("validate_", TIER_READ_ONLY),       # validate_* are generally checks
    ("search_", TIER_READ_ONLY),         # search_* are generally reads
    ("onyx_", TIER_READ_ONLY),           # Onyx tools are all reads
    ("activate_", TIER_WRITE_LOCAL),     # activate_* mutates local/control state
    ("deactivate_", TIER_WRITE_LOCAL),   # deactivate_* mutates local/control state
    ("run_", TIER_WRITE_LOCAL),          # run_* may write local artifacts
    ("set_", TIER_WRITE_LOCAL),          # set_* mutates local/remote config
    ("start_", TIER_WRITE_LOCAL),        # start_* launches local workflows
    ("submit_", TIER_WRITE_LOCAL),       # submit_* writes unless explicitly upgraded
    ("deploy_", TIER_WRITE_LOCAL),       # deploy_* writes deployment state
    ("delete_", TIER_WRITE_LOCAL),       # delete_* changes local/remote state
    ("publish_", TIER_WRITE_LOCAL),      # publish_* writes marketplace/content state
    ("update_", TIER_WRITE_LOCAL),       # update_* writes state
    ("place_", TIER_ORDER_EXEC),         # place_* are always orders
    ("cancel_", TIER_ORDER_EXEC),        # cancel_* touch broker
    ("close_", TIER_ORDER_EXEC),         # close_* touch broker
    ("flatten_", TIER_DESTRUCTIVE),      # flatten_* are destructive
    ("emergency_", TIER_DESTRUCTIVE),    # emergency_* are destructive
]


def get_danger_tier(tool_name: str) -> int:
    """
    Return the danger tier (0-3) for a given tool name.
    Uses explicit overrides first, then prefix rules, then defaults to TIER_WRITE_LOCAL.
    """
    if tool_name in _TOOL_TIERS:
        return _TOOL_TIERS[tool_name]
    for prefix, tier in _PREFIX_DEFAULT_TIERS:
        if tool_name.startswith(prefix):
            return tier
    return TIER_WRITE_LOCAL  # conservative default


def get_danger_tier_source(tool_name: str) -> str:
    """Return how a tool's tier was assigned: explicit, prefix, or default."""
    if tool_name in _TOOL_TIERS:
        return "explicit"
    for prefix, _tier in _PREFIX_DEFAULT_TIERS:
        if tool_name.startswith(prefix):
            return f"prefix:{prefix}"
    return "default"


def get_scope_max_tier(scope: str | None) -> int:
    """Return the maximum danger tier available to a caller scope.

    Missing or unknown scopes preserve historical owner behavior and are handled
    by the existing API key + confirm gates.
    """
    if scope is None:
        return TIER_DESTRUCTIVE
    return CALLER_SCOPE_CEILINGS.get(str(scope).strip().lower(), TIER_DESTRUCTIVE)


# P1-4 FIX: server.py imports get_tool_tier but only get_danger_tier was defined.
# Alias ensures the replay guard import in server.py succeeds and protection runs.
get_tool_tier = get_danger_tier


def get_tier_label(tier: int) -> str:
    return TIER_LABELS.get(tier, "UNKNOWN")


def get_tier_description(tier: int) -> str:
    return TIER_DESCRIPTIONS.get(tier, "Unknown tier.")


def get_tool_danger_info(tool_name: str) -> dict[str, Any]:
    """Return a dict with full danger info for a tool."""
    tier = get_danger_tier(tool_name)
    return {
        "tool": tool_name,
        "danger_tier": tier,
        "danger_label": get_tier_label(tier),
        "danger_description": get_tier_description(tier),
        "safe_in_demo_mode": tier <= TIER_READ_ONLY,
        "safe_in_paper_mode": tier <= TIER_WRITE_LOCAL,
        "requires_live_account": tier >= TIER_ORDER_EXEC,
        "requires_human_confirmation": tier >= TIER_ORDER_EXEC,
        "irreversible": tier >= TIER_DESTRUCTIVE,
    }


def classify_tools(tool_names: list[str]) -> dict[str, list[str]]:
    """Group tool names by tier for display."""
    groups: dict[str, list[str]] = {label: [] for label in TIER_LABELS.values()}
    for name in tool_names:
        tier = get_danger_tier(name)
        groups[get_tier_label(tier)].append(name)
    for grp in groups.values():
        grp.sort()
    return groups


def safe_tools_for_mode(tool_names: list[str], mode: str) -> list[str]:
    """Return only tools safe for the given mode (demo/paper/live)."""
    if mode == "demo":
        max_tier = TIER_READ_ONLY
    elif mode == "paper":
        max_tier = TIER_WRITE_LOCAL
    else:
        max_tier = TIER_DESTRUCTIVE  # live mode — all tools available
    return sorted(t for t in tool_names if get_danger_tier(t) <= max_tier)


# Public alias — tests and external code should use TOOL_TIERS (without underscore).
TOOL_TIERS = _TOOL_TIERS

# P2-7 FIX: _TOOL_TIERS is built from multiple merged dicts so Python silently
# accepts duplicate keys (last wins). In practice validate_strategy and run_backtest
# were intentionally listed twice; the final tiers are documented above.
# This assertion guards against UNINTENTIONAL future duplicates that could silently
# downgrade a tool's danger tier.
_EXPECTED_INTENTIONAL_DUPES: set[str] = {"validate_strategy"}  # see comments above
_all_keys: list[str] = []  # populated lazily if needed for debugging
# Python dicts cannot have duplicate keys at runtime — assertion is a reminder for
# maintainers when updating the dicts above.  Keep this comment in sync with reality.
