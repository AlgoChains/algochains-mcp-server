"""
MCP tool implementation manifest — honest status for CI, Onyx, and agents.

``implementation_status`` values:
  full   — Routes to real broker/data APIs when env is configured
  partial — Works in limited cases or returns degraded data
  stub   — Scaffold / placeholder; do not trust for production decisions
"""
from __future__ import annotations

import os
from typing import Any

SCHEMA_VERSION = 1

# Prefix → default status for tools not listed explicitly (first match wins)
_PREFIX_RULES: list[tuple[str, str, list[str]]] = [
    ("massive_", "full", ["MASSIVE_API_KEY"]),
    ("tradovate_", "partial", ["TRADOVATE_CID", "TRADOVATE_SECRET"]),
    ("alpaca_", "full", ["ALPACA_API_KEY", "ALPACA_SECRET_KEY"]),
    ("oanda_", "full", ["OANDA_ACCESS_TOKEN", "OANDA_ACCOUNT_ID"]),
]

# Explicit entries for Tier-1 and other high-traffic tools
_TOOL_OVERRIDES: dict[str, dict[str, Any]] = {
    "mcp_tool_manifest": {
        "implementation_status": "full",
        "required_env": [],
        "notes": "Self-describing manifest; always safe to call.",
    },
    "discover_tools": {"implementation_status": "full", "required_env": [], "notes": "Semantic search over tool index."},
    "get_tool_details": {"implementation_status": "full", "required_env": [], "notes": ""},
    "execute_dynamic_tool": {"implementation_status": "partial", "required_env": [], "notes": "Dispatches to target tool; target may be stub."},
    "place_order": {"implementation_status": "full", "required_env": ["ALPACA_API_KEY", "ALPACA_SECRET_KEY"], "notes": "Also Tradovate/OANDA when configured via connect_broker."},
    "cancel_order": {"implementation_status": "full", "required_env": ["ALPACA_API_KEY", "ALPACA_SECRET_KEY"], "notes": ""},
    "close_position": {"implementation_status": "full", "required_env": ["ALPACA_API_KEY", "ALPACA_SECRET_KEY"], "notes": ""},
    "get_account": {"implementation_status": "full", "required_env": ["ALPACA_API_KEY", "ALPACA_SECRET_KEY"], "notes": ""},
    "get_positions": {"implementation_status": "full", "required_env": ["ALPACA_API_KEY", "ALPACA_SECRET_KEY"], "notes": ""},
    "get_orders": {"implementation_status": "full", "required_env": ["ALPACA_API_KEY", "ALPACA_SECRET_KEY"], "notes": ""},
    "connect_broker": {"implementation_status": "full", "required_env": [], "notes": "Requires broker-specific env when connecting."},
    "backtest_strategy": {"implementation_status": "partial", "required_env": [], "notes": "Depends on internal backtest engine wiring."},
    "validate_strategy": {"implementation_status": "full", "required_env": [], "notes": "Uses StrategyValidator gates."},
    "optimize_strategy": {"implementation_status": "stub", "required_env": [], "notes": "Scaffold until wired to real optimizer."},
    "deploy_strategy": {"implementation_status": "stub", "required_env": [], "notes": "Scaffold."},
    "get_portfolio_summary": {"implementation_status": "partial", "required_env": [], "notes": ""},
    "get_quote": {"implementation_status": "full", "required_env": [], "notes": "Uses configured data providers."},
    "execute_intent": {"implementation_status": "partial", "required_env": [], "notes": "Intent engine partially scaffolded."},
    "approve_intent": {"implementation_status": "partial", "required_env": [], "notes": ""},
    "create_shadow_portfolio": {"implementation_status": "stub", "required_env": [], "notes": "Simulation only; not live broker PnL."},
    "detect_market_regime": {"implementation_status": "partial", "required_env": [], "notes": "Requires real inputs; no fabricated defaults in tool path."},
    "check_order_safety": {"implementation_status": "full", "required_env": [], "notes": "Account protection engine."},
    "get_protection_config": {"implementation_status": "full", "required_env": [], "notes": ""},
    "submit_to_marketplace": {"implementation_status": "partial", "required_env": ["LISTING_API_KEY"], "notes": "Needs LISTING_API_KEY to stage listing after gates pass."},
    "query_data_warehouse": {"implementation_status": "partial", "required_env": ["SUPABASE_URL", "SUPABASE_ANON_KEY"], "notes": "Builder tier warehouse credentials."},
    "browse_marketplace": {"implementation_status": "full", "required_env": ["LISTING_API_KEY"], "notes": "Fails fast if LISTING_API_KEY missing (unless skip env set)."},
    "get_listing_detail": {"implementation_status": "full", "required_env": ["LISTING_API_KEY"], "notes": ""},
    "subscribe_to_bot": {"implementation_status": "full", "required_env": ["LISTING_API_KEY"], "notes": ""},
}


def _skip_key_check() -> bool:
    return os.environ.get("ALGOCHAINS_SKIP_MARKETPLACE_KEY_CHECK", "").lower() in ("1", "true", "yes")


def build_manifest(
    *,
    tool_names: list[str],
    tier1_names: set[str],
    tool_mode: str,
) -> dict[str, Any]:
    """Build the full manifest dict for JSON serialization."""
    tools_out: list[dict[str, Any]] = []
    summary = {"full": 0, "partial": 0, "stub": 0, "unknown": 0}

    for name in sorted(tool_names):
        entry: dict[str, Any]
        if name in _TOOL_OVERRIDES:
            o = _TOOL_OVERRIDES[name]
            entry = {
                "name": name,
                "implementation_status": o["implementation_status"],
                "required_env": list(o.get("required_env", [])),
                "tier1": name in tier1_names,
                "notes": o.get("notes", ""),
            }
        else:
            status = "stub"
            env: list[str] = []
            for prefix, st, req in _PREFIX_RULES:
                if name.startswith(prefix):
                    status = st
                    env = list(req)
                    break
            entry = {
                "name": name,
                "implementation_status": status,
                "required_env": env,
                "tier1": name in tier1_names,
                "notes": "Default classification by prefix or stub.",
            }

        st = entry["implementation_status"]
        if st in summary:
            summary[st] += 1
        else:
            summary["unknown"] += 1
        tools_out.append(entry)

    return {
        "schema_version": SCHEMA_VERSION,
        "tool_mode": tool_mode,
        "tier1_count": len(tier1_names),
        "total_tools": len(tool_names),
        "marketplace_key_check": "skipped" if _skip_key_check() else "enforced",
        "skip_env": "ALGOCHAINS_SKIP_MARKETPLACE_KEY_CHECK=1",
        "summary_by_status": summary,
        "tools": tools_out,
    }
