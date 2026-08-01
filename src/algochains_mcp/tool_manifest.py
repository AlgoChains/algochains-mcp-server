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

from .tool_danger_tiers import get_danger_tier, get_danger_tier_source, get_tier_label
from .tool_policy import TRANSPORT_STDIO, approval_shape

SCHEMA_VERSION = 2

# Prefix → default status for tools not listed explicitly (first match wins)
_PREFIX_RULES: list[tuple[str, str, list[str]]] = [
    ("massive_", "full", ["MASSIVE_API_KEY"]),
    ("tradovate_", "partial", ["TRADOVATE_CID", "TRADOVATE_SECRET"]),
    ("alpaca_", "full", ["ALPACA_API_KEY", "ALPACA_SECRET_KEY"]),
    ("oanda_", "full", ["OANDA_ACCESS_TOKEN", "OANDA_ACCOUNT_ID"]),
    # Numerai tournament tools — partial until live-tested against tournament API.
    # NUMERAI_SECRET_KEY must NEVER appear in logs/responses (HK-6).
    ("numerai_", "partial", ["NUMERAI_SECRET_KEY"]),
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
    "get_bot_health": {
        "implementation_status": "full",
        "required_env": ["ALGOCHAINS_CONTROL_TOWER"],
        "notes": "Reads bounded control-tower health artifacts and degrades when files are missing.",
    },
    "get_quant_regime_state": {
        "implementation_status": "partial",
        "required_env": ["ALGOCHAINS_CONTROL_TOWER", "SUPABASE_URL"],
        "notes": "Reads shadow-only quant snapshot plus Supabase metrics; agreement summary needs service-role access.",
    },
    "place_order": {"implementation_status": "full", "required_env": ["ALPACA_API_KEY", "ALPACA_SECRET_KEY"], "notes": "Also Tradovate/OANDA when configured via connect_broker."},
    "cancel_order": {"implementation_status": "full", "required_env": ["ALPACA_API_KEY", "ALPACA_SECRET_KEY"], "notes": ""},
    "close_position": {"implementation_status": "full", "required_env": ["ALPACA_API_KEY", "ALPACA_SECRET_KEY"], "notes": ""},
    "get_account": {"implementation_status": "full", "required_env": ["ALPACA_API_KEY", "ALPACA_SECRET_KEY"], "notes": ""},
    "get_positions": {"implementation_status": "full", "required_env": ["ALPACA_API_KEY", "ALPACA_SECRET_KEY"], "notes": ""},
    "get_orders": {"implementation_status": "full", "required_env": ["ALPACA_API_KEY", "ALPACA_SECRET_KEY"], "notes": ""},
    "connect_broker": {"implementation_status": "full", "required_env": [], "notes": "Requires broker-specific env when connecting."},
    "run_backtest": {"implementation_status": "partial", "required_env": [], "notes": "Depends on internal backtest engine wiring."},
    "validate_strategy": {"implementation_status": "full", "required_env": [], "notes": "Validates StrategySpec schema, parameters, and internal consistency."},
    "validate_strategy_metrics": {"implementation_status": "full", "required_env": [], "notes": "Runs marketplace metric gates via StrategyValidator; distinct from StrategySpec schema validation."},
    "optimize_strategy": {"implementation_status": "partial", "required_env": [], "notes": "Wired to StrategyOptimizer (Optuna); requires backtest engine init and real data. Falls back to error if runner unavailable."},
    "deploy_strategy": {"implementation_status": "stub", "required_env": [], "notes": "Registers StrategySpec locally for tracking only. Does not connect to live or paper broker execution."},
    "portfolio_summary": {"implementation_status": "partial", "required_env": [], "notes": ""},
    "get_quote": {"implementation_status": "full", "required_env": [], "notes": "Uses configured data providers."},
    "execute_intent": {"implementation_status": "partial", "required_env": [], "notes": "Intent engine partially scaffolded."},
    "approve_intent": {"implementation_status": "partial", "required_env": [], "notes": ""},
    "create_shadow_portfolio": {"implementation_status": "partial", "required_env": [], "notes": "Wired to ShadowPortfolioEngine; simulates paper positions against real market prices. Not live broker PnL — compare via get_shadow_results(compare_live=True)."},
    "detect_market_regime": {"implementation_status": "partial", "required_env": [], "notes": "Requires real inputs; no fabricated defaults in tool path."},
    "check_order_safety": {"implementation_status": "full", "required_env": [], "notes": "Account protection engine."},
    "get_protection_config": {"implementation_status": "full", "required_env": [], "notes": ""},
    "submit_to_marketplace": {"implementation_status": "partial", "required_env": ["LISTING_API_KEY"], "notes": "Needs LISTING_API_KEY to stage listing after gates pass."},
    "query_data_warehouse": {"implementation_status": "partial", "required_env": ["SUPABASE_URL", "SUPABASE_ANON_KEY"], "notes": "Builder tier warehouse credentials."},
    "browse_marketplace": {"implementation_status": "full", "required_env": ["LISTING_API_KEY"], "notes": "Fails fast if LISTING_API_KEY missing (unless skip env set)."},
    "get_listing_detail": {"implementation_status": "full", "required_env": ["LISTING_API_KEY"], "notes": ""},
    "subscribe_to_bot": {"implementation_status": "full", "required_env": ["LISTING_API_KEY"], "notes": ""},

    "get_learn_hub_health": {
        "implementation_status": "full",
        "required_env": [],
        "notes": "Read-only HTTP smoke check for algochains.ai/learn/. No deploy capability. External HTTPS call; fails gracefully if site unreachable.",
    },
    "get_physical_event_sources": {
        "implementation_status": "partial",
        "required_env": [],
        "notes": "Lists configured physical-world source lanes. Polling rows depend on Sonia Air/tower daemons and Supabase table.",
    },
    "map_physical_event_assets": {
        "implementation_status": "partial",
        "required_env": [],
        "notes": "Static research mapping for CL/NG/MNQ/NQ/MES/ES/BTC/ETH; advisory only.",
    },
    "score_physical_event_alpha": {
        "implementation_status": "partial",
        "required_env": [],
        "notes": "Deterministic priority score from caller-provided real event fields; not broker truth or a trade signal.",
    },
    "get_sonia_air_heartbeat": {
        "implementation_status": "partial",
        "required_env": ["ALGOCHAINS_CONTROL_TOWER"],
        "notes": "Reads control-tower state/sonia_air_heartbeat.json and fails closed when unavailable.",
    },

    # ── Cricket bot (Avi's external partner API — Agent Cricket007 listing) ──
    "get_cricket_bot_performance": {
        "implementation_status": "full",
        "required_env": ["CRICKET_BOT_API_KEY", "CRICKET_BOT_API_URL"],
        "notes": "Read-only GET /performance on Avi's cricket-bot API. Fails closed when env missing/unreachable; advisory only, never a trading dependency.",
    },
    "get_cricket_bot_trades": {
        "implementation_status": "full",
        "required_env": ["CRICKET_BOT_API_KEY", "CRICKET_BOT_API_URL"],
        "notes": "Read-only GET /trades (platform=polymarket|kalshi per row). Fails closed; advisory only.",
    },
    "get_cricket_bot_matches": {
        "implementation_status": "full",
        "required_env": ["CRICKET_BOT_API_KEY", "CRICKET_BOT_API_URL"],
        "notes": "Read-only GET /matches per-match breakdown. Fails closed; advisory only.",
    },
    "get_cricket_bot_signals": {
        "implementation_status": "full",
        "required_env": ["CRICKET_BOT_API_KEY", "CRICKET_BOT_API_URL"],
        "notes": "Read-only GET /signals incl. SKIP rows for transparency. Fails closed; advisory only.",
    },
    "get_cricket_bot_tournaments": {
        "implementation_status": "full",
        "required_env": ["CRICKET_BOT_API_KEY", "CRICKET_BOT_API_URL"],
        "notes": "Read-only GET /tournaments discovery endpoint. Fails closed; advisory only.",
    },

    # ── Numerai tournament tools (§9 / §28.3 build order step 12) ──────────
    "numerai_status": {
        "implementation_status": "partial",
        "required_env": [],
        "notes": "Returns env flags as booleans only. HK-6: never logs key values.",
    },
    "numerai_round_info": {
        "implementation_status": "partial",
        "required_env": ["NUMERAI_PUBLIC_ID", "NUMERAI_SECRET_KEY"],
        "notes": "Calls napi.get_current_round(). Requires credentials.",
    },
    "numerai_download_dataset": {
        "implementation_status": "partial",
        "required_env": ["NUMERAI_PUBLIC_ID", "NUMERAI_SECRET_KEY"],
        "notes": "Downloads train/live parquet to ALGOCHAINS_STATE_DIR/numerai/data/. GCS mirror optional.",
    },
    "numerai_train_baseline": {
        "implementation_status": "partial",
        "required_env": [],
        "notes": "CPU-heavy LightGBM train with era k-fold. Saves to models/numerai/. HK-1: era-based split enforced.",
    },
    "numerai_validate_metrics": {
        "implementation_status": "partial",
        "required_env": [],
        "notes": "Per-era Spearman; calibration check. All metrics labeled proxy_corr/proxy_mmc (HK-10).",
    },
    "numerai_dry_run_submit": {
        "implementation_status": "partial",
        "required_env": [],
        "notes": "Generates submission CSV with ID validation and range check. No upload. HK-3, HK-5.",
    },
    "numerai_upload_predictions": {
        "implementation_status": "partial",
        "required_env": ["NUMERAI_SECRET_KEY", "NUMERAI_ALLOW_LIVE"],
        "notes": (
            "Irreversible tournament submission. Gated: NUMERAI_ALLOW_LIVE=1 AND model_id required. "
            "HK-17: TIER_ORDER_EXEC. HK-7: no NMR staking (Gate 2, manual UI only). "
            "Default = dry-run. NUMERAI_SECRET_KEY never logged."
        ),
    },
    "numerai_get_model_scores": {
        "implementation_status": "partial",
        "required_env": ["NUMERAI_PUBLIC_ID", "NUMERAI_SECRET_KEY"],
        "notes": (
            "Returns raw numerapi response dict (pass-through). HK-13: no hardcoded field names. "
            "HK-10: proxy_mmc != live mmcRep. BMC disclaimer included."
        ),
    },
}


def _skip_key_check() -> bool:
    return os.environ.get("ALGOCHAINS_SKIP_MARKETPLACE_KEY_CHECK", "").lower() in ("1", "true", "yes")


def build_manifest(
    *,
    tool_names: list[str],
    tier1_names: set[str],
    tool_mode: str,
    http_public_tools: set[str] | None = None,
    http_owner_tools: set[str] | None = None,
    subscriber_tools: set[str] | None = None,
) -> dict[str, Any]:
    """Build the full manifest dict for JSON serialization."""
    tools_out: list[dict[str, Any]] = []
    summary = {"full": 0, "partial": 0, "stub": 0, "unknown": 0}
    public = http_public_tools or set()
    owner = http_owner_tools or set()
    subscriber = subscriber_tools or set()

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

        tier = get_danger_tier(name)
        entry.update(
            {
                "danger_tier": tier,
                "danger_label": get_tier_label(tier),
                "tier_source": get_danger_tier_source(name),
                "approval": approval_shape(name, transport=TRANSPORT_STDIO),
                "transports": {
                    "stdio_direct": tool_mode == "full" or name in tier1_names,
                    "stdio_dynamic": True,
                    "http_bridge_public": name in public,
                    "http_bridge_owner": name in owner,
                    "subscriber": name in subscriber,
                },
                "visibility": {
                    "tier1": name in tier1_names,
                    "public_http": name in public,
                    "owner_http": name in owner,
                    "subscriber": name in subscriber,
                },
            }
        )

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


def mcp_tool_manifest() -> dict[str, Any]:
    """
    Convenience wrapper — returns manifest using only the static tool overrides and prefix rules.
    Used by tests and external callers that do not have access to the live TOOLS list.
    """
    all_tool_names = sorted(set(list(_TOOL_OVERRIDES.keys())))
    return build_manifest(
        tool_names=all_tool_names,
        tier1_names=set(),
        tool_mode="static",
    )
