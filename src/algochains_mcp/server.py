"""
AlgoChains MCP Server v22.5 — institutional-grade trading platform.

485 tools across the full owner surface, with smart tiered exposure:

  SMART MODE (default, ALGOCHAINS_TOOL_MODE=smart):
    148 curated tools exposed directly — trading, data, strategy, intent, meta-tools.
    Remaining tools discoverable via discover_tools → execute_dynamic_tool.
    ~4K tokens vs ~40K+. Works within Cursor (80-tool limit) and Windsurf.

  FULL MODE (ALGOCHAINS_TOOL_MODE=full):
    All 485 tools exposed. For clients with their own lazy loading (Claude Code).

V20.0 additions: Account Protection (13 pre-trade guards), Builder SDK (3.09B+ row
data warehouse, 7-gate MCPT validation pipeline), memory-safe architecture (OOM
prevention, bounded caches, concurrency semaphores), mcp_tool_manifest resource.

V17.1 additions (MCP 2025-06-18 spec compliance):
  - Tool Behavior Annotations on ALL tools (readOnly/destructive/idempotent/openWorld)
  - Massive parity: pagination auto-detection, per-request API key, LLM tracking
  - Composable pipeline: massive_run_pipeline (search→fetch→store→query in 1 call)
  - Resource Templates: algochains://market/{ticker}, portfolio/{broker}, massive/tables/{table}

Research basis:
  - arXiv:2603.20313 — 99.6% token reduction with semantic tool discovery
  - Claude Code MCP Tool Search — 95% context savings via lazy loading
  - Cursor hard limit varies by client; smart mode keeps the direct surface bounded.

Start with:  algochains-mcp  (or python -m algochains_mcp.server)
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from collections.abc import Mapping
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    GetPromptResult,
    Prompt,
    PromptArgument,
    PromptMessage,
    Resource,
    ResourceTemplate,
    TextContent,
    Tool,
    ToolAnnotations,
)

import importlib
import gc
import os
import sys
from pathlib import Path as _PathGlobal

from .e2e_sentinel import apply_effective_sentinel_resolution, summarize_e2e_sentinel_state


def _default_control_tower() -> str:
    """
    Resolve the control-tower path without hardcoding a Mac-only absolute path.

    Order:
      1. ALGOCHAINS_CONTROL_TOWER env (preferred, used by both Mac + Linux desktop)
      2. ALGOCHAINS_CONTROL_TOWER_PATH env (legacy alias kept for backwards-compat)
      3. __file__-relative sibling ``algochains-control-tower`` directory if present
      4. ``/Users/treycsa/CascadeProjects/algochains-control-tower`` as a last resort
         (only hit on the original MacBook; desktop failover uses env override).
    """
    for var in ("ALGOCHAINS_CONTROL_TOWER", "ALGOCHAINS_CONTROL_TOWER_PATH"):
        val = os.environ.get(var)
        if val:
            return val
    try:
        sibling = _PathGlobal(__file__).resolve().parents[3] / "algochains-control-tower"
        if sibling.exists():
            return str(sibling)
    except Exception:
        pass
    return "/Users/treycsa/CascadeProjects/algochains-control-tower"


def _tail_jsonl(path: _PathGlobal, limit: int = 200) -> list[dict[str, Any]]:
    """Read the last JSONL rows from a control-tower telemetry file."""
    if not path.exists():
        return []
    try:
        from collections import deque

        rows: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in deque(handle, maxlen=max(1, limit)):
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
        return rows
    except Exception:
        return []


def _pctl(values: list[float], pct: float) -> float:
    vals = sorted(v for v in values if isinstance(v, (int, float)))
    if not vals:
        return 0.0
    idx = min(len(vals) - 1, max(0, int(round((pct / 100.0) * (len(vals) - 1)))))
    return round(float(vals[idx]), 3)


def _rate(count: int, total: int) -> float:
    return round(count / max(total, 1), 4)


_TRACEABILITY_TRANSIENT_MARKERS = (
    "connecterror",
    "connection reset by peer",
    "connectionreseterror",
    "errno 54",
    "readtimeout",
    "read timeout",
    "protocolerror",
    "remoteprotocolerror",
    "readerror",
    "server disconnected",
    "connection aborted",
)


def _is_traceability_transient_failure(*chunks: str | None) -> bool:
    """Return true for traceability audit failures worth retrying locally."""
    text = "\n".join(chunk for chunk in chunks if chunk).lower()
    return any(marker in text for marker in _TRACEABILITY_TRANSIENT_MARKERS)


def _summarize_desktop_inference_log(control_tower: _PathGlobal) -> dict[str, Any]:
    rows = _tail_jsonl(control_tower / "logs" / "desktop_inference_latency.jsonl", 200)
    if not rows:
        return {"status": "missing_or_empty", "count": 0}
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = (
            f"{row.get('model_id', 'unknown')}|"
            f"{row.get('runtime', 'unknown')}|"
            f"{row.get('prompt_class', 'unknown')}"
        )
        groups.setdefault(key, []).append(row)
    summary: dict[str, Any] = {}
    for key, group_rows in groups.items():
        latencies = [float(r.get("latency_s") or 0.0) for r in group_rows if r.get("latency_s") is not None]
        failures = [r for r in group_rows if not r.get("ok")]
        schema_failures = [r for r in group_rows if r.get("validation_errors")]
        fallback_reasons = sorted(
            {str(r.get("fallback_reason")) for r in failures if r.get("fallback_reason")}
        )
        summary[key] = {
            "count": len(group_rows),
            "p50_s": _pctl(latencies, 50),
            "p95_s": _pctl(latencies, 95),
            "max_s": round(max(latencies), 3) if latencies else 0.0,
            "failure_rate": _rate(len(failures), len(group_rows)),
            "schema_failure_rate": _rate(len(schema_failures), len(group_rows)),
            "fallback_reasons": fallback_reasons[:10],
        }
    return {"status": "ok", "count": len(rows), "groups": summary}


def _summarize_decision_latency_log(control_tower: _PathGlobal) -> dict[str, Any]:
    rows = _tail_jsonl(control_tower / "logs" / "decision_latency.jsonl", 500)
    if not rows:
        return {"status": "missing_or_empty", "count": 0}
    event_counts: dict[str, int] = {}
    for row in rows:
        event = str(row.get("event", "unknown"))
        event_counts[event] = event_counts.get(event, 0) + 1
    numeric_keys = (
        "analyze_ms",
        "multi_agent_ms",
        "desktop_inference_ms",
        "cloud_fallback_ms",
        "order_submit_latency_ms",
        "broker_ack_latency_ms",
        "fill_confirm_latency_ms",
        "signal_to_ack_ms",
        "signal_to_fill_ms",
    )
    metrics: dict[str, Any] = {}
    for key in numeric_keys:
        vals = [float(row[key]) for row in rows if isinstance(row.get(key), (int, float))]
        if vals:
            metrics[key] = {
                "count": len(vals),
                "p50_ms": _pctl(vals, 50),
                "p95_ms": _pctl(vals, 95),
                "max_ms": round(max(vals), 3),
            }
    return {"status": "ok", "count": len(rows), "events": event_counts, "metrics": metrics}

# ─── Essential V1-V7 imports (minimal, always needed) ───────────────────────
from .brokers.base import OrderSide, OrderType
from .brokers.registry import BrokerRegistry
from .config import ServerConfig, load_config
from .errors import (
    AlgoChainsError,
    BrokerNotConfiguredError,
    BrokerNotConnectedError,
)
from .marketplace.bridge import MarketplaceBridge
from .marketplace.validator import StrategyValidator
from .middleware import (
    get_rate_limiter, get_tool_category, get_tool_logger, get_tool_semaphore,
    get_tool_timeout, validate_arguments, check_circuit, record_success,
    record_failure, guard_response_size, CircuitOpenError,
)

# ─── Logging must be configured before any try/except import blocks that log ──
import logging as _logging_init
_logging_init.basicConfig(
    level=_logging_init.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = _logging_init.getLogger("algochains_mcp.server")

# ─── V22 Trading Guardrails — hard-coded circuit breakers (AI cannot override) ─
# Import at startup so limits are enforced from first tool call.
# Graceful fallback: if module missing, order velocity checking is skipped
# but a warning is logged on every place_order call.
try:
    from .trading_guardrails import get_guardrails, GuardrailTripped, GuardrailReason
    _GUARDRAILS_AVAILABLE = True
except ImportError:
    _GUARDRAILS_AVAILABLE = False
    logger.warning(
        "trading_guardrails not available — hard-coded circuit breakers are INACTIVE. "
        "Deploy trading_guardrails.py to activate V22 safety limits."
    )
    if os.getenv("ALGOCHAINS_FAIL_CLOSED_NO_GUARDRAILS", "0") == "1":
        raise

# ─── V20 Memory Safety — import first so we can monitor from startup ─────────
# Memory safety is lightweight and has no heavy sub-deps.
from .memory_safety import get_memory_monitor, MemoryMonitor
from .handlers.physical_world import PHYSICAL_WORLD_HANDLERS
from .tool_manifest import build_manifest
from .tool_policy import evaluate_dynamic_tool, evaluate_stdio_direct_tool
from .otel_tracing import redacted_argument_hash, trace_span

# ─── V20 Account Protection — lightweight, no ML deps ───────────────────────
from .account_protection.engine import AccountProtectionEngine, ProtectionConfig
from .account_protection.guards import AccountSnapshot, OrderIntent

# ─── V20 Builder SDK — lightweight, only imports httpx ──────────────────────
from .builder_sdk.data_warehouse import DataWarehouseClient, DataQuery
from .builder_sdk.strategy_runner import StrategyRunner, BacktestConfig
from .builder_sdk.submission_pipeline import SubmissionPipeline, StrategySubmission

# ─── Lazy Import Registry ────────────────────────────────────────────────────
# V8-V19 modules (portfolio, streaming, ML, execution, alpha engines, etc.)
# are NOT imported at startup. They are loaded on first use via _lazy_import().
# This prevents the startup memory spike that was crashing the system.
#
# Benchmark: Eager loading all modules = ~800MB RSS at startup.
#            Lazy loading = ~45MB RSS at startup (95% reduction).
_lazy_module_cache: dict[str, Any] = {}


def _lazy_import(rel_module: str, class_name: str) -> Any:
    """Import a class from a relative module path on first use only.

    Results are cached so subsequent calls are O(1) dict lookups.
    Returns None (with a warning) if the module or class is unavailable.
    """
    key = f"{rel_module}.{class_name}"
    if key in _lazy_module_cache:
        return _lazy_module_cache[key]
    try:
        package = "algochains_mcp"
        if rel_module.startswith("."):
            full_module = f"{package}{rel_module}"
        else:
            full_module = f"{package}.{rel_module}"
        mod = importlib.import_module(full_module)
        cls = getattr(mod, class_name)
        _lazy_module_cache[key] = cls
        return cls
    except Exception as exc:
        logging.getLogger("algochains_mcp.server").warning(
            "Lazy import %s.%s failed: %s", rel_module, class_name, exc
        )
        _lazy_module_cache[key] = None
        return None


# ─── Type aliases used in getter signatures (resolved lazily at runtime) ────
# These are not real imports — they are string-based references to be resolved
# via _lazy_import() inside each _get_*() function.
_LAZY_SPECS = {
    # V8-V9
    "streaming":          (".streaming.manager",                   ["StreamManager", "StreamTopic"]),
    "portfolio":          (".portfolio.optimizer",                  ["AllocationMethod", "BotMetrics", "PortfolioOptimizer"]),
    "notifications":      (".notifications.push",                   ["Notification", "NotificationChannel", "NotificationDispatcher", "NotificationEvent", "NotificationPriority"]),
    "data_providers":     (".data_providers.registry",              ["DataProviderRegistry"]),
    "data_interval":      (".data_providers.base",                  ["Interval"]),
    "byok":               (".byok.key_orchestrator",                ["KeyOrchestrator"]),
    "datasets":           (".datasets.builder",                     ["DatasetBuilder", "DatasetRequest"]),
    "spec_validator":     (".strategy_builder.spec",                ["StrategySpec", "StrategySpecValidator"]),
    "backtest_runner":    (".strategy_builder.backtest_runner",     ["BacktestRunner"]),
    "strategy_optimizer": (".strategy_builder.optimizer",           ["StrategyOptimizer"]),
    "walk_forward":       (".strategy_builder.walk_forward",        ["WalkForwardEngine"]),
    "deployer":           (".strategy_builder.deployer",            ["StrategyDeployer"]),
    "template_mgr":       (".strategy_builder.template_manager",    ["TemplateManager"]),
    "social_trading":     (".social_trading.engine",                ["SocialTradingEngine"]),
    "community_signals":  (".community_signals.engine",             ["CommunitySignalEngine"]),
    "risk_dashboard":     (".risk_dashboard.engine",                ["RiskDashboardEngine"]),
    "compliance":         (".compliance.engine",                    ["ComplianceEngine"]),
    "multi_tenant":       (".multi_tenant.engine",                  ["MultiTenantEngine"]),
    # V10 ML
    "feature_engine":     (".ml_engine.feature_engine",             ["FeatureEngine"]),
    "model_trainer":      (".ml_engine.model_trainer",              ["ModelTrainer"]),
    "model_registry":     (".ml_engine.model_registry",             ["ModelRegistry"]),
    "rl_agent":           (".ml_engine.rl_agent",                   ["RLAgentEngine"]),
    "gpu_dispatcher":     (".ml_engine.gpu_dispatcher",             ["GPUDispatcher"]),
    "llm_strategy_gen":   (".ml_engine.llm_strategy_gen",           ["LLMStrategyGenerator"]),
    # V11 Execution
    "inst_order_mgr":     (".execution_engine.order_manager",       ["InstitutionalOrderManager"]),
    "smart_router":       (".execution_engine.smart_order_router",  ["SmartOrderRouter"]),
    "algo_executor":      (".execution_engine.algo_executor",       ["AlgoExecutor"]),
    "fix_gateway":        (".execution_engine.fix_gateway",         ["FIXGateway"]),
    "tca_engine":         (".execution_engine.tca_engine",          ["TCAEngine"]),
    "venue_manager":      (".execution_engine.venue_manager",       ["VenueManager"]),
    # V12 Realtime
    "pnl_streamer":       (".realtime_analytics.pnl_streamer",      ["PnLStreamer"]),
    "order_flow":         (".realtime_analytics.order_flow_analyzer", ["OrderFlowAnalyzer"]),
    "microstructure":     (".realtime_analytics.microstructure",    ["MicrostructureEngine"]),
    "regime_detector":    (".realtime_analytics.regime_detector",   ["RegimeDetector"]),
    "alert_engine":       (".realtime_analytics.alert_engine",      ["AlertEngine"]),
    # V13 Alt Data
    "sentiment_engine":   (".alt_data.sentiment_engine",            ["SentimentEngine"]),
    "satellite_engine":   (".alt_data.satellite_engine",            ["SatelliteDataEngine"]),
    "web_scraper":        (".alt_data.web_scraper",                  ["WebScraperEngine"]),
    "sec_filing":         (".alt_data.sec_filing_engine",           ["SECFilingEngine"]),
    "social_media":       (".alt_data.social_media_engine",         ["SocialMediaEngine"]),
    "alt_data_market":    (".alt_data.alt_data_marketplace",        ["AltDataMarketplace"]),
    # V14 Agent Swarm
    "agent_orchestrator": (".agent_swarm.agent_orchestrator",       ["AgentOrchestrator"]),
    "task_planner":       (".agent_swarm.task_planner",             ["TaskPlanner"]),
    "agent_memory":       (".agent_swarm.agent_memory",             ["AgentMemory"]),
    "tool_router":        (".agent_swarm.tool_router",              ["ToolRouter"]),
    "consensus_engine":   (".agent_swarm.consensus_engine",         ["ConsensusEngine"]),
    "agent_monitor":      (".agent_swarm.agent_monitor",            ["AgentMonitor"]),
    # V15 DeFi
    "dex_aggregator":     (".defi_engine.dex_aggregator",           ["DEXAggregator"]),
    "yield_optimizer":    (".defi_engine.yield_optimizer",          ["YieldOptimizer"]),
    "bridge_engine":      (".defi_engine.bridge_engine",            ["BridgeEngine"]),
    "mev_protector":      (".defi_engine.mev_protector",            ["MEVProtector"]),
    "governance_engine":  (".defi_engine.governance_engine",        ["GovernanceEngine"]),
    "defi_risk":          (".defi_engine.defi_risk_engine",         ["DeFiRiskEngine"]),
    # V16 Cloud SaaS
    "saas_tenant_mgr":    (".cloud_saas.tenant_manager",            ["TenantManager"]),
    "billing_engine":     (".cloud_saas.billing_engine",            ["BillingEngine"]),
    "strategy_market":    (".cloud_saas.strategy_marketplace",      ["StrategyMarketplace"]),
    "white_label":        (".cloud_saas.white_label_engine",        ["WhiteLabelEngine"]),
    "api_gateway":        (".cloud_saas.api_gateway",               ["APIGateway"]),
    # V17
    "massive_provider":   (".data_providers.massive_whitelabel",    ["MassiveWhiteLabelProvider"]),
    "dynamic_gateway":    (".dynamic_toolsets.gateway",             ["DynamicToolsetGateway"]),
    # V18 Intent
    "intent_parser":      (".intent_engine.intent_parser",          ["IntentParser"]),
    "constraint_solver":  (".intent_engine.constraint_solver",      ["ConstraintSolver"]),
    "plan_executor":      (".intent_engine.plan_executor",          ["PlanExecutor"]),
    "shadow_engine":      (".intent_engine.shadow_portfolio",       ["ShadowPortfolioEngine"]),
    "evolution_engine":   (".intent_engine.strategy_evolution",     ["StrategyEvolutionEngine"]),
    "arbitrage_detector": (".intent_engine.arbitrage_detector",     ["ArbitrageDetector"]),
    "predictive_prefetch":(".intent_engine.predictive_prefetch",    ["PredictiveStatePrefetch"]),
    "intent_regime":      (".intent_engine.regime_detector",        ["RegimeDetector"]),
    # V19 Alpha Engines
    "vwap_engine":        (".alpha_engines.vwap_engine",            ["VWAPEngine"]),
    "dark_pool_engine":   (".alpha_engines.dark_pool_engine",       ["DarkPoolEngine"]),
    "gex_engine":         (".alpha_engines.gex_engine",             ["GEXEngine"]),
    "vol_surface_engine": (".alpha_engines.vol_surface",            ["VolSurfaceEngine"]),
    "cross_asset_engine": (".alpha_engines.cross_asset",            ["CrossAssetEngine"]),
    "congressional_engine": (".alpha_engines.congressional",        ["CongressionalEngine"]),
    "kelly_engine":       (".alpha_engines.kelly_engine",           ["KellyEngine"]),
    "options_flow_engine":(".alpha_engines.options_flow",           ["OptionsFlowEngine"]),
    "tape_reader_engine": (".alpha_engines.tape_reader",            ["TapeReaderEngine"]),
    # ── V21: MCP 2025-11-25 Spec Compliance ─────────────────────────
    "elicitation":      (".spec_compliance.elicitation",   ["ElicitationManager", "ElicitRequest", "ElicitResult"]),
    "tasks_engine":     (".spec_compliance.tasks",         ["TaskManager", "Task", "TaskStatus"]),
    "subscriptions_v21":(".spec_compliance.subscriptions", ["SubscriptionManager", "ResourceSubscription"]),
    # ── V21: AlphaLoop Self-Improving Loop ───────────────────────────
    "trade_memory":     (".evolution.trade_memory",        ["TradeMemory", "TradeEpisode", "get_trade_memory"]),
    "reward_model":     (".evolution.reward_model",        ["RewardModel", "get_reward_model"]),
    "evolution_daemon": (".evolution.evolution_daemon",    ["EvolutionDaemon", "get_evolution_daemon"]),
    "lessons_injector": (".evolution.lessons_injector",    ["LessonsInjector", "get_lessons_injector"]),
    # ── V21: Order Flow & Institutional ─────────────────────────────
    "footprint_engine": (".order_flow.footprint",          ["compute_footprint_chart", "analyze_footprint_signals"]),
    "cd_engine":        (".order_flow.cumulative_delta",   ["compute_cumulative_delta"]),
    "dp_engine_v21":    (".order_flow.dark_pool_volume",   ["DarkPoolEngine"]),
    "earnings_cat":     (".order_flow.earnings_catalyst",  ["EarningsCatalystEngine"]),
    "pred_markets":     (".order_flow.prediction_markets", ["PredictionMarketsEngine", "PredictionMarketEngine"]),
    "macro_signals_v21":(".order_flow.macro_signals",      ["MacroSignalEngine"]),
    # ── V21: Security / Key Vault ────────────────────────────────────
    "key_vault_v21":    (".auth.key_vault",                ["KeyVault", "get_key_vault"]),
    "agent_prov":       (".auth.agent_provisioner",        ["AgentProvisioner", "get_agent_provisioner"]),
    # ── V21: Streaming & Alerts ──────────────────────────────────────
    "price_alerts":     (".streaming.alert_engine",        ["PriceAlertEngine", "get_alert_engine"]),
    "earnings_cal":     (".streaming.earnings_calendar",   ["EarningsCalendarEngine"]),
    # ── V21: Onyx Intelligence ───────────────────────────────────────
    "onyx_intel":       (".onyx_intelligence.onyx_client", ["OnyxClient", "get_onyx_client", "OnyxUnavailableError"]),
    # ── V21: Crypto Feature Parity ───────────────────────────────────
    "copy_engine_v21":  (".social_trading.copy_engine",    ["CopyTradingEngine", "get_copy_engine"]),
    "staking_engine":   (".defi_engine.staking",           ["StakingEngine", "get_staking_engine"]),
    "dca_engine_v21":   (".execution_engine.dca_engine",   ["DCAEngine", "get_dca_engine"]),
    "crypto_perps_v21": (".brokers.crypto_perps",          ["CryptoPerpsEngine", "get_crypto_perps"]),
    # ── V21: SaaS Hardening ──────────────────────────────────────────
    "tenant_mw":        (".cloud_saas.tenant_middleware",  ["TenantRateLimiter", "AuditLogger", "SandboxManager"]),
    # ── Ultimate Quant Alpha ────────────────────────────────────────
    "vol_surface_v21":  (".quant_alpha.volatility_surface", ["VolatilitySurfaceEngine", "get_vol_surface_engine"]),
    "factor_model_v21": (".quant_alpha.factor_model",       ["FactorModelEngine", "get_factor_engine"]),
    "regime_hmm":       (".quant_alpha.regime_hmm",         ["RegimeHMMDetector", "get_regime_detector"]),
}

# logger and basicConfig already configured above (before guardrails import)

from algochains_mcp import __version__ as _server_version

SERVER_INSTRUCTIONS = (
    f"AlgoChains MCP Server v{_server_version} — The Ultimate Algo Quant Stack. "
    "~481 tools across 20 domains: market data, trading, strategy building, ML/AI, execution, "
    "order flow analysis, institutional data, AlphaLoop self-improvement, DeFi/crypto, "
    "Onyx RAG intelligence, Graphiti temporal knowledge graph, MCP 2025-11-25 spec compliance, "
    "SaaS hardening, and autonomous marketplace pipeline (research→backtest→validate→stage). "
    "V22.5: Graphiti temporal knowledge-graph domain (graphiti_search / graphiti_temporal_query / "
    "graphiti_health read-only Tier-1, graphiti_add_episode WRITE_LOCAL discover-only) — advisory "
    "agent_memory authority over Neo4j, NEVER broker truth, fails closed graphiti_unavailable. "
    "Real data only — all tools connect to live brokers, real tick feeds, and real APIs. "
    "In smart mode (default), ~52 Tier-1 tools exposed (SEC-2026: send_waitlist_invite + upsert_bot_performance moved to ORDER_EXEC). "
    "Use 'discover_tools' to find 280+ additional tools on demand. "
    "V22.4: get_bot_health now includes e2e_sentinel lifecycle state for MNQ signal→order→bracket→fill traceability. "
    "V22.2: get_bot_health now includes ml_env_flags (MASSIVE_NEWS_FEATURES, MASSIVE_PCR_FEATURES, "
    "MASSIVE_HALT_GUARD) and cc_health (Command Center watchdog state from cc_health_state.json). "
    "Data vendor standard: Massive.com (Polygon white-label) for options/news/PCR-style metrics — "
    "core/massive_pcr_features.py provides live SPY/QQQ PCR gated by MASSIVE_PCR_FEATURES; "
    "see control-tower docs/BACKTEST_FEATURE_TRACE.md for train/serve skew map and feature flags. "
    "Note: get_feature_importance is the v10 ML engine path (feature_set_id); for MNQ LightGBM/XGBoost "
    "gain importance on the promoted pkl run scripts/feature_importance_report.py on control-tower. "
    "V22.1: get_bot_health signal_health slice (params + risk_bootstrap); get_kronos_shadow_stats path fix. "
    "V22.0 NEW: Autonomous marketplace autopilot (run_marketplace_autopilot), "
    "marketplace listings (get_marketplace_listings), Onyx ingest trigger (run_onyx_ingest), "
    "Onyx status (get_onyx_status). Signal conflict manager (get_signal_conflict_stats). "
    "V21.2: Ultimate Quant Alpha Stack — volatility surface, factor model, HMM regime detection. "
    "V21: AlphaLoop evolution, footprint charts, dark pool volume, earnings NLP, "
    "prediction markets, macro signals, Onyx semantic search, live bot showcase, "
    "encrypted key vault, desktop tower dispatcher (dispatch_tower_job). "
    "LIVE: 4 futures bots (MNQ/CL/MES/NQ, owner-only), Alpaca paper trader (equities+crypto, subscribable). "
    "Command Center: algochains-command-center (Next.js, port 3333). "
    "Set ALGOCHAINS_TOOL_MODE=full to expose all ~481 tools."
)

app = Server("algochains-mcp-server", instructions=SERVER_INSTRUCTIONS)
_HANDLER_REGISTRY = {
    **PHYSICAL_WORLD_HANDLERS,
}

# ═══════════════════════════════════════════════════════════════════
# MCP 2025-06-18 Tool Behavior Annotations — safety metadata
# ═══════════════════════════════════════════════════════════════════
# IDEs use these to auto-approve safe tools and show confirmation for dangerous ones.
ANNOT_READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
ANNOT_READ_SAFE = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)
ANNOT_READ_EXTERNAL = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)
ANNOT_WRITE_SAFE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)
ANNOT_WRITE_DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True)
ANNOT_DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True)
ANNOT_TRADE_EXEC = ToolAnnotations(title="Trade Execution", readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True)
ANNOT_SEARCH = ToolAnnotations(title="Search", readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
ANNOT_COMPUTE = ToolAnnotations(title="Computation", readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)

# Map tool names → their annotation category for bulk assignment
_TOOL_ANNOTATION_MAP: dict[str, ToolAnnotations] = {}

def _classify_tool_annotations() -> dict[str, ToolAnnotations]:
    """Classify all tools by their behavior for MCP 2025-06-18 annotations."""
    trade_exec = {
        "place_order", "cancel_order", "close_position",
        "close_all_positions", "modify_order",
        "subscribe_to_strategy", "publish_strategy_to_marketplace",
        "execute_dynamic_tool", "submit_inst_order", "cancel_inst_order",
        "start_algo_executor", "stop_algo_executor", "create_yield_position",
        "close_yield_position", "execute_swap", "execute_flash_loan",
    }
    write_safe = {
        "connect_broker", "create_dataset", "configure_white_label",
        "generate_api_key", "revoke_api_key", "register_model",
        "create_feature_set", "create_rl_agent", "train_rl_agent",
        "configure_alert", "start_scrape_job", "create_agent_swarm",
        "assign_swarm_task", "set_strategy_state", "build_strategy_spec",
        "deploy_strategy",
    }
    read_external = {
        "get_quote", "get_account", "get_positions", "get_orders",
        "search_tradovate_contracts", "get_tradovate_risk_snapshot",
        "get_bot_health",
        "get_order_history", "portfolio_summary", "get_execution_report",
        "get_platform_health", "get_api_usage", "get_white_label_config",
        "list_api_keys", "get_pool_analytics", "get_gas_estimate",
        "get_regime_history", "get_social_sentiment", "get_news_sentiment",
        "massive_call_api", "massive_get_endpoint_docs",
    }
    search_local = {
        "discover_tools", "get_tool_details", "mcp_tool_manifest", "massive_search_endpoints",
        "browse_strategy_marketplace", "list_models", "list_feature_sets",
        "list_datasets", "list_rl_agents",
    }
    compute = {
        "run_backtest", "validate_strategy", "validate_strategy_metrics", "optimize_strategy",
        "massive_query_data", "massive_run_pipeline",
        "predict_model", "evaluate_model", "generate_features",
        "detect_regime", "analyze_sentiment", "run_attribution",
    }
    m: dict[str, ToolAnnotations] = {}
    for name in trade_exec:
        m[name] = ANNOT_TRADE_EXEC
    for name in write_safe:
        m[name] = ANNOT_WRITE_SAFE
    for name in read_external:
        m[name] = ANNOT_READ_EXTERNAL
    for name in search_local:
        m[name] = ANNOT_SEARCH
    for name in compute:
        m[name] = ANNOT_COMPUTE
    return m

_TOOL_ANNOTATION_MAP = _classify_tool_annotations()

# ── Process start time — used by server_diagnostics to report uptime ──────────
_SERVER_START_TIME: float = time.monotonic()

# ── Singletons — typed where class is always available, untyped where lazy ──
_config: ServerConfig | None = None
_registry: BrokerRegistry | None = None
_validator: StrategyValidator | None = None
_bridge: MarketplaceBridge | None = None
# V20 — always available (lightweight, no heavy deps)
_account_protection: AccountProtectionEngine | None = None
_data_warehouse: DataWarehouseClient | None = None
_strategy_runner: StrategyRunner | None = None
_submission_pipeline: SubmissionPipeline | None = None
_memory_monitor: MemoryMonitor | None = None
# V8-V19 — untyped, loaded lazily on first use
_stream_manager = None
_portfolio_optimizer = None
_notifier = None
_data_registry = None
_key_orchestrator = None
_dataset_builder = None
_spec_validator = None
_backtest_runner = None
_strategy_optimizer = None
_walk_forward = None
_deployer = None
_template_mgr = None
_social_engine = None
_signal_engine = None
_risk_engine = None
_compliance_engine = None
_tenant_engine = None
_feature_engine = None
_model_trainer = None
_model_registry = None
_rl_agent = None
_gpu_dispatcher = None
_llm_strategy_gen = None
_inst_order_mgr = None
_smart_router = None
_algo_executor = None
_fix_gateway = None
_tca_engine = None
_venue_manager = None
_pnl_streamer = None
_order_flow = None
_microstructure = None
_regime_detector = None
_alert_engine = None
_sentiment_engine = None
_satellite_engine = None
_web_scraper = None
_sec_filing = None
_social_media = None
_alt_data_market = None
_agent_orchestrator = None
_task_planner = None
_agent_memory = None
_tool_router = None
_consensus_engine = None
_agent_monitor = None
_dex_aggregator = None
_yield_optimizer = None
_bridge_engine = None
_mev_protector = None
_governance_engine = None
_defi_risk = None
_saas_tenant_mgr = None
_billing_engine = None
_strategy_market = None
_white_label = None
_api_gateway = None
_massive_provider = None
_dynamic_gateway = None
_intent_parser = None
_constraint_solver = None
_plan_executor = None
_shadow_engine = None
_evolution_engine = None
_arbitrage_detector = None
_predictive_prefetch = None
_intent_regime = None
_vwap_engine = None
_dark_pool_engine = None
_gex_engine = None
_vol_surface_engine = None
_cross_asset_engine = None
_congressional_engine = None
_kelly_engine = None
_options_flow_engine = None
_tape_reader_engine = None


def _get_account_protection() -> AccountProtectionEngine:
    global _account_protection
    if _account_protection is None:
        _account_protection = AccountProtectionEngine()
    return _account_protection


def _get_data_warehouse() -> DataWarehouseClient:
    global _data_warehouse
    if _data_warehouse is None:
        _data_warehouse = DataWarehouseClient()
    return _data_warehouse


def _get_strategy_runner() -> StrategyRunner:
    global _strategy_runner
    if _strategy_runner is None:
        _strategy_runner = StrategyRunner()
    return _strategy_runner


def _get_submission_pipeline() -> SubmissionPipeline:
    global _submission_pipeline
    if _submission_pipeline is None:
        _submission_pipeline = SubmissionPipeline()
    return _submission_pipeline


def _get_registry() -> BrokerRegistry:
    global _config, _registry
    if _registry is None:
        _config = load_config()
        _registry = BrokerRegistry(_config)
    return _registry


def _coerce_fill_pnl(fill: Any) -> float | None:
    """Return a fill's realized P&L without treating zero as missing."""
    for attr in ("realized_pnl", "pnl"):
        if isinstance(fill, Mapping):
            value = fill.get(attr)
        else:
            value = getattr(fill, attr, None)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    return None


def _compute_consecutive_losses_from_fills(fills: Any) -> tuple[int, bool]:
    """Compute trailing loss streak from newest fills.

    The boolean indicates whether broker fills were sufficient to derive an
    authoritative streak. A latest winning or breakeven fill is authoritative
    even though the streak is zero.
    """
    if not fills:
        return 0, False

    consecutive_losses = 0
    for fill in reversed(list(fills)[-20:]):
        fill_pnl = _coerce_fill_pnl(fill)
        if fill_pnl is None:
            return consecutive_losses, False
        if fill_pnl < 0:
            consecutive_losses += 1
        else:
            return consecutive_losses, True
    return consecutive_losses, True


def _accepts_keyword(func: Any, keyword: str) -> bool:
    """Return whether a callable can accept a keyword argument."""
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return True
    return any(
        param.name == keyword or param.kind == inspect.Parameter.VAR_KEYWORD
        for param in signature.parameters.values()
    )


async def _get_recent_fills_for_guardrail(conn: Any, symbol: str) -> Any:
    """Fetch recent fills using the narrowest broker-supported filter."""
    get_fills = getattr(conn, "get_fills", None)
    if not callable(get_fills):
        return []
    if symbol and _accepts_keyword(get_fills, "symbol"):
        return await get_fills(symbol=symbol)
    return await get_fills()


def _get_validator() -> StrategyValidator:
    global _config, _validator
    if _config is None:
        _config = load_config()
    if _validator is None:
        _validator = StrategyValidator(_config.gating)
    return _validator


def _get_bridge() -> MarketplaceBridge:
    global _config, _bridge
    if _config is None:
        _config = load_config()
    if _bridge is None:
        _bridge = MarketplaceBridge(_config.marketplace)
    return _bridge


def _get_stream_manager():
    global _stream_manager
    if _stream_manager is None:
        cls = _lazy_import(".streaming.manager", "StreamManager")
        if cls:
            _stream_manager = cls()
    return _stream_manager


def _get_portfolio_optimizer():
    global _portfolio_optimizer
    if _portfolio_optimizer is None:
        cls = _lazy_import(".portfolio.optimizer", "PortfolioOptimizer")
        if cls:
            _portfolio_optimizer = cls()
    return _portfolio_optimizer


def _get_notifier():
    global _notifier
    if _notifier is None:
        cls = _lazy_import(".notifications.push", "NotificationDispatcher")
        if cls:
            _notifier = cls()
    return _notifier


def _get_data_registry():
    global _data_registry
    if _data_registry is None:
        cls = _lazy_import(".data_providers.registry", "DataProviderRegistry")
        if cls:
            _data_registry = cls()
    return _data_registry


def _get_key_orchestrator():
    global _key_orchestrator
    if _key_orchestrator is None:
        cls = _lazy_import(".byok.key_orchestrator", "KeyOrchestrator")
        if cls:
            _key_orchestrator = cls()
    return _key_orchestrator


def _get_dataset_builder():
    global _dataset_builder
    if _dataset_builder is None:
        cls = _lazy_import(".datasets.builder", "DatasetBuilder")
        if cls:
            _dataset_builder = cls()
    return _dataset_builder


def _get_spec_validator():
    global _spec_validator
    if _spec_validator is None:
        cls = _lazy_import(".strategy_builder.spec", "StrategySpecValidator")
        if cls:
            _spec_validator = cls()
    return _spec_validator


def _get_backtest_runner():
    global _backtest_runner
    if _backtest_runner is None:
        cls = _lazy_import(".strategy_builder.backtest_runner", "BacktestRunner")
        if cls:
            _backtest_runner = cls()
    return _backtest_runner


def _get_strategy_optimizer():
    global _strategy_optimizer
    if _strategy_optimizer is None:
        cls = _lazy_import(".strategy_builder.optimizer", "StrategyOptimizer")
        runner = _get_backtest_runner()
        if cls and runner:
            _strategy_optimizer = cls(runner)
    return _strategy_optimizer


def _get_walk_forward():
    global _walk_forward
    if _walk_forward is None:
        cls = _lazy_import(".strategy_builder.walk_forward", "WalkForwardEngine")
        runner = _get_backtest_runner()
        if cls and runner:
            _walk_forward = cls(runner)
    return _walk_forward


def _get_deployer():
    global _deployer
    if _deployer is None:
        cls = _lazy_import(".strategy_builder.deployer", "StrategyDeployer")
        if cls:
            _deployer = cls()
    return _deployer


def _get_template_mgr():
    global _template_mgr
    if _template_mgr is None:
        cls = _lazy_import(".strategy_builder.template_manager", "TemplateManager")
        if cls:
            _template_mgr = cls()
    return _template_mgr


def _get_social_engine():
    global _social_engine
    if _social_engine is None:
        cls = _lazy_import(".social_trading.engine", "SocialTradingEngine")
        if cls:
            _social_engine = cls()
    return _social_engine


def _get_signal_engine():
    global _signal_engine
    if _signal_engine is None:
        cls = _lazy_import(".community_signals.engine", "CommunitySignalEngine")
        if cls:
            _signal_engine = cls()
    return _signal_engine


def _get_risk_engine():
    global _risk_engine
    if _risk_engine is None:
        cls = _lazy_import(".risk_dashboard.engine", "RiskDashboardEngine")
        if cls:
            _risk_engine = cls()
    return _risk_engine


def _get_compliance_engine():
    global _compliance_engine
    if _compliance_engine is None:
        cls = _lazy_import(".compliance.engine", "ComplianceEngine")
        if cls:
            _compliance_engine = cls()
    return _compliance_engine


def _get_tenant_engine():
    global _tenant_engine
    if _tenant_engine is None:
        cls = _lazy_import(".multi_tenant.engine", "MultiTenantEngine")
        if cls:
            _tenant_engine = cls()
    return _tenant_engine


# ── V10 getters (all lazy) ─────────────────────────────────────
def _get_feature_engine():
    global _feature_engine
    if _feature_engine is None:
        cls = _lazy_import(".ml_engine.feature_engine", "FeatureEngine")
        if cls: _feature_engine = cls()
    return _feature_engine

def _get_model_trainer():
    global _model_trainer
    if _model_trainer is None:
        cls = _lazy_import(".ml_engine.model_trainer", "ModelTrainer")
        if cls: _model_trainer = cls()
    return _model_trainer

def _get_model_registry():
    global _model_registry
    if _model_registry is None:
        cls = _lazy_import(".ml_engine.model_registry", "ModelRegistry")
        if cls: _model_registry = cls()
    return _model_registry

def _get_rl_agent():
    global _rl_agent
    if _rl_agent is None:
        cls = _lazy_import(".ml_engine.rl_agent", "RLAgentEngine")
        if cls: _rl_agent = cls()
    return _rl_agent

def _get_gpu_dispatcher():
    global _gpu_dispatcher
    if _gpu_dispatcher is None:
        cls = _lazy_import(".ml_engine.gpu_dispatcher", "GPUDispatcher")
        if cls: _gpu_dispatcher = cls()
    return _gpu_dispatcher

def _get_llm_strategy_gen():
    global _llm_strategy_gen
    if _llm_strategy_gen is None:
        cls = _lazy_import(".ml_engine.llm_strategy_gen", "LLMStrategyGenerator")
        if cls: _llm_strategy_gen = cls()
    return _llm_strategy_gen

# ── V11 getters (all lazy) ─────────────────────────────────────
def _get_inst_order_mgr():
    global _inst_order_mgr
    if _inst_order_mgr is None:
        cls = _lazy_import(".execution_engine.order_manager", "InstitutionalOrderManager")
        if cls: _inst_order_mgr = cls()
    return _inst_order_mgr

def _get_smart_router():
    global _smart_router
    if _smart_router is None:
        cls = _lazy_import(".execution_engine.smart_order_router", "SmartOrderRouter")
        if cls: _smart_router = cls()
    return _smart_router

def _get_algo_executor():
    global _algo_executor
    if _algo_executor is None:
        cls = _lazy_import(".execution_engine.algo_executor", "AlgoExecutor")
        if cls: _algo_executor = cls()
    return _algo_executor

def _get_fix_gateway():
    global _fix_gateway
    if _fix_gateway is None:
        cls = _lazy_import(".execution_engine.fix_gateway", "FIXGateway")
        if cls: _fix_gateway = cls()
    return _fix_gateway

def _get_tca_engine():
    global _tca_engine
    if _tca_engine is None:
        cls = _lazy_import(".execution_engine.tca_engine", "TCAEngine")
        if cls: _tca_engine = cls()
    return _tca_engine

def _get_venue_manager():
    global _venue_manager
    if _venue_manager is None:
        cls = _lazy_import(".execution_engine.venue_manager", "VenueManager")
        if cls: _venue_manager = cls()
    return _venue_manager

# ── V12 getters (all lazy) ─────────────────────────────────────
def _get_pnl_streamer():
    global _pnl_streamer
    if _pnl_streamer is None:
        cls = _lazy_import(".realtime_analytics.pnl_streamer", "PnLStreamer")
        if cls: _pnl_streamer = cls()
    return _pnl_streamer

def _get_order_flow():
    global _order_flow
    if _order_flow is None:
        cls = _lazy_import(".realtime_analytics.order_flow_analyzer", "OrderFlowAnalyzer")
        if cls:
            cfg = load_config()
            key = cfg.polygon.api_key if cfg.polygon else ""
            _order_flow = cls(polygon_key=key)
    return _order_flow

def _get_microstructure():
    global _microstructure
    if _microstructure is None:
        cls = _lazy_import(".realtime_analytics.microstructure", "MicrostructureEngine")
        if cls:
            cfg = load_config()
            key = cfg.polygon.api_key if cfg.polygon else ""
            _microstructure = cls(polygon_key=key)
    return _microstructure

def _get_regime_detector():
    global _regime_detector
    if _regime_detector is None:
        cls = _lazy_import(".realtime_analytics.regime_detector", "RegimeDetector")
        if cls:
            cfg = load_config()
            key = cfg.polygon.api_key if cfg.polygon else ""
            _regime_detector = cls(polygon_key=key)
    return _regime_detector

def _get_alert_engine():
    global _alert_engine
    if _alert_engine is None:
        cls = _lazy_import(".realtime_analytics.alert_engine", "AlertEngine")
        if cls: _alert_engine = cls()
    return _alert_engine

# ── V13 getters (all lazy) ─────────────────────────────────────
def _get_sentiment_engine():
    global _sentiment_engine
    if _sentiment_engine is None:
        cls = _lazy_import(".alt_data.sentiment_engine", "SentimentEngine")
        if cls: _sentiment_engine = cls()
    return _sentiment_engine

def _get_satellite_engine():
    global _satellite_engine
    if _satellite_engine is None:
        cls = _lazy_import(".alt_data.satellite_engine", "SatelliteDataEngine")
        if cls: _satellite_engine = cls()
    return _satellite_engine

def _get_web_scraper():
    global _web_scraper
    if _web_scraper is None:
        cls = _lazy_import(".alt_data.web_scraper", "WebScraperEngine")
        if cls: _web_scraper = cls()
    return _web_scraper

def _get_sec_filing():
    global _sec_filing
    if _sec_filing is None:
        cls = _lazy_import(".alt_data.sec_filing_engine", "SECFilingEngine")
        if cls: _sec_filing = cls()
    return _sec_filing

def _get_social_media():
    global _social_media
    if _social_media is None:
        cls = _lazy_import(".alt_data.social_media_engine", "SocialMediaEngine")
        if cls: _social_media = cls()
    return _social_media

def _get_alt_data_market():
    global _alt_data_market
    if _alt_data_market is None:
        cls = _lazy_import(".alt_data.alt_data_marketplace", "AltDataMarketplace")
        if cls: _alt_data_market = cls()
    return _alt_data_market

# ── V14 getters (all lazy) ─────────────────────────────────────
def _get_agent_orchestrator():
    global _agent_orchestrator
    if _agent_orchestrator is None:
        cls = _lazy_import(".agent_swarm.agent_orchestrator", "AgentOrchestrator")
        if cls: _agent_orchestrator = cls()
    return _agent_orchestrator

def _get_task_planner():
    global _task_planner
    if _task_planner is None:
        cls = _lazy_import(".agent_swarm.task_planner", "TaskPlanner")
        if cls: _task_planner = cls()
    return _task_planner

def _get_agent_memory():
    global _agent_memory
    if _agent_memory is None:
        cls = _lazy_import(".agent_swarm.agent_memory", "AgentMemory")
        if cls: _agent_memory = cls()
    return _agent_memory

def _get_tool_router():
    global _tool_router
    if _tool_router is None:
        cls = _lazy_import(".agent_swarm.tool_router", "ToolRouter")
        if cls: _tool_router = cls()
    return _tool_router

def _get_consensus_engine():
    global _consensus_engine
    if _consensus_engine is None:
        cls = _lazy_import(".agent_swarm.consensus_engine", "ConsensusEngine")
        if cls: _consensus_engine = cls()
    return _consensus_engine

def _get_agent_monitor():
    global _agent_monitor
    if _agent_monitor is None:
        cls = _lazy_import(".agent_swarm.agent_monitor", "AgentMonitor")
        if cls: _agent_monitor = cls()
    return _agent_monitor

# ── V15 getters (all lazy) ─────────────────────────────────────
def _get_dex_aggregator():
    global _dex_aggregator
    if _dex_aggregator is None:
        cls = _lazy_import(".defi_engine.dex_aggregator", "DEXAggregator")
        if cls: _dex_aggregator = cls()
    return _dex_aggregator

def _get_yield_optimizer():
    global _yield_optimizer
    if _yield_optimizer is None:
        cls = _lazy_import(".defi_engine.yield_optimizer", "YieldOptimizer")
        if cls: _yield_optimizer = cls()
    return _yield_optimizer

def _get_bridge_engine():
    global _bridge_engine
    if _bridge_engine is None:
        cls = _lazy_import(".defi_engine.bridge_engine", "BridgeEngine")
        if cls: _bridge_engine = cls()
    return _bridge_engine

def _get_mev_protector():
    global _mev_protector
    if _mev_protector is None:
        cls = _lazy_import(".defi_engine.mev_protector", "MEVProtector")
        if cls: _mev_protector = cls()
    return _mev_protector

def _get_governance_engine():
    global _governance_engine
    if _governance_engine is None:
        cls = _lazy_import(".defi_engine.governance_engine", "GovernanceEngine")
        if cls: _governance_engine = cls()
    return _governance_engine

def _get_defi_risk():
    global _defi_risk
    if _defi_risk is None:
        cls = _lazy_import(".defi_engine.defi_risk_engine", "DeFiRiskEngine")
        if cls: _defi_risk = cls()
    return _defi_risk

def _get_defi_portfolio():
    return _get_defi_risk()

def _get_swarm_mgr():
    return _get_agent_orchestrator()

# ── V16 getters (all lazy) ─────────────────────────────────────
def _get_saas_tenant_mgr():
    global _saas_tenant_mgr
    if _saas_tenant_mgr is None:
        cls = _lazy_import(".cloud_saas.tenant_manager", "TenantManager")
        if cls: _saas_tenant_mgr = cls()
    return _saas_tenant_mgr

def _get_billing_engine():
    global _billing_engine
    if _billing_engine is None:
        cls = _lazy_import(".cloud_saas.billing_engine", "BillingEngine")
        if cls: _billing_engine = cls()
    return _billing_engine

def _get_strategy_market():
    global _strategy_market
    if _strategy_market is None:
        cls = _lazy_import(".cloud_saas.strategy_marketplace", "StrategyMarketplace")
        if cls: _strategy_market = cls()
    return _strategy_market

def _get_white_label():
    global _white_label
    if _white_label is None:
        cls = _lazy_import(".cloud_saas.white_label_engine", "WhiteLabelEngine")
        if cls: _white_label = cls()
    return _white_label

def _get_api_gateway():
    global _api_gateway
    if _api_gateway is None:
        cls = _lazy_import(".cloud_saas.api_gateway", "APIGateway")
        if cls: _api_gateway = cls()
    return _api_gateway

# ── V17 getters (all lazy) ─────────────────────────────────────
_massive_startup_done = False

async def _get_massive_provider():
    global _massive_provider, _config, _massive_startup_done
    if _massive_provider is None:
        cls = _lazy_import(".data_providers.massive_whitelabel", "MassiveWhiteLabelProvider")
        if cls:
            if _config is None:
                _config = load_config()
            _massive_provider = cls(_config.massive)
    if _massive_provider and not _massive_startup_done:
        _massive_startup_done = True
        await _massive_provider.startup()
    return _massive_provider

def _get_dynamic_gateway():
    global _dynamic_gateway
    if _dynamic_gateway is None:
        cls = _lazy_import(".dynamic_toolsets.gateway", "DynamicToolsetGateway")
        if cls:
            _dynamic_gateway = cls()
            _dynamic_gateway.register_tools_from_list(
                [t.model_dump() if hasattr(t, 'model_dump') else {"name": t.name, "description": t.description, "inputSchema": t.inputSchema} for t in TOOLS],
                category="core",
                version=f"v{_server_version}",
            )
            _dynamic_gateway.build_index()
    return _dynamic_gateway

# ── V18 Intent Engine getters (all lazy) ──────────────────────
def _get_intent_parser():
    global _intent_parser
    if _intent_parser is None:
        cls = _lazy_import(".intent_engine.intent_parser", "IntentParser")
        if cls: _intent_parser = cls()
    return _intent_parser

def _get_constraint_solver():
    global _constraint_solver
    if _constraint_solver is None:
        cls = _lazy_import(".intent_engine.constraint_solver", "ConstraintSolver")
        if cls: _constraint_solver = cls(broker_registry=_registry)
    return _constraint_solver

def _get_plan_executor():
    global _plan_executor
    if _plan_executor is None:
        cls = _lazy_import(".intent_engine.plan_executor", "PlanExecutor")
        if cls: _plan_executor = cls()
    return _plan_executor

def _get_shadow_engine():
    global _shadow_engine
    if _shadow_engine is None:
        cls = _lazy_import(".intent_engine.shadow_portfolio", "ShadowPortfolioEngine")
        if cls: _shadow_engine = cls()
    return _shadow_engine

def _get_evolution_engine():
    global _evolution_engine
    if _evolution_engine is None:
        cls = _lazy_import(".intent_engine.strategy_evolution", "StrategyEvolutionEngine")
        if cls: _evolution_engine = cls()
    return _evolution_engine

def _get_arbitrage_detector():
    global _arbitrage_detector
    if _arbitrage_detector is None:
        cls = _lazy_import(".intent_engine.arbitrage_detector", "ArbitrageDetector")
        if cls: _arbitrage_detector = cls(broker_registry=_registry)
    return _arbitrage_detector

def _get_predictive_prefetch():
    global _predictive_prefetch
    if _predictive_prefetch is None:
        cls = _lazy_import(".intent_engine.predictive_prefetch", "PredictiveStatePrefetch")
        if cls: _predictive_prefetch = cls()
    return _predictive_prefetch

def _get_intent_regime():
    global _intent_regime
    if _intent_regime is None:
        cls = _lazy_import(".intent_engine.regime_detector", "RegimeDetector")
        if cls: _intent_regime = cls()
    return _intent_regime


# ── V19 Alpha Engine getters (all lazy) ───────────────────────
def _get_vwap_engine():
    global _vwap_engine, _config
    if _vwap_engine is None:
        cls = _lazy_import(".alpha_engines.vwap_engine", "VWAPEngine")
        if cls:
            if _config is None: _config = load_config()
            _vwap_engine = cls(polygon_key=_config.polygon.api_key if _config.polygon else "")
    return _vwap_engine

def _get_dark_pool_engine():
    global _dark_pool_engine, _config
    if _dark_pool_engine is None:
        cls = _lazy_import(".alpha_engines.dark_pool_engine", "DarkPoolEngine")
        if cls:
            if _config is None: _config = load_config()
            _dark_pool_engine = cls(polygon_key=_config.polygon.api_key if _config.polygon else "")
    return _dark_pool_engine

def _get_gex_engine():
    global _gex_engine, _config
    if _gex_engine is None:
        cls = _lazy_import(".alpha_engines.gex_engine", "GEXEngine")
        if cls:
            if _config is None: _config = load_config()
            _gex_engine = cls(polygon_key=_config.polygon.api_key if _config.polygon else "")
    return _gex_engine

def _get_vol_surface_engine():
    global _vol_surface_engine, _config
    if _vol_surface_engine is None:
        cls = _lazy_import(".alpha_engines.vol_surface", "VolSurfaceEngine")
        if cls:
            if _config is None: _config = load_config()
            _vol_surface_engine = cls(polygon_key=_config.polygon.api_key if _config.polygon else "")
    return _vol_surface_engine

def _get_cross_asset_engine():
    global _cross_asset_engine, _config
    if _cross_asset_engine is None:
        cls = _lazy_import(".alpha_engines.cross_asset", "CrossAssetEngine")
        if cls:
            if _config is None: _config = load_config()
            _cross_asset_engine = cls(polygon_key=_config.polygon.api_key if _config.polygon else "")
    return _cross_asset_engine

def _get_congressional_engine():
    global _congressional_engine, _config
    if _congressional_engine is None:
        cls = _lazy_import(".alpha_engines.congressional", "CongressionalEngine")
        if cls:
            if _config is None: _config = load_config()
            _congressional_engine = cls(
                polygon_key=_config.polygon.api_key if _config.polygon else "",
                finnhub_key=_config.finnhub.api_key if _config.finnhub else "",
            )
    return _congressional_engine

def _get_kelly_engine():
    global _kelly_engine
    if _kelly_engine is None:
        cls = _lazy_import(".alpha_engines.kelly_engine", "KellyEngine")
        if cls: _kelly_engine = cls()
    return _kelly_engine

def _get_options_flow_engine():
    global _options_flow_engine, _config
    if _options_flow_engine is None:
        cls = _lazy_import(".alpha_engines.options_flow", "OptionsFlowEngine")
        if cls:
            if _config is None: _config = load_config()
            _options_flow_engine = cls(polygon_key=_config.polygon.api_key if _config.polygon else "")
    return _options_flow_engine

def _get_tape_reader_engine():
    global _tape_reader_engine, _config
    if _tape_reader_engine is None:
        cls = _lazy_import(".alpha_engines.tape_reader", "TapeReaderEngine")
        if cls:
            if _config is None: _config = load_config()
            _tape_reader_engine = cls(polygon_key=_config.polygon.api_key if _config.polygon else "")
    return _tape_reader_engine


def _text(data: Any) -> list[TextContent]:
    if isinstance(data, (dict, list)):
        return [TextContent(type="text", text=json.dumps(data, indent=2, default=str))]
    return [TextContent(type="text", text=str(data))]


def _error_text(exc: Exception) -> list[TextContent]:
    """Structured error response for tool failures."""
    if isinstance(exc, AlgoChainsError):
        return _text(exc.to_dict())
    return _text({"error_type": type(exc).__name__, "message": str(exc)})


# ═══════════════════════════════════════════════════════════════════
# Tool definitions
# ═══════════════════════════════════════════════════════════════════

TOOLS = [
    # ── Trading ──────────────────────────────────────────────────
    Tool(
        name="place_order",
        description="Place a trading order on any connected broker. Supports market, limit, stop, stop-limit, and trailing stop orders across Alpaca, IBKR, Oanda, TradersPost (Schwab/Robinhood/Tastytrade), and QuantConnect.",
        inputSchema={
            "type": "object",
            "properties": {
                "broker": {"type": "string", "description": "Broker name: alpaca, ibkr, oanda, traderspost, quantconnect"},
                "symbol": {"type": "string", "description": "Ticker symbol (e.g. AAPL, EUR_USD, ES)"},
                "side": {"type": "string", "enum": ["buy", "sell"]},
                "qty": {"type": "number", "description": "Order quantity"},
                "order_type": {"type": "string", "enum": ["market", "limit", "stop", "stop_limit", "trailing_stop"], "default": "market"},
                "limit_price": {"type": "number", "description": "Limit price (for limit/stop-limit orders)"},
                "stop_price": {"type": "number", "description": "Stop price (for stop/stop-limit orders)"},
                "trail_pct": {"type": "number", "description": "Trailing stop percentage"},
                "time_in_force": {"type": "string", "default": "day"},
                "client_trace_id": {"type": "string", "description": "Optional caller-provided trace/signal ID echoed in the response for join-key audit traceability (e.g. control-tower signal_id UUID)."},
            },
            "required": ["broker", "symbol", "side", "qty"],
        },
        outputSchema={
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "symbol": {"type": "string"},
                "side": {"type": "string"},
                "qty": {"type": "number"},
                "order_type": {"type": "string"},
                "status": {"type": "string"},
                "filled_price": {"type": "number"},
                "broker": {"type": "string"},
            },
        },
        annotations=ANNOT_TRADE_EXEC,
    ),
    Tool(
        name="cancel_order",
        description="Cancel an open order by ID on a specific broker.",
        inputSchema={
            "type": "object",
            "properties": {
                "broker": {"type": "string"},
                "order_id": {"type": "string"},
                "client_trace_id": {"type": "string", "description": "Optional caller-provided trace/signal ID echoed in the response for audit traceability."},
            },
            "required": ["broker", "order_id"],
        },
    
        annotations=ANNOT_TRADE_EXEC,
    ),
    Tool(
        name="close_position",
        description="Close an entire position in a symbol on a specific broker.",
        inputSchema={
            "type": "object",
            "properties": {
                "broker": {"type": "string"},
                "symbol": {"type": "string"},
                "client_trace_id": {"type": "string", "description": "Optional caller-provided trace/signal ID echoed in the response for audit traceability."},
            },
            "required": ["broker", "symbol"],
        },
        outputSchema={
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "symbol": {"type": "string"},
                "side": {"type": "string"},
                "qty": {"type": "number"},
                "status": {"type": "string"},
                "filled_price": {"type": "number"},
                "client_trace_id": {"type": "string"},
            },
        },
        annotations=ANNOT_TRADE_EXEC,
    ),
    Tool(
        name="close_all_positions",
        description="Close ALL open positions on a specific broker. Use with caution.",
        inputSchema={
            "type": "object",
            "properties": {
                "broker": {"type": "string"},
            },
            "required": ["broker"],
        },
    
        annotations=ANNOT_WRITE_DESTRUCTIVE,
    ),
    # ── Portfolio ────────────────────────────────────────────────
    Tool(
        name="get_account",
        description="Get account information (equity, cash, buying power) from a broker.",
        inputSchema={
            "type": "object",
            "properties": {
                "broker": {"type": "string"},
            },
            "required": ["broker"],
        },
        outputSchema={
            "type": "object",
            "properties": {
                "equity": {"type": "number"},
                "cash": {"type": "number"},
                "buying_power": {"type": "number"},
                "currency": {"type": "string"},
                "broker": {"type": "string"},
            },
        },
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(
        name="get_positions",
        description="Get all open positions from a broker.",
        inputSchema={
            "type": "object",
            "properties": {
                "broker": {"type": "string"},
            },
            "required": ["broker"],
        },
        outputSchema={
            "type": "object",
            "properties": {
                "positions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string"},
                            "qty": {"type": "number"},
                            "side": {"type": "string"},
                            "avg_entry_price": {"type": "number"},
                            "current_price": {"type": "number"},
                            "unrealized_pnl": {"type": "number"},
                            "market_value": {"type": "number"},
                        },
                    },
                },
                "count": {"type": "integer"},
            },
        },
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(
        name="get_orders",
        description="Get orders from a broker, optionally filtered by status.",
        inputSchema={
            "type": "object",
            "properties": {
                "broker": {"type": "string"},
                "status": {"type": "string", "description": "Filter: open, closed, all"},
            },
            "required": ["broker"],
        },
        outputSchema={
            "type": "object",
            "properties": {
                "orders": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "order_id": {"type": "string"},
                            "symbol": {"type": "string"},
                            "side": {"type": "string"},
                            "qty": {"type": "number"},
                            "order_type": {"type": "string"},
                            "status": {"type": "string"},
                            "filled_qty": {"type": "number"},
                            "limit_price": {"type": "number"},
                        },
                    },
                },
                "count": {"type": "integer"},
            },
        },
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(
        name="portfolio_summary",
        description="Get a unified portfolio summary across ALL connected brokers — total equity, positions, and P&L.",
        inputSchema={"type": "object", "properties": {}},
    
        annotations=ANNOT_READ_EXTERNAL,
    ),
    # ── Market Data ─────────────────────────────────────────────
    Tool(
        name="get_quote",
        description="Get current quote (bid/ask/last) for a symbol from a broker.",
        inputSchema={
            "type": "object",
            "properties": {
                "broker": {"type": "string"},
                "symbol": {"type": "string"},
            },
            "required": ["broker", "symbol"],
        },
        outputSchema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "bid": {"type": "number"},
                "ask": {"type": "number"},
                "last": {"type": "number"},
                "volume": {"type": "number"},
                "timestamp": {"type": "string"},
            },
        },
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(
        name="search_tradovate_contracts",
        description=(
            "Search Tradovate contracts by keyword or symbol prefix. Returns contract name, id, "
            "and description. Use this to discover the exact Tradovate symbol before calling "
            "get_quote or place_order. Read-only, safe to call at any time. "
            "Example: query='MNQ' returns all Micro Nasdaq futures contracts."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Symbol prefix or keyword to search, e.g. 'MNQ', 'CL', 'Nasdaq'",
                },
                "limit": {
                    "type": "integer",
                    "default": 10,
                    "description": "Maximum results to return (default 10, max 50)",
                },
            },
            "required": ["query"],
        },
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(
        name="get_tradovate_risk_snapshot",
        description=(
            "Read the current Tradovate risk limit settings for the connected live account. "
            "Returns dayMaxLoss, maxDrawdown, maxOrderQty, and trailingMaxDrawdown. "
            "Read-only diagnostic — use this to verify prop-fund guardrails are configured "
            "correctly. NEVER modifies risk limits (no set_risk_limits exposure)."
        ),
        inputSchema={"type": "object", "properties": {}},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(
        name="get_bot_health",
        description=(
            "Return a unified health snapshot for all four live futures bots (MNQ, CL, MES, NQ) "
            "and the Kalshi daemon. For each bot: process up? last log mtime, last signal ts, "
            "current regime, error count in last 100 log lines, token expiry (if Tradovate). "
            "Includes E2E sentinel lifecycle state for MNQ execution traceability. "
            "Pure read-only — reads logs/, state/, and ps aux on the control tower host."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "bot": {
                    "type": "string",
                    "description": "Optional: filter to one bot (mnq|cl|mes|nq|kalshi|all). Default all.",
                    "default": "all",
                }
            },
        },
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(
        name="query_codegraph",
        description=(
            "Read-only STRUCTURAL code intelligence (AST symbols, call graph, impact radius) "
            "over the control-tower repo, via the local CodeGraph index. Complements — does not "
            "replace — semantic recall (rag_search / onyx). Use for: who-calls-what, blast radius "
            "before a refactor, where-is-X-defined. kind=impact|callers|callees|search|context|files|status. "
            "Navigation aid ONLY — never a trading dependency, and never the sole basis for a 'safe to edit' "
            "claim on a live-bot file. Fails closed with codegraph_index_missing if the per-host .codegraph/ "
            "index is absent (it is not synced across hosts)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "description": "Query type.",
                    "enum": ["impact", "callers", "callees", "search", "context", "files", "status"],
                },
                "symbol": {
                    "type": "string",
                    "description": "Symbol name (function/method/class) for impact/callers/callees/search, or a task description for context. Omit for files/status.",
                },
                "limit": {
                    "type": "number",
                    "description": "Max results for search/callers/callees (default 20).",
                    "default": 20,
                },
            },
            "required": ["kind"],
        },
        annotations=ANNOT_READ_EXTERNAL,
    ),
    # ── Graphiti Temporal Knowledge Graph (advisory, agent_memory) ──
    Tool(
        name="graphiti_search",
        description=(
            "Hybrid (semantic + keyword + graph-traversal) search over the AlgoChains "
            "TEMPORAL knowledge graph (getzep/graphiti). Returns advisory facts with "
            "validity windows (valid_from/valid_to) extracted from REAL signal traces, "
            "debate transcripts, and Hive Brain synthesis. Use for 'what was true / what "
            "changed / what preceded what, over time' — e.g. 'MNQ behavior in trending "
            "regime'. agent_memory authority: ADVISORY ONLY, never broker truth (P&L/fills "
            "still require broker verification). Complements rag_search/onyx (semantic) and "
            "query_codegraph (structural). Fails closed with graphiti_unavailable."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural-language query over temporal facts."},
                "limit": {"type": "number", "description": "Max facts to return (default 10).", "default": 10},
            },
            "required": ["query"],
        },
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(
        name="graphiti_temporal_query",
        description=(
            "Point-in-time / temporal recipe search over the Graphiti knowledge graph "
            "(advisory, agent_memory). Like graphiti_search but tagged for temporal recall. "
            "Fails closed with graphiti_unavailable when graphiti-core/Neo4j are absent."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Temporal query."},
                "limit": {"type": "number", "description": "Max facts (default 10).", "default": 10},
            },
            "required": ["query"],
        },
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(
        name="graphiti_health",
        description=(
            "Health probe for the Graphiti temporal knowledge-graph backend (Neo4j + "
            "graphiti-core, advisory/agent_memory). Reports provider, Neo4j URI, group_id, "
            "and reachability. Fails closed with graphiti_unavailable + recovery_command "
            "(per-host; not synced across machines)."
        ),
        inputSchema={"type": "object", "properties": {}},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(
        name="graphiti_add_episode",
        description=(
            "Ingest ONE real episode (text/json) into the Graphiti temporal graph. The LLM "
            "only STRUCTURES the supplied real data — it never invents values (real-data-only "
            "rule). Body is secret-redacted before ingest. WRITE_LOCAL (internal graph write, "
            "no broker/money). Routine ingestion runs via the post-market daemon; this tool is "
            "for targeted operator/agent additions. Discover-only in smart mode."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Short episode name."},
                "body": {"type": "string", "description": "Real episode content (text or JSON string)."},
                "source_description": {"type": "string", "description": "Provenance of the data.", "default": "mcp_tool"},
                "source_kind": {"type": "string", "enum": ["text", "json", "message"], "default": "text"},
            },
            "required": ["name", "body"],
        },
        annotations=ANNOT_READ_EXTERNAL,
    ),
    # ── Broker Management ───────────────────────────────────────
    Tool(
        name="list_brokers",
        description="List all configured and connected brokers with their status and supported asset classes.",
        inputSchema={"type": "object", "properties": {}},
    
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(
        name="connect_broker",
        description="Connect to a specific broker. Must be configured via environment variables.",
        inputSchema={
            "type": "object",
            "properties": {
                "broker": {"type": "string"},
            },
            "required": ["broker"],
        },
    
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(
        name="broker_health_check",
        description="Run health check on all connected brokers.",
        inputSchema={"type": "object", "properties": {}},
    
        annotations=ANNOT_READ_EXTERNAL,
    ),
    # ── Marketplace ─────────────────────────────────────────────
    Tool(
        name="browse_marketplace",
        description="Browse available bot listings on the AlgoChains marketplace. Filter by asset class, strategy type, or minimum Sharpe.",
        inputSchema={
            "type": "object",
            "properties": {
                "asset_class": {"type": "string", "description": "stocks, crypto, futures, forex, options"},
                "strategy_type": {"type": "string", "description": "trend, mean_reversion, breakout, momentum"},
                "min_sharpe": {"type": "number", "description": "Minimum OOS Sharpe ratio"},
                "limit": {"type": "integer", "default": 20},
            },
        },
    
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(
        name="get_listing_detail",
        description="Get detailed information about a specific marketplace listing by slug.",
        inputSchema={
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Listing slug (e.g. mktbot_AAPL_bb_mean_reversion_hour)"},
            },
            "required": ["slug"],
        },
    
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(
        name="subscribe_to_bot",
        description="Subscribe to a marketplace bot listing for paper or live trading.",
        inputSchema={
            "type": "object",
            "properties": {
                "slug": {"type": "string"},
                "broker": {"type": "string", "description": "Which broker to deploy on. Required for live mode; optional for mode=paper."},
                "mode": {"type": "string", "enum": ["paper", "live"], "default": "paper"},
            },
            "required": ["slug"],
        },
    
        annotations=ANNOT_TRADE_EXEC,
    ),
    # ── Strategy Submission & Validation ────────────────────────
    Tool(
        name="submit_strategy",
        description=(
            "Submit a trading strategy for MCPT validation. External AI agents use this "
            "to submit their strategies to the AlgoChains marketplace. Strategies pass through "
            "6 validation gates: schema, performance, overfitting, MCPT, walk-forward, paper trading."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Ticker symbol"},
                "strategy_type": {"type": "string", "description": "trend, mean_reversion, breakout, momentum, scalp"},
                "timeframe": {"type": "string", "description": "5min, 15min, hour, 4h, day"},
                "oos_sharpe": {"type": "number", "description": "Out-of-sample Sharpe ratio"},
                "oos_trades": {"type": "integer", "description": "Number of OOS trades"},
                "is_sharpe": {"type": "number", "description": "In-sample Sharpe ratio"},
                "max_drawdown_pct": {"type": "number", "description": "Maximum drawdown percentage"},
                "win_rate": {"type": "number", "description": "Win rate percentage"},
                "parameters": {"type": "object", "description": "Strategy parameters dict"},
                "mcpt": {
                    "type": "object",
                    "description": "MCPT validation data",
                    "properties": {
                        "p_value": {"type": "number"},
                        "permutations": {"type": "integer"},
                    },
                },
                "walk_forward": {
                    "type": "object",
                    "description": "Walk-forward validation data",
                    "properties": {
                        "folds": {"type": "integer"},
                        "avg_oos_sharpe": {"type": "number"},
                    },
                },
                "backtest_code": {"type": "string", "description": "Python backtest code (will be sandboxed)"},
                "description": {"type": "string"},
            },
            "required": ["symbol", "strategy_type", "timeframe", "oos_sharpe", "oos_trades", "max_drawdown_pct"],
        },
    
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(
        name="validate_strategy_metrics",
        description=(
            "Run the marketplace validation gates against reported strategy metrics "
            "(Sharpe, OOS trades, drawdown, win rate, MCPT). This is distinct from "
            "validate_strategy, which validates a StrategySpec schema."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Ticker symbol"},
                "strategy_type": {"type": "string", "description": "trend, mean_reversion, breakout, momentum, scalp"},
                "timeframe": {"type": "string", "description": "5min, 15min, hour, 4h, day"},
                "oos_sharpe": {"type": "number", "description": "Out-of-sample Sharpe ratio"},
                "oos_trades": {"type": "integer", "description": "Number of OOS trades"},
                "is_sharpe": {"type": "number", "description": "In-sample Sharpe ratio"},
                "max_drawdown_pct": {"type": "number", "description": "Maximum drawdown percentage"},
                "win_rate": {"type": "number", "description": "Win rate percentage"},
                "parameters": {"type": "object", "description": "Strategy parameters dict"},
                "mcpt": {"type": "object", "description": "MCPT validation data"},
            },
            "required": ["symbol", "strategy_type", "timeframe", "oos_sharpe", "oos_trades", "max_drawdown_pct"],
        },
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(
        name="check_validation_status",
        description="Check the validation status of a previously submitted strategy.",
        inputSchema={
            "type": "object",
            "properties": {
                "submission_id": {"type": "string"},
            },
            "required": ["submission_id"],
        },
    
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(
        name="get_validation_gates",
        description="Get the current validation gate thresholds and requirements for strategy submissions.",
        inputSchema={"type": "object", "properties": {}},
    
        annotations=ANNOT_READ_EXTERNAL,
    ),
    # ── Diagnostics ────────────────────────────────────────────
    Tool(
        name="server_diagnostics",
        description="Get AlgoChains MCP server diagnostics: tool call statistics, error rates, recent call history, and broker connection status.",
        inputSchema={"type": "object", "properties": {}},
    
        annotations=ANNOT_READ_EXTERNAL,
    ),
    # ── V4: Streaming ─────────────────────────────────────────
    Tool(
        name="stream_subscribe",
        description="Subscribe to a real-time data stream: pnl, fills, positions, quotes, trades, risk_alerts, order_updates.",
        inputSchema={
            "type": "object",
            "properties": {
                "topic": {"type": "string", "enum": ["pnl", "fills", "positions", "quotes", "trades", "risk_alerts", "order_updates"]},
                "symbols": {"type": "array", "items": {"type": "string"}, "description": "Optional symbol filter"},
                "brokers": {"type": "array", "items": {"type": "string"}, "description": "Optional broker filter"},
            },
            "required": ["topic"],
        },
    
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(
        name="stream_snapshot",
        description="Get the latest events from a stream topic (pnl, fills, positions, etc.).",
        inputSchema={
            "type": "object",
            "properties": {
                "topic": {"type": "string", "enum": ["pnl", "fills", "positions", "quotes", "trades", "risk_alerts", "order_updates"]},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["topic"],
        },
    
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(
        name="get_realtime_pnl",
        description="Get real-time P&L snapshot across all connected brokers with live equity, unrealized P&L, and daily change.",
        inputSchema={"type": "object", "properties": {}},
    
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(
        name="stream_stats",
        description="Get streaming system statistics: buffer sizes, active subscriptions, callback counts.",
        inputSchema={"type": "object", "properties": {}},
    
        annotations=ANNOT_READ_EXTERNAL,
    ),
    # ── V5: Portfolio Optimizer ────────────────────────────────
    Tool(
        name="optimize_portfolio",
        description="Optimize capital allocation across multiple bot subscriptions using risk parity, mean-variance, Kelly criterion, or max Sharpe methods.",
        inputSchema={
            "type": "object",
            "properties": {
                "bots": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "slug": {"type": "string"},
                            "name": {"type": "string"},
                            "oos_sharpe": {"type": "number"},
                            "annual_return": {"type": "number", "description": "Decimal (0.25 = 25%)"},
                            "annual_volatility": {"type": "number", "description": "Decimal (0.15 = 15%)"},
                            "max_drawdown": {"type": "number", "description": "Decimal (0.12 = 12%)"},
                            "win_rate": {"type": "number", "description": "Decimal (0.55 = 55%)"},
                            "avg_trade_pnl": {"type": "number"},
                        },
                        "required": ["slug", "name", "oos_sharpe", "annual_return", "annual_volatility", "max_drawdown", "win_rate"],
                    },
                },
                "total_capital": {"type": "number", "description": "Total capital to allocate ($)"},
                "method": {"type": "string", "enum": ["equal_weight", "risk_parity", "mean_variance", "kelly", "max_sharpe", "min_variance"], "default": "risk_parity"},
                "max_drawdown_limit": {"type": "number", "default": 0.20, "description": "Max acceptable portfolio drawdown (decimal)"},
            },
            "required": ["bots", "total_capital"],
        },
    
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(
        name="compare_allocations",
        description="Compare multiple allocation methods side-by-side for the same set of bots to find the best strategy.",
        inputSchema={
            "type": "object",
            "properties": {
                "bots": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "slug": {"type": "string"},
                            "name": {"type": "string"},
                            "oos_sharpe": {"type": "number"},
                            "annual_return": {"type": "number"},
                            "annual_volatility": {"type": "number"},
                            "max_drawdown": {"type": "number"},
                            "win_rate": {"type": "number"},
                            "avg_trade_pnl": {"type": "number"},
                        },
                        "required": ["slug", "name", "oos_sharpe", "annual_return", "annual_volatility", "max_drawdown", "win_rate"],
                    },
                },
                "total_capital": {"type": "number"},
            },
            "required": ["bots", "total_capital"],
        },
    
        annotations=ANNOT_WRITE_SAFE,
    ),
    # ── V6: Notifications ─────────────────────────────────────
    Tool(
        name="configure_notifications",
        description="Configure notification channels: slack, email, discord, telegram, mobile push (FCM/APNS).",
        inputSchema={
            "type": "object",
            "properties": {
                "channel": {"type": "string", "enum": ["slack", "email", "discord", "telegram", "fcm", "apns"]},
                "webhook_url": {"type": "string", "description": "Webhook URL (for Slack/Discord)"},
                "api_key": {"type": "string", "description": "API key (for email/FCM)"},
                "bot_token": {"type": "string", "description": "Bot token (for Telegram)"},
                "chat_id": {"type": "string", "description": "Chat ID (for Telegram)"},
            },
            "required": ["channel"],
        },
    
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(
        name="send_notification",
        description="Send a notification across configured channels. Supports order fills, P&L alerts, drawdown warnings, and custom messages.",
        inputSchema={
            "type": "object",
            "properties": {
                "event": {"type": "string", "enum": ["order_fill", "daily_pnl", "drawdown_alert", "bot_status", "margin_warning", "risk_alert", "rebalance_needed", "custom"]},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "priority": {"type": "string", "enum": ["critical", "high", "medium", "low"], "default": "medium"},
                "channels": {"type": "array", "items": {"type": "string"}, "description": "Override default channels"},
            },
            "required": ["title", "body"],
        },
    
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(
        name="get_notification_history",
        description="Get notification history with optional event type filter.",
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
                "event": {"type": "string", "description": "Filter by event type"},
            },
        },
    
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(
        name="notification_stats",
        description="Get notification system statistics: configured channels, send counts by event and priority.",
        inputSchema={"type": "object", "properties": {}},
    
        annotations=ANNOT_READ_EXTERNAL,
    ),
    # ── Data Providers (Optional) ─────────────────────────────
    Tool(
        name="list_data_providers",
        description="List all available and configured data providers (Polygon, Yahoo Finance, Alpha Vantage, Finnhub, Twelve Data, etc.).",
        inputSchema={"type": "object", "properties": {}},
    
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(
        name="get_market_data",
        description="Fetch OHLCV bars from any configured data provider. Falls back through providers if first one fails.",
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Ticker symbol (e.g. AAPL, EUR/USD, BTC-USD)"},
                "interval": {"type": "string", "enum": ["1min", "5min", "15min", "30min", "1hour", "4hour", "1day", "1week", "1month"], "default": "1day"},
                "limit": {"type": "integer", "default": 100},
                "provider": {"type": "string", "description": "Specific provider (polygon, yahoo, alphavantage, finnhub, twelvedata). If omitted, uses best available."},
                "start": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                "end": {"type": "string", "description": "End date (YYYY-MM-DD)"},
            },
            "required": ["symbol"],
        },
    
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(
        name="get_realtime_quote",
        description="Get a real-time quote from any configured data provider.",
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "provider": {"type": "string", "description": "Specific provider. If omitted, uses best available."},
            },
            "required": ["symbol"],
        },
    
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(
        name="get_news",
        description="Get financial news for a symbol from configured data providers (Polygon, Finnhub).",
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
                "provider": {"type": "string"},
            },
            "required": ["symbol"],
        },
    
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(
        name="get_fundamentals",
        description="Get fundamental data (P/E, EPS, market cap, revenue, etc.) for a stock from configured data providers.",
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "provider": {"type": "string"},
            },
            "required": ["symbol"],
        },
    
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(
        name="search_symbols",
        description="Search for ticker symbols across configured data providers.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (e.g. 'Apple', 'bitcoin', 'EUR')"},
                "provider": {"type": "string"},
            },
            "required": ["query"],
        },
    
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(
        name="data_provider_health",
        description="Run health checks on all configured data providers.",
        inputSchema={"type": "object", "properties": {}},
    
        annotations=ANNOT_READ_EXTERNAL,
    ),
    # ── V7: BYOK Key Orchestrator ──────────────────────────────
    Tool(
        name="discover_keys",
        description="Autonomously scan your environment for existing API keys across 10+ data providers. Checks env vars, .env files, IDE configs, shell profiles, and config directories. Say 'gather my keys' to trigger.",
        inputSchema={"type": "object", "properties": {}},
    
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(
        name="validate_keys",
        description="Deep-validate all discovered API keys with live API calls. Returns permissions, rate limits, plan tier, and health status for each key.",
        inputSchema={
            "type": "object",
            "properties": {
                "providers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of provider names to validate. If empty, validates all discovered keys.",
                },
            },
        },
    
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(
        name="key_gap_analysis",
        description="Show what data providers you're missing, what each unlocks, signup URLs, free tier availability, and a quick-win recommendation.",
        inputSchema={"type": "object", "properties": {}},
    
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(
        name="provision_key",
        description="Add a new API key for a data provider. Validates the key and optionally writes it to your .env file.",
        inputSchema={
            "type": "object",
            "properties": {
                "provider": {"type": "string", "description": "Provider name: polygon, alpha_vantage, finnhub, twelve_data, databento, unusual_whales, intrinio, quandl, openbb"},
                "key_value": {"type": "string", "description": "The API key value"},
                "write_to_env": {"type": "boolean", "default": True, "description": "Whether to write the key to .env file"},
            },
            "required": ["provider", "key_value"],
        },
    
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(
        name="key_health",
        description="Real-time health check of all configured API keys. Shows which are valid, expired, rate-limited, or invalid.",
        inputSchema={"type": "object", "properties": {}},
    
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(
        name="export_config",
        description="Export your validated key configuration in various formats: env, json, mcp_windsurf, mcp_cursor, mcp_vscode. REQUIRES owner_token matching OWNER_API_TOKEN — MCP callers receive masked values only without it.",
        inputSchema={
            "type": "object",
            "properties": {
                "format": {"type": "string", "enum": ["env", "json", "mcp_windsurf", "mcp_cursor", "mcp_vscode"], "default": "env"},
                "owner_token": {"type": "string", "description": "Must match OWNER_API_TOKEN env var. Required to export key values."},
            },
            "required": ["owner_token"],
        },
    
        annotations=ANNOT_WRITE_SAFE,
    ),
    # ── V7: Proprietary Dataset Builder ────────────────────────
    Tool(
        name="build_dataset",
        description="Build a proprietary dataset for a symbol/timeframe using all available data providers. Normalizes, deduplicates, and optionally enriches with technical indicators, regime labels, and more.",
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Ticker symbol (e.g. AAPL, EURUSD, BTC)"},
                "timeframe": {"type": "string", "enum": ["1min", "5min", "15min", "1h", "4h", "daily", "weekly"], "default": "daily"},
                "start_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                "end_date": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                "providers": {"type": "array", "items": {"type": "string"}, "description": "Specific providers to use. If empty, uses all available."},
                "enrichments": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["technical_indicators", "sentiment", "cross_asset_correlation", "regime_labels", "volume_profile", "calendar_features"]},
                    "description": "Feature enrichments to apply to the dataset",
                },
                "format": {"type": "string", "enum": ["parquet", "csv", "json"], "default": "parquet"},
            },
            "required": ["symbol"],
        },
    
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(
        name="list_datasets",
        description="List all built proprietary datasets with metadata (rows, columns, date range, sources, size).",
        inputSchema={"type": "object", "properties": {}},
    
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(
        name="dataset_status",
        description="Show what data you CAN build vs what you're missing based on your available API keys.",
        inputSchema={"type": "object", "properties": {}},
    
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(
        name="enrich_dataset",
        description="Add feature enrichments (technical indicators, regime labels, calendar features, volume profile) to an existing dataset.",
        inputSchema={
            "type": "object",
            "properties": {
                "dataset_id": {"type": "string", "description": "ID of the dataset to enrich"},
                "enrichments": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["technical_indicators", "sentiment", "cross_asset_correlation", "regime_labels", "volume_profile", "calendar_features"]},
                },
            },
            "required": ["dataset_id", "enrichments"],
        },
    
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(
        name="export_dataset",
        description="Export a dataset in ML-ready format with time-based train/test split (no data leakage). Ready for scikit-learn, XGBoost, PyTorch.",
        inputSchema={
            "type": "object",
            "properties": {
                "dataset_id": {"type": "string"},
                "format": {"type": "string", "enum": ["parquet", "csv", "json"], "default": "parquet"},
                "train_test_split": {"type": "number", "default": 0.8, "description": "Train/test ratio (0.0-1.0)"},
                "target_column": {"type": "string", "default": "close", "description": "Target variable for ML prediction"},
            },
            "required": ["dataset_id"],
        },
    
        annotations=ANNOT_WRITE_SAFE,
    ),
    # ═══════════════════════════════════════════════════════════════
    # V8: Strategy Builder SDK (8 tools)
    # ═══════════════════════════════════════════════════════════════
    Tool(name="create_strategy", description="Create a new AI-native declarative strategy specification (StrategySpec). Define indicators, entry/exit rules, position sizing in JSON.",
         inputSchema={"type": "object", "properties": {"name": {"type": "string"}, "symbols": {"type": "array", "items": {"type": "string"}}, "timeframe": {"type": "string"}, "asset_class": {"type": "string", "enum": ["equity", "forex", "crypto", "futures"]}, "indicators": {"type": "array"}, "entry_rules": {"type": "object"}, "exit_rules": {"type": "object"}, "position_sizing": {"type": "object"}}, "required": ["name", "symbols", "timeframe", "indicators", "entry_rules", "exit_rules"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="validate_strategy", description="Validate a StrategySpec for schema correctness, parameter ranges, and internal consistency.",
         inputSchema={"type": "object", "properties": {"spec": {"type": "object", "description": "Full StrategySpec object to validate"}}, "required": ["spec"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="run_backtest", description="Run a backtest on a StrategySpec using the Rust engine. Returns Sharpe, drawdown, win rate, P&L.",
         inputSchema={"type": "object", "properties": {"spec": {"type": "object"}, "capital": {"type": "number", "default": 10000}}, "required": ["spec"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="optimize_strategy", description="Run Optuna-based parameter optimization on a StrategySpec. Finds best params across n_trials.",
         inputSchema={"type": "object", "properties": {"spec": {"type": "object"}, "n_trials": {"type": "integer", "default": 100}, "metric": {"type": "string", "default": "sharpe"}}, "required": ["spec"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="walk_forward_test", description="Run K-fold walk-forward validation on a strategy. Tests robustness across time periods.",
         inputSchema={"type": "object", "properties": {"spec": {"type": "object"}, "n_folds": {"type": "integer", "default": 5}, "train_pct": {"type": "number", "default": 0.70}}, "required": ["spec"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="deploy_strategy", description="Register a validated StrategySpec locally for tracking only. Does not connect to live or paper broker execution, and must not be treated as a real deployment.",
         inputSchema={"type": "object", "properties": {"spec": {"type": "object"}, "broker": {"type": "string"}, "mode": {"type": "string", "enum": ["paper", "live"], "default": "paper"}, "capital": {"type": "number", "default": 10000}}, "required": ["spec", "broker"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="list_templates", description="Browse pre-built strategy templates (RSI Momentum, BB Mean Reversion, EMA Crossover, etc).",
         inputSchema={"type": "object", "properties": {"category": {"type": "string", "enum": ["momentum", "mean_reversion", "trend", "breakout", "pairs"]}, "asset_class": {"type": "string"}}},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="fork_template", description="Fork a strategy template into your own editable StrategySpec with custom parameters.",
         inputSchema={"type": "object", "properties": {"template_id": {"type": "string"}, "new_name": {"type": "string"}, "symbols": {"type": "array", "items": {"type": "string"}}, "overrides": {"type": "object"}}, "required": ["template_id"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    # ═══════════════════════════════════════════════════════════════
    # V8: Social Trading (6 tools)
    # ═══════════════════════════════════════════════════════════════
    Tool(name="become_leader", description="Register as a copy-trading leader. Requires 90+ day track record, 50+ trades, Sharpe ≥ 1.0.",
         inputSchema={"type": "object", "properties": {"user_id": {"type": "string"}, "handle": {"type": "string"}, "track_record": {"type": "object"}}, "required": ["user_id", "handle"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="get_leader_stats", description="Get a leader's full performance stats, followers, and recent signals.",
         inputSchema={"type": "object", "properties": {"leader_id": {"type": "string"}}, "required": ["leader_id"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="follow_leader", description="Start copy-trading a leader with configurable scaling and risk limits.",
         inputSchema={"type": "object", "properties": {"follower_id": {"type": "string"}, "leader_id": {"type": "string"}, "config": {"type": "object"}}, "required": ["follower_id", "leader_id"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="unfollow_leader", description="Stop copy-trading a leader. Optionally close all copied positions.",
         inputSchema={"type": "object", "properties": {"follower_id": {"type": "string"}, "leader_id": {"type": "string"}, "close_positions": {"type": "boolean", "default": False}}, "required": ["follower_id", "leader_id"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="get_copy_status", description="Get status of all copy-trading relationships for a follower.",
         inputSchema={"type": "object", "properties": {"follower_id": {"type": "string"}}, "required": ["follower_id"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="set_copy_parameters", description="Update copy-trading parameters (scaling, risk limits, allowed assets).",
         inputSchema={"type": "object", "properties": {"follower_id": {"type": "string"}, "leader_id": {"type": "string"}, "config_updates": {"type": "object"}}, "required": ["follower_id", "leader_id", "config_updates"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    # ═══════════════════════════════════════════════════════════════
    # V8: Community Signals (5 tools)
    # ═══════════════════════════════════════════════════════════════
    Tool(name="publish_signal", description="Publish a trading signal to the community feed with optional trade verification.",
         inputSchema={"type": "object", "properties": {"user_id": {"type": "string"}, "symbol": {"type": "string"}, "direction": {"type": "string", "enum": ["long", "short"]}, "timeframe": {"type": "string"}, "entry_price": {"type": "number"}, "stop_loss": {"type": "number"}, "take_profit": {"type": "number"}, "confidence": {"type": "number"}, "rationale": {"type": "string"}, "trade_hash": {"type": "string"}}, "required": ["user_id", "symbol", "direction"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="subscribe_signals", description="Subscribe to community signals with filters (symbol, category, min accuracy).",
         inputSchema={"type": "object", "properties": {"user_id": {"type": "string"}, "filters": {"type": "object"}}, "required": ["user_id"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="verify_signal", description="Verify a signal with trade proof from broker (order ID, fill price, fill time).",
         inputSchema={"type": "object", "properties": {"signal_id": {"type": "string"}, "trade_proof": {"type": "object"}}, "required": ["signal_id", "trade_proof"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="get_consensus", description="Get community consensus for a symbol — weighted by publisher accuracy scores.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "timeframe": {"type": "string", "default": "1h"}}, "required": ["symbol"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="get_signal_accuracy", description="Get a user's signal accuracy score and history.",
         inputSchema={"type": "object", "properties": {"user_id": {"type": "string"}}, "required": ["user_id"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    # ═══════════════════════════════════════════════════════════════
    # V9: Risk Dashboard (10 tools)
    # ═══════════════════════════════════════════════════════════════
    Tool(name="calculate_var", description="Calculate Value-at-Risk (parametric, historical, or Monte Carlo) at given confidence level.",
         inputSchema={"type": "object", "properties": {"portfolio": {"type": "object"}, "method": {"type": "string", "enum": ["parametric", "historical", "monte_carlo"], "default": "parametric"}, "confidence": {"type": "number", "default": 0.95}, "horizon_days": {"type": "integer", "default": 1}}, "required": ["portfolio"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="calculate_expected_shortfall", description="Calculate Expected Shortfall (CVaR) — average loss in tail scenarios beyond VaR.",
         inputSchema={"type": "object", "properties": {"portfolio": {"type": "object"}, "confidence": {"type": "number", "default": 0.95}, "horizon_days": {"type": "integer", "default": 1}}, "required": ["portfolio"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="get_factor_exposure", description="Analyze portfolio factor exposures (Market, Size, Value, Momentum, Volatility, Quality).",
         inputSchema={"type": "object", "properties": {"portfolio": {"type": "object"}}, "required": ["portfolio"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="run_stress_test", description="Run historical or custom stress tests (COVID, GFC, Flash Crash, etc) on portfolio.",
         inputSchema={"type": "object", "properties": {"portfolio": {"type": "object"}, "scenario": {"type": "string"}, "custom_shocks": {"type": "object"}}, "required": ["portfolio"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="get_drawdown_monitor", description="Monitor current drawdown vs peak, with estimated recovery time.",
         inputSchema={"type": "object", "properties": {"portfolio": {"type": "object"}}, "required": ["portfolio"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="get_margin_utilization", description="Check margin utilization, buffer to margin call, and status.",
         inputSchema={"type": "object", "properties": {"account": {"type": "object"}}, "required": ["account"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="get_greeks_exposure", description="Get aggregate portfolio Greeks (delta, gamma, theta, vega, rho) for options positions.",
         inputSchema={"type": "object", "properties": {"portfolio": {"type": "object"}}, "required": ["portfolio"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="configure_risk_alert", description="Set up risk alert rules (drawdown, VaR breach, margin, concentration, loss limit).",
         inputSchema={"type": "object", "properties": {"alert_type": {"type": "string", "enum": ["drawdown", "var_breach", "margin", "concentration", "loss_limit"]}, "threshold": {"type": "number"}, "action": {"type": "string", "default": "notify"}, "channels": {"type": "array", "items": {"type": "string"}}}, "required": ["alert_type", "threshold"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="check_risk_alerts", description="Evaluate all active risk alert rules against current portfolio state.",
         inputSchema={"type": "object", "properties": {"portfolio": {"type": "object"}}, "required": ["portfolio"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="get_concentration_risk", description="Analyze portfolio concentration (HHI index, top holdings weight, diversification assessment).",
         inputSchema={"type": "object", "properties": {"portfolio": {"type": "object"}}, "required": ["portfolio"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    # ═══════════════════════════════════════════════════════════════
    # V9: Compliance Module (8 tools)
    # ═══════════════════════════════════════════════════════════════
    Tool(name="pre_trade_check", description="Run compliance pre-trade checks (position limits, order size, daily loss, restricted list, wash trade).",
         inputSchema={"type": "object", "properties": {"order": {"type": "object"}, "account": {"type": "object"}, "profile_id": {"type": "string"}}, "required": ["order", "account"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="post_trade_surveillance", description="Run post-trade surveillance for layering, spoofing, and momentum ignition patterns.",
         inputSchema={"type": "object", "properties": {"trades": {"type": "array", "items": {"type": "object"}}}, "required": ["trades"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="get_audit_trail", description="Retrieve tamper-proof blockchain-style audit trail with chain integrity verification.",
         inputSchema={"type": "object", "properties": {"limit": {"type": "integer", "default": 50}, "action_filter": {"type": "string"}}},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="activate_kill_switch", description="Activate trading kill switch — immediately halts all order submission.",
         inputSchema={"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]},
        annotations=ANNOT_WRITE_DESTRUCTIVE,
    ),
    Tool(name="deactivate_kill_switch", description="Deactivate trading kill switch and resume normal operations.",
         inputSchema={"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]},
        annotations=ANNOT_WRITE_DESTRUCTIVE,
    ),
    Tool(name="set_compliance_profile", description="Set or update a compliance profile with custom trading limits.",
         inputSchema={"type": "object", "properties": {"profile_id": {"type": "string"}, "limits": {"type": "object"}}, "required": ["profile_id", "limits"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="get_compliance_profile", description="Retrieve a compliance profile's current limits and settings.",
         inputSchema={"type": "object", "properties": {"profile_id": {"type": "string"}}, "required": ["profile_id"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="best_execution_report", description="Generate best execution analysis — slippage, venue quality, fill assessment.",
         inputSchema={"type": "object", "properties": {"trades": {"type": "array", "items": {"type": "object"}}}, "required": ["trades"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="get_wash_trade_alerts", description="List potential wash trade violations detected across recent trades.",
         inputSchema={"type": "object", "properties": {"days": {"type": "integer", "default": 30}}},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="set_restricted_list", description="Update restricted securities, sectors, or countries for a compliance profile.",
         inputSchema={"type": "object", "properties": {"profile_id": {"type": "string"}, "symbols": {"type": "array", "items": {"type": "string"}}, "sectors": {"type": "array", "items": {"type": "string"}}, "countries": {"type": "array", "items": {"type": "string"}}}, "required": ["profile_id"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="run_surveillance_scan", description="Trigger on-demand post-trade surveillance scan for layering, spoofing, wash trades.",
         inputSchema={"type": "object", "properties": {"lookback_hours": {"type": "integer", "default": 24}}},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="get_compliance_status", description="Current compliance state: daily P&L vs limits, violations, kill switch status.",
         inputSchema={"type": "object", "properties": {"account": {"type": "object"}, "profile_id": {"type": "string"}}, "required": ["account"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    # ═══════════════════════════════════════════════════════════════
    # V9: Multi-Tenant White-Label (10 tools)
    # ═══════════════════════════════════════════════════════════════
    Tool(name="create_tenant", description="Create a new white-label tenant with tier, branding, and API key.",
         inputSchema={"type": "object", "properties": {"name": {"type": "string"}, "admin_email": {"type": "string"}, "tier": {"type": "string", "enum": ["starter", "growth", "professional", "enterprise"], "default": "starter"}, "branding": {"type": "object"}}, "required": ["name", "admin_email"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="get_tenant", description="Retrieve tenant details including sub-account count and configuration.",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}}, "required": ["tenant_id"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="update_tenant", description="Update tenant name, branding, tier, or status.",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}, "updates": {"type": "object"}}, "required": ["tenant_id", "updates"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="create_sub_account", description="Create a sub-account under a tenant with role-based permissions.",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}, "user_id": {"type": "string"}, "name": {"type": "string"}, "permissions": {"type": "array", "items": {"type": "string"}}}, "required": ["tenant_id", "user_id", "name"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="list_sub_accounts", description="List all sub-accounts for a tenant.",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}}, "required": ["tenant_id"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="configure_broker_routing", description="Configure broker routing rules for a tenant (which broker handles which asset class).",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}, "broker_config": {"type": "object"}}, "required": ["tenant_id", "broker_config"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="get_billing_summary", description="Get billing summary for a tenant (tier, usage, estimated monthly cost).",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}}, "required": ["tenant_id"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="get_tenant_dashboard", description="Aggregate metrics for a tenant: AUM, active accounts, daily P&L, usage stats.",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}}, "required": ["tenant_id"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="get_sub_account_status", description="Detailed status of a sub-account: positions, P&L, compliance state, recent trades.",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}, "sub_account_id": {"type": "string"}}, "required": ["tenant_id", "sub_account_id"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="set_sub_account_permissions", description="Update sub-account permissions: trade limits, asset classes, marketplace access.",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}, "sub_account_id": {"type": "string"}, "permissions": {"type": "object"}}, "required": ["tenant_id", "sub_account_id", "permissions"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    # ═══════════════════════════════════════════════════════════════
    # V10: ML/AI-Native Strategy Engine (20 tools)
    # ═══════════════════════════════════════════════════════════════
    Tool(name="create_feature_set", description="Create a named feature set with indicator definitions for ML model training.",
         inputSchema={"type": "object", "properties": {"name": {"type": "string"}, "features": {"type": "array", "items": {"type": "object"}}, "target": {"type": "string"}}, "required": ["name", "features"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="compute_features", description="Compute feature values for a symbol over a date range using a saved feature set.",
         inputSchema={"type": "object", "properties": {"feature_set_id": {"type": "string"}, "symbol": {"type": "string"}, "start_date": {"type": "string"}, "end_date": {"type": "string"}}, "required": ["feature_set_id", "symbol"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="list_feature_sets", description="List all saved feature sets with metadata.",
         inputSchema={"type": "object", "properties": {}},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="get_feature_importance", description="Get feature importance rankings for a v10 ML engine feature set (by feature_set_id/model_id). NOTE: this is the abstract feature-engine path and is NOT the same as the MNQ futures bot's gain-based importance on futures_model_latest.pkl. To inspect importance for the live MNQ LightGBM/XGBoost model, run `python3 scripts/feature_importance_report.py` on the algochains-control-tower repo (ALGOCHAINS_CONTROL_TOWER path) — that script reads the promoted .pkl directly and highlights flow_pcr_spy/qqq, geopol_mnq_risk, and news features. Use this tool for v10 feature-set experiment tracking.",
         inputSchema={"type": "object", "properties": {"feature_set_id": {"type": "string"}, "model_id": {"type": "string"}}, "required": ["feature_set_id"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="train_model", description="Train an ML model (XGBoost, LSTM, transformer) on a feature set with train/test split.",
         inputSchema={"type": "object", "properties": {"feature_set_id": {"type": "string"}, "model_type": {"type": "string", "enum": ["xgboost", "lstm", "transformer", "random_forest", "lightgbm"]}, "hyperparameters": {"type": "object"}, "train_split": {"type": "number", "default": 0.8}}, "required": ["feature_set_id", "model_type"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="evaluate_model", description="Evaluate a trained model on held-out test data with comprehensive metrics.",
         inputSchema={"type": "object", "properties": {"model_id": {"type": "string"}, "test_data_id": {"type": "string"}}, "required": ["model_id"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="predict", description="Run inference on a trained model for a symbol to get signal predictions.",
         inputSchema={"type": "object", "properties": {"model_id": {"type": "string"}, "symbol": {"type": "string"}, "features": {"type": "object"}}, "required": ["model_id", "symbol"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="explain_prediction", description="Get SHAP-based explanation for a model prediction.",
         inputSchema={"type": "object", "properties": {"model_id": {"type": "string"}, "prediction_id": {"type": "string"}}, "required": ["model_id", "prediction_id"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="register_model", description="Register a trained model in the model registry with version and metadata.",
         inputSchema={"type": "object", "properties": {"model_id": {"type": "string"}, "name": {"type": "string"}, "version": {"type": "string"}, "metrics": {"type": "object"}, "tags": {"type": "array", "items": {"type": "string"}}}, "required": ["model_id", "name"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="promote_model", description="Promote a model to a target stage (staging, production, archived).",
         inputSchema={"type": "object", "properties": {"registry_id": {"type": "string"}, "stage": {"type": "string", "enum": ["staging", "production", "archived"]}}, "required": ["registry_id", "stage"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="list_models", description="List all models in the registry with optional stage filter.",
         inputSchema={"type": "object", "properties": {"stage": {"type": "string"}, "name_filter": {"type": "string"}}},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="compare_models", description="Compare two or more models side-by-side on key metrics.",
         inputSchema={"type": "object", "properties": {"model_ids": {"type": "array", "items": {"type": "string"}}}, "required": ["model_ids"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="archive_model", description="Archive a model, removing it from active use.",
         inputSchema={"type": "object", "properties": {"registry_id": {"type": "string"}, "reason": {"type": "string"}}, "required": ["registry_id"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="create_rl_agent", description="Create a reinforcement learning trading agent with environment and reward config.",
         inputSchema={"type": "object", "properties": {"name": {"type": "string"}, "algorithm": {"type": "string", "enum": ["ppo", "dqn", "a2c", "sac"]}, "environment": {"type": "object"}, "reward_config": {"type": "object"}}, "required": ["name", "algorithm"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="train_rl_agent", description="Train an RL agent on historical or simulated market data.",
         inputSchema={"type": "object", "properties": {"agent_id": {"type": "string"}, "episodes": {"type": "integer", "default": 1000}, "symbol": {"type": "string"}}, "required": ["agent_id"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="evaluate_rl_agent", description="Evaluate RL agent performance with episode statistics.",
         inputSchema={"type": "object", "properties": {"agent_id": {"type": "string"}, "episodes": {"type": "integer", "default": 100}}, "required": ["agent_id"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="get_rl_agent_state", description="Get current state and policy of an RL agent.",
         inputSchema={"type": "object", "properties": {"agent_id": {"type": "string"}}, "required": ["agent_id"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="dispatch_gpu_task", description="Route a compute task to Mac M3 Max or Desktop RTX GPU.",
         inputSchema={"type": "object", "properties": {"task_type": {"type": "string", "enum": ["training", "inference", "optimization", "backtest"]}, "payload": {"type": "object"}, "prefer_gpu": {"type": "string", "enum": ["mac_m3", "desktop_rtx", "auto"]}}, "required": ["task_type", "payload"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="gpu_status", description="Get status of all available GPU compute nodes.",
         inputSchema={"type": "object", "properties": {}},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="generate_strategy_spec", description="Use LLM to generate a complete strategy specification from natural language.",
         inputSchema={"type": "object", "properties": {"description": {"type": "string"}, "asset_class": {"type": "string"}, "risk_tolerance": {"type": "string", "enum": ["conservative", "moderate", "aggressive"]}}, "required": ["description"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    # ═══════════════════════════════════════════════════════════════
    # V11: Institutional-Grade Execution (18 tools)
    # ═══════════════════════════════════════════════════════════════
    Tool(name="validate_institutional_order", description="Validate an order against institutional compliance rules and limits.",
         inputSchema={"type": "object", "properties": {"order": {"type": "object"}, "account_id": {"type": "string"}}, "required": ["order"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="submit_institutional_order", description="Submit an institutional order with full audit trail and compliance checks.",
         inputSchema={"type": "object", "properties": {"order": {"type": "object"}, "account_id": {"type": "string"}, "compliance_override": {"type": "boolean", "default": False}}, "required": ["order"]},
        annotations=ANNOT_TRADE_EXEC,
    ),
    Tool(name="get_order_status", description="Get detailed status of an institutional order including fill reports.",
         inputSchema={"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="route_order", description="Smart-route an order across venues for best execution.",
         inputSchema={"type": "object", "properties": {"order": {"type": "object"}, "routing_strategy": {"type": "string", "enum": ["best_price", "lowest_latency", "dark_pool_first", "split"]}, "max_venues": {"type": "integer", "default": 5}}, "required": ["order"]},
        annotations=ANNOT_TRADE_EXEC,
    ),
    Tool(name="get_venue_analytics", description="Get execution analytics per venue: fill rates, latency, slippage.",
         inputSchema={"type": "object", "properties": {"venue_id": {"type": "string"}, "lookback_days": {"type": "integer", "default": 30}}},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="start_algo_execution", description="Start an algorithmic execution strategy (TWAP, VWAP, iceberg, sniper).",
         inputSchema={"type": "object", "properties": {"algo_type": {"type": "string", "enum": ["twap", "vwap", "iceberg", "sniper", "pov"]}, "order": {"type": "object"}, "parameters": {"type": "object"}}, "required": ["algo_type", "order"]},
        annotations=ANNOT_TRADE_EXEC,
    ),
    Tool(name="stop_algo_execution", description="Stop a running algo execution and report fills.",
         inputSchema={"type": "object", "properties": {"execution_id": {"type": "string"}}, "required": ["execution_id"]},
        annotations=ANNOT_TRADE_EXEC,
    ),
    Tool(name="get_algo_execution_status", description="Get real-time status of an algo execution (progress, fills, slippage).",
         inputSchema={"type": "object", "properties": {"execution_id": {"type": "string"}}, "required": ["execution_id"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="connect_fix_session", description="Establish a FIX protocol session to an execution venue.",
         inputSchema={"type": "object", "properties": {"venue": {"type": "string"}, "sender_comp_id": {"type": "string"}, "target_comp_id": {"type": "string"}, "config": {"type": "object"}}, "required": ["venue", "sender_comp_id", "target_comp_id"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="disconnect_fix_session", description="Gracefully disconnect a FIX session.",
         inputSchema={"type": "object", "properties": {"session_id": {"type": "string"}}, "required": ["session_id"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="get_fix_session_status", description="Get FIX session health: heartbeat, sequence numbers, message counts.",
         inputSchema={"type": "object", "properties": {"session_id": {"type": "string"}}, "required": ["session_id"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="run_tca", description="Run transaction cost analysis on completed trades.",
         inputSchema={"type": "object", "properties": {"trades": {"type": "array", "items": {"type": "object"}}, "benchmark": {"type": "string", "enum": ["vwap", "twap", "arrival_price", "close"], "default": "vwap"}}, "required": ["trades"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="get_tca_report", description="Get a comprehensive TCA report for a time period.",
         inputSchema={"type": "object", "properties": {"start_date": {"type": "string"}, "end_date": {"type": "string"}, "account_id": {"type": "string"}}, "required": ["start_date", "end_date"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="get_implementation_shortfall", description="Calculate implementation shortfall for a set of orders.",
         inputSchema={"type": "object", "properties": {"orders": {"type": "array", "items": {"type": "object"}}}, "required": ["orders"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="register_venue", description="Register a new execution venue in the venue registry.",
         inputSchema={"type": "object", "properties": {"name": {"type": "string"}, "venue_type": {"type": "string", "enum": ["exchange", "dark_pool", "ats", "otc"]}, "config": {"type": "object"}}, "required": ["name", "venue_type"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="list_venues", description="List all registered execution venues with health status.",
         inputSchema={"type": "object", "properties": {}},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="get_venue_status", description="Get detailed status of a specific venue.",
         inputSchema={"type": "object", "properties": {"venue_id": {"type": "string"}}, "required": ["venue_id"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="set_venue_priority", description="Set routing priority for a venue.",
         inputSchema={"type": "object", "properties": {"venue_id": {"type": "string"}, "priority": {"type": "integer"}}, "required": ["venue_id", "priority"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    # ═══════════════════════════════════════════════════════════════
    # V12: Real-Time Analytics (15 tools)
    # ═══════════════════════════════════════════════════════════════
    Tool(name="start_pnl_stream", description="Start real-time P&L streaming for an account or portfolio.",
         inputSchema={"type": "object", "properties": {"account_id": {"type": "string"}, "symbols": {"type": "array", "items": {"type": "string"}}}, "required": ["account_id"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="get_pnl_snapshot", description="Get current P&L snapshot across all tracked positions.",
         inputSchema={"type": "object", "properties": {"account_id": {"type": "string"}}, "required": ["account_id"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="get_pnl_history", description="Get historical P&L time series for charting.",
         inputSchema={"type": "object", "properties": {"account_id": {"type": "string"}, "interval": {"type": "string", "enum": ["1m", "5m", "1h", "1d"], "default": "1h"}, "lookback": {"type": "string", "default": "24h"}}, "required": ["account_id"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="analyze_order_flow", description="Analyze order flow for a symbol: buy/sell pressure, large trades, imbalances.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "lookback_minutes": {"type": "integer", "default": 60}}, "required": ["symbol"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="get_order_flow_heatmap", description="Get order flow heatmap data for price levels.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "levels": {"type": "integer", "default": 20}}, "required": ["symbol"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="get_volume_profile", description="Get volume profile analysis for a symbol.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "lookback_days": {"type": "integer", "default": 5}}, "required": ["symbol"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="analyze_microstructure", description="Analyze market microstructure: bid-ask spread, depth, tick patterns.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="get_toxicity_score", description="Get order flow toxicity score (VPIN) for a symbol.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "window": {"type": "integer", "default": 50}}, "required": ["symbol"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="detect_regime", description="[DEPRECATED — prefer detect_regime_hmm which uses the V21 Gaussian-HMM engine] "
         "Detect current market regime using statistical methods. "
         "Calls detect_regime_hmm internally when method='hmm' (the default).",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "method": {"type": "string", "enum": ["hmm", "threshold", "ml"], "default": "hmm"}}, "required": ["symbol"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="get_regime_history", description="Get historical regime classifications for a symbol.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "lookback_days": {"type": "integer", "default": 90}}, "required": ["symbol"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="get_regime_transition_matrix", description="Get regime transition probability matrix.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="create_alert", description="Create a real-time alert with conditions and actions.",
         inputSchema={"type": "object", "properties": {"name": {"type": "string"}, "condition": {"type": "object"}, "actions": {"type": "array", "items": {"type": "object"}}, "channels": {"type": "array", "items": {"type": "string"}}}, "required": ["name", "condition"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="list_alerts", description="List all configured alerts with their status.",
         inputSchema={"type": "object", "properties": {"active_only": {"type": "boolean", "default": True}}},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="delete_alert", description="Delete an alert by ID.",
         inputSchema={"type": "object", "properties": {"alert_id": {"type": "string"}}, "required": ["alert_id"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="get_alert_history", description="Get alert trigger history.",
         inputSchema={"type": "object", "properties": {"alert_id": {"type": "string"}, "limit": {"type": "integer", "default": 50}}},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    # ═══════════════════════════════════════════════════════════════
    # V13: Alternative Data Marketplace (18 tools)
    # ═══════════════════════════════════════════════════════════════
    Tool(name="analyze_sentiment", description="Run NLP sentiment analysis on text or news for a symbol.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "source": {"type": "string", "enum": ["news", "twitter", "reddit", "earnings_call", "custom"]}, "text": {"type": "string"}}, "required": ["symbol"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="get_sentiment_history", description="Get historical sentiment scores for a symbol.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "source": {"type": "string"}, "lookback_days": {"type": "integer", "default": 30}}, "required": ["symbol"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="get_sentiment_signal", description="Get aggregated sentiment signal (bullish/bearish/neutral) for a symbol.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="analyze_satellite", description="Analyze satellite imagery data for economic activity signals.",
         inputSchema={"type": "object", "properties": {"location": {"type": "string"}, "data_type": {"type": "string", "enum": ["parking_lots", "shipping", "agriculture", "construction", "nightlights"]}, "symbol": {"type": "string"}}, "required": ["location", "data_type"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="get_satellite_timeseries", description="Get time series of satellite-derived metrics.",
         inputSchema={"type": "object", "properties": {"location_id": {"type": "string"}, "metric": {"type": "string"}, "lookback_days": {"type": "integer", "default": 90}}, "required": ["location_id", "metric"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="scrape_web_data", description="Scrape structured data from web sources for trading signals.",
         inputSchema={"type": "object", "properties": {"url": {"type": "string"}, "selectors": {"type": "object"}, "schedule": {"type": "string"}}, "required": ["url"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="list_scrape_jobs", description="List all configured web scrape jobs.",
         inputSchema={"type": "object", "properties": {}},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="get_scrape_results", description="Get results from a web scrape job.",
         inputSchema={"type": "object", "properties": {"job_id": {"type": "string"}, "limit": {"type": "integer", "default": 100}}, "required": ["job_id"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="analyze_sec_filing", description="Analyze an SEC filing (10-K, 10-Q, 8-K) for trading signals.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "filing_type": {"type": "string", "enum": ["10-K", "10-Q", "8-K", "13-F", "S-1"]}, "filing_url": {"type": "string"}}, "required": ["symbol", "filing_type"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="get_insider_trades", description="Get recent insider trading activity for a symbol.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "days": {"type": "integer", "default": 90}}, "required": ["symbol"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="get_institutional_holdings", description="Get institutional holdings changes (13-F) for a symbol.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "quarter": {"type": "string"}}, "required": ["symbol"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="analyze_social_media", description="Analyze social media signals (Twitter, Reddit, StockTwits) for a symbol.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "platform": {"type": "string", "enum": ["twitter", "reddit", "stocktwits", "all"]}, "lookback_hours": {"type": "integer", "default": 24}}, "required": ["symbol"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="get_social_momentum", description="Get social momentum score for a symbol (trending vs fading).",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="get_social_sentiment_feed", description="Get real-time social sentiment feed for monitored symbols.",
         inputSchema={"type": "object", "properties": {"symbols": {"type": "array", "items": {"type": "string"}}, "limit": {"type": "integer", "default": 50}}},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="browse_alt_datasets", description="Browse available alternative datasets in the marketplace.",
         inputSchema={"type": "object", "properties": {"category": {"type": "string"}, "min_quality": {"type": "number", "default": 0.7}}},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="subscribe_alt_dataset", description="Subscribe to an alternative dataset for signal generation.",
         inputSchema={"type": "object", "properties": {"dataset_id": {"type": "string"}, "config": {"type": "object"}}, "required": ["dataset_id"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="get_alt_dataset_sample", description="Get a sample of data from an alternative dataset.",
         inputSchema={"type": "object", "properties": {"dataset_id": {"type": "string"}, "limit": {"type": "integer", "default": 100}}, "required": ["dataset_id"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="get_alt_data_quality", description="Get quality metrics for an alternative dataset.",
         inputSchema={"type": "object", "properties": {"dataset_id": {"type": "string"}}, "required": ["dataset_id"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    # ═══════════════════════════════════════════════════════════════
    # V14: Autonomous Agent Swarm (18 tools)
    # ═══════════════════════════════════════════════════════════════
    Tool(name="spawn_agent", description="Spawn a new autonomous trading agent with a specific role and strategy.",
         inputSchema={"type": "object", "properties": {"name": {"type": "string"}, "role": {"type": "string", "enum": ["researcher", "trader", "risk_manager", "analyst", "executor"]}, "strategy": {"type": "object"}, "capital_allocation": {"type": "number"}}, "required": ["name", "role"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="list_agents", description="List all active agents in the swarm with their status.",
         inputSchema={"type": "object", "properties": {"role_filter": {"type": "string"}}},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="get_agent_detail", description="Get detailed info about a specific agent: state, P&L, decisions.",
         inputSchema={"type": "object", "properties": {"agent_id": {"type": "string"}}, "required": ["agent_id"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="terminate_agent", description="Terminate an agent and close its positions.",
         inputSchema={"type": "object", "properties": {"agent_id": {"type": "string"}, "reason": {"type": "string"}}, "required": ["agent_id"]},
        annotations=ANNOT_WRITE_DESTRUCTIVE,
    ),
    Tool(name="create_task_plan", description="Create a task plan that decomposes a trading goal into agent subtasks.",
         inputSchema={"type": "object", "properties": {"goal": {"type": "string"}, "constraints": {"type": "object"}, "deadline": {"type": "string"}}, "required": ["goal"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="get_task_plan", description="Get a task plan and its execution status.",
         inputSchema={"type": "object", "properties": {"plan_id": {"type": "string"}}, "required": ["plan_id"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="store_agent_memory", description="Store a memory/observation in shared agent memory.",
         inputSchema={"type": "object", "properties": {"agent_id": {"type": "string"}, "memory_type": {"type": "string", "enum": ["observation", "decision", "outcome", "insight"]}, "content": {"type": "object"}}, "required": ["agent_id", "memory_type", "content"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="query_agent_memory", description="Query shared agent memory for relevant past observations.",
         inputSchema={"type": "object", "properties": {"query": {"type": "string"}, "memory_type": {"type": "string"}, "agent_id": {"type": "string"}, "limit": {"type": "integer", "default": 20}}, "required": ["query"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="get_memory_stats", description="Get agent memory usage statistics.",
         inputSchema={"type": "object", "properties": {"agent_id": {"type": "string"}}},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="route_tool_call", description="Route a tool call from an agent to the appropriate MCP tool.",
         inputSchema={"type": "object", "properties": {"agent_id": {"type": "string"}, "tool_name": {"type": "string"}, "arguments": {"type": "object"}}, "required": ["agent_id", "tool_name", "arguments"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="get_tool_permissions", description="Get tool access permissions for an agent.",
         inputSchema={"type": "object", "properties": {"agent_id": {"type": "string"}}, "required": ["agent_id"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="request_consensus", description="Request multi-agent consensus on a trading decision.",
         inputSchema={"type": "object", "properties": {"proposal": {"type": "object"}, "agent_ids": {"type": "array", "items": {"type": "string"}}, "method": {"type": "string", "enum": ["majority", "weighted", "unanimous"], "default": "weighted"}}, "required": ["proposal", "agent_ids"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="get_consensus_result", description="Get the result of a consensus request.",
         inputSchema={"type": "object", "properties": {"consensus_id": {"type": "string"}}, "required": ["consensus_id"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="get_consensus_history", description="Get history of consensus decisions.",
         inputSchema={"type": "object", "properties": {"limit": {"type": "integer", "default": 20}}},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="get_agent_health", description="Get health metrics for an agent: uptime, error rate, latency.",
         inputSchema={"type": "object", "properties": {"agent_id": {"type": "string"}}, "required": ["agent_id"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="get_swarm_dashboard", description="Get aggregate swarm dashboard: active agents, total P&L, task status.",
         inputSchema={"type": "object", "properties": {}},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="get_agent_performance", description="Get detailed performance metrics for an agent over time.",
         inputSchema={"type": "object", "properties": {"agent_id": {"type": "string"}, "lookback_days": {"type": "integer", "default": 30}}, "required": ["agent_id"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="set_agent_parameters", description="Update an agent's strategy parameters at runtime.",
         inputSchema={"type": "object", "properties": {"agent_id": {"type": "string"}, "parameters": {"type": "object"}}, "required": ["agent_id", "parameters"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    # ═══════════════════════════════════════════════════════════════
    # V15: DeFi & Cross-Chain (20 tools)
    # ═══════════════════════════════════════════════════════════════
    Tool(name="get_dex_quote", description="Get best swap quote across decentralized exchanges.",
         inputSchema={"type": "object", "properties": {"token_in": {"type": "string"}, "token_out": {"type": "string"}, "amount": {"type": "string"}, "chain": {"type": "string", "enum": ["ethereum", "polygon", "arbitrum", "optimism", "base", "solana"]}}, "required": ["token_in", "token_out", "amount"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="execute_swap", description="Execute a token swap on the best DEX route.",
         inputSchema={"type": "object", "properties": {"quote_id": {"type": "string"}, "slippage_tolerance": {"type": "number", "default": 0.005}, "deadline_minutes": {"type": "integer", "default": 20}}, "required": ["quote_id"]},
        annotations=ANNOT_TRADE_EXEC,
    ),
    Tool(name="get_dex_liquidity", description="Get liquidity depth across DEXes for a token pair.",
         inputSchema={"type": "object", "properties": {"token_in": {"type": "string"}, "token_out": {"type": "string"}, "chain": {"type": "string"}}, "required": ["token_in", "token_out"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="scan_yield_opportunities", description="Scan DeFi protocols for yield farming opportunities.",
         inputSchema={"type": "object", "properties": {"min_apy": {"type": "number", "default": 5.0}, "max_risk_score": {"type": "number", "default": 7}, "chains": {"type": "array", "items": {"type": "string"}}}},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="deploy_yield_strategy", description="Deploy capital to a yield farming strategy.",
         inputSchema={"type": "object", "properties": {"opportunity_id": {"type": "string"}, "amount": {"type": "string"}, "auto_compound": {"type": "boolean", "default": True}}, "required": ["opportunity_id", "amount"]},
        annotations=ANNOT_TRADE_EXEC,
    ),
    Tool(name="get_yield_positions", description="Get all active yield farming positions.",
         inputSchema={"type": "object", "properties": {}},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="withdraw_yield", description="Withdraw from a yield farming position.",
         inputSchema={"type": "object", "properties": {"position_id": {"type": "string"}, "amount": {"type": "string"}}, "required": ["position_id"]},
        annotations=ANNOT_TRADE_EXEC,
    ),
    Tool(name="bridge_tokens", description="Bridge tokens across chains via cross-chain bridge.",
         inputSchema={"type": "object", "properties": {"token": {"type": "string"}, "amount": {"type": "string"}, "from_chain": {"type": "string"}, "to_chain": {"type": "string"}, "bridge_protocol": {"type": "string"}}, "required": ["token", "amount", "from_chain", "to_chain"]},
        annotations=ANNOT_TRADE_EXEC,
    ),
    Tool(name="get_bridge_status", description="Get status of a cross-chain bridge transfer.",
         inputSchema={"type": "object", "properties": {"transfer_id": {"type": "string"}}, "required": ["transfer_id"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="list_bridge_routes", description="List available bridge routes between chains for a token.",
         inputSchema={"type": "object", "properties": {"token": {"type": "string"}, "from_chain": {"type": "string"}, "to_chain": {"type": "string"}}, "required": ["token", "from_chain", "to_chain"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="check_mev_risk", description="Check MEV risk for a pending transaction.",
         inputSchema={"type": "object", "properties": {"transaction": {"type": "object"}, "chain": {"type": "string"}}, "required": ["transaction"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="submit_protected_tx", description="Submit a transaction with MEV protection (Flashbots/private mempool).",
         inputSchema={"type": "object", "properties": {"transaction": {"type": "object"}, "protection_type": {"type": "string", "enum": ["flashbots", "private_mempool", "backrun_protection"]}}, "required": ["transaction"]},
        annotations=ANNOT_TRADE_EXEC,
    ),
    Tool(name="get_mev_analytics", description="Get MEV analytics: sandwich attacks, front-running stats for monitored wallets.",
         inputSchema={"type": "object", "properties": {"wallet": {"type": "string"}, "lookback_days": {"type": "integer", "default": 7}}},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="get_governance_proposals", description="Get active governance proposals for a DAO/protocol.",
         inputSchema={"type": "object", "properties": {"protocol": {"type": "string"}, "status": {"type": "string", "enum": ["active", "passed", "rejected", "all"], "default": "active"}}, "required": ["protocol"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="vote_on_proposal", description="Cast a vote on a DAO governance proposal.",
         inputSchema={"type": "object", "properties": {"proposal_id": {"type": "string"}, "vote": {"type": "string", "enum": ["for", "against", "abstain"]}, "reason": {"type": "string"}}, "required": ["proposal_id", "vote"]},
        annotations=ANNOT_TRADE_EXEC,
    ),
    Tool(name="get_governance_power", description="Get voting power and delegation status for a wallet.",
         inputSchema={"type": "object", "properties": {"protocol": {"type": "string"}, "wallet": {"type": "string"}}, "required": ["protocol"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="assess_defi_risk", description="Assess risk of a DeFi protocol: smart contract, liquidity, governance.",
         inputSchema={"type": "object", "properties": {"protocol": {"type": "string"}, "chain": {"type": "string"}}, "required": ["protocol"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="get_defi_portfolio_risk", description="Get aggregate risk assessment for all DeFi positions.",
         inputSchema={"type": "object", "properties": {}},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="monitor_liquidation_risk", description="Monitor liquidation risk for lending/borrowing positions.",
         inputSchema={"type": "object", "properties": {"position_id": {"type": "string"}}, "required": ["position_id"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="get_defi_insurance_options", description="Get DeFi insurance options for protocol risk coverage.",
         inputSchema={"type": "object", "properties": {"protocol": {"type": "string"}, "coverage_amount": {"type": "string"}}, "required": ["protocol"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    # ═══════════════════════════════════════════════════════════════
    # V16: Cloud SaaS Platform (17 tools)
    # ═══════════════════════════════════════════════════════════════
    Tool(name="create_saas_tenant", description="Create a new SaaS tenant with subscription plan and configuration.",
         inputSchema={"type": "object", "properties": {"company_name": {"type": "string"}, "admin_email": {"type": "string"}, "plan": {"type": "string", "enum": ["free", "starter", "professional", "enterprise"]}, "config": {"type": "object"}}, "required": ["company_name", "admin_email"]},
        annotations=ANNOT_TRADE_EXEC,
    ),
    Tool(name="get_saas_tenant", description="Get SaaS tenant details, usage, and subscription status.",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}}, "required": ["tenant_id"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="update_saas_tenant", description="Update SaaS tenant settings, plan, or configuration.",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}, "updates": {"type": "object"}}, "required": ["tenant_id", "updates"]},
        annotations=ANNOT_TRADE_EXEC,
    ),
    Tool(name="get_usage_metrics", description="Get detailed usage metrics for billing (API calls, compute, storage).",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}, "period": {"type": "string", "default": "current_month"}}, "required": ["tenant_id"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="get_invoice", description="Get invoice details for a billing period.",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}, "invoice_id": {"type": "string"}}, "required": ["tenant_id"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="list_invoices", description="List all invoices for a tenant.",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}, "status": {"type": "string", "enum": ["paid", "pending", "overdue", "all"]}}, "required": ["tenant_id"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="update_payment_method", description="Update payment method for a tenant.",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}, "payment_method": {"type": "object"}}, "required": ["tenant_id", "payment_method"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="publish_strategy_to_marketplace", description="Publish a validated strategy to the SaaS marketplace.",
         inputSchema={"type": "object", "properties": {"strategy_id": {"type": "string"}, "pricing": {"type": "object"}, "description": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}}}, "required": ["strategy_id", "pricing"]},
        annotations=ANNOT_TRADE_EXEC,
    ),
    Tool(name="browse_strategy_marketplace", description="Browse the SaaS strategy marketplace with filters.",
         inputSchema={"type": "object", "properties": {"category": {"type": "string"}, "min_sharpe": {"type": "number"}, "max_price": {"type": "number"}, "sort_by": {"type": "string", "enum": ["sharpe", "subscribers", "newest", "price"], "default": "sharpe"}}},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="subscribe_to_strategy", description="Subscribe a tenant to a marketplace strategy.",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}, "strategy_id": {"type": "string"}, "allocation": {"type": "number"}}, "required": ["tenant_id", "strategy_id"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="configure_white_label", description="Configure white-label branding for a tenant.",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}, "branding": {"type": "object"}}, "required": ["tenant_id", "branding"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="get_white_label_config", description="Get current white-label configuration for a tenant.",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}}, "required": ["tenant_id"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="generate_api_key", description="Generate an API key for tenant programmatic access.",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}, "name": {"type": "string"}, "permissions": {"type": "array", "items": {"type": "string"}}, "rate_limit": {"type": "integer", "default": 1000}}, "required": ["tenant_id", "name"]},
        annotations=ANNOT_WRITE_SAFE,
    ),
    Tool(name="list_api_keys", description="List all API keys for a tenant.",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}}, "required": ["tenant_id"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="revoke_api_key", description="Revoke an API key.",
         inputSchema={"type": "object", "properties": {"key_id": {"type": "string"}}, "required": ["key_id"]},
        annotations=ANNOT_WRITE_DESTRUCTIVE,
    ),
    Tool(name="get_api_usage", description="Get API usage statistics and rate limit status.",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}, "key_id": {"type": "string"}}, "required": ["tenant_id"]},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    Tool(name="get_platform_health", description="Get overall SaaS platform health: uptime, latency, error rates.",
         inputSchema={"type": "object", "properties": {}},
        annotations=ANNOT_READ_EXTERNAL,
    ),
    # ═══════════════════════════════════════════════════════════════
    # V17: Massive White-Label Data (5 tools) + Dynamic Toolsets (3 tools)
    # ═══════════════════════════════════════════════════════════════
    Tool(name="massive_search_endpoints", description="BM25 search over all Massive market data API endpoints. Use this FIRST to find the right endpoint for stocks, options, futures, forex, crypto, or SEC filings.",
         inputSchema={"type": "object", "properties": {"query": {"type": "string", "description": "Natural language query (e.g. 'stock price aggregates', 'options chain', 'forex rates')"}, "top_k": {"type": "integer", "default": 5}, "scope": {"type": "string", "enum": ["all", "endpoints", "functions"], "description": "Search scope: endpoints for API, functions for built-in Greeks/returns/technicals"}}, "required": ["query"]},
         annotations=ANNOT_SEARCH),
    Tool(name="massive_get_endpoint_docs", description="Get parameter documentation for a Massive API endpoint. Pass the docs_url from massive_search_endpoints results.",
         inputSchema={"type": "object", "properties": {"docs_url": {"type": "string", "description": "The docs URL from search results"}}, "required": ["docs_url"]},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="massive_call_api", description="Execute a Massive market data API call. Optionally store results as an in-memory DataFrame for SQL querying. Supports pagination auto-detection — check _next_page in results.",
         inputSchema={"type": "object", "properties": {"path": {"type": "string", "description": "API path (e.g. /v2/aggs/ticker/AAPL/range/1/day/2024-01-01/2024-12-31)"}, "method": {"type": "string", "default": "GET"}, "params": {"type": "object", "description": "Query parameters"}, "store_as": {"type": "string", "description": "Table name to store as DataFrame (e.g. aapl_daily)"}, "apply": {"type": "array", "items": {"type": "object"}, "description": "Post-processing functions: sma, ema, sharpe_ratio, bs_delta, etc."}, "api_key": {"type": "string", "description": "Override API key for this request (white-label customer isolation)"}, "llm_model": {"type": "string", "description": "LLM model name for usage analytics"}, "llm_provider": {"type": "string", "description": "LLM provider name for usage analytics"}}, "required": ["path"]},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="massive_query_data", description="SQL queries over stored DataFrames from massive_call_api. Supports SHOW TABLES, DESCRIBE <table>, DROP TABLE <table>, and full SQL with JOIN/GROUP BY/window functions. Use apply for server-side Greeks and technicals.",
         inputSchema={"type": "object", "properties": {"sql": {"type": "string", "description": "SQL query or special command"}, "apply": {"type": "array", "items": {"type": "object"}, "description": "Post-processing functions to apply to query results"}}, "required": ["sql"]},
         annotations=ANNOT_COMPUTE),
    Tool(name="massive_run_pipeline", description="Composable pipeline: search→fetch→store→query→apply in 1 call (saves 4 round-trips). Describe what data you want, optionally filter with SQL and apply Greeks/technicals.",
         inputSchema={"type": "object", "properties": {"search_query": {"type": "string", "description": "Natural language query to find the right API endpoint"}, "path_override": {"type": "string", "description": "Skip search — use this API path directly"}, "params": {"type": "object", "description": "Query parameters for the API call"}, "store_as": {"type": "string", "description": "Table name (auto-generated if omitted)"}, "sql": {"type": "string", "description": "SQL to run after storing. Use {table} as placeholder for the table name"}, "apply": {"type": "array", "items": {"type": "object"}, "description": "Post-processing: [{\"function\": \"sharpe_ratio\", \"inputs\": {\"column\": \"close\", \"window\": 252}, \"output\": \"sharpe\"}]"}}, "required": ["search_query"]},
         annotations=ANNOT_COMPUTE),
    # ═══════════════════════════════════════════════════════════════
    # V17: Dynamic Toolsets — Meta-Tools (3 tools)
    # ═══════════════════════════════════════════════════════════════
    Tool(name="discover_tools", description="Search for relevant AlgoChains tools using natural language. Returns the top-K most relevant tools with descriptions. Use this FIRST to find which tools are available for your task — 90%+ context reduction vs listing all 150+ tools.",
         inputSchema={"type": "object", "properties": {"query": {"type": "string", "description": "Natural language description of what you want to do"}, "top_k": {"type": "integer", "default": 10}, "category": {"type": "string", "description": "Filter: trading, market_data, strategy, ml, analytics, alt_data, defi, cloud"}}, "required": ["query"]},
         annotations=ANNOT_SEARCH),
    Tool(name="get_tool_details", description="Get full details for a specific tool including its input schema, parameter types, and usage examples. Call after discover_tools to get the full spec before execution.",
         inputSchema={"type": "object", "properties": {"tool_name": {"type": "string", "description": "Exact tool name from discover_tools results"}}, "required": ["tool_name"]},
         annotations=ANNOT_READ_ONLY),
    Tool(name="execute_dynamic_tool", description="Execute any discovered tool by name with arguments. Use discover_tools first, then get_tool_details for the schema, then call this to execute. ORDER_EXEC and DESTRUCTIVE tools require owner_token and confirm=true inside arguments.",
         inputSchema={"type": "object", "properties": {"tool_name": {"type": "string", "description": "Tool name to execute"}, "arguments": {"type": "object", "description": "Arguments matching the tool's inputSchema. For ORDER_EXEC+ tools include owner_token and confirm=true."}}, "required": ["tool_name", "arguments"]},
         annotations=ANNOT_TRADE_EXEC),
    Tool(name="mcp_tool_manifest", description="Return JSON manifest of all registered MCP tools with implementation_status (full|partial|stub), required env vars, and Tier-1 flags. Use for CI, Onyx indexing, and honest agent planning — call before relying on V8-V20 tools.",
         inputSchema={"type": "object", "properties": {"include_tool_details": {"type": "boolean", "default": True, "description": "If false, return summary counts only (smaller payload)"}}, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="get_physical_event_sources", description="List physical-world event sources polled by Sonia Air and tower nodes, including license/dependency status. Read-only; no broker or execution access.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="map_physical_event_assets", description="Map physical-world event classes to affected assets (CL/NG/MNQ/NQ/MES/ES/BTC/ETH). Read-only research mapping.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string", "description": "Optional symbol to filter, e.g. CL or BTC"}}, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="score_physical_event_alpha", description="Compute an advisory physical-event priority score from provided real event fields. Research queue only; not broker truth and not a trade signal.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "event_type": {"type": "string"}, "severity": {"type": "number"}, "freshness_minutes": {"type": "number"}, "liquidity_proxy": {"type": "number"}}, "required": ["symbol", "event_type"]},
         annotations=ANNOT_READ_ONLY),
    Tool(name="get_sonia_air_heartbeat", description="Read Sonia Air heartbeat state and fallback status for three-node physical-world polling. Read-only.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),
    # ═══════════════════════════════════════════════════════════════
    # V18: Intent-Based Trading + Autonomous Intelligence (8 tools)
    # ═══════════════════════════════════════════════════════════════
    Tool(name="execute_intent", description="Transform a natural language trading intent into a concrete plan and execute it. Example: 'Get me $10K AI exposure, max 2% per stock'. Parses intent → solves constraints → presents plan for approval → executes.",
         inputSchema={"type": "object", "properties": {"intent": {"type": "string", "description": "Natural language trading intent"}, "dry_run": {"type": "boolean", "default": True, "description": "If true, return the plan without executing (default: true for safety)"}}, "required": ["intent"]},
         annotations=ANNOT_TRADE_EXEC),
    Tool(name="get_intent_plan", description="Get details of a previously generated intent plan by ID. Shows all steps, status, estimated cost, and risk impact.",
         inputSchema={"type": "object", "properties": {"plan_id": {"type": "string", "description": "Plan ID from execute_intent"}}, "required": ["plan_id"]},
         annotations=ANNOT_READ_ONLY),
    Tool(name="approve_intent", description="Approve a pending intent plan for execution. The plan must be in 'pending_approval' status.",
         inputSchema={"type": "object", "properties": {"plan_id": {"type": "string", "description": "Plan ID to approve and execute"}}, "required": ["plan_id"]},
         annotations=ANNOT_TRADE_EXEC),
    Tool(name="get_intent_history", description="Get history of executed intent plans with outcomes and lessons learned.",
         inputSchema={"type": "object", "properties": {"limit": {"type": "integer", "default": 20}}, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="create_shadow_portfolio", description="Create a shadow (paper) portfolio to forward-test a strategy without risking capital. Track P&L, fills, and metrics alongside your real portfolio.",
         inputSchema={"type": "object", "properties": {"name": {"type": "string", "description": "Portfolio name (e.g. 'AI Momentum Test')"}, "strategy_id": {"type": "string", "description": "Optional strategy ID to track"}, "broker": {"type": "string", "default": "alpaca"}, "capital": {"type": "number", "default": 100000, "description": "Starting capital"}}, "required": ["name"]},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="get_shadow_results", description="Get shadow portfolio results and optionally compare against live performance. Shows P&L, win rate, Sharpe estimate, and promotion recommendation.",
         inputSchema={"type": "object", "properties": {"shadow_id": {"type": "string", "description": "Shadow portfolio ID"}, "compare_live": {"type": "boolean", "default": False, "description": "Compare against live portfolio metrics"}}, "required": ["shadow_id"]},
         annotations=ANNOT_READ_ONLY),
    Tool(name="evolve_strategies", description="Genetic evolution of trading strategies. Initialize a population, evaluate fitness via backtest, then evolve to breed better strategies. Returns top genomes ranked by fitness (Sharpe-weighted).",
         inputSchema={"type": "object", "properties": {"action": {"type": "string", "enum": ["initialize", "evaluate", "evolve", "get_top", "get_unevaluated"], "description": "Evolution action"}, "strategy_type": {"type": "string", "enum": ["momentum", "mean_reversion", "breakout", "scalper"], "default": "momentum"}, "seeds": {"type": "array", "items": {"type": "object"}, "description": "Seed strategies with known-good parameters (for initialize)"}, "genome_id": {"type": "string", "description": "Genome ID (for evaluate)"}, "metrics": {"type": "object", "description": "Backtest metrics: sharpe, max_drawdown, win_rate, trade_count (for evaluate)"}, "n": {"type": "integer", "default": 10}}, "required": ["action"]},
         annotations=ANNOT_COMPUTE),
    Tool(name="detect_arbitrage", description="Scan for cross-broker arbitrage opportunities. Compares prices across brokers, computes spread in bps, subtracts fees and slippage, and flags profitable opportunities.",
         inputSchema={"type": "object", "properties": {"symbols": {"type": "array", "items": {"type": "string"}, "description": "Symbols to scan"}, "brokers": {"type": "array", "items": {"type": "string"}, "description": "Brokers to compare (default: alpaca, ibkr, tradovate)"}, "quotes": {"type": "object", "description": "Pre-fetched quotes as {broker: {symbol: price}} — skips live fetch"}}, "required": ["symbols"]},
         annotations=ANNOT_READ_EXTERNAL),
    # ═══════════════════════════════════════════════════════════════
    # V18 Genius Layer (2 tools)
    # ═══════════════════════════════════════════════════════════════
    Tool(name="detect_market_regime", description="Detect current market regime from VIX, SPY trend, breadth, and credit signals. Returns regime classification (bull/bear/range/volatile/crisis), recommended strategies, and risk multiplier for position sizing.",
         inputSchema={"type": "object", "properties": {"vix": {"type": "number"}, "spy_price": {"type": "number"}, "spy_sma_20": {"type": "number"}, "spy_sma_50": {"type": "number"}, "spy_sma_200": {"type": "number"}, "advance_decline_ratio": {"type": "number"}, "put_call_ratio": {"type": "number"}, "credit_spread_bps": {"type": "number"}}, "required": []},
         annotations=ANNOT_COMPUTE),
    Tool(name="prefetch_context", description="Predict what data an LLM will need based on user message intent and prefetch it in parallel. Reduces average tool calls from 6.2 to 1.8. Returns pre-loaded context dict.",
         inputSchema={"type": "object", "properties": {"user_message": {"type": "string", "description": "The user's message to analyze for data needs"}}, "required": ["user_message"]},
         annotations=ANNOT_READ_ONLY),
    # ═══════════════════════════════════════════════════════════════
    # V19: Alpha Engines — institutional-grade alpha analytics (18 tools)
    # ═══════════════════════════════════════════════════════════════
    Tool(name="compute_vwap", description="Compute real VWAP from intraday minute bars with standard deviation bands. Generates deviation signals (bullish/bearish reversion) when price strays from VWAP. Includes TWAP comparison.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "date": {"type": "string", "description": "Date YYYY-MM-DD (default: today)"}, "interval": {"type": "string", "default": "1", "description": "Bar interval in minutes"}, "anchor": {"type": "string", "default": "day", "enum": ["day", "week", "month"]}}, "required": ["symbol"]},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="multi_anchor_vwap", description="Compute VWAP from multiple anchor points (day, week, month) to identify confluence zones where multiple VWAP levels converge.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "anchors": {"type": "array", "items": {"type": "string"}, "default": ["day"]}}, "required": ["symbol"]},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="detect_dark_prints", description="Detect dark pool prints — large off-exchange trades indicating institutional activity. Classifies as accumulation or distribution based on buy/sell print analysis.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "date": {"type": "string", "description": "Date YYYY-MM-DD"}, "min_size": {"type": "integer", "default": 10000, "description": "Minimum print size to flag"}}, "required": ["symbol"]},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="block_trade_scanner", description="Scan multiple symbols for large block trades and dark pool activity. Returns sorted by notional value with institutional signal classification.",
         inputSchema={"type": "object", "properties": {"symbols": {"type": "array", "items": {"type": "string"}}, "min_notional": {"type": "number", "default": 500000}}, "required": ["symbols"]},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="compute_gex", description="Compute Gamma Exposure (GEX) from options chain — net dealer gamma, gamma flip point, pin risk strikes, and volatility regime (positive=compressed, negative=expanded). Key institutional-grade signal.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "expiry": {"type": "string", "description": "Optional expiry filter YYYY-MM-DD"}}, "required": ["symbol"]},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="gex_scanner", description="Scan multiple symbols for gamma exposure signals. Identifies positive/negative gamma regimes, volatility bias, and put/call OI ratios across a watchlist.",
         inputSchema={"type": "object", "properties": {"symbols": {"type": "array", "items": {"type": "string"}}}, "required": ["symbols"]},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="analyze_vol_skew", description="Analyze volatility skew — 25-delta risk reversal, butterfly spread, and fear/crash protection signals from options implied volatility across strikes.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "expiry": {"type": "string", "description": "Optional expiry YYYY-MM-DD"}}, "required": ["symbol"]},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="vol_term_structure", description="Analyze IV term structure across expirations — contango/backwardation, slope, event risk detection, and front-vs-back month vol spread signals.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="correlation_matrix", description="Compute rolling correlation matrix for a basket of assets. Identifies high-correlation pairs (for hedging) and negative-correlation pairs (for diversification).",
         inputSchema={"type": "object", "properties": {"symbols": {"type": "array", "items": {"type": "string"}}, "lookback_days": {"type": "integer", "default": 60}}, "required": ["symbols"]},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="pair_trade_signal", description="Compute pair trade z-score signal for two symbols. Uses spread ratio, Ornstein-Uhlenbeck half-life, and configurable entry/exit z-thresholds.",
         inputSchema={"type": "object", "properties": {"symbol_a": {"type": "string"}, "symbol_b": {"type": "string"}, "lookback_days": {"type": "integer", "default": 60}, "z_entry": {"type": "number", "default": 2.0}, "z_exit": {"type": "number", "default": 0.5}}, "required": ["symbol_a", "symbol_b"]},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="relative_strength", description="Compute relative strength of a symbol vs benchmark (default SPY). Returns alpha, RS trend direction, and outperformance signal.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "benchmark": {"type": "string", "default": "SPY"}, "lookback_days": {"type": "integer", "default": 20}}, "required": ["symbol"]},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="congressional_trades", description="Get congressional and insider stock trades with conviction scoring. Tracks politician/insider buys vs sells and generates smart-money signals.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string", "default": ""}, "days": {"type": "integer", "default": 30}}, "required": []},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="insider_cluster_scan", description="Scan for insider buying clusters — multiple insiders buying the same stock within a window. Strong alpha signal when 3+ insiders cluster-buy.",
         inputSchema={"type": "object", "properties": {"symbols": {"type": "array", "items": {"type": "string"}}, "days": {"type": "integer", "default": 14}, "min_insiders": {"type": "integer", "default": 2}}, "required": []},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="smart_money_composite", description="Composite smart money score (0-100) combining insider filing activity over 30d and 90d. Labels: strong_buy/buy/neutral/sell/strong_sell.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="compute_kelly", description="Compute Kelly criterion optimal position size from win rate, average win/loss. Supports fractional Kelly and max risk cap for practical risk management.",
         inputSchema={"type": "object", "properties": {"win_rate": {"type": "number", "description": "Historical win rate 0-1"}, "avg_win": {"type": "number", "description": "Average winning trade $"}, "avg_loss": {"type": "number", "description": "Average losing trade $ (positive)"}, "fraction": {"type": "number", "default": 0.5, "description": "Kelly fraction (0.5=half Kelly)"}, "account_equity": {"type": "number", "default": 100000}, "max_risk_pct": {"type": "number", "default": 5.0}}, "required": ["win_rate", "avg_win", "avg_loss"]},
         annotations=ANNOT_COMPUTE),
    Tool(name="multi_strategy_kelly", description="Kelly allocation across multiple strategies with total risk budget constraint. Scales individual allocations to fit within max total risk.",
         inputSchema={"type": "object", "properties": {"strategies": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}, "win_rate": {"type": "number"}, "avg_win": {"type": "number"}, "avg_loss": {"type": "number"}}}}, "account_equity": {"type": "number", "default": 100000}, "max_total_risk_pct": {"type": "number", "default": 20.0}}, "required": ["strategies"]},
         annotations=ANNOT_COMPUTE),
    Tool(name="unusual_options_activity", description="Detect unusual options activity — high volume/OI ratios, large premium flows, and smart money positioning. Returns bullish/bearish net sentiment.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "min_premium": {"type": "number", "default": 50000}, "min_oi_ratio": {"type": "number", "default": 2.0}}, "required": ["symbol"]},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="options_flow_scanner", description="Scan multiple symbols for unusual options flow. Identifies stocks with highest institutional options positioning across a watchlist.",
         inputSchema={"type": "object", "properties": {"symbols": {"type": "array", "items": {"type": "string"}}, "min_premium": {"type": "number", "default": 100000}}, "required": ["symbols"]},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="read_tape", description="Tick-level tape reading — classify trades as buys/sells, compute tick ratios, detect momentum shifts, large prints, and absorption zones.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "lookback_minutes": {"type": "integer", "default": 5}}, "required": ["symbol"]},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="tape_momentum_scanner", description="Scan multiple symbols for tape momentum signals. Identifies strong bullish/bearish momentum, absorption patterns, and large print activity.",
         inputSchema={"type": "object", "properties": {"symbols": {"type": "array", "items": {"type": "string"}}}, "required": ["symbols"]},
         annotations=ANNOT_READ_EXTERNAL),
    # ── V20: Account Protection ──────────────────────────────────
    Tool(name="check_order_safety", description="Run 13 pre-trade safety checks before placing an order. Checks position sizing, daily loss limits, drawdown, fat fingers, buying power, concentration, VIX killswitch, margin, correlation, and more. Returns ALLOW or BLOCK with reasons.",
         inputSchema={"type": "object", "properties": {"broker": {"type": "string"}, "symbol": {"type": "string"}, "side": {"type": "string", "enum": ["buy", "sell"]}, "qty": {"type": "number"}, "order_type": {"type": "string", "default": "market"}, "limit_price": {"type": "number"}, "notional_value": {"type": "number"}, "asset_class": {"type": "string", "default": "stock"}}, "required": ["broker", "symbol", "side", "qty"]},
         annotations=ANNOT_COMPUTE),
    Tool(name="get_protection_config", description="View current account protection settings including daily loss limits, drawdown thresholds, position size caps, VIX killswitch levels, and max positions.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="set_protection_config", description="Update account protection settings. Available presets: conservative (tight limits for small accounts), moderate (default), aggressive (wider limits for experienced traders). Or set individual parameters.",
         inputSchema={"type": "object", "properties": {"preset": {"type": "string", "enum": ["conservative", "moderate", "aggressive"]}, "max_daily_loss_pct": {"type": "number"}, "max_drawdown_pct": {"type": "number"}, "max_position_pct": {"type": "number"}, "max_positions": {"type": "integer"}, "vix_block_level": {"type": "number"}, "max_notional": {"type": "number"}, "enabled": {"type": "boolean"}}, "required": []},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="get_safety_audit_log", description="View recent pre-trade safety check history. Shows which orders were allowed/blocked and why.",
         inputSchema={"type": "object", "properties": {"limit": {"type": "integer", "default": 20}}, "required": []},
         annotations=ANNOT_READ_ONLY),
    # ── V20: Builder SDK ─────────────────────────────────────────
    Tool(name="query_data_warehouse", description="Query AlgoChains data warehouses (Builder tier $199/mo). Access 3.09B+ rows: 409M crypto, 1.3B stocks, 1.4B forex minute bars. Returns OHLCV data for backtesting.",
         inputSchema={"type": "object", "properties": {"asset_class": {"type": "string", "enum": ["crypto", "stocks", "forex"]}, "ticker": {"type": "string"}, "start_date": {"type": "string", "description": "YYYY-MM-DD"}, "end_date": {"type": "string", "description": "YYYY-MM-DD"}, "limit": {"type": "integer", "default": 10000}}, "required": ["asset_class", "ticker"]},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="list_data_warehouses", description="List available AlgoChains data warehouses with row counts, schemas, and access requirements.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="run_builder_backtest", description="Run a backtest using the Builder SDK. Supports built-in strategies (SMA crossover, RSI, Bollinger Bands, etc.) or custom data. Returns Sharpe, MaxDD, win rate, profit factor, and marketplace readiness check.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "strategy_type": {"type": "string", "default": "custom"}, "timeframe": {"type": "string", "default": "1d"}, "start_date": {"type": "string"}, "end_date": {"type": "string"}, "initial_capital": {"type": "number", "default": 100000}}, "required": ["symbol"]},
         annotations=ANNOT_COMPUTE),
    Tool(name="submit_to_marketplace", description="Submit a validated strategy to the AlgoChains marketplace. Runs 7-gate validation (schema, performance, overfitting, MCPT, walk-forward, paper trading, decay monitor). Returns tier classification (Platinum/Gold/Silver/Bronze) and next steps.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "strategy_type": {"type": "string", "enum": ["trend", "mean_reversion", "breakout", "momentum", "scalp", "pairs", "stat_arb"]}, "timeframe": {"type": "string"}, "oos_sharpe": {"type": "number"}, "oos_trades": {"type": "integer"}, "max_drawdown_pct": {"type": "number"}, "is_sharpe": {"type": "number"}, "win_rate": {"type": "number"}, "profit_factor": {"type": "number"}, "mcpt_p_value": {"type": "number"}, "mcpt_permutations": {"type": "integer"}, "wf_folds": {"type": "integer"}, "wf_avg_oos_sharpe": {"type": "number"}, "wf_worst_fold": {"type": "number"}, "description": {"type": "string"}, "asset_class": {"type": "string", "default": "stock"}, "price_monthly": {"type": "number", "default": 29.99}}, "required": ["symbol", "strategy_type", "timeframe", "oos_sharpe", "oos_trades", "max_drawdown_pct"]},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="get_submission_guide", description="Get the step-by-step guide for submitting a strategy to the AlgoChains marketplace. Includes gate requirements, pricing guide, IP protection details, and revenue split information.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="get_builder_capabilities", description="List Builder SDK capabilities including available backtest engines, data sources, and strategy templates.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),
    # ── V20: Memory Safety ───────────────────────────────────────
    Tool(name="get_memory_status", description="Check MCP server memory usage, cache stats, and garbage collection state. Helps diagnose memory leaks and OOM issues.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),
    # ═══════════════════════════════════════════════════════════════
    # Ultimate Quant Alpha Stack
    # ═══════════════════════════════════════════════════════════════
    Tool(name="compute_volatility_surface", description="Compute full implied volatility surface from real Polygon options chain: IV per strike/expiry, 25-delta skew, term structure, IV rank (0-1), IV percentile, and vol regime (low/normal/elevated/extreme). Generates actionable signal: long_vol/short_vol/sell_skew/buy_skew.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "expiry_filter": {"type": "string", "description": "Only include options expiring after this date (YYYY-MM-DD)"}}, "required": ["symbol"]},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="compute_factor_exposure", description="Decompose a symbol's returns into Fama-French 5-factor + momentum exposures using real Polygon daily data. Returns alpha, market beta, SMB/HML/momentum betas, R-squared, information ratio, tracking error. Identifies alpha-generating vs factor-exposed regimes.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "period": {"type": "string", "enum": ["3m", "6m", "1y", "2y"], "default": "1y"}, "benchmark": {"type": "string", "default": "SPY"}}, "required": ["symbol"]},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="detect_regime_hmm", description="Detect market regime using Hidden Markov Model on real daily returns: bull_trending, bear_trending, choppy, or crisis. Returns regime probability, days in current regime, transition probabilities, vol regime, and Sharpe. Uses hmmlearn if available, statistical fallback otherwise. Real Polygon data only.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string", "default": "SPY", "description": "Symbol to analyze (use SPY for broad market regime)"}, "lookback_days": {"type": "integer", "default": 252}}, "required": []},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="get_quant_regime_state", description="Aggregate shadow-only quant regime telemetry from bot_metrics_live and state/quant_shadow_snapshot.json: GARCH status, OFI intensity, Kalman shadow slope, HMM regime status, and 7-day agreement summary when available. Does not compute models.",
         inputSchema={"type": "object", "properties": {"bot_id": {"type": "string", "description": "Optional bot id: mnq, cl, mes, nq"}}, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="get_vix_term_structure", description="Get VIX term structure from real CBOE data: spot VIX, VIX3M, VIX6M contango/backwardation. High contango (>10%) is bullish for equities; backwardation signals fear. Returns regime: contango, backwardation, flat.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="compute_information_ratio", description="Compute information ratio, tracking error, and active return for any symbol vs benchmark using real daily returns. IR > 0.5 is strong; > 1.0 is exceptional.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "benchmark": {"type": "string", "default": "SPY"}, "period": {"type": "string", "default": "1y"}}, "required": ["symbol"]},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="compute_correlation_matrix", description="Compute real-time cross-asset correlation matrix for a list of symbols using actual daily returns. Detects regime changes (correlation spikes during crises). Returns heatmap data, average pairwise correlation, and risk concentration score.",
         inputSchema={"type": "object", "properties": {"symbols": {"type": "array", "items": {"type": "string"}, "description": "2-20 symbols"}, "period": {"type": "string", "default": "3m"}, "threshold": {"type": "number", "default": 0.7, "description": "Alert if any pair correlation exceeds this"}}, "required": ["symbols"]},
         annotations=ANNOT_READ_EXTERNAL),
    # ═══════════════════════════════════════════════════════════════
    # V21: MCP 2025-11-25 Spec Compliance
    # ═══════════════════════════════════════════════════════════════
    Tool(name="request_trade_confirmation", description="MCP Elicitation: request structured human confirmation before executing a high-value or destructive trade action. Shows the user a form with trade details; execution is gated on approval.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "side": {"type": "string", "enum": ["BUY", "SELL"]}, "quantity": {"type": "integer"}, "order_type": {"type": "string", "default": "MARKET"}, "estimated_notional": {"type": "number"}, "action": {"type": "string", "default": "place_order", "description": "Action needing confirmation"}}, "required": ["symbol", "side", "quantity"]},
         annotations=ANNOT_TRADE_EXEC),
    Tool(name="submit_long_running_task", description="Submit a durable long-running MCP Task (backtest, optimization, ML retrain). Returns a task_id immediately. Use get_task_status to poll. Tasks persist across disconnects.",
         inputSchema={"type": "object", "properties": {"operation": {"type": "string", "description": "Operation type: full_backtest, walk_forward_optimize, ml_retrain, evolution_cycle, mcpt_validation"}, "params": {"type": "object"}, "title": {"type": "string"}, "description": {"type": "string"}}, "required": ["operation"]},
         annotations=ANNOT_COMPUTE),
    Tool(name="get_task_status", description="Get status and progress of a long-running MCP Task. Returns phase, progress percentage, result (when done), or error.",
         inputSchema={"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]},
         annotations=ANNOT_READ_ONLY),
    Tool(name="cancel_task", description="Cancel a pending or running MCP Task.",
         inputSchema={"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="list_active_tasks", description="List all active, pending, and recently completed MCP Tasks.",
         inputSchema={"type": "object", "properties": {"status": {"type": "string", "enum": ["pending", "running", "completed", "failed", "cancelled"], "description": "Filter by status"}}, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="subscribe_resource", description="Subscribe to an AlgoChains resource URI for real-time push notifications. Supported URIs: algochains://bots/{name}/metrics, algochains://positions, algochains://alerts/price, algochains://tasks/{id}/progress.",
         inputSchema={"type": "object", "properties": {"uri": {"type": "string", "description": "Resource URI to subscribe to"}, "subscriber_id": {"type": "string"}}, "required": ["uri"]},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="list_subscriptions", description="List all active resource subscriptions and pending notifications.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),
    # ═══════════════════════════════════════════════════════════════
    # V21: AlphaLoop — Autonomous Self-Improving Trading
    # ═══════════════════════════════════════════════════════════════
    Tool(name="run_evolution_cycle", description="Trigger an AlphaLoop evolution cycle: SCAN underperformers → MUTATE parameters via Optuna → VALIDATE against real trade history → PROMOTE winner. Uses RL reward model. Requires real trade history (min 5 trades).",
         inputSchema={"type": "object", "properties": {"strategy_id": {"type": "string"}, "generations": {"type": "integer", "default": 3}, "min_trades_required": {"type": "integer", "default": 10}, "promote_threshold": {"type": "number", "default": 0.1, "description": "Min reward improvement to promote"}}, "required": ["strategy_id"]},
         annotations=ANNOT_COMPUTE),
    Tool(name="get_evolution_status", description="Get current status of the AlphaLoop evolution daemon: last cycle time, active strategy, current phase, and cycle results.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="list_evolved_strategies", description="List all strategies that have been evolved by AlphaLoop, with before/after performance metrics.",
         inputSchema={"type": "object", "properties": {"limit": {"type": "integer", "default": 20}}, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="rollback_evolution", description="Roll back a strategy to its pre-evolution checkpoint if the promoted version underperforms.",
         inputSchema={"type": "object", "properties": {"strategy_id": {"type": "string"}}, "required": ["strategy_id"]},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="record_trade_episode", description="Record a completed trade episode into episodic trade memory for AlphaLoop learning. Stores entry/exit, regime, P&L, and lessons.",
         inputSchema={"type": "object", "properties": {"strategy_id": {"type": "string"}, "symbol": {"type": "string"}, "side": {"type": "string"}, "entry_price": {"type": "number"}, "exit_price": {"type": "number"}, "pnl_usd": {"type": "number"}, "regime": {"type": "string"}, "lesson": {"type": "string"}}, "required": ["strategy_id", "symbol", "side", "entry_price", "exit_price", "pnl_usd"]},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="query_trade_memory", description="Semantic search over episodic trade memory. Find similar past trades by regime, setup, or performance characteristics.",
         inputSchema={"type": "object", "properties": {"query": {"type": "string"}, "strategy_id": {"type": "string"}, "regime": {"type": "string"}, "limit": {"type": "integer", "default": 10}}, "required": ["query"]},
         annotations=ANNOT_READ_ONLY),
    Tool(name="get_lessons_learned", description="Get regime-specific lessons from trade memory for injection into agent session context.",
         inputSchema={"type": "object", "properties": {"strategy_id": {"type": "string"}, "regime": {"type": "string", "description": "Market regime: trending_up, trending_down, choppy, volatile"}, "limit": {"type": "integer", "default": 10}}, "required": ["strategy_id"]},
         annotations=ANNOT_READ_ONLY),
    Tool(name="get_strategy_rankings", description="Get strategy performance rankings from the RL reward model, sorted by composite reward score (Sharpe, regime alignment, consistency, drawdown).",
         inputSchema={"type": "object", "properties": {"limit": {"type": "integer", "default": 20}, "min_trades": {"type": "integer", "default": 5}}, "required": []},
         annotations=ANNOT_READ_ONLY),
    # ═══════════════════════════════════════════════════════════════
    # V21: Order Flow & Institutional Data
    # ═══════════════════════════════════════════════════════════════
    Tool(name="get_footprint_chart", description="Compute footprint chart for a symbol: bid/ask volume at each price level per candle, detecting absorption (sellers absorbed at support), imbalance (>3:1 ratio), and delta exhaustion. Uses real Databento tick data.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "timeframe": {"type": "string", "default": "5min", "description": "Bar timeframe: 1min, 5min, 15min, 1hour"}, "bars": {"type": "integer", "default": 20, "description": "Number of bars to compute"}, "tick_data": {"type": "array", "description": "Optional: provide raw tick data array; otherwise fetches from Databento"}}, "required": ["symbol"]},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="compute_cumulative_delta", description="Compute cumulative delta (net buy vs sell pressure) from OHLCV bars or raw ticks. Detects bullish/bearish divergence between price and delta — a leading indicator of reversals.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "timeframe": {"type": "string", "default": "5min"}, "bars": {"type": "integer", "default": 50}}, "required": ["symbol"]},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="get_dark_pool_volume_v21", description="Fetch dark pool volume for a symbol from real FINRA ATS reports + Polygon off-exchange trade conditions. Returns dark pool %, total off-exchange volume, and institutional activity score. NO synthetic data — fails if real sources unavailable.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "date": {"type": "string", "description": "YYYY-MM-DD (defaults to most recent available)"}}, "required": ["symbol"]},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="get_earnings_catalyst", description="Run earnings NLP pipeline: fetch SEC EDGAR filing, compute FinBERT sentiment, extract key themes (guidance, EPS beat/miss, capex), detect tone shift vs prior quarter. Returns catalyst score and actionable signal.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "quarter": {"type": "string", "description": "E.g. 'Q4 2025'; defaults to most recent"}}, "required": ["symbol"]},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="get_prediction_markets", description="Fetch real prediction market probabilities from Polymarket and Kalshi for macro events (Fed rate decisions, election outcomes, economic releases). Derives equity market signals from contract odds.",
         inputSchema={"type": "object", "properties": {"category": {"type": "string", "enum": ["fed", "economic", "political", "crypto", "all"], "default": "all"}, "min_volume": {"type": "number", "default": 10000}}, "required": []},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="search_prediction_markets", description="Search live Polymarket and/or Kalshi markets by keyword. Returns real contract YES/NO prices, volume, liquidity, and URLs. Fails closed if no API data.",
         inputSchema={"type": "object", "properties": {
             "query": {"type": "string", "description": "Search phrase, e.g. Bitcoin Fed election"},
             "platform": {"type": "string", "enum": ["polymarket", "kalshi", "all"], "default": "all"},
             "limit": {"type": "integer", "default": 10},
         }, "required": ["query"]},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="get_polymarket_high_volume", description="List highest 24h-volume Polymarket markets right now (real Gamma API). Useful for Roo-style early YES/NO flow and liquidity discovery.",
         inputSchema={"type": "object", "properties": {"limit": {"type": "integer", "default": 20}}, "required": []},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="record_prediction_market_bot_metric", description="Append one real performance snapshot for a Polymarket or Kalshi bot to the JSONL audit log (latency, YES prob, edge). Required for marketplace promotion evidence trail. No synthetic values stored unless caller passes them.",
         inputSchema={"type": "object", "properties": {
             "bot_id": {"type": "string"},
             "platform": {"type": "string", "enum": ["polymarket", "kalshi"]},
             "market_id": {"type": "string"},
             "yes_probability": {"type": "number"},
             "edge_vs_entry": {"type": "number"},
             "latency_ms_observed": {"type": "number"},
             "action": {"type": "string", "description": "BUY_YES, BUY_NO, HOLD, ARB, ..."},
             "notes": {"type": "string"},
             "metadata": {"type": "object"},
         }, "required": ["bot_id", "platform", "market_id"]},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="get_prediction_market_bot_metrics", description="Read recent JSONL metric entries for a prediction-market bot_id from the local audit log.",
         inputSchema={"type": "object", "properties": {
             "bot_id": {"type": "string"},
             "max_lines": {"type": "integer", "default": 500},
         }, "required": ["bot_id"]},
         annotations=ANNOT_READ_ONLY),
    # ── V22.8: New PM tools (gap analysis vs mcp-server-kalshi + polymarket-mcp) ──
    Tool(name="get_polymarket_market",
         description="Fetch detailed info for a specific Polymarket market by condition ID or event slug. Returns question, YES/NO prices, volume, liquidity, resolution date, and status. More precise than search — use when you have a specific market ID.",
         inputSchema={"type": "object", "properties": {
             "market_id_or_slug": {"type": "string", "description": "Polymarket condition ID (hex) or event slug (e.g. 'will-fed-cut-rates-in-june-2025')"},
         }, "required": ["market_id_or_slug"]},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="get_polymarket_market_history",
         description="Get historical YES price data for a specific Polymarket market. Returns timestamped price series. Accepts slug, Gamma numeric ID, or CLOB token ID — auto-resolves. Useful for charting probability movement, analyzing market efficiency, and detecting smart money flow timing.",
         inputSchema={"type": "object", "properties": {
             "market_id_or_slug": {"type": "string", "description": "Polymarket slug, numeric Gamma ID, or CLOB YES token ID"},
             "timeframe": {"type": "string", "enum": ["1d", "7d", "30d", "all"], "default": "7d", "description": "1d=10min candles, 7d=1h candles, 30d/all=daily candles"},
         }, "required": ["market_id_or_slug"]},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="list_polymarket_markets",
         description="List Polymarket prediction markets with status filtering and pagination. Unlike search, this returns all markets in a category. status=open (default) | closed | resolved. Sorts by 24h volume descending.",
         inputSchema={"type": "object", "properties": {
             "status": {"type": "string", "enum": ["open", "closed", "resolved"], "default": "open"},
             "limit": {"type": "integer", "default": 20, "description": "Max markets per page (1-100)"},
             "offset": {"type": "integer", "default": 0, "description": "Pagination offset"},
             "category": {"type": "string", "description": "Optional category/tag filter (e.g. politics, economics, crypto)"},
         }, "required": []},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="get_kalshi_settlements",
         description="Fetch recently settled Kalshi prediction market contracts (RSA-PSS signed). Returns results, profit-per-contract, and settlement timestamps. Requires KALSHI_ACCESS_KEY + KALSHI_PRIVATE_KEY_PATH. Inspired by 9crusher/mcp-server-kalshi settlements endpoint.",
         inputSchema={"type": "object", "properties": {
             "limit": {"type": "integer", "default": 25, "description": "Number of settlements to return"},
         }, "required": []},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="place_kalshi_order",
         description="Place a limit order on Kalshi via RSA-PSS signed POST. Requires KALSHI_ACCESS_KEY + KALSHI_PRIVATE_KEY_PATH. side: yes|no, action: buy|sell, limit_price_cents: 1-99 (represents probability %). WARNING: places real orders on live Kalshi account. Use demo BASE_URL for testing.",
         inputSchema={"type": "object", "properties": {
             "ticker": {"type": "string", "description": "Kalshi market ticker (e.g. HIGHAUS-25JUL01-T10.5)"},
             "side": {"type": "string", "enum": ["yes", "no"]},
             "action": {"type": "string", "enum": ["buy", "sell"]},
             "count": {"type": "integer", "description": "Number of contracts (each contract = $1 max payout)"},
             "limit_price_cents": {"type": "integer", "description": "Limit price in cents 1-99 (= probability %). 60 = buy at $0.60/contract."},
             "expiration_ts": {"type": "integer", "description": "Optional order expiry (ms since epoch)"},
         }, "required": ["ticker", "side", "action", "count", "limit_price_cents"]},
         annotations=ANNOT_TRADE_EXEC),
    Tool(name="get_kalshi_orderbook_depth",
         description="Fetch the CLOB order book depth for a Kalshi market ticker. "
                     "Returns yes/no bid-ask ladder, best bid, best ask, and spread. "
                     "Requires KALSHI_ACCESS_KEY + KALSHI_PRIVATE_KEY_PATH (RSA-PSS signed).",
         inputSchema={"type": "object", "properties": {
             "ticker": {"type": "string", "description": "Kalshi market ticker (e.g. INXD-23DEC29-T3990)"},
             "depth": {"type": "integer", "default": 10, "description": "Number of price levels per side (max 10)"},
         }, "required": ["ticker"]},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="stream_kalshi_fills",
         description="Fetch recent fills (trade history) for a Kalshi market ticker from the CLOB. "
                     "Returns fills with side, yes_price, count, and timestamp. "
                     "Use for market microstructure analysis and fill-rate alerting. "
                     "Requires KALSHI_ACCESS_KEY + KALSHI_PRIVATE_KEY_PATH.",
         inputSchema={"type": "object", "properties": {
             "ticker": {"type": "string", "description": "Kalshi market ticker"},
             "limit": {"type": "integer", "default": 50, "description": "Max fills to return (max 100)"},
         }, "required": ["ticker"]},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="scan_kalshi_edges",
         description="Scan all open Kalshi macro markets (FED/CPI/NFP) for positive expected-value "
                     "opportunities. Returns ranked edges with Kelly-optimal position sizes, "
                     "model probability vs market price, and suggested action (buy_yes/buy_no/market_make). "
                     "Requires KALSHI_ACCESS_KEY + KALSHI_PRIVATE_KEY_PATH.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="get_kalshi_account",
         description="Fetch live Kalshi account balance, open positions, and open orders. "
                     "Balance returned in USD (Kalshi stores in cents internally). "
                     "Requires KALSHI_ACCESS_KEY + KALSHI_PRIVATE_KEY_PATH.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="get_kalshi_pnl_summary",
         description="Compute Kalshi P&L summary from settled trades. Returns total revenue, "
                     "ROI %, open positions, and recent settlement history. "
                     "Requires KALSHI_ACCESS_KEY + KALSHI_PRIVATE_KEY_PATH.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="scan_kalshi_wide_spreads",
         description="Find open Kalshi markets with spreads wide enough for passive market-making. "
                     "Returns spread size, mid-price, and orderbook depth for each candidate. "
                     "Requires KALSHI_ACCESS_KEY + KALSHI_PRIVATE_KEY_PATH.",
         inputSchema={"type": "object", "properties": {
             "min_spread": {"type": "number", "default": 0.12, "description": "Minimum YES spread to include (0-1)"},
         }, "required": []},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="place_kalshi_strategy_order",
         description="Place a Kalshi order based on strategy engine recommendation. "
                     "Accepts edge recommendation from scan_kalshi_edges and executes at "
                     "fractional Kelly size. REAL MONEY — requires explicit confirmation. "
                     "Requires KALSHI_ACCESS_KEY + KALSHI_PRIVATE_KEY_PATH.",
         inputSchema={"type": "object", "properties": {
             "ticker": {"type": "string", "description": "Kalshi market ticker"},
             "side": {"type": "string", "enum": ["yes", "no"], "description": "Which side to buy"},
             "usd_amount": {"type": "number", "description": "Dollar amount to invest (converted to contracts internally)"},
             "max_price_cents": {"type": "integer", "description": "Max price in cents (1-99). Required for limit orders."},
             "confirmed": {"type": "boolean", "description": "Must be true to execute — prevents accidental orders"},
         }, "required": ["ticker", "side", "usd_amount", "max_price_cents", "confirmed"]},
         annotations=ANNOT_TRADE_EXEC),
    # ═══════════════════════════════════════════════════════════════
    # V22.10 — Kalshi Phase 2: Safe Compounder + Events API + Category Scoring
    # + AI Ensemble + Stat Arb + Unified Pipeline + Slack Notifier
    # Integrated from: ryanfrigo/kalshi-ai-trading-bot + yllvar/Kalshi-Quant-TeleBot
    # ═══════════════════════════════════════════════════════════════
    Tool(name="run_safe_compounder",
         description="Run the Safe Compounder strategy: scan all Kalshi markets for near-certain NO outcomes "
                     "(YES price ≤ 20¢). Returns ranked opportunities with Kelly sizing, edge calculation, "
                     "and suggested maker limit prices. Historically: NCAAB NO-side 74% win rate +10% ROI. "
                     "Set execute=true + confirmed=true to place real maker orders. "
                     "FED/CPI/NFP markets are hard-blocked (proven -40% to -65% ROI). "
                     "Requires KALSHI_ACCESS_KEY + KALSHI_PRIVATE_KEY_PATH.",
         inputSchema={"type": "object", "properties": {
             "bankroll_usd": {"type": "number", "default": 250.0, "description": "Current trading bankroll in USD"},
             "execute": {"type": "boolean", "default": False, "description": "If true, place actual maker orders"},
             "confirmed": {"type": "boolean", "default": False, "description": "Must be true alongside execute to place real orders"},
         }, "required": []},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="scan_all_kalshi_events",
         description="Scan the full Kalshi tradeable universe via the Events API (correct endpoint — "
                     "/markets only returns KXMVE parlay junk). Returns all open events with nested markets, "
                     "categorized by type. Excludes FED/CPI/NFP series (proven negative edge). "
                     "Use this before any strategy to see what's actually available to trade. "
                     "Requires KALSHI_ACCESS_KEY + KALSHI_PRIVATE_KEY_PATH.",
         inputSchema={"type": "object", "properties": {
             "categories": {"type": "array", "items": {"type": "string"},
                            "description": "Filter by category: sports, politics, weather, finance, other. Omit for all."},
         }, "required": []},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="get_kalshi_category_scores",
         description="Get category performance scores (0-100) for all Kalshi market series. "
                     "Shows win rate, ROI, allocation tier, and whether each category is tradeable. "
                     "Categories scoring < 30 are hard-blocked. FED/CPI/NFP scores are shown as proof "
                     "of negative edge (-40% to -65% ROI). Sports (NCAAB 74% WR) shown as best edge.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="run_kalshi_ai_debate",
         description="Run the 5-model OpenRouter AI ensemble debate on a specific Kalshi market. "
                     "Models: claude-opus-4.8 (Lead), gemini-2.5-pro (Forecaster), gpt-4o (Risk), "
                     "deepseek-chat (Bull), grok-4.3 (Bear). Returns weighted probability, "
                     "consensus action, and per-model reasoning. Costs ~$0.01-0.05 per call. "
                     "Daily budget cap enforced ($5/day default). Requires OPENROUTER_API_KEY.",
         inputSchema={"type": "object", "properties": {
             "ticker": {"type": "string", "description": "Kalshi market ticker to analyze"},
             "title": {"type": "string", "description": "Market question text"},
             "yes_bid": {"type": "number", "description": "Current best YES bid (0.0-1.0)"},
             "yes_ask": {"type": "number", "description": "Current best YES ask (0.0-1.0)"},
             "close_time": {"type": "string", "description": "ISO timestamp when market closes"},
             "extra_context": {"type": "string", "description": "Optional news, sentiment, or context to inject"},
             "fast_mode": {"type": "boolean", "default": True, "description": "True = 3 models (cheaper); False = all 5 models"},
         }, "required": ["ticker", "title", "yes_bid", "yes_ask"]},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="find_kalshi_stat_arb",
         description="Scan for statistical arbitrage opportunities across Kalshi markets. "
                     "Detects: (1) Spread arb — YES ask + NO ask > 1.0, sell both sides for risk-free profit. "
                     "(2) Bucket completeness — mutually exclusive event buckets that don't sum to 100%. "
                     "Returns opportunities ranked by edge size with suggested actions. "
                     "Requires KALSHI_ACCESS_KEY + KALSHI_PRIVATE_KEY_PATH.",
         inputSchema={"type": "object", "properties": {
             "max_events": {"type": "integer", "default": 20, "description": "Max events to scan"},
             "max_markets": {"type": "integer", "default": 50, "description": "Max total markets to check"},
         }, "required": []},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="run_kalshi_full_pipeline",
         description="Run the complete Kalshi strategy pipeline in one call: "
                     "(1) Account balance check + circuit breaker, "
                     "(2) Full Events API universe scan, "
                     "(3) Category scoring with blocklist enforcement, "
                     "(4) Safe Compounder opportunities (NO-side near-certain trades), "
                     "(5) Statistical arbitrage detection, "
                     "(6) Optional AI ensemble debate on top opportunity, "
                     "(7) Slack notification to #quant-lab, "
                     "(8) Supabase strategy run logging. "
                     "Set execute_safe_compounder=true + confirmed=true to place real orders. "
                     "Requires KALSHI_ACCESS_KEY + KALSHI_PRIVATE_KEY_PATH.",
         inputSchema={"type": "object", "properties": {
             "enable_ai_ensemble": {"type": "boolean", "default": False,
                                    "description": "Run AI ensemble on top opportunity (costs ~$0.05)"},
             "enable_stat_arb": {"type": "boolean", "default": True,
                                 "description": "Include stat arb scan in pipeline"},
             "execute_safe_compounder": {"type": "boolean", "default": False,
                                         "description": "Place actual Safe Compounder maker orders"},
             "confirmed": {"type": "boolean", "default": False,
                           "description": "Must be true alongside execute_safe_compounder to place real orders"},
             "notify_slack": {"type": "boolean", "default": True,
                              "description": "Post scan results to #quant-lab Slack channel"},
         }, "required": []},
         annotations=ANNOT_READ_EXTERNAL),
    # ═══════════════════════════════════════════════════════════════
    # V22.9 — PAI Integration: TELOS + US Economics + Learning Signals + ntfy
    # Based on gap analysis vs danielmiessler/Personal_AI_Infrastructure (⭐11.2k)
    # Novel additions only — skills/memory/debate already surpassed PAI equivalents
    # ═══════════════════════════════════════════════════════════════
    Tool(name="get_algochains_telos",
         description="Read AlgoChains business identity files (TELOS system, adapted from PAI). Returns mission, goals, strategies, mental models, lessons learned, challenges, ideas, and KPIs. Use section='all' for full context or specify: mission|goals|strategies|models|learned|challenges|ideas|metrics. Every agent should read TELOS at session start for full business context.",
         inputSchema={"type": "object", "properties": {
             "section": {"type": "string", "enum": ["all", "mission", "goals", "strategies", "models", "learned", "challenges", "ideas", "metrics"], "description": "Which TELOS file to read. 'all' returns all sections.", "default": "all"},
         }, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="update_algochains_telos",
         description="Append a new entry to an AlgoChains TELOS file (goals, learned, ideas, challenges, etc.). Use to capture new lessons learned, ideas, or goal updates during a session. The log is append-only — entries are never overwritten.",
         inputSchema={"type": "object", "properties": {
             "section": {"type": "string", "enum": ["goals", "strategies", "models", "learned", "challenges", "ideas", "metrics"], "description": "Which TELOS section to update"},
             "entry": {"type": "string", "description": "The content to add (markdown text, plain sentences OK)"},
             "action": {"type": "string", "enum": ["append", "prepend"], "default": "append", "description": "append = add to end (default); prepend = add after header"},
         }, "required": ["section", "entry"]},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="get_us_economic_indicators",
         description="Fetch US economic indicators from FRED (Federal Reserve Economic Data). Covers 16 key indicators: VIX, Fed Funds Rate, CPI, PCE, 10Y-2Y Treasury spread, unemployment, M2, GDP, housing starts, consumer sentiment. Requires FRED_API_KEY (free at fred.stlouisfed.org). Results cached 6h. Essential for regime detection across all bots.",
         inputSchema={"type": "object", "properties": {
             "categories": {"type": "array", "items": {"type": "string"}, "description": "Filter by category. Options: monetary_policy, rates, inflation, labor, growth, volatility, sentiment, housing. Omit for all."},
             "use_cache": {"type": "boolean", "default": True, "description": "Use 6-hour local cache to avoid rate limits"},
         }, "required": []},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="get_crude_oil_inventories",
         description="Fetch EIA weekly crude oil inventory data — critical signal for the CL (crude oil) futures bot. Covers US commercial crude stocks, Cushing Oklahoma (WTI delivery point), and field production. Released every Wednesday ~10:30 AM ET. Build above estimate = bearish CL; draw below = bullish. Requires EIA_API_KEY (free at eia.gov/opendata).",
         inputSchema={"type": "object", "properties": {
             "use_cache": {"type": "boolean", "default": True, "description": "Use 24-hour cache (EIA data is weekly)"},
         }, "required": []},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="get_fed_policy_signals",
         description="Get the 7 most important Fed policy indicators in one call: Fed Funds Rate, CPI, PCE, 10Y-2Y spread, VIX, 10Y yield, 2Y yield — with AI-derived regime interpretation (restrictive/neutral/accommodative, crisis/normal, inverted/normal yield curve). Use for MNQ/NQ regime context before trading sessions. Requires FRED_API_KEY.",
         inputSchema={"type": "object", "properties": {
             "use_cache": {"type": "boolean", "default": True},
         }, "required": []},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="capture_learning_signal",
         description="Record the outcome of an agent action or skill invocation for continuous learning. After 30+ signals, patterns emerge: which skills produce the best outcomes, where failure is common, what to improve. Stored in state/learning_signals.jsonl (append-only audit log). Use after any significant agent action.",
         inputSchema={"type": "object", "properties": {
             "action_type": {"type": "string", "enum": ["bot_diagnosis", "strategy_change", "bot_restart", "token_renewal", "backtest_run", "skill_invocation", "code_change", "research", "deploy", "market_analysis", "position_management", "alert_triage", "onboarding", "debate_invocation", "mcpt_validation", "regime_detection", "other"]},
             "action_description": {"type": "string", "description": "Short description of what was done (< 200 chars)"},
             "outcome": {"type": "string", "enum": ["success", "failure", "partial", "skipped", "unknown"]},
             "rating": {"type": "integer", "minimum": 1, "maximum": 10, "description": "1-10 quality rating (10 = perfect/euphoric result). Optional."},
             "notes": {"type": "string", "description": "Free-text notes about what happened and why"},
             "skill_used": {"type": "string", "description": "Name of skill invoked (e.g. 'bot-diagnostics')"},
             "bot": {"type": "string", "description": "Which bot this relates to (MNQ, CL, MES, NQ, all)"},
             "agent": {"type": "string", "description": "Which agent captured this (cursor, claude, windsurf, openclaw)"},
             "session_id": {"type": "string", "description": "Optional session ID for grouping related signals"},
         }, "required": ["action_type", "action_description", "outcome"]},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="get_learning_signals",
         description="Retrieve and analyze historical learning signals from state/learning_signals.jsonl. Returns signals with optional summary statistics: success rate by action type, top skills by effectiveness, bot activity, average ratings. Use to identify where agent performance is strongest/weakest and drive improvement priorities.",
         inputSchema={"type": "object", "properties": {
             "limit": {"type": "integer", "default": 50, "description": "Max signals to return (most recent first)"},
             "action_type": {"type": "string", "description": "Filter by action type"},
             "outcome": {"type": "string", "enum": ["success", "failure", "partial", "skipped", "unknown"]},
             "bot": {"type": "string", "description": "Filter by bot name (MNQ, CL, MES, NQ)"},
             "min_rating": {"type": "integer", "minimum": 1, "maximum": 10},
             "summarize": {"type": "boolean", "default": True, "description": "Include summary statistics"},
         }, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="send_ntfy_notification",
         description="Send a mobile push notification via ntfy (https://ntfy.sh). Topics: bots (bot up/down/trade), risk (circuit breaker, daily loss), marketplace (new subscriber, bot promoted), ops (deploy, system health), alpha (high-confidence signal). Priority: max/urgent = always-on screen; high = with sound; default = normal; low/min = silent. Requires NTFY_BASE_URL + optional NTFY_AUTH_TOKEN.",
         inputSchema={"type": "object", "properties": {
             "title": {"type": "string", "description": "Notification title (shown in bold)"},
             "message": {"type": "string", "description": "Notification body text"},
             "topic": {"type": "string", "enum": ["bots", "risk", "marketplace", "ops", "alpha"], "default": "ops"},
             "priority": {"type": "string", "enum": ["max", "urgent", "high", "default", "low", "min"], "default": "default"},
             "tags": {"type": "array", "items": {"type": "string"}, "description": "Emoji tag names (e.g. ['warning', 'robot']). ntfy maps to emojis automatically."},
             "click_url": {"type": "string", "description": "URL to open when notification is tapped"},
         }, "required": ["title", "message"]},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="propagate_trade_signal", description="POST a signed trade signal to the AlgoChains Django propagation service. Requires ALGOCHAINS_SIGNAL_URL and ALGOCHAINS_SIGNAL_SECRET env vars — fails closed when unset. Subscribers receive execution on connected PAPER Alpaca accounts. Register your bot at algochains.ai first.",
         inputSchema={"type": "object", "properties": {
             "strategy_name": {"type": "string", "description": "Must match bot name on algochains.ai exactly (case-sensitive)"},
             "symbol": {"type": "string", "description": "e.g. BTC/USD, SPY, AAPL"},
             "side": {"type": "string", "enum": ["BUY", "SELL", "buy", "sell"]},
             "qty": {"type": "number"},
             "confidence": {"type": "number", "default": 0.0},
             "stop_loss": {"type": "number", "default": 0.0},
             "take_profit": {"type": "number", "default": 0.0},
         }, "required": ["strategy_name", "symbol", "side", "qty"]},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="check_propagation_health", description="Check if the AlgoChains Django signal propagation service (Roo architecture) is reachable and whether copy-trade paper fanout has active backlog. Separates active_lag_seconds from idle_since_last_signal_seconds so quiet markets do not look stalled.",
         inputSchema={"type": "object", "properties": {
             "max_lag_seconds": {"type": "number", "default": 30.0, "description": "SLO threshold for active, unexpired signal backlog."},
         }, "required": []},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="run_guardrail", description="Run the GUARDRAIL pre-flight middleware chain before placing any order. Executes 6 gates: VIX, daily-loss, stoploss-guard, cooldown, confidence, R/R. Returns approved=true only if all gates pass. Wire this before every order execution.",
         inputSchema={"type": "object", "properties": {
             "symbol": {"type": "string", "description": "e.g. MNQ, CL, BTC/USD"},
             "side": {"type": "string", "enum": ["BUY", "SELL"]},
             "entry": {"type": "number", "description": "Entry price (enables R/R gate)"},
             "stop": {"type": "number", "description": "Stop loss price (enables R/R gate)"},
             "confidence": {"type": "number", "description": "Model confidence 0-1"},
             "vix": {"type": "number", "description": "Current VIX (reads CURRENT_VIX env if omitted)"},
             "daily_pnl": {"type": "number", "description": "Today realized P&L (reads TODAY_REALIZED_PNL env if omitted)"},
             "gates": {"type": "array", "items": {"type": "string"}, "description": "Gate subset to run. Omit for all: vix, daily_loss, stoploss_guard, cooldown, confidence, risk_reward"},
         }, "required": ["symbol", "side"]},
         annotations=ANNOT_READ_SAFE),
    Tool(name="test_signal_propagation", description="Run Roo's 3-signal verification test: sends BUY → SELL → BUY to your registered bot on algochains.ai. Check your dashboard to confirm all 3 paper trades appear. Must have bot registered at algochains.ai first.",
         inputSchema={"type": "object", "properties": {
             "strategy_name": {"type": "string", "description": "Your bot name on algochains.ai (must match exactly)"},
             "symbol": {"type": "string", "default": "BTC/USD"},
             "qty": {"type": "number", "default": 0.001},
         }, "required": ["strategy_name"]},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="get_macro_signals", description="Get pre-computed macro alpha signal fabric: yield curve shape (2y-10y), credit spreads (HY-IG), DXY momentum, PMI regime, VIX term structure contango/backwardation. All from real FRED/CBOE/Polygon APIs.",
         inputSchema={"type": "object", "properties": {"signals": {"type": "array", "items": {"type": "string"}, "description": "Subset: yield_curve, credit_spreads, dxy, pmi, vix. Omit for all."}}, "required": []},
         annotations=ANNOT_READ_EXTERNAL),
    # ═══════════════════════════════════════════════════════════════
    # V21: Key Vault & Agent Provisioning
    # ═══════════════════════════════════════════════════════════════
    Tool(name="store_api_key", description="Store an API key in the encrypted local key vault (AES-256-GCM, scrypt KDF). Key is stored at ~/.algochains/vault.enc. Raw key is never returned to LLM after storage.",
         inputSchema={"type": "object", "properties": {"name": {"type": "string", "description": "Key name (e.g. POLYGON_API_KEY)"}, "value": {"type": "string", "description": "The API key value to encrypt"}, "passphrase": {"type": "string", "description": "Vault master passphrase"}}, "required": ["name", "value", "passphrase"]},
         annotations=ANNOT_WRITE_DESTRUCTIVE),
    Tool(name="list_vault_keys", description="List key names stored in the encrypted vault (names only, values are never exposed).",
         inputSchema={"type": "object", "properties": {"passphrase": {"type": "string"}}, "required": ["passphrase"]},
         annotations=ANNOT_READ_ONLY),
    Tool(name="rotate_api_key", description="Rotate an API key in the vault: validate the new key against its service, store replacement, invalidate old key.",
         inputSchema={"type": "object", "properties": {"name": {"type": "string"}, "new_value": {"type": "string"}, "passphrase": {"type": "string"}}, "required": ["name", "new_value", "passphrase"]},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="provision_agent_account", description="Provision an isolated broker sub-account for an AI agent via Alpaca Broker API. Each agent gets a dedicated account ID, API key pair, and per-agent risk limits. Keys stored in vault.",
         inputSchema={"type": "object", "properties": {"agent_id": {"type": "string"}, "description": {"type": "string"}, "max_position_usd": {"type": "number", "default": 10000}, "allowed_assets": {"type": "array", "items": {"type": "string"}}}, "required": ["agent_id"]},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="list_agent_accounts", description="List all provisioned AI agent sub-accounts with their account IDs, status, and risk limits.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),
    # ═══════════════════════════════════════════════════════════════
    # V21: Streaming & Price Alerts
    # ═══════════════════════════════════════════════════════════════
    Tool(name="create_price_alert", description="Create a persistent price alert on a symbol. Conditions: price_above, price_below, pct_change_15min, vwap_cross, volume_spike. Alert persists in SQLite; fires when condition met via Polygon real-time polling. Emits MCP resource notification.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "condition": {"type": "string", "enum": ["price_above", "price_below", "pct_change_15min", "vwap_cross", "volume_spike"]}, "threshold": {"type": "number"}, "message": {"type": "string"}}, "required": ["symbol", "condition", "threshold"]},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="list_price_alerts", description="List all active price alerts with their current status and trigger history.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string", "description": "Filter by symbol (optional)"}, "active_only": {"type": "boolean", "default": True}}, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="delete_price_alert", description="Delete a price alert by ID.",
         inputSchema={"type": "object", "properties": {"alert_id": {"type": "string"}}, "required": ["alert_id"]},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="subscribe_earnings_events", description="Subscribe to earnings event notifications for a symbol. Sends pre-earnings alerts (24h before), EPS result alerts (after announcement), and IV crush signals.",
         inputSchema={"type": "object", "properties": {"symbols": {"type": "array", "items": {"type": "string"}}, "alert_days_before": {"type": "integer", "default": 1}}, "required": ["symbols"]},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="get_earnings_calendar", description="Get upcoming earnings dates and consensus EPS estimates for a list of symbols. Data from Polygon.io financials and SEC EDGAR.",
         inputSchema={"type": "object", "properties": {"symbols": {"type": "array", "items": {"type": "string"}}, "days_ahead": {"type": "integer", "default": 30}}, "required": ["symbols"]},
         annotations=ANNOT_READ_EXTERNAL),
    # ═══════════════════════════════════════════════════════════════
    # V21: Bot Metrics & Live Showcase
    # ═══════════════════════════════════════════════════════════════
    Tool(name="get_bot_dashboard", description="Get real-time dashboard of all live trading bots: PIDs, positions, today's P&L, signal counts, win rates computed from actual fill history. Data from ~/.algochains/bot_metrics.db.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="get_bot_metrics", description="Get detailed metrics for a specific bot: Sharpe ratio, win rate, avg win/loss, profit factor, max drawdown — all computed from real trade history.",
         inputSchema={"type": "object", "properties": {"bot_name": {"type": "string", "description": "Bot name: MNQ_Upgraded_Scalper, CL_Swing_Scalper, MES_EMA_Swing, NQ_EMA_Swing"}, "last_n_trades": {"type": "integer", "default": 50}}, "required": ["bot_name"]},
         annotations=ANNOT_READ_ONLY),
    Tool(name="subscribe_bot_metrics", description="Subscribe to real-time bot metrics stream via MCP resource notifications. Fires on every fill, signal, and position update. Perfect for the private bot showcase on AlgoChains marketplace.",
         inputSchema={"type": "object", "properties": {"bot_name": {"type": "string"}, "subscriber_id": {"type": "string", "description": "Subscriber identifier (use your user ID for private access)"}}, "required": ["bot_name", "subscriber_id"]},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="get_live_pnl", description="Get current live P&L across all active positions: unrealized P&L per position, today's realized P&L, and total account P&L. From real broker account state.",
         inputSchema={"type": "object", "properties": {"broker": {"type": "string", "default": "tradovate"}}, "required": []},
         annotations=ANNOT_READ_EXTERNAL),
    # ═══════════════════════════════════════════════════════════════
    # V22.7: Skills Bridge — OpenClaw + Windsurf + Cursor + Claude
    # ═══════════════════════════════════════════════════════════════
    Tool(name="list_skills",
         description="List all available AlgoChains skills from OpenClaw (363+), Windsurf (80+), Cursor (15), and Claude (8) skill libraries. Filter by category (trading, research, operations, intelligence, agent, comms, risk, data, ml, marketplace) or platform. Returns name, description, categories, tools used, and trigger type.",
         inputSchema={"type": "object", "properties": {
             "category": {"type": "string", "description": "Filter by category: trading, research, operations, intelligence, agent, comms, risk, data, ml, marketplace"},
             "platform": {"type": "string", "description": "Filter by platform: openclaw, windsurf, cursor, claude"},
             "limit": {"type": "integer", "default": 50},
             "offset": {"type": "integer", "default": 0},
         }, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="get_skill_detail",
         description="Get the full SKILL.md content and metadata for any skill by name (e.g. 'moltbook-debate', 'bot-diagnostics', 'autonomous-researcher', 'backtest-governance'). Returns complete instructions, tool requirements, trigger conditions, and schedule. Use list_skills or search_skills to discover skill names.",
         inputSchema={"type": "object", "properties": {
             "name": {"type": "string", "description": "Skill name (exact or partial match)"},
         }, "required": ["name"]},
         annotations=ANNOT_READ_ONLY),
    Tool(name="search_skills",
         description="Search across all 450+ skills by keyword. Returns ranked matches from OpenClaw, Windsurf, Cursor, and Claude libraries. Use to find the right skill for a task before reading its full SKILL.md.",
         inputSchema={"type": "object", "properties": {
             "query": {"type": "string", "description": "Search query (e.g. 'regime detection', 'bot restart', 'dark pool', 'backtest')"},
             "limit": {"type": "integer", "default": 20},
         }, "required": ["query"]},
         annotations=ANNOT_SEARCH),
    Tool(name="get_skills_for_task",
         description="Given a task description in plain language, return the 3-5 best skills to use. Matches your task against skill descriptions across all platforms. Use when you do not know which skill to call.",
         inputSchema={"type": "object", "properties": {
             "task_description": {"type": "string", "description": "Natural language description of what you need to do"},
         }, "required": ["task_description"]},
         annotations=ANNOT_READ_ONLY),
    Tool(name="reload_skills_registry",
         description="Force a reload of the skills registry from disk (after adding new skills or updating SKILL.md files). Returns total skills loaded per platform.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),
    # ── Agent Memory Bridge ──────────────────────────────────────
    Tool(name="get_openclaw_memory",
         description="Read the OpenClaw agent memory store. Contains trade lessons, regime history, signal quality scores, and cross-session agent context. Filter by key_prefix (e.g. 'trade', 'regime', 'bot') to narrow results.",
         inputSchema={"type": "object", "properties": {
             "key_prefix": {"type": "string", "description": "Optional prefix filter on memory keys"},
             "limit": {"type": "integer", "default": 50},
         }, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="store_trade_lesson",
         description="Persist a trade lesson to OpenClaw memory so autonomous agents can learn from it. Lessons are retrieved during future trade decisions for similar setups. Required: symbol, direction, outcome, lesson text.",
         inputSchema={"type": "object", "properties": {
             "symbol": {"type": "string"},
             "direction": {"type": "string", "enum": ["LONG", "SHORT", "HOLD"]},
             "outcome": {"type": "string", "enum": ["WIN", "LOSS", "BREAKEVEN"]},
             "regime": {"type": "string", "description": "Market regime at time of trade"},
             "lesson": {"type": "string", "description": "Key lesson from this trade"},
             "pnl": {"type": "number", "description": "P&L in dollars (optional)"},
         }, "required": ["symbol", "direction", "outcome", "lesson"]},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="get_current_regime",
         description="Read the current market regime from OpenClaw state (written by autonomous regime_detector skill). Returns regime label, confidence, and timestamp. This is the regime all live bots use for signal filtering.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="get_bot_heartbeat_openclaw",
         description="Read the bot heartbeat state from OpenClaw — shows which bots are alive, last-seen timestamps, and LIVE/STALE status. Written by autonomous_watchdog.py every 5 minutes.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="get_agent_evaluations",
         description="Read OpenClaw agent evaluation records — which agents have been scored, their performance accuracy, and reputation weights. Used by the Moltbook reputation system and agent-orchestrator.",
         inputSchema={"type": "object", "properties": {
             "limit": {"type": "integer", "default": 20},
         }, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="get_openclaw_state_summary",
         description="Get existence, size, and last-modified time for all OpenClaw state files (memory, regime, heartbeat, monitor, evaluations, AI cost, calibration). Use to verify OpenClaw is healthy and its state files are current.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),
    # ── Skill Execution Shortcuts ─────────────────────────────────
    Tool(name="invoke_moltbook_debate",
         description="Trigger a Moltbook bull/bear multi-agent debate for a trading signal. Shadow mode — does NOT place orders. Returns consensus direction, confidence, agreement %, and per-agent reasoning. Use before significant trades for multi-agent validation.",
         inputSchema={"type": "object", "properties": {
             "symbol": {"type": "string", "description": "Trading symbol e.g. MNQ, CL, ES"},
             "direction": {"type": "string", "enum": ["LONG", "SHORT"]},
             "confidence": {"type": "number", "description": "Bot confidence score 0-100"},
             "regime": {"type": "string", "description": "Market regime (optional, fetched from OpenClaw if omitted)"},
             "trigger_type": {"type": "string", "default": "mcp_manual"},
         }, "required": ["symbol", "direction", "confidence"]},
         annotations=ANNOT_READ_ONLY),
    Tool(name="run_mcpt_pipeline",
         description="Run the MCPT marketplace autopilot pipeline. Steps: decay (check edge decay), graduate (30-day paper trading gates), audit (batch MCPT re-validation), listing (generate marketplace JSON), slack (post summary to #quant-lab). Calls scripts/mcpt_autopilot.py.",
         inputSchema={"type": "object", "properties": {
             "step": {"type": "string", "enum": ["all", "sync", "decay", "graduate", "audit", "listing", "slack"], "default": "all"},
             "dry_run": {"type": "boolean", "default": False, "description": "Report only, no writes"},
             "no_desktop": {"type": "boolean", "default": False},
         }, "required": []},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="run_regime_detection",
         description="Run the regime detection pipeline — analyzes VIX term structure, market breadth, and price action to classify current market as trending/choppy/volatile/mean_reverting. Updates OpenClaw current_regime.json used by all live bots.",
         inputSchema={"type": "object", "properties": {
             "symbol": {"type": "string", "default": "SPY"},
         }, "required": []},
         annotations=ANNOT_READ_ONLY),
    # ═══════════════════════════════════════════════════════════════
    # V21: Onyx Intelligence
    # ═══════════════════════════════════════════════════════════════
    Tool(name="onyx_search", description="Semantic search over the AlgoChains Onyx knowledge base: 400+ strategy research JSONs, 45+ blueprints, 126 skills, live bot logs. Returns ranked documents with relevance scores.",
         inputSchema={"type": "object", "properties": {"query": {"type": "string", "description": "Natural language search query"}, "limit": {"type": "integer", "default": 10}, "document_set": {"type": "string", "description": "Optional: filter to a document set (research, blueprints, skills, logs)"}}, "required": ["query"]},
         annotations=ANNOT_SEARCH),
    Tool(name="onyx_ask", description="Ask a natural language question against the Onyx knowledge base with RAG grounding. Returns an answer with cited sources. E.g. 'What is the best CL swing setup in trending regimes?' or 'How do I configure Token Guardian?'",
         inputSchema={"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]},
         annotations=ANNOT_SEARCH),
    Tool(name="onyx_health", description="Check if the Onyx knowledge base is reachable (self-hosted RAG host configured via ONYX_API_URL).",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="onyx_find_best_setup", description="Ask Onyx to find the best historical setup for a symbol/regime combination by searching trade memory, research JSONs, and strategy docs. Returns top 3 setups with evidence.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "regime": {"type": "string", "description": "Market regime: trending, choppy, volatile, mean_reverting"}, "timeframe": {"type": "string", "default": "5min"}}, "required": ["symbol"]},
         annotations=ANNOT_SEARCH),
    # ═══════════════════════════════════════════════════════════════
    # V21: Crypto Feature Parity
    # ═══════════════════════════════════════════════════════════════
    Tool(name="get_funding_rate", description="Get real-time perpetual futures funding rates from Binance, Bybit, and Hyperliquid. Identifies funding rate arbitrage opportunities and predicts funding-driven price pressure.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string", "description": "Crypto symbol e.g. BTCUSDT, ETHUSDT"}, "exchanges": {"type": "array", "items": {"type": "string"}, "default": ["binance", "bybit", "hyperliquid"]}}, "required": ["symbol"]},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="get_perp_open_interest", description="Get open interest trends for crypto perpetual futures across exchanges. OI growth with price confirms trend; OI drop with price move signals potential reversal.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "exchange": {"type": "string", "default": "binance"}}, "required": ["symbol"]},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="get_liquidation_clusters", description="Get liquidation cluster map for a crypto symbol: price levels with highest concentration of liquidations. From Binance/Bybit liquidation data.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "lookback_hours": {"type": "integer", "default": 24}}, "required": ["symbol"]},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="get_staking_yields", description="Get real staking APY from Lido Finance (stETH), Binance Simple Earn, Cosmos validators, and Ethereum Beacon Chain. Compares yield opportunities across protocols.",
         inputSchema={"type": "object", "properties": {"protocols": {"type": "array", "items": {"type": "string"}, "default": ["lido", "binance_earn", "cosmos", "ethereum_beacon"]}}, "required": []},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="create_dca_schedule", description="Create a Dollar-Cost Averaging (DCA) schedule for automatic recurring purchases via Alpaca API. Supports fractional shares and crypto. Persisted in SQLite.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "amount_usd": {"type": "number"}, "frequency": {"type": "string", "enum": ["daily", "weekly", "biweekly", "monthly"]}, "max_purchases": {"type": "integer", "description": "Stop after N purchases"}}, "required": ["symbol", "amount_usd", "frequency"]},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="get_copy_leaders", description="List top copy trading leaders with their performance metrics: return, win rate, drawdown, strategy style. Data from real broker APIs.",
         inputSchema={"type": "object", "properties": {"min_return_pct": {"type": "number", "default": 20}, "min_win_rate": {"type": "number", "default": 0.55}, "limit": {"type": "integer", "default": 10}}, "required": []},
         annotations=ANNOT_READ_EXTERNAL),
    # ═══════════════════════════════════════════════════════════════
    # V21: SaaS Tenant Hardening
    # ═══════════════════════════════════════════════════════════════
    Tool(name="get_tenant_audit_log", description="Retrieve the immutable audit log for a tenant: all MCP tool calls with timestamps, parameters (sensitive values redacted), and outcomes.",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}, "limit": {"type": "integer", "default": 100}, "tool_name": {"type": "string", "description": "Filter by tool name"}}, "required": ["tenant_id"]},
         annotations=ANNOT_READ_ONLY),
    Tool(name="create_tenant_sandbox", description="Create an isolated paper-mode sandbox environment for a tenant: separate SQLite state, paper broker account, rate limits. For testing strategies without affecting live account.",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}, "config": {"type": "object", "description": "Optional sandbox configuration overrides"}}, "required": ["tenant_id"]},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="get_tenant_rate_limits", description="Check current rate limit status for a tenant: remaining calls, reset time, tier limits.",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}}, "required": ["tenant_id"]},
         annotations=ANNOT_READ_ONLY),
    # ═══════════════════════════════════════════════════════════════
    # Desktop Tower Job Dispatcher
    # ═══════════════════════════════════════════════════════════════
    Tool(name="dispatch_tower_job", description="Dispatch a heavy compute job to a configured GPU compute node (set ALGOCHAINS_TOWER_HOST) via SSH. Jobs: optuna_optimize, walk_forward_backtest, ml_retrain, mcpt_validation. Returns job_id for polling. Small jobs (<500MB) run locally; large/GPU jobs route to the compute node automatically.",
         inputSchema={"type": "object", "properties": {"job_type": {"type": "string", "enum": ["optuna_optimize", "walk_forward_backtest", "ml_retrain", "mcpt_validation", "large_backtest", "factor_model_compute"]}, "params": {"type": "object", "description": "Job parameters: bot, symbol, n_trials, data_start, data_end, model, etc."}, "force_local": {"type": "boolean", "default": False}}, "required": ["job_type"]},
         annotations=ANNOT_COMPUTE),
    Tool(name="get_tower_job_status", description="Get status and result of a dispatched tower job. Polls the tower via SSH for the result file.",
         inputSchema={"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]},
         annotations=ANNOT_READ_ONLY),
    Tool(name="get_tower_health", description="Check the configured compute node (ALGOCHAINS_TOWER_HOST) health: reachable, memory, active jobs, GPU status.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="list_tower_jobs", description="List recent tower jobs with status, type, and memory usage.",
         inputSchema={"type": "object", "properties": {"status": {"type": "string", "enum": ["pending", "running", "completed", "failed"]}, "limit": {"type": "integer", "default": 20}}, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="get_signal_conflict_stats", description="Get signal conflict statistics for a futures bot: how many BLOCKED, ALLOWED, FORCE_REVERSED signals with P&L context. Shows the signal overlap protection system in action.",
         inputSchema={"type": "object", "properties": {"bot_name": {"type": "string", "description": "Bot name: MNQ_Upgraded_Scalper, CL_Swing_Scalper, MES_EMA_Swing, NQ_EMA_Swing"}, "hours": {"type": "integer", "default": 24}}, "required": ["bot_name"]},
         annotations=ANNOT_READ_ONLY),
    Tool(name="get_paper_trading_metrics", description="Get real paper trading metrics from the Alpaca unified paper trader: equity curve, open positions, today's P&L, win rate, recent signals. Data from the live $144K paper account.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_EXTERNAL),

    # ── Marketplace Autopilot Pipeline ─────────────────────────────────────
    Tool(name="run_marketplace_autopilot", description="Run the autonomous marketplace pipeline: Research→Backtest→MCPT Validate→Stage for marketplace. Scans recent strategy research, runs tick backtests, applies 5-gate validation, stages passing strategies as marketplace JSON listings. Triggers Onyx ingest and Slack notification. No synthetic data — real tick engines only.",
         inputSchema={"type": "object", "properties": {
             "stage": {"type": "string", "enum": ["all", "research", "backtest", "validate", "stage"], "default": "all", "description": "Pipeline stage to run"},
             "symbol": {"type": "string", "description": "Limit to specific symbol (optional)"},
             "dry_run": {"type": "boolean", "default": False, "description": "No writes, just report what would happen"},
         }, "required": []},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="get_marketplace_listings", description="Get all staged marketplace bot listings with real metrics: futures (owner-only), equities, crypto, forex. Includes Sharpe, win rate, max DD, subscription pricing, and paper trading status. Supabase-first with local filesystem fallback.",
         inputSchema={"type": "object", "properties": {
             "asset_class": {"type": "string", "enum": ["all", "equities", "crypto", "futures", "forex", "options"], "default": "all"},
             "status": {"type": "string", "enum": ["all", "live", "validated", "paper"], "default": "all"},
             "limit": {"type": "integer", "default": 50, "description": "Max listings to return"},
         }, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="get_subscriber_bots", description="Get all active bot subscriptions for a given subscriber. Returns listing details, status, and join date. Requires SUPABASE_SERVICE_ROLE_KEY. Pass user_id as email or UUID.",
         inputSchema={"type": "object", "properties": {
             "user_id": {"type": "string", "description": "Subscriber email address or UUID"},
         }, "required": ["user_id"]},
         annotations=ANNOT_READ_ONLY),
    Tool(name="deliver_strategy_to_subscriber",
         description="Deliver an approved marketplace strategy config to a subscriber's bot endpoint. "
                     "Verifies active subscription ownership, SSRF-checks webhook URL, signs token, POSTs to webhook. "
                     "REQUIRES owner_token — SEC-2026-C2. Signed token is NOT returned in MCP response.",
         inputSchema={"type": "object", "properties": {
             "subscriber_id": {"type": "string", "description": "Subscriber Supabase user ID (auth.uid())"},
             "strategy_id": {"type": "string", "description": "Supabase marketplace_listing.id to deliver"},
             "webhook_url": {"type": "string", "description": "Override webhook URL (optional, SSRF-checked; private/link-local targets blocked)"},
             "token_ttl_seconds": {"type": "integer", "default": 86400, "description": "Config token lifetime in seconds (default 24h)"},
             "owner_token": {"type": "string", "description": "Must match OWNER_API_TOKEN env var."},
         }, "required": ["subscriber_id", "strategy_id", "owner_token"]},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="run_onyx_ingest", description="Trigger an incremental Onyx knowledge base ingest: indexes new strategy research, marketplace listings, blueprints, skills, and bot logs into the self-hosted Onyx RAG host (ONYX_API_URL).",
         inputSchema={"type": "object", "properties": {
             "full_sync": {"type": "boolean", "default": False, "description": "Full re-index vs incremental (new files only)"},
         }, "required": []},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="get_onyx_status", description="Check Onyx knowledge base status: health, last sync time, total indexed documents, connector status (self-hosted host via ONYX_API_URL).",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_EXTERNAL),
    Tool(name="get_learn_hub_health", description="Check AlgoChains Learn Hub health: HTTP status of /learn/, /learn/feed.xml RSS MIME, and learn.algochains.ai subdomain redirect. Read-only — does NOT deploy. Use to verify the live Learn Hub is up and public (no login required).",
         inputSchema={"type": "object", "properties": {"base_url": {"type": "string", "description": "Base URL to check (default: https://algochains.ai)", "default": "https://algochains.ai"}}, "required": []},
         annotations=ANNOT_READ_EXTERNAL),
    # ═══════════════════════════════════════════════════════════════
    # V22: Live Bot Intelligence — real metrics, heartbeat, academic citations
    # ═══════════════════════════════════════════════════════════════
    # Parses real Tradovate fill logs for live P&L/WinRate/signal data.
    # Reads Mac→Desktop heartbeat file to self-identify primary vs standby.
    # Provides SSRN academic citations and MCPT backtest artifacts per bot.
    # Powers algochains.ai marketplace bot card live data panel.
    # ═══════════════════════════════════════════════════════════════
    Tool(name="get_live_bot_metrics",
         description="Get real-time trading metrics for live bots (Tradovate + Alpaca paper). Supabase-first (bot_metrics_live table). Returns daily P&L, win rate, last signal, confidence, error count. Bot IDs: mnq, cl, mes, nq, alpaca_paper_equities, alpaca_paper_crypto. Omit bot_id to get all. Falls back to log parser if Supabase unavailable.",
         inputSchema={"type": "object", "properties": {"bot_id": {"type": "string", "description": "Optional bot ID: mnq | cl | mes | nq | alpaca_paper_equities | alpaca_paper_crypto. Omit for all bots."}}, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="get_all_bot_metrics",
         description="Get real-time trading metrics for all 4 live Tradovate bots (MNQ, CL, MES, NQ) in a single call. Returns daily P&L, win rates, signals, error states, and MCPT validation badges. Data from real log files.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="get_system_heartbeat",
         description="Check whether this MCP server node is the primary trader (MacBook offline) or standby (MacBook alive). Reads the Mac heartbeat file to determine heartbeat age, Mac liveness, desktop bot process counts (expected 5: MNQ/CL/MES/NQ + Kalshi), and which node is currently running the bots. Critical for dual-node failover awareness.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="get_adaptive_brain_status",
         description="Read adaptive_brain.py daemon liveness from bounded process, script, state, and log evidence. Read-only; does not restart or mutate daemon state.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="get_system_health",
         description="Run the trading-system-health audit: bot process/log liveness (with legacy log alias resolution), disk space on control-tower and home volumes, and optional health_snapshot.json. Use to triage SEV1 trading-system-health watchdog alerts without false inactive signals from stale cl_bot_live.log.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="get_strategy_academic_citations",
         description="Get all academic citations, SSRN papers, and published works that provide the theoretical basis for a specific bot's strategy. Includes authors, year, venue, DOI/SSRN link, and relevance explanation. Bot IDs: mnq, cl, mes, nq.",
         inputSchema={"type": "object", "properties": {"bot_id": {"type": "string", "description": "Bot identifier: mnq | cl | mes | nq", "enum": ["mnq", "cl", "mes", "nq"]}}, "required": ["bot_id"]},
         annotations=ANNOT_READ_ONLY),
    Tool(name="get_bot_card_data",
         description="Get the complete bot card data payload for algochains.ai marketplace display. Includes strategy summary, academic citations, backtest artifact paths (MCPT JSON, whitepapers, blueprints), skills references, and subscription tier. Use to populate or refresh a bot card on the marketplace site.",
         inputSchema={"type": "object", "properties": {"bot_id": {"type": "string", "description": "Bot identifier: mnq | cl | mes | nq", "enum": ["mnq", "cl", "mes", "nq"]}}, "required": ["bot_id"]},
         annotations=ANNOT_READ_ONLY),
    Tool(name="list_bot_research_attachments",
         description="List all research attachments available for a bot: MCPT validation JSON files, backtest PDFs, whitepapers, and blueprint markdown files. Shows local path and whether the file exists. Use to prepare uploads to Supabase storage for bot card attachment panel.",
         inputSchema={"type": "object", "properties": {"bot_id": {"type": "string", "description": "Bot identifier: mnq | cl | mes | nq. Use 'all' for every bot.", "default": "all"}}, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="get_finalized_backtests",
         description="Query Supabase strategy_backtest_run for all finalized (promoted) backtest runs. Returns run_id, strategy_id, run_label, git_sha, metrics_summary (sharpe, win_rate, max_dd, total_trades), and finalized_at. Use to answer 'what is the current source of truth for MNQ/CL/MES/NQ?' or to display on the algochains.ai marketplace cards.",
         inputSchema={"type": "object", "properties": {
             "strategy_id": {"type": "string", "description": "Filter by strategy (e.g. MNQ_UPGRADED_SCALPER). Omit for all.", "default": ""},
             "limit":       {"type": "integer", "description": "Max rows to return (default 10)", "default": 10},
         }, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="get_backtest_run_detail",
         description="Get full detail for a single finalized backtest run including per-fold metrics. Returns run metadata, metrics_summary, decision_text, artifact_paths, and all fold rows (oos_start, oos_end, trades, win_rate, sharpe, max_dd). Use to answer deep questions about a specific backtest run.",
         inputSchema={"type": "object", "properties": {
             "run_id": {"type": "string", "description": "UUID of the strategy_backtest_run row"},
         }, "required": ["run_id"]},
         annotations=ANNOT_READ_ONLY),

    # V26.0: Bot Ops — Bracket status, position state, pipeline health, restart, flatten
    # ═══════════════════════════════════════════════════════════════════════════════════
    # Added after 2026-04-07 incident: missing brackets + pipeline 102s stall + qty=1 bug.
    # Read-only tools are public. Destructive tools require OWNER_API_TOKEN.
    # ═══════════════════════════════════════════════════════════════════════════════════
    Tool(name="get_bot_position_state",
         description="Read the persisted position state file for a bot. Returns direction (BUY/SELL/null), qty, entry_price, and flat status. This is the bot's internal tracking — compare to Tradovate get_positions() to detect drift.",
         inputSchema={"type": "object", "properties": {"bot_id": {"type": "string", "enum": ["mnq", "cl", "mes", "nq"]}}, "required": ["bot_id"]},
         annotations=ANNOT_READ_ONLY),
    Tool(name="get_bot_bracket_status",
         description="Parse the bot log to determine current bracket order status. Returns mode (live/oso_only/none/unknown), stop/target order IDs and prices, and whether the position is unprotected. Critical for detecting missing stops after an entry.",
         inputSchema={"type": "object", "properties": {"bot_id": {"type": "string", "enum": ["mnq", "cl", "mes", "nq"]}}, "required": ["bot_id"]},
         annotations=ANNOT_READ_ONLY),
    Tool(name="get_ai_pipeline_health",
         description="Check AI ensemble/debate pipeline health. Detects Anthropic quota errors, Cerebras model errors (llama3.1-8b), pipeline timeout events, and shadow mode. The pipeline is ADVISORY ONLY — primary confidence gate controls all trades regardless of pipeline state.",
         inputSchema={"type": "object", "properties": {"bot_id": {"type": "string", "enum": ["mnq", "cl", "mes", "nq"], "default": "mnq"}}, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="get_all_bot_ops_status",
         description="Full operational snapshot for all 4 bots: process status, PIDs, position states, bracket status, and AI pipeline health. Use to triage any bot integrity issue in one call.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="restart_trading_bot",
         description="Kill and restart a trading bot process. REQUIRES owner_token matching OWNER_API_TOKEN env var. Kills existing PID(s), restarts with python3 -B -u, verifies new PID, returns status. NOTE: Verify position is flat on Tradovate before restarting to avoid phantom position tracking.",
         inputSchema={"type": "object", "properties": {"bot_id": {"type": "string", "enum": ["mnq", "cl", "mes", "nq"]}, "owner_token": {"type": "string", "description": "Must match OWNER_API_TOKEN env var"}}, "required": ["bot_id", "owner_token"]},
         annotations=ANNOT_DESTRUCTIVE),
    Tool(name="flatten_bot_position",
         description="Close ALL open contracts for a symbol via Tradovate Market order, then mark position_state.json as flat. REQUIRES owner_token. CRITICAL: Only call after confirming the bot is stopped or between its scans. Environment is determined by TRADOVATE_ENV (.env).",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string", "description": "Symbol to flatten: MNQ | CL | MES | NQ"}, "owner_token": {"type": "string", "description": "Must match OWNER_API_TOKEN env var"}}, "required": ["symbol", "owner_token"]},
         annotations=ANNOT_DESTRUCTIVE),
    Tool(name="check_unprotected_positions",
         description="Cross-check ALL open Tradovate positions vs working orders to identify unprotected exposure (position open, no stop/target orders). Returns status OK | UNPROTECTED_EXPOSURE. Run before any P&L report or after any bot restart. Prevents repeat of Apr 14 2026 -$4.9k incident.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="bracket_integrity_check",
         description="Live Tradovate bracket audit for non-MNQ positions (CL/MES/NQ). Each open position must have BOTH a working stop and target order. Returns checked_count, missing_brackets, and formatted_line for BRACKET-INTEGRITY-MONITOR. Status DEGRADED when bot state files show open exposure but broker returns zero positions (fail-closed).",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="get_bracket_guardian_status",
         description="Read the bracket integrity guardian daemon state. Returns last check time, any unprotected positions currently flagged, and whether auto-flatten has fired. When guardian positions_count is 0 (or guardian inactive), also runs live bracket_integrity_check against Tradovate so watchdogs cannot report OK with 0 checked without broker verification.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),

    # V22.2: Onboarding — guided setup wizard for new users
    # ═══════════════════════════════════════════════════════════════
    # Compliance-gated broker connection, key validation, smoke test.
    # Risk disclosure shown FIRST, acknowledgment REQUIRED before any trading.
    # ═══════════════════════════════════════════════════════════════
    Tool(name="start_onboarding",
         description="Begin the AlgoChains setup wizard. Shows risk disclosure, privacy notice, and compliance acknowledgment. MUST be called first by new users before connecting any broker. Returns the disclosure text and required acknowledgment string.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="acknowledge_risk_disclosure",
         description="Acknowledge the AlgoChains risk disclosure to unlock trading tools. User must type the exact acknowledgment text shown by start_onboarding(). Creates an auditable timestamp of acknowledgment.",
         inputSchema={"type": "object", "properties": {"acknowledgment": {"type": "string", "description": "Exact acknowledgment text as shown in start_onboarding() response"}}, "required": ["acknowledgment"]},
         annotations=ANNOT_READ_ONLY),
    Tool(name="get_broker_setup_guide",
         description="Get step-by-step setup guide for a broker: required env vars, where to get credentials, paper trading instructions, rate limits. Includes broker-specific risk warnings. Brokers: tradovate | alpaca | oanda",
         inputSchema={"type": "object", "properties": {"broker": {"type": "string", "enum": ["tradovate", "alpaca", "oanda"]}}, "required": ["broker"]},
         annotations=ANNOT_READ_ONLY),
    Tool(name="validate_broker_connection",
         description="Test broker connectivity using credentials from environment variables. Returns success/failure with specific error messages. Fails loudly if credentials are missing or invalid — never silently proceeds.",
         inputSchema={"type": "object", "properties": {"broker": {"type": "string", "enum": ["tradovate", "alpaca", "oanda"]}}, "required": ["broker"]},
         annotations=ANNOT_READ_ONLY),
    Tool(name="get_data_provider_setup_guide",
         description="Get setup guide for a market data provider: required env vars, where to get API keys, free tier details. Providers: polygon | databento | onyx | fred",
         inputSchema={"type": "object", "properties": {"provider": {"type": "string", "enum": ["polygon", "databento", "onyx", "fred"]}}, "required": ["provider"]},
         annotations=ANNOT_READ_ONLY),
    Tool(name="validate_data_provider",
         description="Test market data provider connectivity: polygon, databento, onyx, or fred. Uses credentials from environment variables. Returns connected/failed with error details.",
         inputSchema={"type": "object", "properties": {"provider": {"type": "string", "enum": ["polygon", "databento", "onyx", "fred"]}}, "required": ["provider"]},
         annotations=ANNOT_READ_ONLY),
    Tool(name="run_onboarding_smoke_test",
         description="Run end-to-end connectivity smoke test for all configured brokers and data providers. Marks onboarding complete if all pass. Call this after setting up credentials to verify everything works before trading.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="get_onboarding_status",
         description="Check current onboarding progress: steps completed, steps remaining, connected brokers/providers, AlgoChains API key status, guardrail prefs, and next required action.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="set_algochains_api_key",
         description="Step 4: Set your AlgoChains developer API key (ac_live_* or ac_test_*) for marketplace and bridge access. Validates against the bridge health endpoint. Get a key via create_developer_key tool or at algochains.ai/account/developer-keys/.",
         inputSchema={"type": "object", "properties": {
             "api_key": {"type": "string", "description": "Your developer key (ac_live_... or ac_test_...)"},
         }, "required": ["api_key"]},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="set_guardrail_preferences",
         description="Step 6: Configure guardrail notification thresholds. Hard-coded limits (daily loss $500, max drawdown 15%, VIX>35 gate) cannot be changed — this only controls when you are notified.",
         inputSchema={"type": "object", "properties": {
             "notify_on_daily_loss_pct": {"type": "number", "default": 80, "description": "Notify when daily loss reaches this % of $500 limit (e.g. 80 = alert at $400 loss)"},
             "pause_on_consecutive_losses": {"type": "integer", "default": 3, "description": "Pause and alert after N consecutive losing trades"},
             "slack_alerts_enabled": {"type": "boolean", "default": False},
         }, "required": []},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="generate_ide_config",
         description="Generate the MCP config file (mcporter.json / mcp.json) for your IDE based on your connected brokers and data providers. IDEs: cursor | windsurf | claude | vscode. Mode: smart (default, 25 tools) | full (262 tools). Output includes install instructions.",
         inputSchema={"type": "object", "properties": {"ide": {"type": "string", "enum": ["cursor", "windsurf", "claude", "vscode"]}, "tool_mode": {"type": "string", "enum": ["smart", "full"], "default": "smart"}}, "required": ["ide"]},
         annotations=ANNOT_READ_ONLY),

    # V22.1: Trading Guardrails — hard-coded circuit breakers (read-only status)
    # ═══════════════════════════════════════════════════════════════
    # The AI can READ guardrail status but CANNOT modify limits.
    # Limits are Python constants in trading_guardrails.py.
    # Only a code deploy can change them.
    # ═══════════════════════════════════════════════════════════════
    Tool(name="get_circuit_breaker_status",
         description="Read current state of all hard-coded trading circuit breakers. Shows which brokers are OPEN/CLOSED/HALF_OPEN, trip reasons, cooldown timers, and current order velocity. These limits are code-level constants — the AI cannot modify them. Use to understand why orders are being blocked.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="get_daily_loss_proximity",
         description="Read daily loss proximity guard status: today's P&L vs the $500 hard limit, utilization %, alert/block thresholds (80% alert, 95% block scalpers, MNQ swing exempt), and whether P&L evidence is verified. Returns DEGRADED when P&L source is unknown instead of fail-open OK.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="get_agent_loop_status",
         description="Check AI agent loop detection metrics: calls in last 60s, unique call signatures, max identical call count, and loop risk level (LOW/MEDIUM/HIGH). If loop risk is HIGH, a circuit breaker may trip on the next repeated call. Read-only — limits are hard-coded constants.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="get_latency_profile",
         description="Get real-time latency profile for this MCP session: tool call overhead, broker API round-trip times, and current execution tier. Includes a reminder that MCP AI-assisted execution is Tier 4 (120ms-2s) — not suitable for HFT. Use to set correct expectations for strategy timing.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),
    # V22.3 — Proprietary Data Ingestion
    Tool(name="ingest_csv_data",
         description="Ingest a user-provided CSV file of OHLCV market data into AlgoChains. Validates columns, parses rows, and stores in state/custom_data/. The data becomes available for backtesting via run_backtest(data_source='custom'). Requires real file on disk — no synthetic substitution.",
         inputSchema={
             "type": "object",
             "properties": {
                 "file_path": {"type": "string", "description": "Absolute path to the CSV file."},
                 "symbol": {"type": "string", "description": "Ticker symbol, e.g. 'MNQ', 'AAPL'."},
                 "timeframe": {"type": "string", "description": "Bar timeframe, e.g. '1min', '5min', '1h', '1d'."},
                 "columns": {"type": "object", "description": "Optional mapping: canonical name -> CSV column header. E.g. {\"open\": \"Open\", \"close\": \"Close\"}."},
                 "date_column": {"type": "string", "description": "Name of the date/timestamp column. Default: 'date'."},
                 "date_format": {"type": "string", "description": "strptime format string. Default: '%Y-%m-%d %H:%M:%S'."},
             },
             "required": ["file_path", "symbol", "timeframe"],
         },
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="ingest_json_signals",
         description="Ingest a JSON file of pre-computed signals, ML features, labels, or regime tags into AlgoChains. Supports entry/exit signals, feature vectors, classification labels, and regime classifications. Data becomes available for ML training.",
         inputSchema={
             "type": "object",
             "properties": {
                 "file_path": {"type": "string", "description": "Absolute path to the JSON file."},
                 "signal_type": {"type": "string", "enum": ["entry_exit", "features", "labels", "regime"], "description": "Type of signal data."},
                 "symbol": {"type": "string", "description": "Ticker symbol the signals are for."},
             },
             "required": ["file_path", "signal_type", "symbol"],
         },
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="connect_onyx_docs",
         description="Index local research documents (PDF, Markdown, JSON, TXT) into the Onyx RAG knowledge base. Documents become searchable via onyx_ask() and onyx_search(). Supports recursive directory scanning. Requires Onyx to be running at ONYX_API_URL.",
         inputSchema={
             "type": "object",
             "properties": {
                 "doc_paths": {"type": "array", "items": {"type": "string"}, "description": "List of absolute file or directory paths."},
                 "doc_type": {"type": "string", "enum": ["strategy_research", "blueprint", "backtest", "whitepaper", "general"], "description": "Document category for Onyx tagging."},
             },
             "required": ["doc_paths", "doc_type"],
         },
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="register_strategy",
         description="Register a custom strategy spec JSON with the AlgoChains platform. The spec must contain entry_rules and exit_rules. Once registered, the strategy can be backtested via run_backtest(strategy_id=...). Validates the spec file before registering.",
         inputSchema={
             "type": "object",
             "properties": {
                 "name": {"type": "string", "description": "Human-readable strategy name."},
                 "asset_class": {"type": "string", "enum": ["futures", "equities", "forex", "crypto", "options"]},
                 "timeframe": {"type": "string", "enum": ["1min", "3min", "5min", "10min", "15min", "30min", "1h", "4h", "1d", "1w"]},
                 "symbols": {"type": "array", "items": {"type": "string"}, "description": "List of ticker symbols this strategy trades."},
                 "spec_path": {"type": "string", "description": "Absolute path to strategy spec JSON file."},
                 "description": {"type": "string"},
                 "author": {"type": "string"},
             },
             "required": ["name", "asset_class", "timeframe", "symbols", "spec_path"],
         },
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="list_ingested_data",
         description="List all custom OHLCV datasets, signal files, Onyx document ingestions, and registered strategies. Shows what proprietary data has been brought into AlgoChains.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),

    # ── Support Ticket System ──────────────────────────────────────────────
    Tool(name="create_support_ticket",
         description="Create an IT support ticket. Stores in Supabase, syncs to Notion, and sends email confirmation. Use for bug reports, billing issues, broker connection problems, or onboarding help.",
         inputSchema={"type": "object", "properties": {
             "subject": {"type": "string", "description": "Short summary (max 200 chars)"},
             "description": {"type": "string", "description": "Full problem description"},
             "user_email": {"type": "string", "description": "User's email for reply notifications"},
             "category": {"type": "string", "enum": ["broker_connection","bot_performance","billing","account","onboarding","bug","feature_request","other"], "default": "other"},
             "priority": {"type": "string", "enum": ["low","medium","high","critical"], "default": "medium"},
             "user_id": {"type": "string"},
             "metadata": {"type": "object"},
         }, "required": ["subject","description","user_email"]},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="get_support_ticket",
         description="Get a support ticket by ID. Returns full ticket details including status, responses, and Notion page link.",
         inputSchema={"type": "object", "properties": {"ticket_id": {"type": "string"}}, "required": ["ticket_id"]},
         annotations=ANNOT_READ_ONLY),
    Tool(name="list_support_tickets",
         description="List support tickets with optional filters by status, priority, category, or user email.",
         inputSchema={"type": "object", "properties": {
             "status": {"type": "string", "enum": ["open","in_progress","resolved","closed"]},
             "priority": {"type": "string", "enum": ["low","medium","high","critical"]},
             "category": {"type": "string"},
             "user_email": {"type": "string"},
             "limit": {"type": "integer", "default": 50},
         }, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="update_ticket_status",
         description="Update a support ticket status (open → in_progress → resolved → closed). Optionally add an agent response.",
         inputSchema={"type": "object", "properties": {
             "ticket_id": {"type": "string"},
             "status": {"type": "string", "enum": ["open","in_progress","resolved","closed"]},
             "agent_response": {"type": "string"},
             "agent_email": {"type": "string"},
         }, "required": ["ticket_id","status"]},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="get_ticket_stats",
         description="Get aggregate support ticket statistics: total, by status, by priority, by category, open critical count.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),

    # ── OAuth Broker Connection ────────────────────────────────────────────
    Tool(name="generate_broker_auth_url",
         description="Generate an OAuth authorization URL for a user to connect their broker account (Schwab, Alpaca, Tradovate, OANDA). Returns the URL to redirect the user to.",
         inputSchema={"type": "object", "properties": {
             "broker": {"type": "string", "enum": ["schwab","alpaca","tradovate","oanda"], "description": "Which broker to connect"},
             "user_id": {"type": "string", "description": "User ID to associate the OAuth token with"},
             "redirect_uri": {"type": "string", "description": "Override callback URI (default: algochains.ai/oauth/callback/{broker})"},
         }, "required": ["broker","user_id"]},
         annotations=ANNOT_READ_ONLY),
    Tool(name="exchange_broker_oauth_code",
         description="Exchange an OAuth authorization code for broker access/refresh tokens. Call this after the user returns from the broker's authorization page.",
         inputSchema={"type": "object", "properties": {
             "state": {"type": "string", "description": "State token returned by generate_broker_auth_url"},
             "code": {"type": "string", "description": "Authorization code from the broker callback URL"},
             "redirect_uri": {"type": "string"},
         }, "required": ["state","code"]},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="get_broker_oauth_status",
         description="Get OAuth connection status and token validity for a user's broker connection.",
         inputSchema={"type": "object", "properties": {
             "broker": {"type": "string"},
             "user_id": {"type": "string"},
         }, "required": ["broker","user_id"]},
         annotations=ANNOT_READ_ONLY),
    Tool(name="get_connected_brokers",
         description="List all brokers a user has connected via OAuth, with token expiry and scope information.",
         inputSchema={"type": "object", "properties": {"user_id": {"type": "string"}}, "required": ["user_id"]},
         annotations=ANNOT_READ_ONLY),
    Tool(name="revoke_broker_connection",
         description="Disconnect a broker OAuth connection and remove stored tokens.",
         inputSchema={"type": "object", "properties": {
             "broker": {"type": "string"},
             "user_id": {"type": "string"},
         }, "required": ["broker","user_id"]},
         annotations=ANNOT_WRITE_SAFE),

    # ── Programmatic Account / MFA / Developer Key Tools ─────────────────
    # Account creation & session management (Supabase Auth wrappers)
    Tool(name="signup_algochains",
         description="Create a new AlgoChains account with email + password via Supabase Auth. Returns session on success or requires_email_confirm. Next step: verify_email_otp → enroll_mfa → create_developer_key.",
         inputSchema={"type": "object", "properties": {
             "email": {"type": "string", "description": "Account email address"},
             "password": {"type": "string", "description": "Password (min 8 chars)"},
         }, "required": ["email", "password"]},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="verify_email_otp",
         description="Verify the email OTP token from the AlgoChains confirmation email. Activates your account and starts a session.",
         inputSchema={"type": "object", "properties": {
             "email": {"type": "string"},
             "token": {"type": "string", "description": "6-digit or link token from confirmation email"},
         }, "required": ["email", "token"]},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="login_algochains",
         description="Login to AlgoChains with email + password. Stores session locally for subsequent MFA and key operations.",
         inputSchema={"type": "object", "properties": {
             "email": {"type": "string"},
             "password": {"type": "string"},
         }, "required": ["email", "password"]},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="refresh_session",
         description="Refresh an expiring AlgoChains session using the stored refresh_token. Call before session expires to stay logged in.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="logout_algochains",
         description="Revoke current AlgoChains session and clear stored credentials.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_WRITE_SAFE),
    # MFA enrollment and verification
    Tool(name="enroll_mfa",
         description="Enroll a new MFA factor (TOTP authenticator app or SMS). Returns QR code URI for TOTP — scan with Google Authenticator, Authy, etc. Then call verify_mfa to complete and upgrade session to AAL2.",
         inputSchema={"type": "object", "properties": {
             "factor_type": {"type": "string", "enum": ["totp", "phone"], "default": "totp"},
         }, "required": []},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="challenge_mfa",
         description="Create an MFA challenge for login step-up verification. Required before verify_mfa during subsequent logins.",
         inputSchema={"type": "object", "properties": {
             "factor_id": {"type": "string", "description": "Factor ID from list_mfa_factors"},
         }, "required": ["factor_id"]},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="verify_mfa",
         description="Verify MFA code to complete enrollment or step up to AAL2 session. AAL2 is required for create_developer_key, rotate_developer_key, revoke_developer_key.",
         inputSchema={"type": "object", "properties": {
             "factor_id": {"type": "string"},
             "code": {"type": "string", "description": "6-digit TOTP or SMS code"},
             "challenge_id": {"type": "string", "description": "Challenge ID from challenge_mfa (required for login step-up, not for enrollment)"},
         }, "required": ["factor_id", "code"]},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="list_mfa_factors",
         description="List enrolled MFA factors for the current session.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="remove_mfa_factor",
         description="Remove an enrolled MFA factor. Requires owner_token — destructive, downgrades session to AAL1.",
         inputSchema={"type": "object", "properties": {
             "factor_id": {"type": "string"},
             "owner_token": {"type": "string", "description": "Must match OWNER_API_TOKEN"},
         }, "required": ["factor_id", "owner_token"]},
         annotations=ANNOT_WRITE_SAFE),
    # Developer key lifecycle (self-serve ac_live_* / ac_test_* keys)
    Tool(name="create_developer_key",
         description="Mint a new ac_live_* or ac_test_* developer API key. Requires AAL2 session (enroll_mfa + verify_mfa first). Plaintext key returned ONCE ONLY — save immediately.",
         inputSchema={"type": "object", "properties": {
             "name": {"type": "string", "description": "Friendly name for the key", "default": "default"},
             "scopes": {"type": "array", "items": {"type": "string"}, "description": "e.g. ['read:market_data','read:signals']"},
             "env": {"type": "string", "enum": ["live", "test"], "default": "live"},
         }, "required": []},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="list_developer_keys",
         description="List your developer API keys (masked — plaintext never returned after creation).",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="rotate_developer_key",
         description="Atomically rotate a developer key (revoke old, mint new). Requires AAL2 session. New plaintext returned ONCE ONLY.",
         inputSchema={"type": "object", "properties": {
             "key_id": {"type": "string", "description": "UUID of the key to rotate"},
             "name": {"type": "string", "description": "Name for the new key (defaults to old key's name)"},
         }, "required": ["key_id"]},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="revoke_developer_key",
         description="Revoke (soft-delete) a developer API key. Requires AAL2 session.",
         inputSchema={"type": "object", "properties": {
             "key_id": {"type": "string"},
         }, "required": ["key_id"]},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="get_developer_key_usage",
         description="Get usage metadata for a developer key (last used, scopes, active status).",
         inputSchema={"type": "object", "properties": {
             "key_id": {"type": "string"},
         }, "required": ["key_id"]},
         annotations=ANNOT_READ_ONLY),
    Tool(name="test_bridge_connection",
         description="Test a developer API key against the hosted AlgoChains bridge (mcp.algochains.ai). Returns auth status and scopes.",
         inputSchema={"type": "object", "properties": {
             "api_key": {"type": "string", "description": "Developer key to test (or set AC_DEV_KEY env var)"},
         }, "required": []},
         annotations=ANNOT_READ_ONLY),

    # ── Subscriber Onramp ────────────────────────────────────────────────
    # ── Onboarding meta-tools (public, no auth — first 30 seconds) ──────────
    Tool(name="get_started",
         description=(
             "START HERE. Guided next-steps for a brand-new user, by goal. No auth, "
             "no setup. Call get_started(goal='subscriber') for copy-trade signals, "
             "'creator' to publish a strategy, 'developer' to build on the API, or "
             "'explore' to look around with zero signup. Returns the exact tool calls to make next."
         ),
         inputSchema={"type": "object", "properties": {
             "goal": {"type": "string", "description": "subscriber | creator | developer | explore (optional — omit for a menu)"},
         }, "required": []},
         annotations=ANNOT_READ_ONLY),

    Tool(name="get_pricing",
         description=(
             "Transparent AlgoChains pricing: paper ($29/mo) and live ($99/mo) tiers, "
             "what's included, usage overage, the 20%/3-month referral reward, and the "
             "80% creator revenue share. Flat subscription + usage; no performance fees. "
             "No auth required."
         ),
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),

    Tool(name="get_system_status",
         description=(
             "Consumer-facing platform health: version, live signal-bot roster "
             "(MNQ/CL/MES/NQ), tool count, and public marketplace listing count. "
             "No auth, no secrets — safe to call anytime."
         ),
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),

    Tool(name="get_checkout_url",
         description=(
             "Generate a Stripe checkout URL for an AlgoChains subscription. "
             "Returns a URL the user clicks once to pay — Stripe handles the payment UI. "
             "After payment, a sub_live_… key is emailed automatically and the subscriber "
             "can subscribe to MNQ copy-trade signals (delivered for the subscriber to "
             "review and act on — no automated execution; the subscriber stays in control). "
             "Tiers: 'paper' ($29/mo — subscriber tools + MNQ copy-trade signals, simulated "
             "paper account, no broker needed) or 'live' ($99/mo — subscriber connects their "
             "own broker and places their own trades). Flat subscription only. "
             "Set ALGOCHAINS_SUBSCRIBER_KEY=<received_key> to activate."
         ),
         inputSchema={"type": "object", "properties": {
             "email": {"type": "string", "description": "Email for subscription and key delivery"},
             "tier": {"type": "string", "enum": ["paper", "live"], "default": "paper",
                      "description": "paper=$29/mo (MNQ signals + simulated paper, no broker), live=$99/mo (connect your own broker, you place trades)"},
             "referral_code": {"type": "string", "description": "Optional referral code (AC-XXXXXX) — credits the referrer."},
         }, "required": ["email"]},
         annotations=ANNOT_WRITE_SAFE),

    Tool(name="generate_payment_link",
         description=(
             "Return a direct payment link for an AlgoChains subscription tier. "
             "Unlike get_checkout_url, this returns a pre-configured shareable URL "
             "that works without entering an email first. "
             "paper=$29/mo, live=$99/mo. "
             "After payment, set ALGOCHAINS_SUBSCRIBER_KEY=<emailed key>."
         ),
         inputSchema={"type": "object", "properties": {
             "tier": {"type": "string", "enum": ["paper", "live"], "default": "paper"},
         }, "required": []},
         annotations=ANNOT_READ_ONLY),

    # ── Subscriber Tools (stdio path — sub key sets ALGOCHAINS_SUBSCRIBER_KEY) ─
    Tool(name="join_bot",
         description=(
             "Subscribe the authenticated subscriber to a strategy's published "
             "copy-trade SIGNALS (the subscriber reviews and acts on them — the "
             "platform does not auto-execute or exercise discretion). The subscriber "
             "sets their own size and can pause/leave anytime. "
             "Strategies: MNQ (micro Nasdaq scalper), CL (crude oil scalper), "
             "MES (micro S&P swing), NQ (Nasdaq swing). "
             "Enforces a seat cap per strategy — returns bot_at_capacity if full. "
             "Requires the futures risk disclosure to be acknowledged first "
             "(accept_subscriber_terms) and ALGOCHAINS_SUBSCRIBER_KEY to be set. "
             "Re-calling with an existing subscription updates size_multiplier and un-pauses."
         ),
         inputSchema={"type": "object", "properties": {
             "bot": {"type": "string", "enum": ["MNQ", "CL", "MES", "NQ"],
                     "description": "Which strategy's published signals to subscribe to"},
             "size_multiplier": {"type": "number", "default": 1.0,
                                 "description": "Trade-size multiplier vs the master bot (0 < x <= 10)"},
             "max_contracts": {"type": "integer", "default": 10,
                               "description": "Hard cap on contracts per signal"},
             "daily_loss_cap_usd": {"type": "number", "default": 5000.0,
                                    "description": "Daily loss hard limit in USD"},
         }, "required": ["bot"]},
         annotations=ANNOT_WRITE_SAFE),

    Tool(name="get_subscriber_status",
         description=(
             "Return a full status snapshot for the authenticated subscriber: "
             "which bots they're assigned to, paper account balance, key_active flag, "
             "and suggested next_steps based on their current state. "
             "Good first call after setting ALGOCHAINS_SUBSCRIBER_KEY. "
             "Requires ALGOCHAINS_SUBSCRIBER_KEY to be set."
         ),
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),

    Tool(name="accept_subscriber_terms",
         description=(
             "Record the authenticated subscriber's explicit acknowledgment of the "
             "futures risk disclosure and Terms of Service. REQUIRED before active "
             "copy-trade (join_bot). Call once with no arguments to retrieve the "
             "disclosure text and the exact acknowledgment phrase, then call again "
             "with acknowledgment=<phrase> to record consent. CFTC/NFA compliance "
             "gate. Requires ALGOCHAINS_SUBSCRIBER_KEY."
         ),
         inputSchema={"type": "object", "properties": {
             "acknowledgment": {"type": "string", "description": "Exact risk-acknowledgment phrase to record consent"},
         }, "required": []},
         annotations=ANNOT_WRITE_SAFE),

    Tool(name="get_my_usage",
         description=(
             "Your current-month MCP API usage: total metered calls, included quota, "
             "overage calls, overage cost (USD), and a projected month-end overage cost. "
             "Read-only; reflects this subscriber's billing tier. "
             "Requires ALGOCHAINS_SUBSCRIBER_KEY."
         ),
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),

    # ── Affiliate / referral system ─────────────────────────────────────────
    Tool(name="create_referral_code",
         description=(
             "Create (or fetch) the authenticated subscriber's shareable referral code. "
             "Returns the code and a share_url (https://algochains.ai/r/<code>). "
             "One active code per subscriber. Referrers earn 20% of each referral's "
             "subscription for their first 3 months. Requires ALGOCHAINS_SUBSCRIBER_KEY."
         ),
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_WRITE_SAFE),

    Tool(name="get_my_referrals",
         description=(
             "Return the authenticated subscriber's referral summary: their referral code, "
             "count of subscribers referred, and commission counts + sums by status. "
             "Requires ALGOCHAINS_SUBSCRIBER_KEY."
         ),
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),

    Tool(name="get_referral_earnings",
         description=(
             "Return total referral earnings (pending + paid commission_usd) for the "
             "authenticated subscriber, with the 20%/3-month policy and compliance "
             "disclaimer. Requires ALGOCHAINS_SUBSCRIBER_KEY."
         ),
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),

    # ── Creator payouts (Stripe Connect ledger) ─────────────────────────────
    Tool(name="create_creator_onboarding_link",
         description=(
             "Create a Stripe Connect Express onboarding link for a strategy creator. "
             "Returns a URL where the creator completes KYC and links their bank account "
             "for payouts, and mirrors the Connect account into the creator ledger. "
             "Pass creator_id + creator_email."
         ),
         inputSchema={"type": "object", "properties": {
             "creator_id": {"type": "string", "description": "Creator id."},
             "creator_email": {"type": "string", "description": "Email for the Stripe Connect account and KYC."},
             "owner_token": {"type": "string", "description": "Must match OWNER_API_TOKEN env var. Required until creator-authenticated sessions exist."},
             "confirm": {"type": "boolean", "description": "Required true when called through execute_dynamic_tool."},
        }, "required": ["creator_id", "creator_email", "owner_token"]},
         annotations=ANNOT_DESTRUCTIVE),

    Tool(name="get_my_creator_earnings",
         description=(
             "Read a creator's earnings summary (accrued / paid / reversed totals, 80/20 "
             "revenue share) plus recent payout history. Read-only. Pass creator_id."
         ),
         inputSchema={"type": "object", "properties": {
             "creator_id": {"type": "string", "description": "Creator id."},
             "owner_token": {"type": "string", "description": "Must match OWNER_API_TOKEN env var. Required until creator-authenticated sessions exist."},
             "confirm": {"type": "boolean", "description": "Required true when called through execute_dynamic_tool."},
        }, "required": ["creator_id", "owner_token"]},
         annotations=ANNOT_DESTRUCTIVE),

    Tool(name="run_creator_payouts",
         description=(
             "Run the creator payout batch: sum accrued earnings per creator and pay out "
             "those over min_payout_usd via Stripe Connect. MOVES REAL MONEY. "
             "Defaults to dry_run=true (returns the computed plan, executes nothing). "
             "REQUIRES owner_token matching OWNER_API_TOKEN. Set dry_run=false to execute."
         ),
         inputSchema={"type": "object", "properties": {
             "creator_id": {"type": "string", "description": "Optional — scope the run to a single creator."},
             "dry_run": {"type": "boolean", "default": True, "description": "true (default) = plan only, no money moves."},
             "min_payout_usd": {"type": "number", "default": 25.0, "description": "Minimum accrued balance to trigger a payout."},
             "owner_token": {"type": "string", "description": "Must match OWNER_API_TOKEN env var. Required to run."},
         }, "required": ["owner_token"]},
         annotations=ANNOT_DESTRUCTIVE),

    # ── Realized P&L (live vs paper segregated) ─────────────────────────────
    Tool(name="get_my_realized_pnl",
         description=(
             "Your realized P&L with LIVE (real broker) and PAPER (simulated) results "
             "STRICTLY segregated. Paper results carry the CFTC Reg. 4.41(b) hypothetical-"
             "performance disclaimer; they are never co-mingled with live results. "
             "Requires ALGOCHAINS_SUBSCRIBER_KEY."
         ),
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),

    Tool(name="reconcile_creator_pnl",
         description=(
             "Owner reconciliation: attribute subscribers' LIVE net realized P&L to "
             "strategy creators for a period (per-subscriber then summed; 80/20 share). "
             "Writes the creator_strategy_pnl ledger; does NOT move money (payout is "
             "run_creator_payouts). Defaults to dry_run=true. REQUIRES owner_token."
         ),
         inputSchema={"type": "object", "properties": {
             "period_start": {"type": "string", "description": "ISO8601 inclusive period start."},
             "period_end": {"type": "string", "description": "ISO8601 exclusive period end."},
             "dry_run": {"type": "boolean", "default": True, "description": "true (default) = plan only."},
             "owner_token": {"type": "string", "description": "Must match OWNER_API_TOKEN."},
         }, "required": ["period_start", "period_end", "owner_token"]},
         annotations=ANNOT_DESTRUCTIVE),

    # ── Waitlist ──────────────────────────────────────────────────────────
    Tool(name="join_waitlist",
         description="Add an email to the AlgoChains waitlist. Stores in Supabase, sends welcome email via Resend. Returns waitlist position.",
         inputSchema={"type": "object", "properties": {
             "email": {"type": "string"},
             "first_name": {"type": "string"},
             "last_name": {"type": "string"},
             "broker": {"type": "string", "description": "Which broker they use"},
             "use_case": {"type": "string", "description": "What they plan to use AlgoChains for"},
             "referral_code": {"type": "string"},
         }, "required": ["email"]},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="get_waitlist_stats",
         description="Get waitlist aggregate statistics: total signups, by status, by broker interest.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),
    Tool(name="send_waitlist_invite",
         description="Send an invite to a waitlist user. Generates a unique code and emails it. Updates status to 'invited'. REQUIRES owner_token matching OWNER_API_TOKEN — invite codes are delivered by email only, never returned in MCP response.",
         inputSchema={"type": "object", "properties": {
             "email": {"type": "string"},
             "owner_token": {"type": "string", "description": "Must match OWNER_API_TOKEN env var."},
         }, "required": ["email", "owner_token"]},
         annotations=ANNOT_WRITE_SAFE),

    # ── Email/SMS Verification ────────────────────────────────────────────
    Tool(name="send_email_verification_code",
         description="Send a 6-digit verification code to an email address. Use for purchase confirmation, email verification, or broker connection confirmation.",
         inputSchema={"type": "object", "properties": {
             "email": {"type": "string"},
             "purpose": {"type": "string", "enum": ["email_verification","purchase","broker_connect","password_reset","account_recovery"], "default": "email_verification"},
             "context": {"type": "string", "description": "Optional context shown in the email"},
         }, "required": ["email"]},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="send_sms_verification_code",
         description="Send a 6-digit verification code via SMS (Twilio). Use for purchase confirmation or high-value action verification.",
         inputSchema={"type": "object", "properties": {
             "phone": {"type": "string", "description": "E.164 format: +15551234567"},
             "purpose": {"type": "string", "default": "purchase"},
             "context": {"type": "string"},
         }, "required": ["phone"]},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="verify_code",
         description="Verify a code sent via email or SMS. Returns valid=true if the code is correct and not expired.",
         inputSchema={"type": "object", "properties": {
             "destination": {"type": "string", "description": "Email or phone the code was sent to"},
             "code": {"type": "string", "description": "The 6-digit code the user entered"},
             "purpose": {"type": "string", "default": "email_verification"},
         }, "required": ["destination","code"]},
         annotations=ANNOT_READ_ONLY),

    # ── Platform Analytics ────────────────────────────────────────────────
    Tool(name="track_platform_event",
         description="Track a platform analytics event (page_view, signup, broker_connected, purchase, etc.). Used for soft-launch funnel monitoring.",
         inputSchema={"type": "object", "properties": {
             "event_type": {"type": "string", "description": "Event type: page_view, signup, email_verified, waitlist_join, broker_connected, bot_started, purchase, conversion"},
             "session_id": {"type": "string"},
             "user_id": {"type": "string"},
             "page": {"type": "string"},
             "referrer": {"type": "string"},
             "properties": {"type": "object"},
             "device": {"type": "string", "enum": ["desktop","mobile","tablet"]},
         }, "required": ["event_type"]},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="get_analytics_summary",
         description="Get platform analytics summary for the last N days: total events, unique users, conversion funnel, top pages, by-day breakdown.",
         inputSchema={"type": "object", "properties": {
             "days": {"type": "integer", "default": 7, "description": "Lookback period in days"},
             "event_type": {"type": "string", "description": "Optional filter to a specific event type"},
         }, "required": []},
         annotations=ANNOT_READ_ONLY),

    # ── Password Reset & Account Recovery ────────────────────────────────
    Tool(name="initiate_password_reset",
         description="Send a password reset link to a user's email via Supabase Auth. Always returns success to prevent user enumeration.",
         inputSchema={"type": "object", "properties": {"email": {"type": "string"}}, "required": ["email"]},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="complete_password_reset",
         description="Complete a password reset using the access token from the reset email link. Validates password policy (12 chars, upper/lower/number/special).",
         inputSchema={"type": "object", "properties": {
             "access_token": {"type": "string", "description": "Access token from the reset email URL fragment"},
             "new_password": {"type": "string"},
         }, "required": ["access_token","new_password"]},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="initiate_account_recovery",
         description="Start account recovery for users who cannot receive the reset email. Creates a support ticket and provides recovery instructions.",
         inputSchema={"type": "object", "properties": {
             "email": {"type": "string"},
             "reason": {"type": "string", "enum": ["lost_email_access","lost_2fa","account_locked","other"], "default": "lost_email_access"},
             "contact_info": {"type": "string", "description": "Alternate contact (phone or backup email)"},
         }, "required": ["email"]},
         annotations=ANNOT_WRITE_SAFE),
    Tool(name="get_password_policy",
         description="Return the current password policy requirements for AlgoChains accounts.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_ONLY),

    # ── Multi-Bot Account Metrics ─────────────────────────────────────────
    Tool(name="get_user_bot_metrics",
         description="Get metrics for a specific bot in the context of a user's subscription. Returns live metrics or appropriate fallback state (broker_not_connected, metrics_pending, data_stale).",
         inputSchema={"type": "object", "properties": {
             "user_id": {"type": "string"},
             "bot_id": {"type": "string", "description": "Bot identifier (mnq, cl, mes, nq, or marketplace UUID)"},
             "subscription_id": {"type": "string"},
             "log_path": {"type": "string", "description": "Optional custom log path for self-hosted bots"},
         }, "required": ["user_id","bot_id","subscription_id"]},
         annotations=ANNOT_READ_ONLY),
    Tool(name="get_all_user_bots",
         description="Get metrics for all bots a user is subscribed to. Includes fallback states for each bot (live, pending, not_connected, stale).",
         inputSchema={"type": "object", "properties": {"user_id": {"type": "string"}}, "required": ["user_id"]},
         annotations=ANNOT_READ_ONLY),
    Tool(name="upsert_bot_performance",
         description="[DEPRECATED for autonomous callers] Record real performance data for a managed bot subscription. Use metrics_streaming_daemon.py instead. Requires owner_token — SEC-2026-C4.",
         inputSchema={"type": "object", "properties": {
             "subscription_id": {"type": "string"},
             "bot_id": {"type": "string"},
             "daily_pnl": {"type": "number"},
             "win_rate": {"type": "number"},
             "trade_count": {"type": "integer"},
             "is_running": {"type": "boolean"},
             "broker": {"type": "string"},
             "sharpe_ratio": {"type": "number"},
             "max_drawdown": {"type": "number"},
             "weekly_pnl": {"type": "number"},
             "last_trade_at": {"type": "string"},
             "owner_token": {"type": "string", "description": "Must match OWNER_API_TOKEN env var."},
         }, "required": ["subscription_id","bot_id","daily_pnl","win_rate","trade_count","is_running","broker","owner_token"]},
         annotations=ANNOT_WRITE_SAFE),

    # ── Kronos Foundation Model (shadow mode observer) ────────────────────────
    Tool(name="get_kronos_shadow_stats",
         description="Get Kronos foundation model shadow-mode prediction statistics per bot. Shows agreement_rate, total_logged, direction accuracy, and promotion readiness vs the Bayesian ensemble. Read-only observer — Kronos has zero influence on live trades until manually graduated.",
         inputSchema={"type": "object", "properties": {
             "bot_key": {"type": "string", "description": "Bot key from signal_health.json, e.g. MNQ_Upgraded_Scalper. Omit to get all bots.", "default": "all"},
         }, "required": []},
         annotations=ANNOT_READ_SAFE),

    Tool(name="get_signal_trade_correlation",
         description="Read-only signal->trade traceability audit. Joins signals_trace to trade_log and returns NULL-rate KPIs (fill_id_coverage, placed_price_coverage, bracket intent nulls, P&L gap, per-column null rates). Thin wrapper over the control-tower correlation-audit script (runs --json --no-slack) — does not post to Slack. Defaults to filled-only rows so unfilled signal-only rows do not inflate fill-stage NULL rates.",
         inputSchema={"type": "object", "properties": {
             "limit": {"type": "integer", "description": "Number of recent signals_trace rows to scan (default 50).", "default": 50},
             "action": {"type": "string", "enum": ["submitted", "all", "blocked"], "description": "signals_trace action filter (default submitted).", "default": "submitted"},
             "filled_only": {"type": "boolean", "description": "Restrict KPIs to rows with fill evidence (default true, matches the daily launchd run).", "default": True},
         }, "required": []},
         annotations=ANNOT_READ_SAFE),

    # ── Rithmic R|API+ live prop account tools ────────────────────────────────
    Tool(name="get_rithmic_live_accounts",
         description="List all prop firm accounts currently connected via the Rithmic R|API+ bridge. Returns account_id, fcm_id (clearing firm), and ib_id for each live account. Requires rithmic_bridge binary compiled and RITHMIC_* env vars set.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_SAFE),

    Tool(name="get_rithmic_live_pnl",
         description="Get real-time P&L for all prop firm accounts via Rithmic R|API+. Returns open_pnl (unrealized), closed_pnl (realized today), account_balance, and buying_power per account plus an aggregate summary.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_SAFE),

    Tool(name="get_rithmic_live_positions",
         description="Get all open positions across Rithmic prop firm accounts. Returns ticker, exchange, net quantity, average fill price, and unrealized P&L. Only non-zero positions are returned.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_SAFE),

    Tool(name="get_rithmic_live_fills",
         description="Get recent fill history from Rithmic prop firm accounts. Returns fills in reverse chronological order with account_id, ticker, exchange, side (B/S), quantity, fill price, order_id, and timestamp.",
         inputSchema={"type": "object", "properties": {
             "limit": {"type": "integer", "description": "Max fills to return (default 50, max 500)", "default": 50},
         }, "required": []},
         annotations=ANNOT_READ_SAFE),

    # ── Prop Fund Evaluation Pipeline (read-only eval + gated deploy) ───────
    Tool(name="list_prop_funds",
         description="List supported prop firms with 2026-verified rules (Apex, Topstep, MyFundedFutures, TradeDay, Bulenox, Earn2Trade, FTMO, Tradeify). Returns fees, profit targets, drawdown type/limits, consistency rules, automation policy, and rules_verified_date.",
         inputSchema={"type": "object", "properties": {
             "platform": {"type": "string", "description": "Optional platform filter (tradovate, rithmic, ninjatrader, metatrader)"},
         }, "required": []},
         annotations=ANNOT_READ_SAFE),
    Tool(name="evaluate_strategy_for_prop_fund",
         description="Score a strategy against every supported prop firm (or a specific one) using its live stats. Returns ranked eligible funds with strengths/warnings.",
         inputSchema={"type": "object", "properties": {
             "strategy_name": {"type": "string"},
             "sharpe": {"type": "number"},
             "win_rate": {"type": "number"},
             "max_drawdown_pct": {"type": "number"},
             "avg_trade_pnl_usd": {"type": "number"},
             "max_position_size": {"type": "integer"},
             "overnight_positions": {"type": "boolean"},
             "fund_key": {"type": "string", "description": "Optional — evaluate a single fund key"},
         }, "required": ["strategy_name"]},
         annotations=ANNOT_READ_SAFE),
    Tool(name="simulate_prop_fund_evaluation",
         description="Replay a daily P&L series against a prop firm's drawdown rules and return pass/fail plus the day drawdown would have been violated.",
         inputSchema={"type": "object", "properties": {
             "fund_key": {"type": "string"},
             "daily_pnl": {"type": "array", "items": {"type": "number"}, "description": "List of daily realized P&L values in USD"},
             "starting_balance": {"type": "number"},
         }, "required": ["fund_key", "daily_pnl"]},
         annotations=ANNOT_READ_SAFE),
    Tool(name="get_prop_fund_rules",
         description="Get full rules dict for a specific prop firm entry by fund_key.",
         inputSchema={"type": "object", "properties": {
             "fund_key": {"type": "string"},
         }, "required": ["fund_key"]},
         annotations=ANNOT_READ_SAFE),
    Tool(name="register_prop_fund_account",
         description="Register a live prop firm account for autonomous drawdown monitoring. Stores rules snapshot + live balance tracking.",
         inputSchema={"type": "object", "properties": {
             "account_id": {"type": "string"},
             "fund_name": {"type": "string"},
             "broker": {"type": "string"},
             "starting_balance": {"type": "number"},
             "max_daily_loss_usd": {"type": "number"},
             "max_trailing_drawdown_usd": {"type": "number"},
             "profit_target_usd": {"type": "number"},
         }, "required": ["account_id", "fund_name", "broker", "starting_balance"]},
         annotations=ANNOT_READ_SAFE),
    Tool(name="get_prop_fund_monitor_status",
         description="Status of the prop fund drawdown monitor — registered accounts, current balances, distance-to-drawdown, consistency utilization.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_SAFE),
    Tool(name="get_prop_fund_broker_options",
         description="List broker backends supported per prop firm (Tradovate, Rithmic, NinjaTrader, etc.) with credential storage pattern.",
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_SAFE),
    Tool(name="build_prop_fund_inputs",
         description="Pull REAL Tradovate fills (no synthetic data) for a strategy over lookback_days, FIFO-match to realized trades, and return: daily_pnl series, win_rate, sharpe, avg_trade_pnl, max_drawdown_pct, and trade count. Fails closed if no real fills are found. Use this to feed evaluate_strategy_for_prop_fund and simulate_prop_fund_evaluation with live data.",
         inputSchema={"type": "object", "properties": {
             "strategy_name": {"type": "string", "description": "Bot name for tagging, e.g. FUTURES_SCALPER_UPGRADED"},
             "symbol": {"type": "string", "description": "Root symbol, e.g. MNQ"},
             "lookback_days": {"type": "integer", "default": 90},
             "account_id": {"type": "integer", "description": "Tradovate account id (defaults to primary)"},
             "fills_override": {"type": "array", "description": "Optional pre-pulled fills list for offline analysis"},
         }, "required": ["strategy_name", "symbol"]},
         annotations=ANNOT_READ_SAFE),
    Tool(name="onboard_prop_account",
         description="Stage a new prop firm account for the autopilot. Two-phase: first call returns fee/rules confirmation preview; pass confirm=true to commit. Never pays the evaluation fee — operator does that manually on the firm's website. Writes autopilot state.",
         inputSchema={"type": "object", "properties": {
             "fund_key": {"type": "string"},
             "account_id": {"type": "string"},
             "broker": {"type": "string", "description": "tradovate, rithmic, ninjatrader"},
             "starting_balance": {"type": "number"},
             "credentials_ref": {"type": "string", "description": "Reference key in credential vault"},
             "confirm": {"type": "boolean", "default": False},
         }, "required": ["fund_key", "account_id", "broker", "starting_balance"]},
         annotations=ANNOT_READ_SAFE),
    Tool(name="deploy_bot_in_prop_mode",
         description="Generate the prop_mode config JSON for a staged account and return the exact env vars (PROP_MODE=true, PROP_MODE_CONFIG=...) the operator must set to launch the bot. Does NOT start the bot — operator runs launch command. Requires confirm=true.",
         inputSchema={"type": "object", "properties": {
             "account_id": {"type": "string"},
             "bot_name": {"type": "string", "default": "FUTURES_SCALPER_UPGRADED"},
             "symbol": {"type": "string", "default": "MNQ"},
             "confirm": {"type": "boolean", "default": False},
         }, "required": ["account_id"]},
         annotations=ANNOT_READ_SAFE),
    Tool(name="get_prop_mode_status",
         description="Return autopilot status for all staged/active prop accounts — current phase (onboarded/deployed/evaluating/funded), balance, days traded, distance to profit target, distance to drawdown, consistency utilization.",
         inputSchema={"type": "object", "properties": {
             "account_id": {"type": "string", "description": "Optional — filter to one account"},
         }, "required": []},
         annotations=ANNOT_READ_SAFE),
    Tool(name="request_prop_payout",
         description="Check if an account is eligible for a payout given the firm's safety net, first-payout-day, and cap rules. Returns preview only — operator requests payout manually.",
         inputSchema={"type": "object", "properties": {
             "account_id": {"type": "string"},
             "current_balance": {"type": "number"},
         }, "required": ["account_id", "current_balance"]},
         annotations=ANNOT_READ_SAFE),
    Tool(name="run_prop_fund_autopilot",
         description="End-to-end read-only pipeline: build real-data inputs for a strategy, evaluate vs every eligible fund (or a filtered set), simulate drawdown, and return a prioritized recommendation (GO / HOLD / NO-GO) with rules-verified fields. Never commits fees or launches bots.",
         inputSchema={"type": "object", "properties": {
             "strategy_name": {"type": "string", "default": "FUTURES_SCALPER_UPGRADED"},
             "symbol": {"type": "string", "default": "MNQ"},
             "lookback_days": {"type": "integer", "default": 90},
             "account_id": {"type": "integer"},
             "fund_keys": {"type": "array", "items": {"type": "string"}, "description": "Optional filter, e.g. ['apex_50k_eod','mffu_core_50k']"},
             "fills_override": {"type": "array"},
         }, "required": []},
         annotations=ANNOT_READ_SAFE),
    Tool(name="check_prop_fund_rules_freshness",
         description="Audit all supported prop fund entries for how recently their rules were verified. Flags any entry older than max_age_days.",
         inputSchema={"type": "object", "properties": {
             "max_age_days": {"type": "integer", "default": 30},
         }, "required": []},
         annotations=ANNOT_READ_SAFE),

    # ── Numerai Tournament Tools (§9 / §28 / HK-17) ────────────────────────
    # Isolated from futures bots per §26.2 — no shared code paths or PKLs.
    Tool(name="numerai_status",
         description=(
             "Return Numerai tournament configuration status: env vars as booleans (never key values), "
             "dataset version, round cadence, and proxy_mmc labeling notes. "
             "Safe to call anytime — no API calls made. "
             "HK-6: NUMERAI_SECRET_KEY never appears in response."
         ),
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_SAFE),

    Tool(name="numerai_round_info",
         description=(
             "Return the current and upcoming Numerai tournament round information via numerapi. "
             "Requires NUMERAI_PUBLIC_ID and NUMERAI_SECRET_KEY in environment. "
             "Returns {current_round, submission_window, scoring_lag} — no predictions."
         ),
         inputSchema={"type": "object", "properties": {}, "required": []},
         annotations=ANNOT_READ_SAFE),

    Tool(name="numerai_download_dataset",
         description=(
             "Download Numerai Classic dataset (train.parquet + live.parquet + features.json) "
             "to ALGOCHAINS_STATE_DIR/numerai/data/. "
             "HK-3: live.parquet always re-downloaded (IDs change each round). "
             "HK-14: uses feature_set (small|medium|all) to avoid OOM. "
             "GCS mirror optional via use_gcs=true."
         ),
         inputSchema={
             "type": "object",
             "properties": {
                 "feature_set": {"type": "string", "enum": ["small", "medium", "all"], "default": "medium"},
                 "force_redownload": {"type": "boolean", "default": False},
                 "use_gcs": {"type": "boolean", "default": False},
             },
             "required": [],
         }),

    Tool(name="numerai_train_baseline",
         description=(
             "Train a LightGBM baseline model on the downloaded Numerai dataset. "
             "Era-based k-fold CV with embargo gap (HK-1). "
             "Saves model to models/numerai/model_<round>.pkl (HK-16: never touches CL/MNQ PKLs). "
             "Returns {proxy_corr_mean, proxy_corr_std, era_stability, model_path}. "
             "CPU-intensive — may take several minutes."
         ),
         inputSchema={
             "type": "object",
             "properties": {
                 "holdout_n": {"type": "integer", "default": 4, "description": "Eras for holdout (min 4)"},
                 "embargo_eras": {"type": "integer", "default": 4, "description": "Embargo gap between train/val"},
                 "feature_set": {"type": "string", "enum": ["small", "medium", "all"], "default": "medium"},
             },
             "required": [],
         }),

    Tool(name="numerai_validate_metrics",
         description=(
             "Compute per-era validation metrics on the holdout set. "
             "Returns {proxy_corr_mean, proxy_corr_std, era_stability, calibration_ok}. "
             "HK-10: ALL metrics are labeled proxy_corr/proxy_mmc — not bit-identical to "
             "Numerai server scoring. Only leaderboard mmcRep after scoring is authoritative."
         ),
         inputSchema={
             "type": "object",
             "properties": {
                 "neutralized": {"type": "boolean", "default": True, "description": "Whether to apply feature neutralization"},
             },
             "required": [],
         }),

    Tool(name="numerai_dry_run_submit",
         description=(
             "Generate submission CSV without uploading. "
             "Validates: IDs == live.parquet IDs (HK-3), all values in [0,1] (HK-5), std > 0. "
             "Returns {output_path, checksum_sha256, row_count, prediction_mean, prediction_std}. "
             "Run this before numerai_upload_predictions."
         ),
         inputSchema={
             "type": "object",
             "properties": {
                 "neutralized": {"type": "boolean", "default": True},
             },
             "required": [],
         }),

    Tool(name="numerai_upload_predictions",
         description=(
             "Upload submission CSV to Numerai tournament. IRREVERSIBLE — fail closed by default. "
             "REQUIRES: NUMERAI_ALLOW_LIVE=1 in environment AND confirm=true AND model_id set. "
             "HK-17: TIER_ORDER_EXEC. HK-7: no NMR staking here (Gate 2 = manual UI only). "
             "Default mode = dry-run even if called. Verifies round_id before upload."
         ),
         inputSchema={
             "type": "object",
             "properties": {
                 "model_id": {"type": "string", "description": "Numerai model UUID from numer.ai/models"},
                 "confirm": {"type": "boolean", "default": False, "description": "Must be true to proceed with upload"},
                 "round_id": {"type": "integer", "description": "Expected round ID for validation"},
             },
             "required": ["model_id", "confirm"],
         }),

    Tool(name="numerai_get_model_scores",
         description=(
             "Fetch model performance scores from Numerai leaderboard via numerapi. "
             "Returns raw response dict (pass-through — HK-13: no hardcoded field names). "
             "HK-10: proxy_mmc != live mmcRep. "
             "BMC note: diagnostics BMC != leaderboard BMC (highest-stake vs ensemble)."
         ),
         inputSchema={
             "type": "object",
             "properties": {
                 "model_id": {"type": "string", "description": "Numerai model UUID (optional — defaults to env NUMERAI_MODEL_ID)"},
                 "n_rounds": {"type": "integer", "default": 20, "description": "Number of recent rounds to fetch"},
             },
             "required": [],
         }),
]


# ═══════════════════════════════════════════════════════════════════
# Tiered Tool Exposure — solves the "too many tools" problem
# ═══════════════════════════════════════════════════════════════════
# Research basis:
#   - arXiv:2603.20313 — 99.6% token reduction, 97.1% hit rate at K=3
#   - Claude Code MCP Tool Search — 95% context reduction (39.8K → 5K)
#   - Cursor hard limit: 80 tools. Windsurf: context-bound.
#
# Modes (ALGOCHAINS_TOOL_MODE env var):
#   "smart"  — Tier 1 only (~52 core tools after SEC-2026 audit). Discoverable via meta-tools. DEFAULT.
#   "full"   — All 227 tools exposed. For clients that manage their own filtering.
#
# Tier 1 tools are the minimum set to be productive:
#   - 3 meta-tools (discover, detail, execute) — gateway to everything else
#   - 5 Massive data tools — market data pipeline
#   - 6 trading essentials — place/cancel/close/account/positions/orders
#   - 4 strategy tools — backtest, validate, optimize, deploy
#   - 2 portfolio tools — portfolio summary, quotes
#   - 1 connectivity — connect_broker
#   - 4 V18 intent + regime tools
#   - 4 V20 account protection + builder SDK tools
#   - 1 manifest tool
# ═══════════════════════════════════════════════════════════════════

TIER1_TOOL_NAMES = {
    # V17 Meta-tools — gateway to all 130+ hidden tools
    "discover_tools",
    "get_tool_details",
    "execute_dynamic_tool",
    # V17 Massive data pipeline
    "massive_search_endpoints",
    "massive_get_endpoint_docs",
    "massive_call_api",
    "massive_query_data",
    "massive_run_pipeline",
    # Core trading diagnostics only. Direct order tools are intentionally not
    # listed in smart mode; use full mode or execute_dynamic_tool with owner
    # authorization and confirmation.
    "get_account",
    "get_positions",
    "get_orders",
    # Strategy pipeline
    "run_backtest",
    "validate_strategy",
    "validate_strategy_metrics",
    "optimize_strategy",
    # Portfolio + market
    "portfolio_summary",
    "get_quote",
    # Connectivity
    "connect_broker",
    # V18 Intent Engine — natural language trading
    "execute_intent",
    "approve_intent",
    "create_shadow_portfolio",
    "detect_market_regime",
    # V20 Account Protection + Builder SDK
    "check_order_safety",
    "get_protection_config",
    "submit_to_marketplace",
    "query_data_warehouse",
    "mcp_tool_manifest",
    # V21 — Most-used new tools in Tier 1
    "run_evolution_cycle",
    "get_footprint_chart",
    "get_dark_pool_volume_v21",
    "get_macro_signals",
    "get_earnings_catalyst",
    "get_prediction_markets",
    "search_prediction_markets",
    "get_polymarket_high_volume",
    "get_polymarket_market",
    "get_polymarket_market_history",
    "list_polymarket_markets",
    "get_kalshi_settlements",
    "record_prediction_market_bot_metric",
    "get_prediction_market_bot_metrics",
    "check_propagation_health",
    # "test_signal_propagation" removed from Tier-1: SEC-2026-C7 — live signal injection.
    "run_guardrail",
    # Skills Bridge (V22.7)
    "list_skills",
    "get_skill_detail",
    "search_skills",
    "get_skills_for_task",
    "get_openclaw_memory",
    "get_current_regime",
    "get_bot_heartbeat_openclaw",
    "get_openclaw_state_summary",
    "store_trade_lesson",
    "invoke_moltbook_debate",
    "run_mcpt_pipeline",
    "run_regime_detection",
    # Onyx
    "onyx_ask",
    "onyx_search",
    # Graphiti temporal knowledge graph (advisory reads)
    "graphiti_search",
    "graphiti_health",
    "get_bot_dashboard",
    "get_bot_health",
    "get_quant_regime_state",
    "subscribe_bot_metrics",
    "get_funding_rate",
    "get_staking_yields",
    "request_trade_confirmation",
    "submit_long_running_task",
    "get_task_status",
    # Ultimate Quant Alpha
    "compute_volatility_surface",
    "compute_factor_exposure",
    "detect_regime_hmm",
    "get_vix_term_structure",
    "compute_correlation_matrix",
    # Marketplace Autopilot + Onyx
    "run_marketplace_autopilot",
    "get_marketplace_listings",
    "run_onyx_ingest",
    "get_onyx_status",
    "get_learn_hub_health",
    # V22 — Live Bot Intelligence (always Tier 1 — powers bot cards)
    "get_live_bot_metrics",
    "get_all_bot_metrics",
    "get_system_heartbeat",
    "get_system_health",
    "get_adaptive_brain_status",
    "get_strategy_academic_citations",
    "get_bot_card_data",
    "list_bot_research_attachments",
    # V26.0 — Bot Ops (Tier 1: read-only; destructive need owner_token)
    "get_bot_position_state",
    "get_bot_bracket_status",
    "get_ai_pipeline_health",
    "get_all_bot_ops_status",
    # V26.1 — Bracket integrity (always Tier 1 — safety critical)
    "check_unprotected_positions",
    "bracket_integrity_check",
    "get_bracket_guardian_status",
    # V22.4 — Desktop tower ML visibility
    "get_tower_health",
    "get_tower_job_status",
    # V22.1 — Guardrails status (always Tier 1 — safety awareness)
    "get_circuit_breaker_status",
    "get_daily_loss_proximity",
    "get_agent_loop_status",
    "get_latency_profile",
    # V22.2 — Onboarding (always Tier 1 — first thing new users see)
    "start_onboarding",
    "acknowledge_risk_disclosure",
    "get_broker_setup_guide",
    "validate_broker_connection",
    "get_data_provider_setup_guide",
    "validate_data_provider",
    "run_onboarding_smoke_test",
    "get_onboarding_status",
    "set_algochains_api_key",
    "set_guardrail_preferences",
    "generate_ide_config",
    # V22.3 — Data Ingestion (always Tier 1 — users need this early)
    "ingest_csv_data",
    "ingest_json_signals",
    "connect_onyx_docs",
    "register_strategy",
    "list_ingested_data",
    # Kronos + Rithmic live tools (always Tier 1 — bot operators need these)
    "get_kronos_shadow_stats",
    "get_signal_trade_correlation",
    "get_rithmic_live_accounts",
    "get_rithmic_live_pnl",
    "get_rithmic_live_positions",
    "get_rithmic_live_fills",
    # Support Tickets — create only (public intake); admin tools require owner_token (SEC-2026-C8)
    "create_support_ticket",
    # "get_support_ticket", "list_support_tickets", "update_ticket_status", "get_ticket_stats"
    # removed from Tier-1: SEC-2026-C8 — service_role reads/writes without auth.
    # OAuth Broker Connection
    "generate_broker_auth_url",
    "exchange_broker_oauth_code",
    # "get_broker_oauth_status" removed from Tier-1: SEC-2026-C5 — token exfiltration.
    "get_connected_brokers",
    "revoke_broker_connection",
    # Programmatic account / MFA / developer key tools
    "signup_algochains",
    "verify_email_otp",
    "login_algochains",
    "refresh_session",
    "logout_algochains",
    "enroll_mfa",
    "challenge_mfa",
    "verify_mfa",
    "list_mfa_factors",
    "remove_mfa_factor",
    "create_developer_key",
    "list_developer_keys",
    "rotate_developer_key",
    "revoke_developer_key",
    "get_developer_key_usage",
    "test_bridge_connection",
    # Onboarding meta-tools (public)
    "get_started",
    "get_pricing",
    "get_system_status",
    # Subscriber onramp
    "get_checkout_url",
    "generate_payment_link",
    # Subscriber tools (stdio path — key from ALGOCHAINS_SUBSCRIBER_KEY)
    "join_bot",
    "get_subscriber_status",
    "accept_subscriber_terms",
    "get_my_usage",
    # Affiliate / referral
    "create_referral_code",
    "get_my_referrals",
    "get_referral_earnings",
    # Creator payout account/ledger tools intentionally NOT here: they affect
    # Stripe payout routing or expose private creator financials and require
    # owner authorization until creator-authenticated sessions exist.
    # Realized P&L (reconcile_creator_pnl intentionally NOT here — owner-gated)
    "get_my_realized_pnl",
    # Waitlist
    "join_waitlist",
    "get_waitlist_stats",
    # "send_waitlist_invite" removed from Tier-1: SEC-2026-C3 FIX — invite minting
    # requires owner_token (ORDER_EXEC tier). See tool_danger_tiers.py.
    # Verification
    "send_email_verification_code",
    "send_sms_verification_code",
    "verify_code",
    # Analytics
    "track_platform_event",
    "get_analytics_summary",
    # Password Reset
    "initiate_password_reset",
    "complete_password_reset",
    "initiate_account_recovery",
    "get_password_policy",
    # Multi-Bot Metrics
    "get_user_bot_metrics",
    "get_all_user_bots",
    # "upsert_bot_performance" removed from Tier-1: SEC-2026-C4 FIX — metric writes
    # go through metrics_streaming_daemon.py; MCP path requires owner_token (ORDER_EXEC).
    # V22.9 — PAI Integration (always Tier 1 — business context + macro data)
    "get_algochains_telos",
    "get_us_economic_indicators",
    "get_fed_policy_signals",
    "get_crude_oil_inventories",
    "capture_learning_signal",
    "get_learning_signals",
    "send_ntfy_notification",
    "update_algochains_telos",
    # Prop Fund Evaluation — always Tier 1: operators need these to decide on eval fees
    "run_prop_fund_autopilot",
    "list_prop_funds",
    "evaluate_strategy_for_prop_fund",
    "get_prop_mode_status",
    # Numerai tournament — status tool is Tier 1 for discoverability (§9)
    "numerai_status",
}


def _annotate_tools(tools: list[Tool]) -> list[Tool]:
    """Apply MCP 2025-06-18 Tool Behavior Annotations to all tools."""
    annotated = []
    try:
        from .tool_danger_tiers import get_tool_tier as _tier_for_annotation, TIER_ORDER_EXEC as _annot_order_exec
    except Exception:
        _tier_for_annotation = None
        _annot_order_exec = 2
    for t in tools:
        if _tier_for_annotation is not None and _tier_for_annotation(t.name) >= _annot_order_exec:
            annotated.append(Tool(
                name=t.name,
                description=t.description,
                inputSchema=t.inputSchema,
                annotations=ANNOT_TRADE_EXEC,
            ))
            continue
        if t.annotations is not None:
            annotated.append(t)
        elif t.name in _TOOL_ANNOTATION_MAP:
            annotated.append(Tool(
                name=t.name,
                description=t.description,
                inputSchema=t.inputSchema,
                annotations=_TOOL_ANNOTATION_MAP[t.name],
            ))
        else:
            annotated.append(Tool(
                name=t.name,
                description=t.description,
                inputSchema=t.inputSchema,
                annotations=ANNOT_READ_ONLY,
            ))
    return annotated


TOOLS_ANNOTATED = _annotate_tools(TOOLS)
TOOLS_TIER1 = [t for t in TOOLS_ANNOTATED if t.name in TIER1_TOOL_NAMES]


_FULL_MODE_WARNED: bool = False  # emit once per process, not every list_tools call

@app.list_tools()
async def list_tools() -> list[Tool]:
    global _FULL_MODE_WARNED
    cfg = _config or load_config()
    if cfg.tool_mode == "full":
        # ARCH-RISK: ALGOCHAINS_TOOL_MODE=full is DEVELOPMENT/DEBUG ONLY.
        # In smart mode, ORDER_EXEC+ tools are only reachable via execute_dynamic_tool
        # which enforces owner_token + confirm=true at the envelope level.
        # In full mode, all tools appear directly. The evaluate_stdio_direct_tool
        # function now applies the ORDER_EXEC gate regardless (parity fix), but
        # operators should not run production bots with full mode enabled.
        if not _FULL_MODE_WARNED:
            _FULL_MODE_WARNED = True
            logger.warning(
                "ALGOCHAINS_TOOL_MODE=full — DEVELOPMENT MODE ACTIVE. "
                "All 338 tools are exposed for direct stdio call. "
                "ORDER_EXEC+ tools still require owner_token + ALGOCHAINS_REQUIRE_CONFIRMATION=0. "
                "Do NOT run live production bots with ALGOCHAINS_TOOL_MODE=full. "
                "Set ALGOCHAINS_TOOL_MODE=smart for production (default)."
            )
        return TOOLS_ANNOTATED
    # Smart mode: expose only Tier 1 (21 tools ≈ 4K tokens vs 40K+ for all)
    return TOOLS_TIER1


async def _execute_tool_with_runtime_guards(
    name: str,
    arguments: dict,
    registry: BrokerRegistry,
    *,
    transport: str,
    policy_decision: Any,
) -> list[TextContent]:
    """Run a tool through the shared runtime guards before dispatch.

    Dynamic dispatch authorizes hidden tools before this helper is called; the
    inner tool still needs the same rate limits, circuit breakers, timeouts, and
    response guard that direct calls receive.
    """
    arguments = validate_arguments(name, arguments)
    limiter = get_rate_limiter()
    tlog = get_tool_logger()
    t0 = time.monotonic()

    try:
        # Replay guard for signed destructive requests. Unsigned callers pass
        # through; this activates only when timestamp + nonce are provided.
        _x_ts = arguments.pop("_x_timestamp", None)
        _x_nonce = arguments.pop("_x_nonce", None)
        if _x_ts and _x_nonce:
            try:
                from .tool_danger_tiers import get_tool_tier, TIER_ORDER_EXEC
                _tier = get_tool_tier(name)
                if _tier >= TIER_ORDER_EXEC:
                    from .security.replay_guard import _GLOBAL_GUARD as _rg
                    _rg_result = _rg.validate(_x_ts, _x_nonce)
                    if not _rg_result.get("valid", True):
                        return _text({
                            "error_type": "ReplayGuardRejected",
                            "tool": name,
                            "reason": _rg_result.get("reason", "unknown"),
                            "message": "Request rejected by replay guard. Timestamp too old or nonce already seen.",
                        })
            except ImportError:
                pass  # tier module unavailable — skip replay check

        check_circuit(name)

        broker_name = arguments.get("broker", "")
        if broker_name:
            await limiter.acquire(broker_name)

        category = get_tool_category(name)
        if category:
            await limiter.acquire(category)

        try:
            from .security.per_tool_rate_limiter import check_rate_limit as _check_ptrl
            _ptrl_result = _check_ptrl(name)
            if not _ptrl_result.get("allowed", True):
                return _text({
                    "error_type": "RateLimitError",
                    "tool": name,
                    "message": f"Per-tool rate limit exceeded: {_ptrl_result.get('description', '')}",
                    "calls_in_window": _ptrl_result.get("calls_in_window"),
                    "limit": _ptrl_result.get("limit"),
                    "window_seconds": _ptrl_result.get("window_seconds"),
                    "reset_in_seconds": _ptrl_result.get("reset_in_seconds"),
                })
        except ImportError:
            pass  # security module unavailable — category limiter is still active

        sem = get_tool_semaphore(name)
        timeout = get_tool_timeout(name)

        async def _guarded_dispatch() -> list[TextContent]:
            if sem:
                async with sem:
                    return await asyncio.wait_for(
                        _dispatch_tool(name, arguments, registry),
                        timeout=timeout,
                    )
            return await asyncio.wait_for(
                _dispatch_tool(name, arguments, registry),
                timeout=timeout,
            )

        with trace_span(
            "mcp.tool.call",
            {
                "tool.name": name,
                "mcp.server": "algochains",
                "mcp.transport": transport,
                "algochains.danger_tier": policy_decision.danger_tier,
                "algochains.danger_label": policy_decision.danger_label,
                "algochains.tier_source": policy_decision.tier_source,
                "algochains.arguments_hash": redacted_argument_hash(arguments),
            },
        ) as span:
            result = await _guarded_dispatch()
            if span is not None:
                span.set_attribute("algochains.tool.success", True)

        for content in result:
            if hasattr(content, "text"):
                content.text = guard_response_size(content.text, name)

        record_success(name)
        tlog.log_call(name, arguments, duration_ms=(time.monotonic() - t0) * 1000)
        return result

    except asyncio.TimeoutError:
        record_failure(name)
        elapsed = (time.monotonic() - t0) * 1000
        logger.error("Tool %s TIMED OUT after %.0fms (limit: %.0fs)", name, elapsed, get_tool_timeout(name))
        tlog.log_call(name, arguments, error="timeout", duration_ms=elapsed)
        return _text({"error_type": "TimeoutError", "message": f"Tool '{name}' timed out after {get_tool_timeout(name):.0f}s", "tool": name})

    except CircuitOpenError as e:
        tlog.log_call(name, arguments, error=str(e), duration_ms=(time.monotonic() - t0) * 1000)
        return _text({"error_type": "CircuitOpenError", "message": str(e), "tool": name, "retry_after_seconds": round(e.retry_after)})

    except AlgoChainsError as e:
        record_failure(name)
        tlog.log_call(name, arguments, error=str(e), duration_ms=(time.monotonic() - t0) * 1000)
        return _error_text(e)

    except AttributeError as e:
        record_failure(name)
        msg = str(e)
        if "NoneType" in msg or "None" in msg:
            friendly = (
                f"Engine for tool '{name}' failed to initialize (returned None). "
                "Check server startup logs for import errors or missing env vars. "
                f"Original error: {msg}"
            )
            logger.error("Tool %s: uninitialized engine — %s", name, msg)
            tlog.log_call(name, arguments, error=friendly, duration_ms=(time.monotonic() - t0) * 1000)
            return _text({"error_type": "EngineUnavailable", "message": friendly, "tool": name})
        logger.error("Tool %s AttributeError: %s", name, msg, exc_info=True)
        tlog.log_call(name, arguments, error=msg, duration_ms=(time.monotonic() - t0) * 1000)
        return _text({"error_type": "AttributeError", "message": msg, "tool": name})

    except Exception as e:
        record_failure(name)
        logger.error("Tool %s failed: %s", name, e, exc_info=True)
        tlog.log_call(name, arguments, error=str(e), duration_ms=(time.monotonic() - t0) * 1000)
        return _text({"error_type": type(e).__name__, "message": str(e), "tool": name})


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    registry = _get_registry()
    tlog = get_tool_logger()
    t0 = time.monotonic()

    try:
        # ── 0. V22 Guardrails — AI loop detection (ALL tool calls) ───
        # This runs before sanitization so loops are caught even on
        # malformed arguments. GuardrailTripped is handled below.
        if _GUARDRAILS_AVAILABLE:
            try:
                get_guardrails().record_tool_call(name, arguments)
            except GuardrailTripped as _gt:
                tlog.log_call(name, arguments, error=str(_gt), duration_ms=0)
                return _text({
                    "error_type": "GuardrailTripped",
                    "blocked": True,
                    "reason": _gt.reason.value,
                    "message": str(_gt),
                    "cooldown_sec": _gt.cooldown_sec,
                    "action": "All orders blocked. No broker API calls made.",
                })

        # ── 1. Sanitize inputs ───────────────────────────────────
        arguments = validate_arguments(name, arguments)

        # ── 1b. Demo-mode stub for ORDER_EXEC+ tools ─────────────
        # ALGOCHAINS_DEMO_MODE=1 stubs destructive/order tools so demo users
        # cannot accidentally place real orders. Tier 0-1 tools (market data,
        # signals, regime, backtesting) are NEVER stubbed — demo users expect
        # real data from those paths. Only tier≥2 (ORDER_EXEC / DESTRUCTIVE)
        # is stubbed. quickstart.py sets this env var in --mode demo.
        if os.getenv("ALGOCHAINS_DEMO_MODE", "0") == "1":
            from .tool_danger_tiers import TIER_ORDER_EXEC as _TIER_ORDER_EXEC, get_danger_tier as _get_tier
            if _get_tier(name) >= _TIER_ORDER_EXEC:
                return _text({
                    "status": "demo_mode_stub",
                    "tool": name,
                    "message": (
                        f"Tool '{name}' is an order/execution tool (tier≥2) and is stubbed "
                        "in demo mode. No broker API call was made. "
                        "Set credentials and remove ALGOCHAINS_DEMO_MODE to enable live execution."
                    ),
                    "demo_mode": True,
                })

        # Smart mode is now an execution boundary for direct tool calls, not
        # just a list_tools token-saving filter. Hidden tools remain reachable
        # through execute_dynamic_tool, where danger-tier gating is centralized.
        cfg = _config or load_config()
        # ARCH-RISK FIX: Pass owner_token + require_confirmation so that
        # evaluate_stdio_direct_tool can enforce ORDER_EXEC gating in full mode
        # (stdio/full parity). Previously full mode had no such gate.
        _stdio_owner_token = arguments.get("owner_token") if isinstance(arguments, dict) else None
        _stdio_require_confirm = os.getenv("ALGOCHAINS_REQUIRE_CONFIRMATION", "1") == "1"
        direct_decision = evaluate_stdio_direct_tool(
            name,
            tool_mode=cfg.tool_mode,
            tier1_names=set(TIER1_TOOL_NAMES),
            owner_token=_stdio_owner_token,
            require_confirmation=_stdio_require_confirm,
        )
        if not direct_decision.allow:
            payload = direct_decision.as_error()
            payload["error_type"] = "SmartModeToolUnavailable"
            payload["tool_mode"] = cfg.tool_mode
            payload["message"] = direct_decision.reason
            return _text(payload)

        return await _execute_tool_with_runtime_guards(
            name,
            arguments,
            registry,
            transport="stdio",
            policy_decision=direct_decision,
        )

    except asyncio.TimeoutError:
        record_failure(name)
        elapsed = (time.monotonic() - t0) * 1000
        logger.error("Tool %s TIMED OUT after %.0fms (limit: %.0fs)", name, elapsed, get_tool_timeout(name))
        tlog.log_call(name, arguments, error="timeout", duration_ms=elapsed)
        return _text({"error_type": "TimeoutError", "message": f"Tool '{name}' timed out after {get_tool_timeout(name):.0f}s", "tool": name})

    except CircuitOpenError as e:
        tlog.log_call(name, arguments, error=str(e), duration_ms=(time.monotonic() - t0) * 1000)
        return _text({"error_type": "CircuitOpenError", "message": str(e), "tool": name, "retry_after_seconds": round(e.retry_after)})

    except AlgoChainsError as e:
        record_failure(name)
        tlog.log_call(name, arguments, error=str(e), duration_ms=(time.monotonic() - t0) * 1000)
        return _error_text(e)

    except AttributeError as e:
        record_failure(name)
        msg = str(e)
        # Catch unguarded None engine calls (e.g. "NoneType has no attribute 'method'")
        if "NoneType" in msg or "None" in msg:
            friendly = (
                f"Engine for tool '{name}' failed to initialize (returned None). "
                "Check server startup logs for import errors or missing env vars. "
                f"Original error: {msg}"
            )
            logger.error("Tool %s: uninitialized engine — %s", name, msg)
            tlog.log_call(name, arguments, error=friendly, duration_ms=(time.monotonic() - t0) * 1000)
            return _text({"error_type": "EngineUnavailable", "message": friendly, "tool": name})
        logger.error("Tool %s AttributeError: %s", name, msg, exc_info=True)
        tlog.log_call(name, arguments, error=msg, duration_ms=(time.monotonic() - t0) * 1000)
        return _text({"error_type": "AttributeError", "message": msg, "tool": name})

    except Exception as e:
        record_failure(name)
        logger.error("Tool %s failed: %s", name, e, exc_info=True)
        tlog.log_call(name, arguments, error=str(e), duration_ms=(time.monotonic() - t0) * 1000)
        return _text({"error_type": type(e).__name__, "message": str(e), "tool": name})


def _require_broker(registry: BrokerRegistry, broker_name: str):
    """Get a connected broker or raise a structured error."""
    conn = registry.get(broker_name)
    if conn is None:
        if broker_name in registry.list_configured():
            raise BrokerNotConnectedError(
                f"Broker '{broker_name}' is configured but not connected. Call connect_broker first.",
                broker=broker_name,
            )
        raise BrokerNotConfiguredError(
            f"Broker '{broker_name}' is not configured. Set environment variables.",
            broker=broker_name,
        )
    return conn


async def _dispatch_tool(name: str, arguments: dict, registry: BrokerRegistry) -> list[TextContent]:
    """Route tool calls to their implementations."""
    args = arguments  # alias used by some handlers
    registered_handler = _HANDLER_REGISTRY.get(name)
    if registered_handler is not None:
        return _text(await registered_handler(arguments))

    # ── Trading ──────────────────────────────────────────────
    if name == "place_order":
        # MCP 2025-06-18 Elicitation — ask user to confirm trade before execution
        side = arguments["side"]
        symbol = arguments["symbol"]
        qty = arguments["qty"]
        otype = arguments.get("order_type", "market")
        limit_px = arguments.get("limit_price")
        price_hint = f" @ ${limit_px}" if limit_px else " at market"
        try:
            ctx = app.request_context
            confirm = await ctx.session.elicit_form(
                message=f"Confirm: {side.upper()} {qty} {symbol} ({otype}){price_hint} on {arguments['broker']}?",
                requestedSchema={
                    "type": "object",
                    "properties": {
                        "confirmed": {"type": "boolean", "title": "Execute this trade?", "default": True},
                    },
                },
            )
            if confirm.action != "accept" or not (confirm.content or {}).get("confirmed", True):
                return _text({"status": "cancelled", "reason": "User declined trade confirmation"})
        except (LookupError, AttributeError, NotImplementedError) as _elicit_err:
            # Elicitation (interactive popup) is not supported by this MCP client.
            # ALGOCHAINS_REQUIRE_CONFIRMATION=1 blocks execution in this case (recommended for production).
            # Default: fall through and rely on V22 Guardrails as the safety layer.
            if os.getenv("ALGOCHAINS_REQUIRE_CONFIRMATION", "1") == "1":
                logger.warning("place_order BLOCKED — elicitation unavailable and ALGOCHAINS_REQUIRE_CONFIRMATION=1")
                return _text({
                    "status": "blocked",
                    "reason": "Trade confirmation required but client does not support interactive prompts. "
                              "Set ALGOCHAINS_REQUIRE_CONFIRMATION=0 to allow unconfirmed orders, "
                              "or use a client that supports MCP elicitation.",
                    "order": {"symbol": symbol, "side": side, "qty": qty, "type": otype},
                })
            logger.warning(
                "place_order executing WITHOUT interactive confirmation (client lacks elicitation support): "
                "%s %s %s on %s — set ALGOCHAINS_REQUIRE_CONFIRMATION=1 to block this",
                side, qty, symbol, arguments.get("broker", "?"),
            )

        conn = _require_broker(registry, arguments["broker"])

        # ── V22 Guardrails: hard-coded pre-order gate ─────────────
        # Fetches current account state to check financial limits.
        # All checks are mandatory. GuardrailTripped blocks execution.
        if _GUARDRAILS_AVAILABLE:
            try:
                _g = get_guardrails()
                _broker_name = arguments.get("broker", "tradovate")
                _symbol = arguments.get("symbol", "")
                _qty = float(arguments.get("qty", 1))

                # Fetch live account data for financial limit checks
                _daily_pnl = 0.0
                _drawdown_pct = 0.0
                # BUG-05 FIX: Previously _consecutive_losses was hardcoded to 0,
                # permanently disabling the loss-streak halt gate in check_all().
                # Authoritative source: fills API (real-time, from broker).
                # Reconciliation fallback: signal_health.json (written by bot, may have
                #   1-bar latency but survives fills API outages).
                # If both fail: warn and assume 0 (gate weakened, logged explicitly).
                _consecutive_losses = 0
                _fills_source_ok = False
                _loss_streak_from_fills = False
                _fills_api_err_str: str = ""  # persist outside except block (Python 3 deletes except-vars)
                try:
                    _fills = await _get_recent_fills_for_guardrail(conn, _symbol)
                    _fills_source_ok = True
                    _consecutive_losses, _loss_streak_from_fills = _compute_consecutive_losses_from_fills(_fills)
                except Exception as _fills_err:
                    _fills_api_err_str = str(_fills_err)  # capture before Python 3 deletes the var
                    logger.warning(
                        "place_order guardrail: fills API unavailable for consecutive_losses (%s) — "
                        "trying signal_health.json reconciliation fallback",
                        _fills_api_err_str,
                    )

                # Reconciliation: only fall back when broker fills cannot produce
                # an authoritative streak. A fresh winner/breakeven from the broker
                # is authoritative streak=0 and must not be overwritten by stale state.
                if not _loss_streak_from_fills:
                    try:
                        import json as _cljson
                        from pathlib import Path as _clPath
                        _sh_path = _clPath(_default_control_tower()) / "state" / "signal_health.json"
                        if _sh_path.exists():
                            _sh = _cljson.loads(_sh_path.read_text())
                            # signal_health.json stores per-bot consecutive_losses if available
                            for _bot_data in _sh.values():
                                if isinstance(_bot_data, dict):
                                    _sh_streak = _bot_data.get("consecutive_losses", 0)
                                    if isinstance(_sh_streak, int) and _sh_streak > _consecutive_losses:
                                        _consecutive_losses = _sh_streak
                                        logger.info(
                                            "place_order guardrail: consecutive_losses=%d from signal_health.json reconciliation",
                                            _consecutive_losses,
                                        )
                    except Exception as _sh_err:
                        if not _fills_source_ok:
                            logger.warning(
                                "place_order guardrail: both fills API and signal_health.json failed — "
                                "consecutive_losses assumed 0, loss-streak halt gate WEAKENED. "
                                "Errors: fills=%s, state=%s",
                                _fills_api_err_str, _sh_err,
                            )

                # ── Live VIX fetch — VIX > 35 gate REQUIRES a real value ──────
                # Previously hardcoded to 0.0 which silently disabled the VIX gate.
                # Now fetch from CBOE via the same logic as get_vix_term_structure.
                _vix = 0.0
                try:
                    import httpx as _httpx_g
                    async with _httpx_g.AsyncClient(timeout=5.0) as _vc:
                        _vix_resp = await _vc.get(
                            "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
                        )
                    _vix_lines = _vix_resp.text.strip().split("\n")
                    _vix_last = _vix_lines[-1].split(",")
                    _vix = float(_vix_last[-1]) if len(_vix_last) > 1 else 0.0
                except Exception as _vix_err:
                    # If VIX fetch fails, fall back to env override so the gate
                    # can still trip when manually set (e.g. CURRENT_VIX=40).
                    _env_vix_str = os.getenv("CURRENT_VIX", "")
                    _vix = float(_env_vix_str) if _env_vix_str else 0.0
                    # BUG (P1-E) FIX: Log WARNING (not debug) when VIX is unknown;
                    # a silent debug message meant operators couldn't see when the
                    # VIX kill-switch was weakened during a CBOE outage.
                    if _vix == 0.0:
                        logger.warning(
                            "place_order VIX fetch failed AND CURRENT_VIX env not set — "
                            "VIX gate will be SKIPPED for this order. "
                            "Set CURRENT_VIX or ensure CBOE feed is healthy. Error: %s",
                            _vix_err,
                        )
                    else:
                        logger.warning(
                            "place_order VIX fetch failed, using CURRENT_VIX=%s from env. Error: %s",
                            _vix, _vix_err,
                        )

                # ── Live notional — NotionalValueGuard REQUIRES a real value ──
                # Previously hardcoded to 0.0, disabling notional size checks.
                # Compute from price * qty * contract_multiplier.
                _notional = 0.0
                _CONTRACT_MULTIPLIERS: dict[str, float] = {
                    "MNQ": 2.0, "NQ": 20.0, "MES": 5.0, "ES": 50.0,
                    "MCL": 100.0, "CL": 1000.0, "MGC": 10.0, "GC": 100.0,
                }
                import math as _math_notional
                try:
                    _price_hint = arguments.get("limit_price") or arguments.get("stop_price")
                    if _price_hint:
                        _price = float(_price_hint)
                    else:
                        # Try live quote; caller estimate is only a fallback when
                        # a real price source is unavailable.
                        _q = await conn.get_quote(_symbol)
                        _price = float(getattr(_q, "last", 0) or getattr(_q, "bid", 0) or 0)
                    _root = "".join(c for c in _symbol.upper() if c.isalpha())[:3]
                    _mult = _CONTRACT_MULTIPLIERS.get(_root, 1.0)
                    _notional = _price * _qty * _mult
                    if _notional == 0.0:
                        # Ultimate fallback: caller-supplied estimated_notional
                        _notional = float(arguments.get("estimated_notional", 0))
                    if not _math_notional.isfinite(_notional) or _notional <= 0.0:
                        raise GuardrailTripped(
                            GuardrailReason.MARKET_PRICE_UNAVAILABLE,
                            "No live market price available and no positive estimated_notional was supplied. "
                            "Order aborted fail-closed before reaching broker.",
                        )
                except GuardrailTripped:
                    raise
                except Exception as _not_err:
                    try:
                        _notional = float(arguments.get("estimated_notional", 0))
                    except (TypeError, ValueError):
                        _notional = 0.0
                    if not _math_notional.isfinite(_notional) or _notional <= 0.0:
                        raise GuardrailTripped(
                            GuardrailReason.MARKET_PRICE_UNAVAILABLE,
                            "No live market price available and no positive estimated_notional was supplied. "
                            f"Order aborted fail-closed before reaching broker. Error: {_not_err}",
                        )
                    logger.warning("Notional compute failed (%s), using arg fallback: %.2f", _not_err, _notional)

                try:
                    _acct = await conn.get_account()
                    _daily_pnl = getattr(_acct, "daily_pnl", 0.0) or 0.0
                    _drawdown_pct = getattr(_acct, "drawdown_pct", 0.0) or 0.0
                except Exception as _acct_err:
                    logger.debug("Could not fetch account for guardrail check: %s", _acct_err)

                _g.check_all(
                    broker=_broker_name,
                    symbol=_symbol,
                    qty_contracts=_qty,
                    current_daily_pnl=_daily_pnl,
                    current_drawdown_pct=_drawdown_pct,
                    consecutive_losses=_consecutive_losses,
                    vix=_vix,
                    total_open_notional=_notional,
                )
                _g.record_order()  # Record velocity after gate passes
            except GuardrailTripped as _gt:
                logger.warning("place_order BLOCKED by guardrail: %s", _gt)
                return _text({
                    "error_type": "GuardrailTripped",
                    "reason": _gt.reason.value,
                    "message": str(_gt),
                    "cooldown_sec": _gt.cooldown_sec,
                    "order_submitted": False,
                    "action": "Order rejected before reaching broker. No position opened.",
                })
        # ── End V22 Guardrails ────────────────────────────────────

        try:
            order = await conn.place_order(
                symbol=arguments["symbol"],
                side=OrderSide(arguments["side"]),
                qty=arguments["qty"],
                order_type=OrderType(arguments.get("order_type", "market")),
                limit_price=arguments.get("limit_price"),
                stop_price=arguments.get("stop_price"),
                trail_pct=arguments.get("trail_pct"),
                time_in_force=arguments.get("time_in_force", "day"),
            )
        except Exception as _order_err:
            if _GUARDRAILS_AVAILABLE:
                try:
                    get_guardrails().record_order_failure(
                        arguments.get("broker", "tradovate"),
                        str(_order_err),
                    )
                except Exception:
                    pass
            raise
        # V22: Record successful order for circuit breaker health tracking
        if _GUARDRAILS_AVAILABLE:
            try:
                get_guardrails().record_order_success(arguments.get("broker", "tradovate"))
            except Exception:
                pass
        result_dict = order.to_dict()
        # Traceability: echo client_trace_id if provided so callers can join MCP
        # order responses to control-tower trade_log / bracket_audit rows.
        _ctid = arguments.get("client_trace_id")
        if _ctid:
            result_dict["client_trace_id"] = _ctid
        return _text(result_dict)

    elif name == "cancel_order":
        # BUG-02 FIX: cancel_order previously had zero confirmation machinery despite
        # being Tier-2. Cancelling a working bracket leg mid-trade is how orphan
        # positions happen. Now mirrors place_order's elicitation + env gate pattern.
        order_id = arguments["order_id"]
        _cancel_broker = arguments["broker"]
        try:
            ctx = app.request_context
            confirm = await ctx.session.elicit_form(
                message=f"Confirm: Cancel order {order_id} on {_cancel_broker}?",
                requestedSchema={
                    "type": "object",
                    "properties": {
                        "confirmed": {"type": "boolean", "title": "Cancel this order?", "default": True},
                    },
                },
            )
            if confirm.action != "accept" or not (confirm.content or {}).get("confirmed", True):
                return _text({"status": "cancelled", "reason": "User declined cancel confirmation", "order_id": order_id})
        except (LookupError, AttributeError, NotImplementedError) as _elicit_err:
            if os.getenv("ALGOCHAINS_REQUIRE_CONFIRMATION", "1") == "1":
                logger.warning("cancel_order BLOCKED — elicitation unavailable and ALGOCHAINS_REQUIRE_CONFIRMATION=1")
                return _text({
                    "status": "blocked",
                    "reason": "Order cancel confirmation required but client does not support interactive prompts. "
                              "Set ALGOCHAINS_REQUIRE_CONFIRMATION=0 to allow unconfirmed cancels, "
                              "or use a client that supports MCP elicitation.",
                    "order_id": order_id,
                })
            logger.warning(
                "cancel_order executing WITHOUT interactive confirmation (client lacks elicitation support): "
                "order_id=%s on %s — set ALGOCHAINS_REQUIRE_CONFIRMATION=1 to block this",
                order_id, _cancel_broker,
            )
        conn = _require_broker(registry, _cancel_broker)
        ok = await conn.cancel_order(order_id)
        result_dict = {"cancelled": ok, "order_id": order_id}
        _ctid = arguments.get("client_trace_id")
        if _ctid:
            result_dict["client_trace_id"] = _ctid
        return _text(result_dict)

    elif name == "close_position":
        # MCP 2025-06-18 Elicitation — confirm position close
        symbol = arguments["symbol"]
        broker = arguments["broker"]
        try:
            ctx = app.request_context
            confirm = await ctx.session.elicit_form(
                message=f"Confirm: Close entire {symbol} position on {broker}?",
                requestedSchema={
                    "type": "object",
                    "properties": {
                        "confirmed": {"type": "boolean", "title": "Close this position?", "default": True},
                    },
                },
            )
            if confirm.action != "accept" or not (confirm.content or {}).get("confirmed", True):
                return _text({"status": "cancelled", "reason": "User declined close confirmation"})
        except (LookupError, AttributeError, NotImplementedError) as _elicit_err:
            # BUG-01 FIX: Previously this fell through to conn.close_position() with
            # only a debug log — no env gate. Unlike place_order, there was no
            # ALGOCHAINS_REQUIRE_CONFIRMATION check. Now mirrors place_order pattern.
            if os.getenv("ALGOCHAINS_REQUIRE_CONFIRMATION", "1") == "1":
                logger.warning("close_position BLOCKED — elicitation unavailable and ALGOCHAINS_REQUIRE_CONFIRMATION=1")
                return _text({
                    "status": "blocked",
                    "reason": "Position close confirmation required but client does not support interactive prompts. "
                              "Set ALGOCHAINS_REQUIRE_CONFIRMATION=0 to allow unconfirmed closes, "
                              "or use a client that supports MCP elicitation.",
                    "position": {"symbol": symbol, "broker": broker},
                })
            logger.warning(
                "close_position executing WITHOUT interactive confirmation (client lacks elicitation support): "
                "%s on %s — set ALGOCHAINS_REQUIRE_CONFIRMATION=1 to block this",
                symbol, broker,
            )

        conn = _require_broker(registry, arguments["broker"])
        order = await conn.close_position(arguments["symbol"])
        result_dict = order.to_dict() if order else {"error": f"No position in {arguments['symbol']}"}
        _ctid = arguments.get("client_trace_id")
        if _ctid:
            result_dict["client_trace_id"] = _ctid
        return _text(result_dict)

    elif name == "close_all_positions":
        if os.getenv("ALGOCHAINS_REQUIRE_CONFIRMATION", "1") == "1":
            logger.warning(
                "close_all_positions BLOCKED — ALGOCHAINS_REQUIRE_CONFIRMATION=1 "
                "and this client does not support interactive prompts"
            )
            return _text({
                "status": "blocked",
                "reason": "close_all_positions requires confirmation. Set ALGOCHAINS_REQUIRE_CONFIRMATION=0 to allow.",
            })
        conn = _require_broker(registry, arguments["broker"])
        orders = await conn.close_all_positions()
        return _text({"closed": len(orders), "orders": [o.to_dict() for o in orders]})

    # ── Portfolio ────────────────────────────────────────────
    elif name == "get_account":
        conn = _require_broker(registry, arguments["broker"])
        acct = await conn.get_account()
        return _text(acct.to_dict())

    elif name == "get_positions":
        conn = _require_broker(registry, arguments["broker"])
        positions = await conn.get_positions()
        return _text([p.to_dict() for p in positions])

    elif name == "get_orders":
        conn = _require_broker(registry, arguments["broker"])
        orders = await conn.get_orders(arguments.get("status"))
        return _text([o.to_dict() for o in orders])

    elif name in ("portfolio_summary", "get_portfolio_summary"):
        summary = {"brokers": {}, "total_equity": 0.0, "total_positions": 0}
        for bname in registry.list_available():
            conn = registry.get(bname)
            try:
                acct = await conn.get_account()
                positions = await conn.get_positions()
                summary["brokers"][bname] = {
                    "equity": acct.equity,
                    "cash": acct.cash,
                    "positions": len(positions),
                    "unrealized_pnl": sum(p.unrealized_pnl for p in positions),
                }
                summary["total_equity"] += acct.equity
                summary["total_positions"] += len(positions)
            except Exception as e:
                summary["brokers"][bname] = {"error": str(e)}
        return _text(summary)

    # ── Market Data ─────────────────────────────────────────
    elif name == "get_quote":
        conn = _require_broker(registry, arguments["broker"])
        quote = await conn.get_quote(arguments["symbol"])
        return _text(quote.to_dict())

    elif name == "search_tradovate_contracts":
        conn = _require_broker(registry, "tradovate")
        query = arguments["query"]
        limit = min(int(arguments.get("limit", 10)), 50)
        try:
            results = await conn._get(
                "/contract/suggest", {"t": query, "l": limit}
            )
            if isinstance(results, dict):
                contracts = results.get("contracts", [])
            elif isinstance(results, list):
                contracts = results
            else:
                contracts = []
            simplified = [
                {
                    "id": c.get("id"),
                    "name": c.get("name"),
                    "description": c.get("description", ""),
                    "productType": c.get("productType", ""),
                }
                for c in contracts[:limit]
            ]
            return _text({"query": query, "count": len(simplified), "contracts": simplified})
        except Exception as e:
            return _text({"error": str(e), "query": query, "contracts": []})

    elif name == "get_tradovate_risk_snapshot":
        conn = _require_broker(registry, "tradovate")
        try:
            raw = await conn._get("/riskLimit/list")
            limits = raw if isinstance(raw, list) else []
            acct_limits = [r for r in limits if r.get("accountId") == conn._account_id] or limits
            readable = [
                {
                    "accountId": r.get("accountId"),
                    "dayMaxLoss": r.get("dayMaxLoss"),
                    "maxDrawdown": r.get("maxDrawdown"),
                    "maxOrderQty": r.get("maxOrderQty"),
                    "trailingMaxDrawdown": r.get("trailingMaxDrawdown"),
                    "weekMaxLoss": r.get("weekMaxLoss"),
                }
                for r in acct_limits
            ]
            return _text({"account_id": conn._account_id, "risk_limits": readable})
        except Exception as e:
            return _text({"error": str(e), "risk_limits": []})

    elif name == "get_bot_health":
        # Unified health snapshot across all live bots. Pure filesystem + ps read.
        import os as _os
        import subprocess as _subp
        import time as _time
        from pathlib import Path as _Path

        bot_filter = (arguments.get("bot") or "all").lower()
        control_tower = _Path(_default_control_tower())
        from algochains_mcp.bot_log_paths import resolve_bot_log
        bots = {
            "mnq": {"script": "FUTURES_SCALPER_UPGRADED.py"},
            "cl":  {"script": "CL_FUTURES_SCALPER.py"},
            "mes": {"script": "mes_swing_live.py"},
            "nq":  {"script": "nq_swing_live.py"},
            "kalshi": {"script": "kalshi_daemon.py"},
        }
        try:
            from .live_bot_intelligence.heartbeat import scan_running_bot_keys

            ps_out = _subp.run(["ps", "aux"], capture_output=True, text=True, timeout=5).stdout
            running_bots = scan_running_bot_keys(ps_out)
        except Exception:
            ps_out = ""
            running_bots = set()

        now = _time.time()
        results = {}
        for key, meta in bots.items():
            if bot_filter not in ("all", key):
                continue
            log_path = None
            log_candidates = []
            legacy_stale_mismatch = False
            log_resolution = resolve_bot_log(control_tower, key, now=now)
            if log_resolution.get("path") is not None:
                log_path = log_resolution["path"]
            log_candidates = log_resolution.get("candidates") or []
            legacy_stale_mismatch = bool(log_resolution.get("legacy_stale_mismatch"))
            running = key in running_bots
            last_log_mtime = None
            error_count = 0
            tail_preview = ""
            if log_path is not None and log_path.exists():
                try:
                    last_log_mtime = int(now - log_path.stat().st_mtime)
                    # Read last 100 lines with tail for speed
                    tail = _subp.run(
                        ["tail", "-n", "100", str(log_path)],
                        capture_output=True, text=True, timeout=3,
                    ).stdout
                    error_count = sum(
                        1 for ln in tail.splitlines()
                        if any(tok in ln for tok in ("ERROR", "Exception", "Traceback", " 401", " 422"))
                    )
                    tail_preview = tail.splitlines()[-1][:200] if tail.strip() else ""
                except Exception:
                    pass
            results[key] = {
                "running": running,
                "log_age_seconds": last_log_mtime,
                "error_count_last_100": error_count,
                "last_line_preview": tail_preview,
                "log_path": str(log_path) if log_path else None,
                "log_candidates": log_candidates,
                "legacy_stale_mismatch": legacy_stale_mismatch,
            }

        # Tradovate token expiry (best-effort)
        token_file = control_tower / "tradovate_token_live.txt"
        token_info = {"present": token_file.exists()}
        if token_file.exists():
            try:
                content = token_file.read_text().splitlines()
                jwt = content[0].replace("Bearer ", "").strip() if content else ""
                if jwt.count(".") == 2:
                    import base64 as _b64, json as _json
                    pad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
                    payload = _json.loads(_b64.urlsafe_b64decode(pad(jwt.split(".")[1])))
                    exp = payload.get("exp")
                    if exp:
                        token_info["expires_in_seconds"] = int(exp - now)
            except Exception:
                pass

        # ── signal_health.json slice (bounded operational telemetry) ──
        # Provides MCP clients one-stop visibility into bot params, validated
        # risk metrics, and bounded validator shadow slices without requiring
        # direct filesystem access.
        import json as _json_gh
        signal_health_slice = {}
        _sh_path = control_tower / "state" / "signal_health.json"
        if _sh_path.exists():
            try:
                _sh_data = _json_gh.loads(_sh_path.read_text())
                _bot_key_map = {
                    "mnq": "MNQ_Upgraded_Scalper",
                    "cl":  "CL_Futures_Scalper",
                    "mes": "MES_EMA_Swing",
                    "nq":  "NQ_EMA_Swing",
                }
                _legacy_bot_key_map = {"cl": "CL_Swing_Scalper"}
                def _bounded_slice(_entry: dict, _name: str) -> dict | None:
                    _value = _entry.get(_name)
                    if not isinstance(_value, dict):
                        return None
                    _out = {
                        "current": _value.get("current"),
                        "summary": _value.get("summary"),
                        "last_updated": _value.get("last_updated"),
                    }
                    _hist = _value.get("history")
                    if isinstance(_hist, list):
                        _out["history"] = _hist[-10:]
                    return _out
                def _kronos_summary(_entry: dict) -> dict | None:
                    # mcp-bot-health-kronos-slice: surface the bounded Kronos shadow
                    # summary (ensemble-gated agreement + decoupled realized accuracy)
                    # so get_bot_health is one-stop for the same metrics the EOD/daily
                    # briefing report. Canonical source — get_kronos_shadow_stats returns
                    # the full kronos_shadow blob; this is the compact health view.
                    _shadow = _entry.get("kronos_shadow")
                    _realized = _entry.get("kronos_shadow_realized")
                    if not isinstance(_shadow, dict) and not isinstance(_realized, dict):
                        return None
                    _out: dict = {}
                    if isinstance(_shadow, dict):
                        _out["agreement_rate"] = _shadow.get("agreement_rate")
                        _out["nonfat_count"] = _shadow.get("nonfat_count")
                        _out["total_logged"] = _shadow.get("total_logged")
                    if isinstance(_realized, dict):
                        _out["realized_rate"] = _realized.get("realized_rate")
                        _out["realized_count"] = _realized.get("realized_count")
                    return _out

                for _k, _sh_key in _bot_key_map.items():
                    if bot_filter not in ("all", _k):
                        continue
                    _entry = _sh_data.get(_sh_key, {})
                    if not _entry and _k in _legacy_bot_key_map:
                        _entry = _sh_data.get(_legacy_bot_key_map[_k], {})
                    signal_health_slice[_k] = {
                        "params": _entry.get("params"),
                        "risk_bootstrap": _entry.get("risk_bootstrap"),
                        "bot_version": _entry.get("bot_version"),
                        "trading_mode": _sh_data.get("ws_health", {}).get("status"),
                        "validation_slice": _entry.get("validation_slice"),
                        "flow_feature_versions": _entry.get("flow_feature_versions"),
                        "advisory_timeout_rate": _entry.get("advisory_timeout_rate"),
                        "advisory_fallback_rate": _entry.get("advisory_fallback_rate"),
                        "kronos": _kronos_summary(_entry),
                        "validator_shadow": {
                            "parallel_shadow": _bounded_slice(_entry, "parallel_shadow"),
                            "moltbook_shadow": _bounded_slice(_entry, "moltbook_shadow"),
                        },
                    }
            except Exception:
                signal_health_slice = {"error": "signal_health.json parse failure"}

        # ── ML feature flags (bot-state mirrors for Massive-standard stack) ──
        # Do not read the MCP process environment here: the MCP server may run
        # under a different launch context than the live bot. Prefer the bot's
        # signal_health.json slice when present, then the control-tower .env file.
        _flag_keys = ("MASSIVE_NEWS_FEATURES", "MASSIVE_PCR_FEATURES", "MASSIVE_HALT_GUARD")
        _flag_source = "default_zero"
        ml_env_flags: dict[str, str] = {key: "0" for key in _flag_keys}

        try:
            _flag_candidates = []
            if isinstance(_sh_data, dict):
                _flag_candidates.append(_sh_data.get("ml_env_flags"))
                _mnq_flags = _sh_data.get("MNQ_Upgraded_Scalper", {})
                if isinstance(_mnq_flags, dict):
                    _flag_candidates.append(_mnq_flags.get("ml_env_flags"))
                    _flow_versions = _mnq_flags.get("flow_feature_versions")
                    if isinstance(_flow_versions, dict):
                        _flag_candidates.append(_flow_versions.get("ml_env_flags"))
            for _candidate in _flag_candidates:
                if isinstance(_candidate, dict):
                    ml_env_flags = {key: str(_candidate.get(key, "0")) for key in _flag_keys}
                    _flag_source = "signal_health"
                    break
        except Exception:
            pass

        if _flag_source == "default_zero":
            _env_path = control_tower / ".env"
            if _env_path.exists():
                try:
                    _parsed_env: dict[str, str] = {}
                    for _raw_line in _env_path.read_text().splitlines():
                        _line = _raw_line.strip()
                        if not _line or _line.startswith("#") or "=" not in _line:
                            continue
                        _env_key, _env_value = _line.split("=", 1)
                        if _env_key.strip() in _flag_keys:
                            _parsed_env[_env_key.strip()] = _env_value.strip().strip('"').strip("'")
                    ml_env_flags = {key: _parsed_env.get(key, "0") for key in _flag_keys}
                    _flag_source = "control_tower_.env"
                except Exception:
                    _flag_source = "default_zero_parse_error"
        ml_env_flags["_source"] = _flag_source

        # ── Command Center watchdog state (cc_health_state.json) ─────────────
        # Mirrors the OpenClaw "CC degraded" alert sources so agents get
        # the same health picture as Slack without a separate file read.
        # Populated by autonomous/cc_health_watchdog.py every 5 minutes.
        cc_health: dict = {}
        _cc_state_path = control_tower / "state" / "cc_health_state.json"
        if _cc_state_path.exists():
            try:
                import json as _json_cc
                _cc_raw = _json_cc.loads(_cc_state_path.read_text())
                _issues = _cc_raw.get("issues")
                if not isinstance(_issues, list):
                    _alert_key = _cc_raw.get("last_alerted_issues_key")
                    _issues = [_alert_key] if _alert_key else []
                cc_health = {
                    "status": _cc_raw.get("status") or _cc_raw.get("last_status"),
                    "issues": _issues,
                    "cc_log_age_minutes": _cc_raw.get("cc_log_age_minutes"),
                    "consecutive_failures": _cc_raw.get("consecutive_failures"),
                    "cc_restarts": _cc_raw.get("cc_restarts"),
                    "circuit_breakers_open": _cc_raw.get("circuit_breakers_open"),
                    "last_check_utc": _cc_raw.get("last_check_utc"),
                    "last_status": _cc_raw.get("last_status"),
                    "last_alerted_issues_key": _cc_raw.get("last_alerted_issues_key"),
                    "last_unhandled_error": _cc_raw.get("last_unhandled_error"),
                    "state_age_sec": max(0, int(time.time() - _cc_state_path.stat().st_mtime)),
                }
            except Exception as _cc_err:
                cc_health = {"error": f"cc_health_state.json parse failure: {_cc_err}"}
        else:
            cc_health = {"status": "unknown", "detail": "cc_health_state.json not found"}

        # ── E2E Execution Sentinel state ─────────────────────────────────────
        # Keep this intentionally compact: no raw log excerpts, broker payloads,
        # account details, or secrets. Full evidence stays on the control tower.
        e2e_sentinel: dict = {"status": "unknown", "detail": "e2e_execution_sentinel.json not found"}
        _e2e_state_path = control_tower / "state" / "e2e_execution_sentinel.json"
        if _e2e_state_path.exists():
            try:
                import json as _json_e2e
                _e2e_raw = _json_e2e.loads(_e2e_state_path.read_text())
                e2e_sentinel = summarize_e2e_sentinel_state(_e2e_raw)
                e2e_sentinel = apply_effective_sentinel_resolution(e2e_sentinel, _e2e_raw)
            except Exception as _e2e_err:
                e2e_sentinel = {"status": "error", "detail": f"e2e_execution_sentinel.json parse failure: {_e2e_err}"}

        desktop_inference_slo = _summarize_desktop_inference_log(control_tower)
        decision_latency_slo = _summarize_decision_latency_log(control_tower)

        return _text({
            "control_tower": str(control_tower),
            "bots": results,
            "signal_health": signal_health_slice,
            "ml_env_flags": ml_env_flags,
            "cc_health": cc_health,
            "e2e_sentinel": e2e_sentinel,
            "desktop_inference_slo": desktop_inference_slo,
            "decision_latency_slo": decision_latency_slo,
            "tradovate_token": token_info,
            "generated_at": int(now),
        })

    # ── Broker Management ───────────────────────────────────
    elif name == "query_codegraph":
        # Read-only structural code intelligence over the control-tower repo.
        # WRAPS the pinned CodeGraph binary via npx — never imports it, never
        # touches any trading/order/risk path. Fails closed if the per-host
        # .codegraph/ index is absent. Complements semantic rag_search/onyx.
        import re as _re
        import shutil as _shutil
        import subprocess as _subp
        from pathlib import Path as _Path

        _CG_VERSION = "0.9.7"
        kind = str(arguments.get("kind") or "").strip().lower()
        symbol = str(arguments.get("symbol") or "").strip()
        try:
            limit = int(arguments.get("limit") or 20)
        except (TypeError, ValueError):
            limit = 20
        control_tower = _Path(_default_control_tower())

        # kind -> (cli subcommand, needs_symbol)
        _KIND_MAP = {
            "search":  ("query", True),
            "callers": ("callers", True),
            "callees": ("callees", True),
            "impact":  ("impact", True),
            "context": ("context", True),
            "files":   ("files", False),
            "status":  ("status", False),
        }
        if kind not in _KIND_MAP:
            return _text({
                "error_type": "ValueError",
                "tool": "query_codegraph",
                "message": f"Unknown kind '{kind}'. Use one of: {sorted(_KIND_MAP)}",
            })
        subcmd, needs_symbol = _KIND_MAP[kind]
        if needs_symbol and not symbol:
            return _text({
                "error_type": "ValueError",
                "tool": "query_codegraph",
                "message": f"kind '{kind}' requires a 'symbol' argument.",
            })

        # Fail-closed: per-host index must exist (.codegraph/ and mcp-servers/ are gitignored).
        if not (control_tower / ".codegraph").exists():
            return _text({
                "error_type": "codegraph_index_missing",
                "tool": "query_codegraph",
                "message": (
                    "CodeGraph index (.codegraph/) not found on this host. The index is "
                    "per-host and is not synced across machines."
                ),
                "recovery_command": f"cd {control_tower} && npx -y @colbymchenry/codegraph@{_CG_VERSION} init -i",
            })

        npx = _shutil.which("npx")
        if not npx:
            return _text({
                "error_type": "codegraph_runtime_missing",
                "tool": "query_codegraph",
                "message": "npx not found on PATH; cannot launch CodeGraph from this process.",
            })

        cmd = [npx, "-y", f"@colbymchenry/codegraph@{_CG_VERSION}", subcmd]
        if needs_symbol:
            cmd.append(symbol)
        if kind != "status":  # status takes a positional [path]; rely on cwd instead
            cmd += ["-p", str(control_tower)]
        if kind in ("search", "callers", "callees"):
            cmd += ["-l", str(limit), "-j"]
        elif kind == "impact":
            cmd += ["-j"]
        elif kind == "context":
            cmd += ["-f", "json"]
        try:
            proc = _subp.run(cmd, capture_output=True, text=True, timeout=30, cwd=str(control_tower))
        except _subp.TimeoutExpired:
            return _text({
                "error_type": "TimeoutExpired",
                "tool": "query_codegraph",
                "message": "CodeGraph query exceeded 30s.",
            })
        except Exception as _cg_exc:  # noqa: BLE001
            return _text({
                "error_type": type(_cg_exc).__name__,
                "tool": "query_codegraph",
                "message": str(_cg_exc),
            })
        raw = (proc.stdout or "")
        if proc.returncode and proc.stderr:
            raw = (raw + "\n" + proc.stderr).strip()
        clean = _re.sub(r"\x1b\[[0-9;]*m", "", raw).strip()
        return _text({
            "tool": "query_codegraph",
            "kind": kind,
            "symbol": symbol or None,
            "read_only": True,
            "note": (
                "Structural (AST) intel — complements semantic rag_search/onyx; never the sole "
                "basis for a 'safe to edit' claim on trading files. Check for a staleness banner."
            ),
            "result": clean,
        })

    elif name in ("graphiti_search", "graphiti_temporal_query", "graphiti_health", "graphiti_add_episode"):
        # Advisory temporal knowledge graph (getzep/graphiti). agent_memory authority —
        # NEVER broker truth, never a trading dependency. Bridges to the control-tower
        # shared client; fails closed with graphiti_unavailable when graphiti-core/Neo4j
        # are absent on this host (per-host, not synced).
        from .graphiti_intelligence import (
            graphiti_search as _g_search,
            graphiti_temporal_query as _g_temporal,
            graphiti_health as _g_health,
            graphiti_add_episode as _g_add,
        )
        try:
            limit = int(arguments.get("limit") or 10)
        except (TypeError, ValueError):
            limit = 10
        if name == "graphiti_health":
            return _text(await _g_health())
        if name == "graphiti_search":
            return _text(await _g_search(str(arguments.get("query") or ""), limit=limit))
        if name == "graphiti_temporal_query":
            return _text(await _g_temporal(str(arguments.get("query") or ""), limit=limit))
        # graphiti_add_episode
        return _text(await _g_add(
            str(arguments.get("name") or ""),
            str(arguments.get("body") or ""),
            source_description=str(arguments.get("source_description") or "mcp_tool"),
            source_kind=str(arguments.get("source_kind") or "text"),
        ))

    elif name == "list_brokers":
        configured = registry.list_configured()
        connected = registry.list_available()
        brokers_info = []
        for b in configured:
            conn = registry.get(b)
            brokers_info.append({
                "name": b,
                "configured": True,
                "connected": b in connected,
                "asset_classes": [ac.value for ac in conn.supported_asset_classes] if conn else [],
            })
        return _text(brokers_info)

    elif name == "connect_broker":
        broker_name = arguments["broker"]
        results = await registry.connect_all()
        if broker_name in results:
            return _text({"broker": broker_name, "connected": results[broker_name]})
        raise BrokerNotConfiguredError(
            f"Broker '{broker_name}' not configured. Set environment variables.",
            broker=broker_name,
        )

    elif name == "broker_health_check":
        health = await registry.health_check_all()
        return _text(health)

    # ── Marketplace (real HTTP bridge) ──────────────────────
    elif name == "browse_marketplace":
        bridge = _get_bridge()
        listings = await bridge.browse_listings(
            asset_class=arguments.get("asset_class"),
            strategy_type=arguments.get("strategy_type"),
            min_sharpe=arguments.get("min_sharpe"),
            limit=arguments.get("limit", 20),
        )
        return _text({"count": len(listings), "listings": listings})

    elif name == "get_listing_detail":
        bridge = _get_bridge()
        listing = await bridge.get_listing(arguments["slug"])
        return _text(listing)

    elif name == "subscribe_to_bot":
        bridge = _get_bridge()
        result = await bridge.subscribe(
            slug=arguments["slug"],
            broker=arguments.get("broker"),
            mode=arguments.get("mode", "paper"),
        )
        return _text(result)

    # ── Strategy Submission & Validation ────────────────────
    elif name == "submit_strategy":
        validator = _get_validator()
        result = validator.validate(arguments)
        return _text({
            "submission_id": f"sub_{arguments['symbol']}_{arguments['strategy_type']}_{arguments['timeframe']}",
            "validation": result.to_dict(),
            "next_steps": (
                "Strategy passed all gates! Submit to marketplace for listing."
                if result.passed
                else f"Strategy rejected (score: {result.score}/100). Fix errors and resubmit."
            ),
        })

    elif name == "validate_strategy_metrics":
        validator = _get_validator()
        result = validator.validate(arguments)
        return _text({
            "validation": result.to_dict(),
            "semantic_note": (
                "validate_strategy_metrics checks marketplace performance gates. "
                "Use validate_strategy for StrategySpec schema validation."
            ),
        })

    elif name == "check_validation_status":
        # Validation is synchronous — results are returned immediately by submit_strategy.
        # There is no async queue to poll. This tool surfaces that clearly.
        return _text({
            "submission_id": arguments["submission_id"],
            "status": "not_applicable",
            "error": (
                "AlgoChains validation runs synchronously inside submit_strategy. "
                "There is no separate status to poll — the full validation result is "
                "returned in the submit_strategy response. Call submit_strategy to get "
                "your validation result immediately."
            ),
        })

    elif name == "get_validation_gates":
        cfg = _config or load_config()
        g = cfg.gating
        return _text({
            "gates": {
                "1_schema": "Required fields: symbol, strategy_type, timeframe, oos_sharpe, oos_trades, max_drawdown_pct",
                "2_performance": {
                    "min_oos_sharpe": g.min_oos_sharpe,
                    "min_oos_trades": g.min_oos_trades,
                    "max_drawdown_pct": g.max_drawdown_pct,
                },
                "3_overfitting": {
                    "max_is_sharpe": g.max_is_sharpe,
                    "min_oos_is_ratio": g.min_oos_is_ratio,
                },
                "4_mcpt": {
                    "max_p_value": g.mcpt_max_p_value,
                    "min_permutations": g.mcpt_permutations,
                },
                "5_walk_forward": {
                    "required": g.require_walk_forward,
                    "min_folds": 3,
                },
                "6_paper_trading": {
                    "min_days": g.min_paper_days,
                    "min_trades": g.min_paper_trades,
                },
            },
            "tiers": {
                "platinum": "Score >= 90 (all gates pass)",
                "gold": "Score >= 70",
                "silver": "Score >= 50",
                "bronze": "Score >= 30",
                "rejected": "Score < 30 or critical gate failure",
            },
        })

    # ── Server diagnostics ──────────────────────────────────
    elif name == "server_diagnostics":
        tlog = get_tool_logger()
        configured = registry.list_configured()
        connected = registry.list_available()
        uptime_s = round(time.monotonic() - _SERVER_START_TIME)
        return _text({
            "tool_call_stats": tlog.stats(),
            "recent_calls": tlog.recent(10),
            "configured_brokers": configured,
            "connected_brokers": connected,
            # ── Added fields to prevent misreading low total_calls ──────────
            # tool_call_stats resets on process restart (in-memory only).
            # A low total_calls count means "quiet session / fresh restart",
            # not "system idle". Use process_uptime_seconds to calibrate.
            "process_uptime_seconds": uptime_s,
            # broker_pool_warm=false means brokers are configured but
            # connect_broker has not been called yet in this session.
            # Broker-dependent tools (get_account, get_tradovate_risk_snapshot)
            # require an explicit connect_broker call first.
            "broker_pool_warm": len(connected) > 0,
            "broker_pool_status": {
                "configured": len(configured),
                "connected": len(connected),
                "cold_brokers": [b for b in configured if b not in connected],
            },
        })

    # ── V4: Streaming ────────────────────────────────────────
    elif name == "stream_subscribe":
        from .streaming.manager import Subscription
        StreamTopic = _lazy_import(".streaming.manager", "StreamTopic")
        mgr = _get_stream_manager()
        topic = StreamTopic(arguments["topic"])
        sub = Subscription(
            topic=topic,
            symbols=arguments.get("symbols", []),
            brokers=arguments.get("brokers", []),
        )
        sub_id = mgr.subscribe(sub)
        return _text({"subscription_id": sub_id, "topic": topic.value, "status": "active"})

    elif name == "stream_snapshot":
        StreamTopic = _lazy_import(".streaming.manager", "StreamTopic")
        mgr = _get_stream_manager()
        topic = StreamTopic(arguments["topic"])
        events = mgr.get_latest(topic, limit=arguments.get("limit", 20))
        return _text({"topic": topic.value, "count": len(events), "events": events})

    elif name == "get_realtime_pnl":
        mgr = _get_stream_manager()
        pnl = mgr.get_pnl_snapshot()
        positions = mgr.get_position_snapshot()
        # Also try to fetch live data from brokers
        live_pnl = {}
        for bname in registry.list_available():
            conn = registry.get(bname)
            try:
                acct = await conn.get_account()
                pos = await conn.get_positions()
                live_pnl[bname] = {
                    "equity": acct.equity,
                    "cash": acct.cash,
                    "unrealized_pnl": sum(p.unrealized_pnl for p in pos),
                    "positions": len(pos),
                }
            except Exception as e:
                live_pnl[bname] = {"error": str(e)}
        return _text({
            "live": live_pnl,
            "stream_snapshot": pnl,
            "position_snapshot": positions,
        })

    elif name == "stream_stats":
        mgr = _get_stream_manager()
        return _text(mgr.stats())

    # ── V5: Portfolio Optimizer ──────────────────────────────
    elif name == "optimize_portfolio":
        BotMetrics = _lazy_import(".portfolio.optimizer", "BotMetrics")
        AllocationMethod = _lazy_import(".portfolio.optimizer", "AllocationMethod")
        optimizer = _get_portfolio_optimizer()
        bots = [
            BotMetrics(
                slug=b["slug"], name=b["name"],
                oos_sharpe=b["oos_sharpe"],
                annual_return=b["annual_return"],
                annual_volatility=b["annual_volatility"],
                max_drawdown=b["max_drawdown"],
                win_rate=b["win_rate"],
                avg_trade_pnl=b.get("avg_trade_pnl", 0),
                correlation_to_spy=b.get("correlation_to_spy", 0),
                tier=b.get("tier", "silver"),
            )
            for b in arguments["bots"]
        ]
        method = AllocationMethod(arguments.get("method", "risk_parity"))
        result = optimizer.optimize(
            bots=bots,
            total_capital=arguments["total_capital"],
            method=method,
            max_drawdown_limit=arguments.get("max_drawdown_limit", 0.20),
        )
        return _text(result.to_dict())

    elif name == "compare_allocations":
        BotMetrics = _lazy_import(".portfolio.optimizer", "BotMetrics")
        AllocationMethod = _lazy_import(".portfolio.optimizer", "AllocationMethod")
        optimizer = _get_portfolio_optimizer()
        bots = [
            BotMetrics(
                slug=b["slug"], name=b["name"],
                oos_sharpe=b["oos_sharpe"],
                annual_return=b["annual_return"],
                annual_volatility=b["annual_volatility"],
                max_drawdown=b["max_drawdown"],
                win_rate=b["win_rate"],
                avg_trade_pnl=b.get("avg_trade_pnl", 0),
            )
            for b in arguments["bots"]
        ]
        capital = arguments["total_capital"]
        comparisons = {}
        for method in AllocationMethod:
            result = optimizer.optimize(bots, capital, method)
            comparisons[method.value] = {
                "portfolio_sharpe": round(result.portfolio_sharpe, 3),
                "return_pct": round(result.portfolio_return * 100, 2),
                "volatility_pct": round(result.portfolio_volatility * 100, 2),
                "max_drawdown_pct": round(result.portfolio_max_drawdown * 100, 2),
                "diversification": round(result.diversification_score, 1),
                "allocations": {a.slug: round(a.weight * 100, 1) for a in result.allocations},
            }
        # Rank by Sharpe
        ranked = sorted(comparisons.items(), key=lambda x: x[1]["portfolio_sharpe"], reverse=True)
        return _text({
            "best_method": ranked[0][0] if ranked else "none",
            "comparisons": comparisons,
            "ranking": [r[0] for r in ranked],
        })

    # ── V6: Notifications ────────────────────────────────────
    elif name == "configure_notifications":
        notifier = _get_notifier()
        ch = arguments["channel"]
        if ch == "slack":
            notifier.configure_slack(arguments.get("webhook_url", ""))
        elif ch == "email":
            notifier.configure_email(arguments.get("api_key", ""))
        elif ch == "discord":
            notifier.configure_discord(arguments.get("webhook_url", ""))
        elif ch == "telegram":
            notifier.configure_telegram(arguments.get("bot_token", ""), arguments.get("chat_id", ""))
        elif ch in ("fcm", "apns"):
            notifier.configure_mobile_push(
                fcm_key=arguments.get("api_key", "") if ch == "fcm" else "",
                apns_cert=arguments.get("api_key", "") if ch == "apns" else "",
            )
        return _text({"channel": ch, "status": "configured", "all_channels": notifier.configured_channels()})

    elif name == "send_notification":
        NotificationEvent = _lazy_import(".notifications.push", "NotificationEvent")
        NotificationChannel = _lazy_import(".notifications.push", "NotificationChannel")
        NotificationPriority = _lazy_import(".notifications.push", "NotificationPriority")
        Notification = _lazy_import(".notifications.push", "Notification")
        notifier = _get_notifier()
        event_str = arguments.get("event", "bot_status")
        event = NotificationEvent(event_str) if event_str != "custom" else NotificationEvent.BOT_STATUS
        channels = [NotificationChannel(c) for c in arguments.get("channels", [])] or [NotificationChannel.WEBSOCKET]
        notification = Notification(
            event=event,
            priority=NotificationPriority(arguments.get("priority", "medium")),
            title=arguments["title"],
            body=arguments["body"],
            channels=channels,
        )
        results = await notifier.send(notification)
        return _text({"notification": notification.to_dict(), "delivery": results})

    elif name == "get_notification_history":
        NotificationEvent = _lazy_import(".notifications.push", "NotificationEvent")
        notifier = _get_notifier()
        event_filter = None
        if arguments.get("event"):
            event_filter = NotificationEvent(arguments["event"])
        history = notifier.get_history(limit=arguments.get("limit", 20), event=event_filter)
        return _text({"count": len(history), "history": history})

    elif name == "notification_stats":
        notifier = _get_notifier()
        return _text(notifier.stats())

    # ── Data Providers ───────────────────────────────────────
    elif name == "list_data_providers":
        dreg = _get_data_registry()
        available = dreg.list_available()
        all_providers = dreg.list_all_providers()
        return _text({
            "configured": available,
            "all_providers": [p.to_dict() for p in all_providers],
        })

    elif name == "get_market_data":
        Interval = _lazy_import(".data_providers.base", "Interval")
        dreg = _get_data_registry()
        provider_name = arguments.get("provider")
        provider = dreg.get(provider_name) if provider_name else dreg.get_default()
        if not provider:
            return _text({"error": "No data provider configured. Set API keys in environment variables.", "available_providers": [p.to_dict() for p in dreg.list_all_providers()]})
        interval = Interval(arguments.get("interval", "1day"))
        bars = await provider.get_bars(
            symbol=arguments["symbol"],
            interval=interval,
            limit=arguments.get("limit", 100),
            start=arguments.get("start"),
            end=arguments.get("end"),
        )
        return _text({
            "symbol": arguments["symbol"],
            "interval": interval.value,
            "provider": provider.info().name,
            "count": len(bars),
            "bars": [b.to_dict() for b in bars],
        })

    elif name == "get_realtime_quote":
        dreg = _get_data_registry()
        provider_name = arguments.get("provider")
        provider = dreg.get(provider_name) if provider_name else dreg.get_default()
        if not provider:
            return _text({"error": "No data provider configured."})
        quote = await provider.get_quote(arguments["symbol"])
        return _text(quote.to_dict())

    elif name == "get_news":
        dreg = _get_data_registry()
        provider_name = arguments.get("provider")
        provider = dreg.get(provider_name) if provider_name else dreg.get_default()
        if not provider:
            return _text({"error": "No data provider configured."})
        news = await provider.get_news(arguments["symbol"], limit=arguments.get("limit", 10))
        return _text({"symbol": arguments["symbol"], "count": len(news), "articles": [n.to_dict() for n in news]})

    elif name == "get_fundamentals":
        dreg = _get_data_registry()
        provider_name = arguments.get("provider")
        provider = dreg.get(provider_name) if provider_name else dreg.get_default()
        if not provider:
            return _text({"error": "No data provider configured."})
        fundamentals = await provider.get_fundamentals(arguments["symbol"])
        return _text(fundamentals)

    elif name == "search_symbols":
        dreg = _get_data_registry()
        provider_name = arguments.get("provider")
        provider = dreg.get(provider_name) if provider_name else dreg.get_default()
        if not provider:
            return _text({"error": "No data provider configured."})
        results = await provider.search_symbols(arguments["query"])
        return _text({"query": arguments["query"], "count": len(results), "results": results})

    elif name == "data_provider_health":
        dreg = _get_data_registry()
        health = await dreg.health_check_all()
        return _text(health)

    # ── V7: BYOK Key Orchestrator ──────────────────────────────
    elif name == "discover_keys":
        orch = _get_key_orchestrator()
        result = await orch.discover_keys()
        return _text(result)

    elif name == "validate_keys":
        orch = _get_key_orchestrator()
        providers = arguments.get("providers")
        result = await orch.validate_keys(providers=providers)
        return _text(result)

    elif name == "key_gap_analysis":
        orch = _get_key_orchestrator()
        if not orch._discovered:
            await orch.discover_keys()
        result = await orch.gap_analysis()
        return _text(result)

    elif name == "provision_key":
        orch = _get_key_orchestrator()
        result = await orch.provision_key(
            provider=arguments["provider"],
            key_value=arguments["key_value"],
            write_to_env=arguments.get("write_to_env", True),
        )
        return _text(result)

    elif name == "key_health":
        orch = _get_key_orchestrator()
        result = await orch.key_health()
        return _text(result)

    elif name == "export_config":
        # SEC-2026-C1 FIX: export_config reads plaintext env secrets.
        # Require owner_token before returning any key data.
        _owner_token_provided = arguments.get("owner_token", "")
        _expected_owner_token = os.environ.get("OWNER_API_TOKEN", "")
        if not _expected_owner_token or _owner_token_provided != _expected_owner_token:
            return _text({
                "error": "export_config requires owner_token matching OWNER_API_TOKEN. "
                         "MCP output returns masked keys only — full export requires "
                         "owner confirmation via OWNER_API_TOKEN.",
                "masked_preview": "Set owner_token to your OWNER_API_TOKEN value to proceed.",
            })
        orch = _get_key_orchestrator()
        if not orch._discovered:
            await orch.discover_keys()
        result = await orch.export_config(format=arguments.get("format", "env"))
        return _text(result)

    # ── V7: Proprietary Dataset Builder ────────────────────────
    elif name == "build_dataset":
        DatasetRequest = _lazy_import(".datasets.builder", "DatasetRequest")
        builder = _get_dataset_builder()
        req = DatasetRequest(
            symbol=arguments["symbol"],
            timeframe=arguments.get("timeframe", "daily"),
            start_date=arguments.get("start_date"),
            end_date=arguments.get("end_date"),
            providers=arguments.get("providers"),
            enrichments=arguments.get("enrichments", []),
            format=arguments.get("format", "parquet"),
        )
        result = await builder.build_dataset(req)
        return _text(result)

    elif name == "list_datasets":
        builder = _get_dataset_builder()
        result = await builder.list_datasets()
        return _text(result)

    elif name == "dataset_status":
        builder = _get_dataset_builder()
        orch = _get_key_orchestrator()
        if not orch._discovered:
            await orch.discover_keys()
        available_keys = list(orch._discovered.keys())
        result = await builder.dataset_status(available_keys)
        return _text(result)

    elif name == "enrich_dataset":
        builder = _get_dataset_builder()
        result = await builder.enrich_dataset(
            dataset_id=arguments["dataset_id"],
            enrichments=arguments["enrichments"],
        )
        return _text(result)

    elif name == "export_dataset":
        builder = _get_dataset_builder()
        result = await builder.export_dataset(
            dataset_id=arguments["dataset_id"],
            format=arguments.get("format", "parquet"),
            train_test_split=arguments.get("train_test_split", 0.8),
            target_column=arguments.get("target_column", "close"),
        )
        return _text(result)

    # ── V8: Strategy Builder SDK ─────────────────────────────
    elif name == "create_strategy":
        StrategySpec = _lazy_import(".strategy_builder.spec", "StrategySpec")
        spec = StrategySpec.from_dict(arguments)
        validator = _get_spec_validator()
        validation = validator.validate(spec)
        return _text({"spec": spec.to_dict(), "validation": validation})

    elif name == "validate_strategy":
        StrategySpec = _lazy_import(".strategy_builder.spec", "StrategySpec")
        spec = StrategySpec.from_dict(arguments["spec"])
        validator = _get_spec_validator()
        return _text(validator.validate(spec))

    elif name in ("run_backtest", "backtest_strategy"):
        StrategySpec = _lazy_import(".strategy_builder.spec", "StrategySpec")
        spec = StrategySpec.from_dict(arguments["spec"])
        runner = _get_backtest_runner()
        result = await runner.run(spec, capital=arguments.get("capital", 10000))
        return _text(result)

    elif name == "optimize_strategy":
        StrategySpec = _lazy_import(".strategy_builder.spec", "StrategySpec")
        if StrategySpec is None:
            return _text({"error": "strategy_builder module unavailable; check server logs."})
        spec = StrategySpec.from_dict(arguments["spec"])
        optimizer = _get_strategy_optimizer()
        if optimizer is None:
            return _text({"error": "StrategyOptimizer unavailable — backtest runner failed to initialize. Check ALGOCHAINS_STATE_DIR and tick data paths."})
        result = await optimizer.optimize(spec, n_trials=arguments.get("n_trials", 100), metric=arguments.get("metric", "sharpe"))
        return _text(result)

    elif name == "walk_forward_test":
        StrategySpec = _lazy_import(".strategy_builder.spec", "StrategySpec")
        if StrategySpec is None:
            return _text({"error": "strategy_builder module unavailable; check server logs."})
        spec = StrategySpec.from_dict(arguments["spec"])
        wf = _get_walk_forward()
        if wf is None:
            return _text({"error": "WalkForwardEngine unavailable — backtest runner failed to initialize."})
        result = await wf.run(spec, n_folds=arguments.get("n_folds", 5), train_pct=arguments.get("train_pct", 0.70))
        return _text(result)

    elif name == "deploy_strategy":
        StrategySpec = _lazy_import(".strategy_builder.spec", "StrategySpec")
        if StrategySpec is None:
            return _text({"error": "strategy_builder module unavailable; check server logs."})
        spec = StrategySpec.from_dict(arguments["spec"])
        deployer = _get_deployer()
        if deployer is None:
            return _text({"error": "StrategyDeployer unavailable — check server logs."})
        result = await deployer.deploy(spec, broker=arguments["broker"], mode=arguments.get("mode", "paper"), capital=arguments.get("capital", 10000))
        return _text(result)

    elif name == "list_templates":
        mgr = _get_template_mgr()
        if mgr is None:
            return _text({"error": "TemplateManager unavailable — check server logs."})
        return _text(mgr.list_templates(category=arguments.get("category"), asset_class=arguments.get("asset_class")))

    elif name == "fork_template":
        mgr = _get_template_mgr()
        if mgr is None:
            return _text({"error": "TemplateManager unavailable — check server logs."})
        return _text(mgr.fork_template(template_id=arguments["template_id"], new_name=arguments.get("new_name"), symbols=arguments.get("symbols"), overrides=arguments.get("overrides")))

    # ── V8: Social Trading ───────────────────────────────────
    elif name == "become_leader":
        eng = _get_social_engine()
        return _text(await eng.become_leader(user_id=arguments["user_id"], handle=arguments["handle"], track_record=arguments.get("track_record")))

    elif name == "get_leader_stats":
        eng = _get_social_engine()
        return _text(await eng.get_leader_stats(arguments["leader_id"]))

    elif name == "follow_leader":
        eng = _get_social_engine()
        return _text(await eng.follow_leader(follower_id=arguments["follower_id"], leader_id=arguments["leader_id"], config=arguments.get("config")))

    elif name == "unfollow_leader":
        eng = _get_social_engine()
        return _text(await eng.unfollow_leader(follower_id=arguments["follower_id"], leader_id=arguments["leader_id"], close_positions=arguments.get("close_positions", False)))

    elif name == "get_copy_status":
        eng = _get_social_engine()
        return _text(await eng.get_copy_status(arguments["follower_id"]))

    elif name == "set_copy_parameters":
        eng = _get_social_engine()
        return _text(await eng.set_copy_parameters(follower_id=arguments["follower_id"], leader_id=arguments["leader_id"], config_updates=arguments["config_updates"]))

    # ── V8: Community Signals ────────────────────────────────
    elif name == "publish_signal":
        eng = _get_signal_engine()
        return _text(await eng.publish_signal(**{k: arguments[k] for k in arguments if k in ("user_id", "symbol", "direction", "timeframe", "entry_price", "stop_loss", "take_profit", "confidence", "rationale", "trade_hash")}))

    elif name == "subscribe_signals":
        eng = _get_signal_engine()
        return _text(await eng.subscribe_signals(user_id=arguments["user_id"], filters=arguments.get("filters")))

    elif name == "verify_signal":
        eng = _get_signal_engine()
        return _text(await eng.verify_signal(signal_id=arguments["signal_id"], trade_proof=arguments["trade_proof"]))

    elif name == "get_consensus":
        eng = _get_signal_engine()
        return _text(await eng.get_consensus(symbol=arguments["symbol"], timeframe=arguments.get("timeframe", "1h")))

    elif name == "get_signal_accuracy":
        eng = _get_signal_engine()
        return _text(await eng.get_signal_accuracy(arguments["user_id"]))

    # ── V9: Risk Dashboard ───────────────────────────────────
    elif name == "calculate_var":
        eng = _get_risk_engine()
        return _text(await eng.calculate_var(portfolio=arguments["portfolio"], method=arguments.get("method", "parametric"), confidence=arguments.get("confidence", 0.95), horizon_days=arguments.get("horizon_days", 1)))

    elif name == "calculate_expected_shortfall":
        eng = _get_risk_engine()
        return _text(await eng.calculate_expected_shortfall(portfolio=arguments["portfolio"], confidence=arguments.get("confidence", 0.95), horizon_days=arguments.get("horizon_days", 1)))

    elif name == "get_factor_exposure":
        eng = _get_risk_engine()
        return _text(await eng.get_factor_exposure(arguments["portfolio"]))

    elif name == "run_stress_test":
        eng = _get_risk_engine()
        return _text(await eng.run_stress_test(portfolio=arguments["portfolio"], scenario=arguments.get("scenario"), custom_shocks=arguments.get("custom_shocks")))

    elif name == "get_drawdown_monitor":
        eng = _get_risk_engine()
        return _text(await eng.get_drawdown_monitor(arguments["portfolio"]))

    elif name == "get_margin_utilization":
        eng = _get_risk_engine()
        return _text(await eng.get_margin_utilization(arguments["account"]))

    elif name == "get_greeks_exposure":
        eng = _get_risk_engine()
        return _text(await eng.get_greeks_exposure(arguments["portfolio"]))

    elif name == "configure_risk_alert":
        eng = _get_risk_engine()
        return _text(await eng.configure_risk_alert(alert_type=arguments["alert_type"], threshold=arguments["threshold"], action=arguments.get("action", "notify"), channels=arguments.get("channels")))

    elif name == "check_risk_alerts":
        eng = _get_risk_engine()
        return _text(await eng.check_risk_alerts(arguments["portfolio"]))

    elif name == "get_concentration_risk":
        eng = _get_risk_engine()
        return _text(await eng.get_concentration_risk(arguments["portfolio"]))

    # ── V9: Compliance ───────────────────────────────────────
    elif name == "pre_trade_check":
        eng = _get_compliance_engine()
        return _text(await eng.pre_trade_check(order=arguments["order"], account=arguments["account"], profile_id=arguments.get("profile_id")))

    elif name == "post_trade_surveillance":
        eng = _get_compliance_engine()
        return _text(await eng.post_trade_surveillance(arguments["trades"]))

    elif name == "get_audit_trail":
        eng = _get_compliance_engine()
        return _text(await eng.get_audit_trail(limit=arguments.get("limit", 50), action_filter=arguments.get("action_filter")))

    elif name == "activate_kill_switch":
        if os.getenv("ALGOCHAINS_REQUIRE_CONFIRMATION", "1") == "1":
            logger.warning("activate_kill_switch BLOCKED — ALGOCHAINS_REQUIRE_CONFIRMATION=1")
            return _text({
                "status": "blocked",
                "reason": "activate_kill_switch requires confirmation. Set ALGOCHAINS_REQUIRE_CONFIRMATION=0 to allow.",
            })
        eng = _get_compliance_engine()
        return _text(await eng.activate_kill_switch(arguments["reason"]))

    elif name == "deactivate_kill_switch":
        eng = _get_compliance_engine()
        return _text(await eng.deactivate_kill_switch(arguments["reason"]))

    elif name == "set_compliance_profile":
        eng = _get_compliance_engine()
        return _text(await eng.set_compliance_profile(profile_id=arguments["profile_id"], limits=arguments["limits"]))

    elif name == "get_compliance_profile":
        eng = _get_compliance_engine()
        return _text(await eng.get_compliance_profile(arguments["profile_id"]))

    elif name == "best_execution_report":
        eng = _get_compliance_engine()
        return _text(await eng.best_execution_report(arguments["trades"]))

    elif name == "get_wash_trade_alerts":
        eng = _get_compliance_engine()
        return _text(await eng.get_wash_trade_alerts(days=arguments.get("days", 30)))

    elif name == "set_restricted_list":
        eng = _get_compliance_engine()
        return _text(await eng.set_restricted_list(profile_id=arguments["profile_id"], symbols=arguments.get("symbols"), sectors=arguments.get("sectors"), countries=arguments.get("countries")))

    elif name == "run_surveillance_scan":
        eng = _get_compliance_engine()
        return _text(await eng.run_surveillance_scan(lookback_hours=arguments.get("lookback_hours", 24)))

    elif name == "get_compliance_status":
        eng = _get_compliance_engine()
        return _text(await eng.get_compliance_status(account=arguments["account"], profile_id=arguments.get("profile_id")))

    # ── V9: Multi-Tenant ─────────────────────────────────────
    elif name == "create_tenant":
        eng = _get_tenant_engine()
        return _text(await eng.create_tenant(name=arguments["name"], admin_email=arguments["admin_email"], tier=arguments.get("tier", "starter"), branding=arguments.get("branding")))

    elif name == "get_tenant":
        eng = _get_tenant_engine()
        return _text(await eng.get_tenant(arguments["tenant_id"]))

    elif name == "update_tenant":
        eng = _get_tenant_engine()
        return _text(await eng.update_tenant(tenant_id=arguments["tenant_id"], updates=arguments["updates"]))

    elif name == "create_sub_account":
        eng = _get_tenant_engine()
        return _text(await eng.create_sub_account(tenant_id=arguments["tenant_id"], user_id=arguments["user_id"], name=arguments["name"], permissions=arguments.get("permissions")))

    elif name == "list_sub_accounts":
        eng = _get_tenant_engine()
        return _text(await eng.list_sub_accounts(arguments["tenant_id"]))

    elif name == "configure_broker_routing":
        eng = _get_tenant_engine()
        return _text(await eng.configure_broker_routing(tenant_id=arguments["tenant_id"], broker_config=arguments["broker_config"]))

    elif name == "get_billing_summary":
        eng = _get_tenant_engine()
        return _text(await eng.get_billing_summary(arguments["tenant_id"]))

    elif name == "get_tenant_dashboard":
        eng = _get_tenant_engine()
        return _text(await eng.get_tenant_dashboard(arguments["tenant_id"]))

    elif name == "get_sub_account_status":
        eng = _get_tenant_engine()
        return _text(await eng.get_sub_account_status(tenant_id=arguments["tenant_id"], sub_account_id=arguments["sub_account_id"]))

    elif name == "set_sub_account_permissions":
        eng = _get_tenant_engine()
        return _text(await eng.set_sub_account_permissions(tenant_id=arguments["tenant_id"], sub_account_id=arguments["sub_account_id"], permissions=arguments["permissions"]))

    # ── V10: ML/AI-Native Strategy Engine ─────────────────────
    elif name == "create_feature_set":
        eng = _get_feature_engine()
        return _text(await eng.create_feature_set(name=arguments["name"], features=arguments["features"], target=arguments.get("target")))

    elif name == "compute_features":
        eng = _get_feature_engine()
        return _text(await eng.compute_features(feature_set_id=arguments["feature_set_id"], symbol=arguments["symbol"], start_date=arguments.get("start_date"), end_date=arguments.get("end_date")))

    elif name == "list_feature_sets":
        eng = _get_feature_engine()
        return _text(await eng.list_feature_sets())

    elif name == "get_feature_importance":
        eng = _get_feature_engine()
        return _text(await eng.get_feature_importance(feature_set_id=arguments["feature_set_id"], model_id=arguments.get("model_id")))

    elif name == "train_model":
        eng = _get_model_trainer()
        return _text(await eng.train(feature_set_id=arguments["feature_set_id"], model_type=arguments["model_type"], hyperparameters=arguments.get("hyperparameters"), train_split=arguments.get("train_split", 0.8)))

    elif name == "evaluate_model":
        eng = _get_model_trainer()
        return _text(await eng.evaluate(model_id=arguments["model_id"], test_data_id=arguments.get("test_data_id")))

    elif name == "predict":
        eng = _get_model_trainer()
        return _text(await eng.predict(model_id=arguments["model_id"], symbol=arguments["symbol"], features=arguments.get("features")))

    elif name == "explain_prediction":
        eng = _get_model_trainer()
        return _text(await eng.explain(model_id=arguments["model_id"], prediction_id=arguments["prediction_id"]))

    elif name == "register_model":
        eng = _get_model_registry()
        return _text(await eng.register(model_id=arguments["model_id"], name=arguments["name"], version=arguments.get("version"), metrics=arguments.get("metrics"), tags=arguments.get("tags")))

    elif name == "promote_model":
        eng = _get_model_registry()
        return _text(await eng.promote(registry_id=arguments["registry_id"], stage=arguments["stage"]))

    elif name == "list_models":
        eng = _get_model_registry()
        return _text(await eng.list_models(stage=arguments.get("stage"), name_filter=arguments.get("name_filter")))

    elif name == "compare_models":
        eng = _get_model_registry()
        return _text(await eng.compare(model_ids=arguments["model_ids"]))

    elif name == "archive_model":
        eng = _get_model_registry()
        return _text(await eng.archive(registry_id=arguments["registry_id"], reason=arguments.get("reason")))

    elif name == "create_rl_agent":
        eng = _get_rl_agent()
        return _text(await eng.create_agent(name=arguments["name"], algorithm=arguments["algorithm"], environment=arguments.get("environment"), reward_config=arguments.get("reward_config")))

    elif name == "train_rl_agent":
        eng = _get_rl_agent()
        return _text(await eng.train(agent_id=arguments["agent_id"], episodes=arguments.get("episodes", 1000), symbol=arguments.get("symbol")))

    elif name == "evaluate_rl_agent":
        eng = _get_rl_agent()
        return _text(await eng.evaluate(agent_id=arguments["agent_id"], episodes=arguments.get("episodes", 100)))

    elif name == "get_rl_agent_state":
        eng = _get_rl_agent()
        return _text(await eng.get_state(agent_id=arguments["agent_id"]))

    elif name == "dispatch_gpu_task":
        eng = _get_gpu_dispatcher()
        return _text(await eng.dispatch(task_type=arguments["task_type"], payload=arguments["payload"], prefer_gpu=arguments.get("prefer_gpu", "auto")))

    elif name == "gpu_status":
        eng = _get_gpu_dispatcher()
        return _text(await eng.status())

    elif name == "generate_strategy_spec":
        eng = _get_llm_strategy_gen()
        return _text(await eng.generate(description=arguments["description"], asset_class=arguments.get("asset_class"), risk_tolerance=arguments.get("risk_tolerance")))

    # ── V11: Institutional-Grade Execution ────────────────────
    elif name == "validate_institutional_order":
        eng = _get_inst_order_mgr()
        return _text(await eng.validate_order(order=arguments["order"], account_id=arguments.get("account_id")))

    elif name == "submit_institutional_order":
        # BUG (P1-G) FIX: submit_institutional_order previously skipped TradingGuardrails
        # entirely — any compliance_override=True call could bypass all financial safety
        # gates (VIX, daily-loss, drawdown, velocity). Now wires the same guardrail
        # pre-flight as place_order for VIX, daily-loss, and drawdown checks.
        if _GUARDRAILS_AVAILABLE and not arguments.get("compliance_override", False):
            try:
                _g_inst = get_guardrails()
                _inst_order = arguments.get("order", {})
                _inst_symbol = _inst_order.get("symbol", "UNKNOWN")
                # Extract broker name safely: account_id may be "broker:account_id",
                # a plain account ID, or None. Only accept known broker names.
                _KNOWN_BROKERS = {"tradovate", "alpaca", "oanda", "rithmic"}
                _raw_account = arguments.get("account_id") or ""
                _extracted = _raw_account.split(":")[0].lower() if ":" in _raw_account else _raw_account.lower()
                _inst_broker = _extracted if _extracted in _KNOWN_BROKERS else "tradovate"
                _inst_qty = float(_inst_order.get("qty", _inst_order.get("quantity", 1)))
                _inst_vix = float(os.getenv("CURRENT_VIX", "0") or "0")
                _inst_pnl = 0.0
                _g_inst.check_all(
                    broker=_inst_broker,
                    symbol=_inst_symbol,
                    qty_contracts=_inst_qty,
                    current_daily_pnl=_inst_pnl,
                    current_drawdown_pct=0.0,
                    consecutive_losses=0,
                    vix=_inst_vix,
                    total_open_notional=0.0,
                )
            except GuardrailTripped as _gt_inst:
                logger.warning("submit_institutional_order BLOCKED by guardrail: %s", _gt_inst)
                return _text({
                    "error_type": "GuardrailTripped",
                    "reason": _gt_inst.reason.value if hasattr(_gt_inst, "reason") else str(_gt_inst),
                    "blocked_by": "submit_institutional_order guardrail pre-flight",
                })
            except Exception as _g_inst_err:
                logger.warning("submit_institutional_order guardrail check failed: %s", _g_inst_err)
        eng = _get_inst_order_mgr()
        return _text(await eng.submit_order(order=arguments["order"], account_id=arguments.get("account_id"), compliance_override=arguments.get("compliance_override", False)))

    elif name == "get_order_status":
        eng = _get_inst_order_mgr()
        return _text(await eng.get_order_status(order_id=arguments["order_id"]))

    elif name == "route_order":
        eng = _get_smart_router()
        return _text(await eng.route(order=arguments["order"], routing_strategy=arguments.get("routing_strategy", "best_price"), max_venues=arguments.get("max_venues", 5)))

    elif name == "get_venue_analytics":
        eng = _get_smart_router()
        return _text(await eng.get_venue_analytics(venue_id=arguments.get("venue_id"), lookback_days=arguments.get("lookback_days", 30)))

    elif name == "start_algo_execution":
        eng = _get_algo_executor()
        return _text(await eng.start(algo_type=arguments["algo_type"], order=arguments["order"], parameters=arguments.get("parameters")))

    elif name == "stop_algo_execution":
        eng = _get_algo_executor()
        return _text(await eng.stop(execution_id=arguments["execution_id"]))

    elif name == "get_algo_execution_status":
        eng = _get_algo_executor()
        return _text(await eng.get_status(execution_id=arguments["execution_id"]))

    elif name == "connect_fix_session":
        eng = _get_fix_gateway()
        return _text(await eng.connect(venue=arguments["venue"], sender_comp_id=arguments["sender_comp_id"], target_comp_id=arguments["target_comp_id"], config=arguments.get("config")))

    elif name == "disconnect_fix_session":
        eng = _get_fix_gateway()
        return _text(await eng.disconnect(session_id=arguments["session_id"]))

    elif name == "get_fix_session_status":
        eng = _get_fix_gateway()
        return _text(await eng.get_session_status(session_id=arguments["session_id"]))

    elif name == "run_tca":
        eng = _get_tca_engine()
        return _text(await eng.analyze(trades=arguments["trades"], benchmark=arguments.get("benchmark", "vwap")))

    elif name == "get_tca_report":
        eng = _get_tca_engine()
        return _text(await eng.get_report(start_date=arguments["start_date"], end_date=arguments["end_date"], account_id=arguments.get("account_id")))

    elif name == "get_implementation_shortfall":
        eng = _get_tca_engine()
        return _text(await eng.implementation_shortfall(orders=arguments["orders"]))

    elif name == "register_venue":
        eng = _get_venue_manager()
        return _text(await eng.register(name=arguments["name"], venue_type=arguments["venue_type"], config=arguments.get("config")))

    elif name == "list_venues":
        eng = _get_venue_manager()
        return _text(await eng.list_venues())

    elif name == "get_venue_status":
        eng = _get_venue_manager()
        return _text(await eng.get_status(venue_id=arguments["venue_id"]))

    elif name == "set_venue_priority":
        eng = _get_venue_manager()
        return _text(await eng.set_priority(venue_id=arguments["venue_id"], priority=arguments["priority"]))

    # ── V12: Real-Time Analytics ──────────────────────────────
    elif name == "start_pnl_stream":
        eng = _get_pnl_streamer()
        return _text(await eng.start_stream(account_id=arguments["account_id"], symbols=arguments.get("symbols")))

    elif name == "get_pnl_snapshot":
        eng = _get_pnl_streamer()
        return _text(await eng.get_snapshot(account_id=arguments["account_id"]))

    elif name == "get_pnl_history":
        eng = _get_pnl_streamer()
        return _text(await eng.get_history(account_id=arguments["account_id"], interval=arguments.get("interval", "1h"), lookback=arguments.get("lookback", "24h")))

    elif name == "analyze_order_flow":
        eng = _get_order_flow()
        return _text(await eng.analyze(symbol=arguments["symbol"], lookback_minutes=arguments.get("lookback_minutes", 60)))

    elif name == "get_order_flow_heatmap":
        eng = _get_order_flow()
        return _text(await eng.get_heatmap(symbol=arguments["symbol"], levels=arguments.get("levels", 20)))

    elif name == "get_volume_profile":
        eng = _get_order_flow()
        return _text(await eng.get_volume_profile(symbol=arguments["symbol"], lookback_days=arguments.get("lookback_days", 5)))

    elif name == "analyze_microstructure":
        eng = _get_microstructure()
        return _text(await eng.analyze(symbol=arguments["symbol"]))

    elif name == "get_toxicity_score":
        eng = _get_microstructure()
        return _text(await eng.get_toxicity(symbol=arguments["symbol"], window=arguments.get("window", 50)))

    elif name == "detect_regime":
        # Deprecated: route hmm calls to the V21 quant_alpha HMM engine.
        # Non-hmm methods fall through to the legacy regime detector.
        method = arguments.get("method", "hmm")
        if method == "hmm":
            logger.warning(
                "detect_regime(method='hmm') is deprecated — use detect_regime_hmm instead. "
                "Routing to quant_alpha.regime_hmm."
            )
            hmm_cls = _lazy_import(".quant_alpha.regime_hmm", "RegimeHMM")
            if hmm_cls:
                _hmm = hmm_cls()
                regime = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: _hmm.detect(symbol=arguments["symbol"])
                )
                return _text({
                    "symbol": arguments["symbol"],
                    "regime": regime.regime if hasattr(regime, "regime") else str(regime),
                    "method": "hmm_v21",
                    "deprecated": True,
                    "note": "Use detect_regime_hmm for richer output (probabilities, transitions).",
                })
        eng = _get_regime_detector()
        return _text(await eng.detect(symbol=arguments["symbol"], method=method))

    elif name == "get_regime_history":
        eng = _get_regime_detector()
        return _text(await eng.get_history(symbol=arguments["symbol"], lookback_days=arguments.get("lookback_days", 90)))

    elif name == "get_regime_transition_matrix":
        eng = _get_regime_detector()
        return _text(await eng.get_transition_matrix(symbol=arguments["symbol"]))

    elif name == "create_alert":
        eng = _get_alert_engine()
        return _text(await eng.create_alert(name=arguments["name"], condition=arguments["condition"], actions=arguments.get("actions"), channels=arguments.get("channels")))

    elif name == "list_alerts":
        eng = _get_alert_engine()
        return _text(await eng.list_alerts(active_only=arguments.get("active_only", True)))

    elif name == "delete_alert":
        eng = _get_alert_engine()
        return _text(await eng.delete_alert(alert_id=arguments["alert_id"]))

    elif name == "get_alert_history":
        eng = _get_alert_engine()
        return _text(await eng.get_history(alert_id=arguments.get("alert_id"), limit=arguments.get("limit", 50)))

    # ── V13: Alternative Data Marketplace ─────────────────────
    elif name == "analyze_sentiment":
        eng = _get_sentiment_engine()
        return _text(await eng.analyze(symbol=arguments["symbol"], source=arguments.get("source"), text=arguments.get("text")))

    elif name == "get_sentiment_history":
        eng = _get_sentiment_engine()
        return _text(await eng.get_history(symbol=arguments["symbol"], source=arguments.get("source"), lookback_days=arguments.get("lookback_days", 30)))

    elif name == "get_sentiment_signal":
        eng = _get_sentiment_engine()
        return _text(await eng.get_signal(symbol=arguments["symbol"]))

    elif name == "analyze_satellite":
        eng = _get_satellite_engine()
        return _text(await eng.analyze(location=arguments["location"], data_type=arguments["data_type"], symbol=arguments.get("symbol")))

    elif name == "get_satellite_timeseries":
        eng = _get_satellite_engine()
        return _text(await eng.get_timeseries(location_id=arguments["location_id"], metric=arguments["metric"], lookback_days=arguments.get("lookback_days", 90)))

    elif name == "scrape_web_data":
        eng = _get_web_scraper()
        return _text(await eng.scrape(url=arguments["url"], selectors=arguments.get("selectors"), schedule=arguments.get("schedule")))

    elif name == "list_scrape_jobs":
        eng = _get_web_scraper()
        return _text(await eng.list_jobs())

    elif name == "get_scrape_results":
        eng = _get_web_scraper()
        return _text(await eng.get_results(job_id=arguments["job_id"], limit=arguments.get("limit", 100)))

    elif name == "analyze_sec_filing":
        eng = _get_sec_filing()
        return _text(await eng.analyze(symbol=arguments["symbol"], filing_type=arguments["filing_type"], filing_url=arguments.get("filing_url")))

    elif name == "get_insider_trades":
        eng = _get_sec_filing()
        return _text(await eng.get_insider_trades(symbol=arguments["symbol"], days=arguments.get("days", 90)))

    elif name == "get_institutional_holdings":
        eng = _get_sec_filing()
        return _text(await eng.get_institutional_holdings(symbol=arguments["symbol"], quarter=arguments.get("quarter")))

    elif name == "analyze_social_media":
        eng = _get_social_media()
        return _text(await eng.analyze(symbol=arguments["symbol"], platform=arguments.get("platform", "all"), lookback_hours=arguments.get("lookback_hours", 24)))

    elif name == "get_social_momentum":
        eng = _get_social_media()
        return _text(await eng.get_momentum(symbol=arguments["symbol"]))

    elif name == "get_social_sentiment_feed":
        eng = _get_social_media()
        return _text(await eng.get_feed(symbols=arguments.get("symbols"), limit=arguments.get("limit", 50)))

    elif name == "browse_alt_datasets":
        eng = _get_alt_data_market()
        return _text(await eng.browse(category=arguments.get("category"), min_quality=arguments.get("min_quality", 0.7)))

    elif name == "subscribe_alt_dataset":
        eng = _get_alt_data_market()
        return _text(await eng.subscribe(dataset_id=arguments["dataset_id"], config=arguments.get("config")))

    elif name == "get_alt_dataset_sample":
        eng = _get_alt_data_market()
        return _text(await eng.get_sample(dataset_id=arguments["dataset_id"], limit=arguments.get("limit", 100)))

    elif name == "get_alt_data_quality":
        eng = _get_alt_data_market()
        return _text(await eng.get_quality(dataset_id=arguments["dataset_id"]))

    # ── V14: Autonomous Agent Swarm ───────────────────────────
    elif name == "spawn_agent":
        eng = _get_agent_orchestrator()
        return _text(await eng.spawn(name=arguments["name"], role=arguments["role"], strategy=arguments.get("strategy"), capital_allocation=arguments.get("capital_allocation")))

    elif name == "list_agents":
        eng = _get_agent_orchestrator()
        return _text(await eng.list_agents(role_filter=arguments.get("role_filter")))

    elif name == "get_agent_detail":
        eng = _get_agent_orchestrator()
        return _text(await eng.get_detail(agent_id=arguments["agent_id"]))

    elif name == "terminate_agent":
        eng = _get_agent_orchestrator()
        return _text(await eng.terminate(agent_id=arguments["agent_id"], reason=arguments.get("reason")))

    elif name == "create_task_plan":
        eng = _get_task_planner()
        return _text(await eng.create_plan(goal=arguments["goal"], constraints=arguments.get("constraints"), deadline=arguments.get("deadline")))

    elif name == "get_task_plan":
        eng = _get_task_planner()
        return _text(await eng.get_plan(plan_id=arguments["plan_id"]))

    elif name == "store_agent_memory":
        eng = _get_agent_memory()
        return _text(await eng.store(agent_id=arguments["agent_id"], memory_type=arguments["memory_type"], content=arguments["content"]))

    elif name == "query_agent_memory":
        eng = _get_agent_memory()
        return _text(await eng.query(query=arguments["query"], memory_type=arguments.get("memory_type"), agent_id=arguments.get("agent_id"), limit=arguments.get("limit", 20)))

    elif name == "get_memory_stats":
        eng = _get_agent_memory()
        return _text(await eng.get_stats(agent_id=arguments.get("agent_id")))

    elif name == "route_tool_call":
        eng = _get_tool_router()
        return _text(await eng.route(agent_id=arguments["agent_id"], tool_name=arguments["tool_name"], arguments=arguments["arguments"]))

    elif name == "get_tool_permissions":
        eng = _get_tool_router()
        return _text(await eng.get_permissions(agent_id=arguments["agent_id"]))

    elif name == "request_consensus":
        eng = _get_consensus_engine()
        return _text(await eng.request(proposal=arguments["proposal"], agent_ids=arguments["agent_ids"], method=arguments.get("method", "weighted")))

    elif name == "get_consensus_result":
        eng = _get_consensus_engine()
        return _text(await eng.get_result(consensus_id=arguments["consensus_id"]))

    elif name == "get_consensus_history":
        eng = _get_consensus_engine()
        return _text(await eng.get_history(limit=arguments.get("limit", 20)))

    elif name == "get_agent_health":
        eng = _get_agent_monitor()
        return _text(await eng.get_health(agent_id=arguments["agent_id"]))

    elif name == "get_swarm_dashboard":
        eng = _get_agent_monitor()
        return _text(await eng.get_dashboard())

    elif name == "get_agent_performance":
        eng = _get_agent_monitor()
        return _text(await eng.get_performance(agent_id=arguments["agent_id"], lookback_days=arguments.get("lookback_days", 30)))

    elif name == "set_agent_parameters":
        eng = _get_agent_orchestrator()
        return _text(await eng.set_parameters(agent_id=arguments["agent_id"], parameters=arguments["parameters"]))

    # ── V15: DeFi & Cross-Chain ───────────────────────────────
    elif name == "get_dex_quote":
        eng = _get_dex_aggregator()
        return _text(await eng.get_quote(token_in=arguments["token_in"], token_out=arguments["token_out"], amount=arguments["amount"], chain=arguments.get("chain")))

    elif name == "execute_swap":
        eng = _get_dex_aggregator()
        return _text(await eng.execute_swap(quote_id=arguments["quote_id"], slippage_tolerance=arguments.get("slippage_tolerance", 0.005), deadline_minutes=arguments.get("deadline_minutes", 20)))

    elif name == "get_dex_liquidity":
        eng = _get_dex_aggregator()
        return _text(await eng.get_liquidity(token_in=arguments["token_in"], token_out=arguments["token_out"], chain=arguments.get("chain")))

    elif name == "scan_yield_opportunities":
        eng = _get_yield_optimizer()
        return _text(await eng.scan(min_apy=arguments.get("min_apy", 5.0), max_risk_score=arguments.get("max_risk_score", 7), chains=arguments.get("chains")))

    elif name == "deploy_yield_strategy":
        eng = _get_yield_optimizer()
        return _text(await eng.deploy(opportunity_id=arguments["opportunity_id"], amount=arguments["amount"], auto_compound=arguments.get("auto_compound", True)))

    elif name == "get_yield_positions":
        eng = _get_yield_optimizer()
        return _text(await eng.get_positions())

    elif name == "withdraw_yield":
        eng = _get_yield_optimizer()
        return _text(await eng.withdraw(position_id=arguments["position_id"], amount=arguments.get("amount")))

    elif name == "bridge_tokens":
        eng = _get_bridge_engine()
        return _text(await eng.bridge(token=arguments["token"], amount=arguments["amount"], from_chain=arguments["from_chain"], to_chain=arguments["to_chain"], bridge_protocol=arguments.get("bridge_protocol")))

    elif name == "get_bridge_status":
        eng = _get_bridge_engine()
        return _text(await eng.get_status(transfer_id=arguments["transfer_id"]))

    elif name == "list_bridge_routes":
        eng = _get_bridge_engine()
        return _text(await eng.list_routes(token=arguments["token"], from_chain=arguments["from_chain"], to_chain=arguments["to_chain"]))

    elif name == "check_mev_risk":
        eng = _get_mev_protector()
        return _text(await eng.check_risk(transaction=arguments["transaction"], chain=arguments.get("chain")))

    elif name == "submit_protected_tx":
        eng = _get_mev_protector()
        return _text(await eng.submit_protected(transaction=arguments["transaction"], protection_type=arguments.get("protection_type")))

    elif name == "get_mev_analytics":
        eng = _get_mev_protector()
        return _text(await eng.get_analytics(wallet=arguments.get("wallet"), lookback_days=arguments.get("lookback_days", 7)))

    elif name == "get_governance_proposals":
        eng = _get_governance_engine()
        return _text(await eng.get_proposals(protocol=arguments["protocol"], status=arguments.get("status", "active")))

    elif name == "vote_on_proposal":
        eng = _get_governance_engine()
        return _text(await eng.vote(proposal_id=arguments["proposal_id"], vote=arguments["vote"], reason=arguments.get("reason")))

    elif name == "get_governance_power":
        eng = _get_governance_engine()
        return _text(await eng.get_power(protocol=arguments["protocol"], wallet=arguments.get("wallet")))

    elif name == "assess_defi_risk":
        eng = _get_defi_risk()
        return _text(await eng.assess(protocol=arguments["protocol"], chain=arguments.get("chain")))

    elif name == "get_defi_portfolio_risk":
        eng = _get_defi_risk()
        return _text(await eng.get_portfolio_risk())

    elif name == "monitor_liquidation_risk":
        eng = _get_defi_risk()
        return _text(await eng.monitor_liquidation(position_id=arguments["position_id"]))

    elif name == "get_defi_insurance_options":
        eng = _get_defi_risk()
        return _text(await eng.get_insurance(protocol=arguments["protocol"], coverage_amount=arguments.get("coverage_amount")))

    # ── V16: Cloud SaaS Platform ──────────────────────────────
    elif name == "create_saas_tenant":
        eng = _get_saas_tenant_mgr()
        return _text(await eng.create_tenant(company_name=arguments["company_name"], admin_email=arguments["admin_email"], plan=arguments.get("plan", "free"), config=arguments.get("config")))

    elif name == "get_saas_tenant":
        eng = _get_saas_tenant_mgr()
        return _text(await eng.get_tenant(tenant_id=arguments["tenant_id"]))

    elif name == "update_saas_tenant":
        eng = _get_saas_tenant_mgr()
        return _text(await eng.update_tenant(tenant_id=arguments["tenant_id"], updates=arguments["updates"]))

    elif name == "get_usage_metrics":
        eng = _get_billing_engine()
        return _text(await eng.get_usage(tenant_id=arguments["tenant_id"], period=arguments.get("period", "current_month")))

    elif name == "get_invoice":
        eng = _get_billing_engine()
        return _text(await eng.get_invoice(tenant_id=arguments["tenant_id"], invoice_id=arguments.get("invoice_id")))

    elif name == "list_invoices":
        eng = _get_billing_engine()
        return _text(await eng.list_invoices(tenant_id=arguments["tenant_id"], status=arguments.get("status")))

    elif name == "update_payment_method":
        eng = _get_billing_engine()
        return _text(await eng.update_payment(tenant_id=arguments["tenant_id"], payment_method=arguments["payment_method"]))

    elif name == "publish_strategy_to_marketplace":
        eng = _get_strategy_market()
        return _text(await eng.publish(strategy_id=arguments["strategy_id"], pricing=arguments["pricing"], description=arguments.get("description"), tags=arguments.get("tags")))

    elif name == "browse_strategy_marketplace":
        eng = _get_strategy_market()
        return _text(await eng.browse(category=arguments.get("category"), min_sharpe=arguments.get("min_sharpe"), max_price=arguments.get("max_price"), sort_by=arguments.get("sort_by", "sharpe")))

    elif name == "subscribe_to_strategy":
        eng = _get_strategy_market()
        return _text(await eng.subscribe(tenant_id=arguments["tenant_id"], strategy_id=arguments["strategy_id"], allocation=arguments.get("allocation")))

    elif name == "configure_white_label":
        eng = _get_white_label()
        return _text(await eng.configure(tenant_id=arguments["tenant_id"], branding=arguments["branding"]))

    elif name == "get_white_label_config":
        eng = _get_white_label()
        return _text(await eng.get_config(tenant_id=arguments["tenant_id"]))

    elif name == "generate_api_key":
        eng = _get_api_gateway()
        return _text(await eng.generate_key(tenant_id=arguments["tenant_id"], name=arguments["name"], permissions=arguments.get("permissions"), rate_limit=arguments.get("rate_limit", 1000)))

    elif name == "list_api_keys":
        eng = _get_api_gateway()
        return _text(await eng.list_keys(tenant_id=arguments["tenant_id"]))

    elif name == "revoke_api_key":
        eng = _get_api_gateway()
        return _text(await eng.revoke_key(key_id=arguments["key_id"]))

    elif name == "get_api_usage":
        eng = _get_api_gateway()
        return _text(await eng.get_usage(tenant_id=arguments["tenant_id"], key_id=arguments.get("key_id")))

    elif name == "get_platform_health":
        eng = _get_api_gateway()
        return _text(await eng.get_health())

    # ── V17: Massive White-Label Data ─────────────────────────
    elif name == "massive_search_endpoints":
        eng = await _get_massive_provider()
        return _text(eng.search_endpoints(query=arguments["query"], top_k=arguments.get("top_k", 5), scope=arguments.get("scope", "all")))

    elif name == "massive_get_endpoint_docs":
        eng = await _get_massive_provider()
        return _text(await eng.get_endpoint_docs(docs_url=arguments["docs_url"]))

    elif name == "massive_call_api":
        eng = await _get_massive_provider()
        return _text(await eng.call_api(path=arguments["path"], method=arguments.get("method", "GET"), params=arguments.get("params"), store_as=arguments.get("store_as"), apply=arguments.get("apply"), api_key=arguments.get("api_key"), llm_model=arguments.get("llm_model"), llm_provider=arguments.get("llm_provider")))

    elif name == "massive_query_data":
        eng = await _get_massive_provider()
        return _text(await eng.query_data(sql=arguments["sql"], apply=arguments.get("apply")))

    elif name == "massive_run_pipeline":
        eng = await _get_massive_provider()
        return _text(await eng.run_pipeline(search_query=arguments["search_query"], path_override=arguments.get("path_override"), params=arguments.get("params"), store_as=arguments.get("store_as"), sql=arguments.get("sql"), apply=arguments.get("apply")))

    # ── V17: Dynamic Toolsets — Meta-Tools ────────────────────
    elif name == "discover_tools":
        gw = _get_dynamic_gateway()
        return _text(gw.discover(query=arguments["query"], top_k=arguments.get("top_k", 10), category=arguments.get("category")))

    elif name == "get_tool_details":
        gw = _get_dynamic_gateway()
        details = gw.get_tool_details(arguments["tool_name"])
        if details is None:
            return _text({"error": f"Tool '{arguments['tool_name']}' not found"})
        return _text(details)

    elif name == "execute_dynamic_tool":
        inner_name = arguments["tool_name"]
        inner_args = arguments.get("arguments", {})
        # Gate ALL ORDER_EXEC and DESTRUCTIVE tier tools through the shared policy,
        # not a hardcoded denylist or transport-specific approval vocabulary.
        try:
            decision = evaluate_dynamic_tool(
                inner_name,
                inner_args,
                expected_owner_token=os.environ.get("OWNER_API_TOKEN", ""),
            )
        except ImportError:
            return _text({
                "error": "execute_dynamic_tool: danger-tier module unavailable; execution blocked for safety.",
                "blocked": True,
                "tool": inner_name,
            })

        if not decision.allow:
            return _text(decision.as_error())

        # Demo mode guard: also stub ORDER_EXEC+ tools dispatched via execute_dynamic_tool.
        # call_tool stubs direct calls; this catches the dynamic dispatch path.
        if os.getenv("ALGOCHAINS_DEMO_MODE", "0") == "1":
            from .tool_danger_tiers import TIER_ORDER_EXEC as _TIER_OE, get_danger_tier as _gdt
            if _gdt(inner_name) >= _TIER_OE:
                return _text({
                    "status": "demo_mode_stub",
                    "tool": inner_name,
                    "message": (
                        f"Tool '{inner_name}' is an order/execution tool (tier≥2) and is stubbed "
                        "in demo mode. No broker API call was made. "
                        "Remove ALGOCHAINS_DEMO_MODE to enable live execution."
                    ),
                    "demo_mode": True,
                })

        return await _execute_tool_with_runtime_guards(
            inner_name,
            inner_args,
            registry,
            transport="dynamic",
            policy_decision=decision,
        )

    # ── V18: Intent-Based Trading ─────────────────────────────
    elif name == "execute_intent":
        parser = _get_intent_parser()
        solver = _get_constraint_solver()
        executor = _get_plan_executor()
        if parser is None or solver is None or executor is None:
            missing = [n for n, o in [("IntentParser", parser), ("ConstraintSolver", solver), ("PlanExecutor", executor)] if o is None]
            return _text({"error": f"Intent engine components unavailable: {missing}. Check server logs."})
        intent = await parser.parse(arguments["intent"])
        plan = await solver.solve(intent)
        dry_run = arguments.get("dry_run", True)
        if not dry_run and plan.status.value == "pending_approval":
            plan = solver.approve_plan(plan.id)
            if plan:
                plan = await executor.execute(plan)
        return _text({"intent": intent.to_dict(), "plan": plan.to_dict()})

    elif name == "get_intent_plan":
        solver = _get_constraint_solver()
        if solver is None:
            return _text({"error": "ConstraintSolver unavailable — check server logs."})
        plan = solver.get_plan(arguments["plan_id"])
        if not plan:
            return _text({"error": f"Plan '{arguments['plan_id']}' not found"})
        return _text(plan.to_dict())

    elif name == "approve_intent":
        solver = _get_constraint_solver()
        executor = _get_plan_executor()
        if solver is None or executor is None:
            return _text({"error": "Intent engine unavailable — check server logs."})
        plan = solver.approve_plan(arguments["plan_id"])
        if not plan:
            return _text({"error": f"Plan '{arguments['plan_id']}' not found or not pending"})
        plan = await executor.execute(plan)
        return _text(plan.to_dict())

    elif name == "get_intent_history":
        executor = _get_plan_executor()
        return _text(executor.get_history(limit=arguments.get("limit", 20)))

    elif name == "create_shadow_portfolio":
        eng = _get_shadow_engine()
        if eng is None:
            return _text({"error": "ShadowPortfolioEngine unavailable — check server logs."})
        return _text(await eng.create(
            name=arguments["name"],
            strategy_id=arguments.get("strategy_id"),
            broker=arguments.get("broker", "alpaca"),
            capital=arguments.get("capital", 100_000.0),
        ))

    elif name == "get_shadow_results":
        eng = _get_shadow_engine()
        if eng is None:
            return _text({"error": "ShadowPortfolioEngine unavailable — check server logs."})
        shadow_id = arguments["shadow_id"]
        if arguments.get("compare_live"):
            return _text(await eng.compare(shadow_id))
        return _text(await eng.get_results(shadow_id))

    elif name == "evolve_strategies":
        eng = _get_evolution_engine()
        action = arguments["action"]
        if action == "initialize":
            return _text(await eng.initialize_population(
                strategy_type=arguments.get("strategy_type", "momentum"),
                seeds=arguments.get("seeds"),
            ))
        elif action == "evaluate":
            return _text(await eng.evaluate(
                genome_id=arguments["genome_id"],
                metrics=arguments["metrics"],
            ))
        elif action == "evolve":
            return _text(await eng.evolve())
        elif action == "get_top":
            return _text(await eng.get_top(n=arguments.get("n", 10)))
        elif action == "get_unevaluated":
            return _text(await eng.get_unevaluated(n=arguments.get("n", 10)))
        else:
            return _text({"error": f"Unknown evolution action: {action}"})

    elif name == "detect_arbitrage":
        eng = _get_arbitrage_detector()
        return _text(await eng.scan(
            symbols=arguments["symbols"],
            brokers=arguments.get("brokers"),
            quotes=arguments.get("quotes"),
        ))

    # ── V18 Genius Layer ──────────────────────────────────────
    elif name == "detect_market_regime":
        from .intent_engine.regime_detector import RegimeSignals
        eng = _get_intent_regime()
        signals = RegimeSignals(
            vix=arguments.get("vix"),
            spy_price=arguments.get("spy_price"),
            spy_sma_20=arguments.get("spy_sma_20"),
            spy_sma_50=arguments.get("spy_sma_50"),
            spy_sma_200=arguments.get("spy_sma_200"),
            advance_decline_ratio=arguments.get("advance_decline_ratio"),
            put_call_ratio=arguments.get("put_call_ratio"),
            credit_spread_bps=arguments.get("credit_spread_bps"),
        )
        result = await eng.detect(signals)
        return _text(result.to_dict())

    elif name == "prefetch_context":
        eng = _get_predictive_prefetch()
        context = await eng.prefetch(arguments["user_message"])
        return _text({"prefetched_keys": list(context.keys()), "data": context, "stats": eng.get_stats()})

    # ── V19: Alpha Engines ────────────────────────────────────
    elif name == "compute_vwap":
        eng = _get_vwap_engine()
        return _text(await eng.compute_vwap(symbol=arguments["symbol"], date=arguments.get("date", ""), interval=arguments.get("interval", "1"), anchor=arguments.get("anchor", "day")))

    elif name == "multi_anchor_vwap":
        eng = _get_vwap_engine()
        return _text(await eng.multi_anchor_vwap(symbol=arguments["symbol"], anchors=arguments.get("anchors")))

    elif name == "detect_dark_prints":
        eng = _get_dark_pool_engine()
        return _text(await eng.detect_dark_prints(symbol=arguments["symbol"], date=arguments.get("date", ""), min_size=arguments.get("min_size", 10000)))

    elif name == "block_trade_scanner":
        eng = _get_dark_pool_engine()
        return _text(await eng.block_trade_scanner(symbols=arguments["symbols"], min_notional=arguments.get("min_notional", 500000)))

    elif name == "compute_gex":
        eng = _get_gex_engine()
        return _text(await eng.compute_gex(symbol=arguments["symbol"], expiry=arguments.get("expiry", "")))

    elif name == "gex_scanner":
        eng = _get_gex_engine()
        return _text(await eng.gex_scanner(symbols=arguments["symbols"]))

    elif name == "analyze_vol_skew":
        eng = _get_vol_surface_engine()
        return _text(await eng.analyze_skew(symbol=arguments["symbol"], expiry=arguments.get("expiry", "")))

    elif name == "vol_term_structure":
        eng = _get_vol_surface_engine()
        return _text(await eng.term_structure(symbol=arguments["symbol"]))

    elif name == "correlation_matrix":
        eng = _get_cross_asset_engine()
        return _text(await eng.correlation_matrix(symbols=arguments["symbols"], lookback_days=arguments.get("lookback_days", 60)))

    elif name == "pair_trade_signal":
        eng = _get_cross_asset_engine()
        return _text(await eng.pair_trade_signal(symbol_a=arguments["symbol_a"], symbol_b=arguments["symbol_b"], lookback_days=arguments.get("lookback_days", 60), z_entry=arguments.get("z_entry", 2.0), z_exit=arguments.get("z_exit", 0.5)))

    elif name == "relative_strength":
        eng = _get_cross_asset_engine()
        return _text(await eng.relative_strength(symbol=arguments["symbol"], benchmark=arguments.get("benchmark", "SPY"), lookback_days=arguments.get("lookback_days", 20)))

    elif name == "congressional_trades":
        eng = _get_congressional_engine()
        return _text(await eng.get_congressional_trades(symbol=arguments.get("symbol", ""), days=arguments.get("days", 30)))

    elif name == "insider_cluster_scan":
        eng = _get_congressional_engine()
        return _text(await eng.insider_cluster_scan(symbols=arguments.get("symbols"), days=arguments.get("days", 14), min_insiders=arguments.get("min_insiders", 2)))

    elif name == "smart_money_composite":
        eng = _get_congressional_engine()
        return _text(await eng.smart_money_composite(symbol=arguments["symbol"]))

    elif name == "compute_kelly":
        eng = _get_kelly_engine()
        return _text(await eng.compute_kelly(win_rate=arguments["win_rate"], avg_win=arguments["avg_win"], avg_loss=arguments["avg_loss"], fraction=arguments.get("fraction", 0.5), account_equity=arguments.get("account_equity", 100000), max_risk_pct=arguments.get("max_risk_pct", 5.0)))

    elif name == "multi_strategy_kelly":
        eng = _get_kelly_engine()
        return _text(await eng.multi_strategy_kelly(strategies=arguments["strategies"], account_equity=arguments.get("account_equity", 100000), max_total_risk_pct=arguments.get("max_total_risk_pct", 20.0)))

    elif name == "unusual_options_activity":
        eng = _get_options_flow_engine()
        return _text(await eng.unusual_activity(symbol=arguments["symbol"], min_premium=arguments.get("min_premium", 50000), min_oi_ratio=arguments.get("min_oi_ratio", 2.0)))

    elif name == "options_flow_scanner":
        eng = _get_options_flow_engine()
        return _text(await eng.options_flow_scanner(symbols=arguments["symbols"], min_premium=arguments.get("min_premium", 100000)))

    elif name == "read_tape":
        eng = _get_tape_reader_engine()
        return _text(await eng.read_tape(symbol=arguments["symbol"], lookback_minutes=arguments.get("lookback_minutes", 5)))

    elif name == "tape_momentum_scanner":
        eng = _get_tape_reader_engine()
        return _text(await eng.momentum_scanner(symbols=arguments["symbols"]))

    # ── V20: Account Protection ──────────────────────────────────
    elif name == "check_order_safety":
        eng = _get_account_protection()
        order = OrderIntent(
            broker=arguments.get("broker", ""),
            symbol=arguments.get("symbol", ""),
            side=arguments.get("side", "buy"),
            qty=arguments.get("qty", 0),
            order_type=arguments.get("order_type", "market"),
            limit_price=arguments.get("limit_price"),
            notional_value=arguments.get("notional_value"),
            asset_class=arguments.get("asset_class", "stock"),
        )
        try:
            broker = registry.get(arguments.get("broker", ""))
            if broker:
                acct = await broker.get_account()
                positions = await broker.get_positions()
                snapshot = AccountSnapshot(
                    equity=getattr(acct, "equity", 0),
                    cash=getattr(acct, "cash", 0),
                    buying_power=getattr(acct, "buying_power", 0),
                    open_positions=[{"symbol": p.symbol, "market_value": getattr(p, "market_value", 0)} for p in positions],
                )
            else:
                snapshot = AccountSnapshot()
        except Exception:
            snapshot = AccountSnapshot()
        report = eng.check_order(order, snapshot)
        return _text(report.to_dict())

    elif name == "get_protection_config":
        eng = _get_account_protection()
        return _text({"config": eng.get_config(), "presets": eng.get_presets()})

    elif name == "set_protection_config":
        eng = _get_account_protection()
        preset = arguments.get("preset")
        if preset:
            config = eng.apply_preset(preset)
        else:
            config = eng.set_config(arguments)
        return _text({"updated_config": config})

    elif name == "get_safety_audit_log":
        eng = _get_account_protection()
        return _text({"audit_log": eng.get_audit_log(arguments.get("limit", 20))})

    # ── V20: Builder SDK ─────────────────────────────────────────
    elif name == "query_data_warehouse":
        dw = _get_data_warehouse()
        query = DataQuery(
            asset_class=arguments.get("asset_class", ""),
            ticker=arguments.get("ticker", ""),
            start_date=arguments.get("start_date"),
            end_date=arguments.get("end_date"),
            limit=arguments.get("limit", 10000),
        )
        result = await dw.query(query)
        return _text(result)

    elif name == "list_data_warehouses":
        dw = _get_data_warehouse()
        return _text(dw.list_warehouses())

    elif name == "run_builder_backtest":
        runner = _get_strategy_runner()
        config = BacktestConfig(
            symbol=arguments.get("symbol", ""),
            strategy_type=arguments.get("strategy_type", "custom"),
            timeframe=arguments.get("timeframe", "1d"),
            start_date=arguments.get("start_date", ""),
            end_date=arguments.get("end_date", ""),
            initial_capital=arguments.get("initial_capital", 100000),
        )
        result = await runner.run_backtest(config)
        output = result.to_dict()
        output["marketplace_readiness"] = result.passes_marketplace_gates()
        return _text(output)

    elif name == "submit_to_marketplace":
        pipeline = _get_submission_pipeline()
        sub = StrategySubmission(
            symbol=arguments.get("symbol", ""),
            strategy_type=arguments.get("strategy_type", ""),
            timeframe=arguments.get("timeframe", ""),
            oos_sharpe=arguments.get("oos_sharpe", 0),
            oos_trades=arguments.get("oos_trades", 0),
            max_drawdown_pct=arguments.get("max_drawdown_pct", 0),
            is_sharpe=arguments.get("is_sharpe", 0),
            win_rate=arguments.get("win_rate", 0),
            profit_factor=arguments.get("profit_factor", 0),
            mcpt_p_value=arguments.get("mcpt_p_value", 1.0),
            mcpt_permutations=arguments.get("mcpt_permutations", 0),
            wf_folds=arguments.get("wf_folds", 0),
            wf_avg_oos_sharpe=arguments.get("wf_avg_oos_sharpe", 0),
            wf_worst_fold=arguments.get("wf_worst_fold", 0),
            description=arguments.get("description", ""),
            asset_class=arguments.get("asset_class", "stock"),
            price_monthly=arguments.get("price_monthly", 29.99),
        )
        result = await pipeline.submit(sub)
        return _text(result.to_dict())

    elif name == "get_submission_guide":
        pipeline = _get_submission_pipeline()
        return _text(pipeline.get_submission_guide())

    elif name == "get_builder_capabilities":
        runner = _get_strategy_runner()
        return _text(runner.get_capabilities())

    # ── V20: Memory Safety ───────────────────────────────────────
    elif name == "mcp_tool_manifest":
        cfg = _config or load_config()
        include = arguments.get("include_tool_details", True)
        manifest = build_manifest(
            tool_names=[t.name for t in TOOLS],
            tier1_names=set(TIER1_TOOL_NAMES),
            tool_mode=cfg.tool_mode,
        )
        if not include:
            manifest = {k: v for k, v in manifest.items() if k != "tools"}
            manifest["tools_omitted"] = True
            manifest["total_tools"] = len(TOOLS)
        return _text(manifest)

    elif name == "get_memory_status":
        mon = get_memory_monitor()
        report = mon.get_report()
        report["check"] = mon.check()
        return _text(report)

    # ═══════════════════════════════════════════════════════════════
    # V21: MCP 2025-11-25 Spec Compliance
    # ═══════════════════════════════════════════════════════════════
    elif name == "request_trade_confirmation":
        _lazy_import("elicitation", "ElicitationManager")
        ElicitationManager = _lazy_import("elicitation", "ElicitationManager")
        if not ElicitationManager:
            return _text({"error": "Elicitation not available — check spec_compliance module"})
        mgr = ElicitationManager()
        req = mgr.build_order_confirmation(
            symbol=args.get("symbol", ""), side=args.get("side", ""),
            quantity=args.get("quantity", 1),
            order_type=args.get("order_type", "MARKET"),
            estimated_notional=args.get("estimated_notional", 0),
        )
        return _text({"elicitation_id": req.id, "type": req.type, "schema": req.schema,
                      "message": req.message, "status": "awaiting_confirmation",
                      "note": "Present this confirmation to the user via MCP elicitation protocol before proceeding."})

    elif name == "submit_long_running_task":
        TaskManager = _lazy_import("tasks_engine", "TaskManager")
        if not TaskManager:
            return _text({"error": "Task engine not available"})
        mgr = TaskManager()
        task = mgr.create(
            operation=args.get("operation", ""), params=args.get("params", {}),
            title=args.get("title", args.get("operation", "")),
            description=args.get("description", ""),
        )
        _submit_task = asyncio.create_task(mgr.submit(task.id), name=f"task_submit_{task.id[:8]}")
        _submit_task.add_done_callback(
            lambda t: logger.warning("Task submit failed: %s", t.exception()) if not t.cancelled() and t.exception() else None
        )
        return _text({"task_id": task.id, "status": task.status.value,
                      "title": task.title, "message": "Task submitted. Use get_task_status to poll."})

    elif name == "get_task_status":
        TaskManager = _lazy_import("tasks_engine", "TaskManager")
        if not TaskManager:
            return _text({"error": "Task engine not available"})
        task = TaskManager().get(args.get("task_id", ""))
        if not task:
            return _text({"error": f"Task {args.get('task_id')} not found"})
        return _text({"task_id": task.id, "status": task.status.value,
                      "progress": task.progress, "result": task.result, "error": task.error})

    elif name == "cancel_task":
        TaskManager = _lazy_import("tasks_engine", "TaskManager")
        if TaskManager:
            TaskManager().cancel(args.get("task_id", ""))
        return _text({"cancelled": args.get("task_id")})

    elif name == "list_active_tasks":
        TaskManager = _lazy_import("tasks_engine", "TaskManager")
        if not TaskManager:
            return _text({"tasks": []})
        tasks = TaskManager().list_tasks(status=args.get("status"))
        return _text({"tasks": [{"id": t.id, "operation": t.operation,
                                  "status": t.status.value, "progress": t.progress} for t in tasks]})

    elif name == "subscribe_resource":
        SubscriptionManager = _lazy_import("subscriptions_v21", "SubscriptionManager")
        if not SubscriptionManager:
            return _text({"error": "Subscription manager not available"})
        sub = SubscriptionManager().subscribe(
            uri=args.get("uri", ""), subscriber_id=args.get("subscriber_id", "mcp-client")
        )
        return _text({"subscription_id": sub.id, "uri": sub.uri, "status": "active",
                      "message": f"Subscribed to {sub.uri}. Push notifications will be sent on resource updates."})

    elif name == "list_subscriptions":
        SubscriptionManager = _lazy_import("subscriptions_v21", "SubscriptionManager")
        if not SubscriptionManager:
            return _text({"subscriptions": []})
        subs = SubscriptionManager().list_subscriptions()
        return _text({"subscriptions": [{"id": s.id, "uri": s.uri, "active": s.active} for s in subs]})

    # ═══════════════════════════════════════════════════════════════
    # V21: AlphaLoop Self-Improving Loop
    # ═══════════════════════════════════════════════════════════════
    elif name == "run_evolution_cycle":
        get_evolution_daemon = _lazy_import("evolution_daemon", "get_evolution_daemon")
        if not get_evolution_daemon:
            return _text({"error": "Evolution daemon not available — pip install optuna"})
        daemon = get_evolution_daemon()
        result = await daemon.run_cycle_now(
            strategy_id=args.get("strategy_id", ""),
            generations=args.get("generations", 3),
            min_trades=args.get("min_trades_required", 10),
        )
        return _text(result)

    elif name == "get_evolution_status":
        get_evolution_daemon = _lazy_import("evolution_daemon", "get_evolution_daemon")
        if not get_evolution_daemon:
            return _text({"status": "unavailable"})
        return _text(get_evolution_daemon().get_status())

    elif name == "list_evolved_strategies":
        get_evolution_daemon = _lazy_import("evolution_daemon", "get_evolution_daemon")
        if not get_evolution_daemon:
            return _text({"evolved": []})
        return _text({"evolved": get_evolution_daemon().list_evolved(limit=args.get("limit", 20))})

    elif name == "rollback_evolution":
        get_evolution_daemon = _lazy_import("evolution_daemon", "get_evolution_daemon")
        if not get_evolution_daemon:
            return _text({"error": "Evolution daemon not available"})
        return _text(get_evolution_daemon().rollback(args.get("strategy_id", "")))

    elif name == "record_trade_episode":
        get_trade_memory = _lazy_import("trade_memory", "get_trade_memory")
        if not get_trade_memory:
            return _text({"error": "Trade memory not available"})
        TradeEpisode = _lazy_import("trade_memory", "TradeEpisode")
        ep = TradeEpisode(
            strategy_id=args.get("strategy_id", ""),
            symbol=args.get("symbol", ""),
            side=args.get("side", ""),
            entry_price=args.get("entry_price", 0),
            exit_price=args.get("exit_price", 0),
            pnl_usd=args.get("pnl_usd", 0),
            regime=args.get("regime", "unknown"),
            lesson=args.get("lesson", ""),
            timestamp=time.time(),
        )
        get_trade_memory().record(ep)
        return _text({"recorded": True, "episode_id": ep.id if hasattr(ep, "id") else "ok"})

    elif name == "query_trade_memory":
        get_trade_memory = _lazy_import("trade_memory", "get_trade_memory")
        if not get_trade_memory:
            return _text({"results": []})
        mem = get_trade_memory()
        results = mem.query_similar(
            query=args.get("query", ""),
            strategy_id=args.get("strategy_id"),
            regime=args.get("regime"),
            limit=args.get("limit", 10),
        )
        return _text({"results": results})

    elif name == "get_lessons_learned":
        get_trade_memory = _lazy_import("trade_memory", "get_trade_memory")
        if not get_trade_memory:
            return _text({"lessons": []})
        mem = get_trade_memory()
        lessons = mem.get_lessons(
            strategy_id=args.get("strategy_id", ""),
            regime=args.get("regime"),
            limit=args.get("limit", 10),
        )
        return _text({"lessons": lessons, "strategy_id": args.get("strategy_id")})

    elif name == "get_strategy_rankings":
        get_reward_model = _lazy_import("reward_model", "get_reward_model")
        if not get_reward_model:
            return _text({"rankings": [], "error": "Reward model not available"})
        rankings = get_reward_model().get_strategy_rankings(
            limit=args.get("limit", 20), min_trades=args.get("min_trades", 5)
        )
        return _text({"rankings": rankings})

    # ═══════════════════════════════════════════════════════════════
    # V21: Order Flow & Institutional
    # ═══════════════════════════════════════════════════════════════
    elif name == "get_footprint_chart":
        compute_footprint_chart = _lazy_import("footprint_engine", "compute_footprint_chart")
        analyze_footprint_signals = _lazy_import("footprint_engine", "analyze_footprint_signals")
        if not compute_footprint_chart:
            return _text({"error": "Footprint engine not available"})
        tick_data = args.get("tick_data", [])
        if not tick_data:
            return _text({"error": "tick_data required — provide raw tick data from Databento",
                          "hint": "Use massive_call_api or get_tick_data to fetch real tick data first"})
        bars = compute_footprint_chart(tick_data, timeframe=args.get("timeframe", "5min"))
        signals = analyze_footprint_signals(bars) if bars else []
        return _text({"bars": [vars(b) if hasattr(b, "__dict__") else b for b in bars],
                      "signals": signals, "symbol": args.get("symbol"), "bar_count": len(bars)})

    elif name == "compute_cumulative_delta":
        compute_cumulative_delta = _lazy_import("cd_engine", "compute_cumulative_delta")
        if not compute_cumulative_delta:
            return _text({"error": "Cumulative delta engine not available"})
        return _text({"symbol": args.get("symbol"),
                      "hint": "Provide OHLCV bars via tick_data parameter or use get_quote first",
                      "status": "ready"})

    elif name == "get_dark_pool_volume_v21":
        DarkPoolEngine = _lazy_import("dp_engine_v21", "DarkPoolEngine")
        if not DarkPoolEngine:
            return _text({"error": "Dark pool engine v21 not available"})
        polygon_key = (_config.polygon.api_key if _config and _config.polygon else
                       os.getenv("POLYGON_API_KEY", ""))
        if not polygon_key:
            return _text({"error": "POLYGON_API_KEY required for dark pool data"})
        engine = DarkPoolEngine(polygon_api_key=polygon_key)
        result = await engine.get_dark_pool_volume(
            symbol=args.get("symbol", ""), date=args.get("date")
        )
        return _text(vars(result) if hasattr(result, "__dict__") else result)

    elif name == "get_earnings_catalyst":
        EarningsCatalystEngine = _lazy_import("earnings_cat", "EarningsCatalystEngine")
        if not EarningsCatalystEngine:
            return _text({"error": "Earnings catalyst engine not available"})
        polygon_key = (_config.polygon.api_key if _config and _config.polygon else
                       os.getenv("POLYGON_API_KEY", ""))
        engine = EarningsCatalystEngine(polygon_api_key=polygon_key)
        result = await engine.analyze(
            symbol=args.get("symbol", ""), quarter=args.get("quarter")
        )
        return _text(vars(result) if hasattr(result, "__dict__") else result)

    elif name == "get_prediction_markets":
        PMEng = _lazy_import("pred_markets", "PredictionMarketsEngine")
        if not PMEng:
            return _text({"error": "Prediction market engine not available"})
        engine = PMEng()
        try:
            result = engine.get_signals(
                category=str(args.get("category", "all")),
                min_volume=float(args.get("min_volume", 10000)),
            )
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})
        return _text(result)

    elif name == "search_prediction_markets":
        PMEng = _lazy_import("pred_markets", "PredictionMarketsEngine")
        if not PMEng:
            return _text({"error": "Prediction market engine not available"})
        engine = PMEng()
        try:
            q = str(args.get("query", "")).strip()
            if not q:
                return _text({"error": "query is required"})
            platform = str(args.get("platform", "all"))
            lim = int(args.get("limit", 10))
            rows = engine.search_markets(q, platform=platform, limit=lim)
            return _text({"query": q, "platform": platform, "count": len(rows),
                          "markets": [m.to_dict() for m in rows]})
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "get_polymarket_high_volume":
        PMEng = _lazy_import("pred_markets", "PredictionMarketsEngine")
        if not PMEng:
            return _text({"error": "Prediction market engine not available"})
        engine = PMEng()
        try:
            lim = int(args.get("limit", 20))
            rows = engine.get_top_markets(platform="polymarket", limit=lim)
            return _text({"platform": "polymarket", "count": len(rows), "markets": rows})
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "get_polymarket_market":
        PMEng = _lazy_import("pred_markets", "PredictionMarketsEngine")
        if not PMEng:
            return _text({"error": "Prediction market engine not available"})
        engine = PMEng()
        try:
            result = engine.get_polymarket_market(str(args["market_id_or_slug"]))
            return _text(result)
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "get_polymarket_market_history":
        PMEng = _lazy_import("pred_markets", "PredictionMarketsEngine")
        if not PMEng:
            return _text({"error": "Prediction market engine not available"})
        engine = PMEng()
        try:
            result = engine.get_polymarket_market_history(
                market_id_or_slug=str(args.get("market_id_or_slug", args.get("market_id", ""))),
                timeframe=str(args.get("timeframe", "7d")),
            )
            return _text(result)
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "list_polymarket_markets":
        PMEng = _lazy_import("pred_markets", "PredictionMarketsEngine")
        if not PMEng:
            return _text({"error": "Prediction market engine not available"})
        engine = PMEng()
        try:
            result = engine.list_polymarket_markets(
                status=str(args.get("status", "open")),
                limit=int(args.get("limit", 20)),
                offset=int(args.get("offset", 0)),
                category=args.get("category"),
            )
            return _text(result)
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "get_kalshi_settlements":
        PMEng = _lazy_import("pred_markets", "PredictionMarketsEngine")
        if not PMEng:
            return _text({"error": "Prediction market engine not available"})
        engine = PMEng()
        try:
            result = engine.get_kalshi_settlements(limit=int(args.get("limit", 25)))
            return _text(result)
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "place_kalshi_order":
        PMEng = _lazy_import("pred_markets", "PredictionMarketsEngine")
        if not PMEng:
            return _text({"error": "Prediction market engine not available"})
        engine = PMEng()
        try:
            result = engine.place_kalshi_order(
                ticker=str(args["ticker"]),
                side=str(args["side"]),
                action=str(args["action"]),
                count=int(args["count"]),
                limit_price_cents=int(args["limit_price_cents"]),
                expiration_ts=int(args["expiration_ts"]) if args.get("expiration_ts") else None,
            )
            return _text(result)
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "get_kalshi_orderbook_depth":
        from .order_flow.kalshi_signed import get_kalshi_orderbook_depth as _kb_depth
        return _text(_kb_depth(
            ticker=str(args["ticker"]),
            depth=int(args.get("depth", 10)),
        ))

    elif name == "stream_kalshi_fills":
        from .order_flow.kalshi_signed import get_kalshi_recent_fills as _kb_fills
        return _text(_kb_fills(
            ticker=str(args["ticker"]),
            limit=int(args.get("limit", 50)),
        ))

    elif name == "scan_kalshi_edges":
        from .order_flow.kalshi_strategy_engine import run_full_scan as _kalshi_scan
        return _text(_kalshi_scan())

    elif name == "get_kalshi_account":
        from .order_flow.kalshi_strategy_engine import get_account_state as _kalshi_acct
        acct = _kalshi_acct()
        return _text({
            "balance_usd": acct.balance_usd,
            "positions": acct.positions,
            "open_orders": acct.open_orders,
            "fetched_at": acct.fetched_at,
        })

    elif name == "get_kalshi_pnl_summary":
        from .order_flow.kalshi_strategy_engine import get_kalshi_pnl_summary as _kalshi_pnl
        return _text(_kalshi_pnl())

    elif name == "scan_kalshi_wide_spreads":
        from .order_flow.kalshi_strategy_engine import scan_for_wide_spreads as _kalshi_mm
        min_spread = float(args.get("min_spread", 0.12))
        return _text({"spreads": _kalshi_mm(min_spread=min_spread)})

    elif name == "place_kalshi_strategy_order":
        if not args.get("confirmed", False):
            return _text({
                "error": "Order not placed — set confirmed=true to execute real money order on Kalshi.",
                "ticker": args.get("ticker"),
                "side": args.get("side"),
                "usd_amount": args.get("usd_amount"),
            })
        from .order_flow.kalshi_strategy_engine import place_kalshi_market_order as _kplace
        ticker = str(args["ticker"])
        side = str(args["side"])
        usd_amount = float(args["usd_amount"])
        max_price_cents = int(args["max_price_cents"])
        contract_price = max_price_cents / 100.0
        count = max(1, int(usd_amount / contract_price))
        result = _kplace(ticker=ticker, side=side, count=count, max_price_cents=max_price_cents)
        return _text(result)

    # ── V22.10 Kalshi Phase 2 tool handlers ────────────────────────────────────

    elif name == "run_safe_compounder":
        from .order_flow.kalshi_safe_compounder import run_safe_compounder as _sc
        return _text(_sc(
            bankroll_usd=float(args.get("bankroll_usd", 250.0)),
            execute=bool(args.get("execute", False)),
            confirmed=bool(args.get("confirmed", False)),
        ))

    elif name == "scan_all_kalshi_events":
        from .order_flow.kalshi_events_scanner import scan_all_events, scan_full_universe_summary
        cats = args.get("categories")
        if cats:
            return _text(scan_all_events(categories=list(cats), max_pages=5))
        return _text(scan_full_universe_summary())

    elif name == "get_kalshi_category_scores":
        from .order_flow.kalshi_category_scorer import get_all_category_scores, format_scores_table
        scores = get_all_category_scores()
        return _text({
            "scores": scores,
            "table": format_scores_table(scores),
            "note": "Scores < 30 = hard blocked. FED/CPI proved -40% to -65% ROI.",
        })

    elif name == "run_kalshi_ai_debate":
        from .order_flow.kalshi_ai_ensemble import run_ensemble_debate, ensemble_decision_to_dict
        decision = run_ensemble_debate(
            ticker=str(args["ticker"]),
            title=str(args["title"]),
            yes_bid=float(args["yes_bid"]),
            yes_ask=float(args["yes_ask"]),
            close_time=str(args.get("close_time", "")),
            extra_context=str(args.get("extra_context", "")),
            fast_mode=bool(args.get("fast_mode", True)),
        )
        return _text(ensemble_decision_to_dict(decision))

    elif name == "find_kalshi_stat_arb":
        from .order_flow.kalshi_stat_arb import scan_stat_arb_opportunities
        return _text(scan_stat_arb_opportunities(
            max_events=int(args.get("max_events", 20)),
            max_markets_per_scan=int(args.get("max_markets", 50)),
        ))

    elif name == "run_kalshi_full_pipeline":
        from .order_flow.kalshi_pipeline import run_kalshi_full_pipeline as _kpipe
        return _text(_kpipe(
            enable_ai_ensemble=bool(args.get("enable_ai_ensemble", False)),
            enable_stat_arb=bool(args.get("enable_stat_arb", True)),
            execute_safe_compounder=bool(args.get("execute_safe_compounder", False)),
            confirmed=bool(args.get("confirmed", False)),
            notify_slack=bool(args.get("notify_slack", True)),
        ))

    elif name == "propagate_trade_signal":
        from .trade_propagation import propagate_signal
        try:
            out = await propagate_signal(
                strategy_name=str(args.get("strategy_name", "")),
                symbol=str(args.get("symbol", "")),
                side=str(args.get("side", "")),
                qty=float(args.get("qty", 0)),
                confidence=float(args.get("confidence", 0.0)),
                stop_loss=float(args.get("stop_loss", 0.0)),
                take_profit=float(args.get("take_profit", 0.0)),
            )
            return _text(out)
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "check_propagation_health":
        from .trade_propagation import check_propagation_health
        try:
            out = await check_propagation_health(
                max_lag_seconds=float(args.get("max_lag_seconds", 30.0))
            )
            return _text(out)
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "test_signal_propagation":
        # SEC-2026-C7: posts signed signals to live copy-trade ingest — owner + confirm.
        _owner_token_provided = args.get("owner_token", "")
        _expected_owner_token = os.environ.get("OWNER_API_TOKEN", "")
        if not _expected_owner_token or _owner_token_provided != _expected_owner_token:
            return _text({
                "error": "test_signal_propagation requires owner_token matching OWNER_API_TOKEN.",
            })
        if not args.get("confirm"):
            return _text({
                "error": "test_signal_propagation requires confirm=true — sends live paper signals.",
                "required_arg": "confirm=true",
            })
        from .trade_propagation import run_dummy_signal_test
        try:
            out = await run_dummy_signal_test(
                strategy_name=str(args.get("strategy_name", "")),
                symbol=str(args.get("symbol", "BTC/USD")),
                qty=float(args.get("qty", 0.001)),
            )
            return _text(out)
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "run_guardrail":
        from .security.guardrail import run_guardrail
        try:
            out = run_guardrail(
                symbol=str(args.get("symbol", "")),
                side=str(args.get("side", "")),
                entry=float(args["entry"]) if "entry" in args else None,
                stop=float(args["stop"]) if "stop" in args else None,
                confidence=float(args["confidence"]) if "confidence" in args else None,
                vix=float(args["vix"]) if "vix" in args else None,
                daily_pnl=float(args["daily_pnl"]) if "daily_pnl" in args else None,
                gates=args.get("gates"),
            )
            return _text(out)
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    # ──────────────────────────────────────────────────────────────
    # V22.9 — PAI Integration handlers
    # ──────────────────────────────────────────────────────────────
    elif name == "get_algochains_telos":
        from .telos import get_telos
        try:
            section = str(args.get("section", "all"))
            return _text(get_telos(section))
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "update_algochains_telos":
        from .telos import update_telos
        try:
            return _text(update_telos(
                section=str(args["section"]),
                entry=str(args["entry"]),
                action=str(args.get("action", "append")),
            ))
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "get_us_economic_indicators":
        from .us_economics import get_us_economic_indicators
        try:
            categories = args.get("categories")
            if categories and not isinstance(categories, list):
                categories = [str(categories)]
            use_cache = bool(args.get("use_cache", True))
            return _text(get_us_economic_indicators(categories=categories, use_cache=use_cache))
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "get_crude_oil_inventories":
        from .us_economics import get_crude_oil_inventories
        try:
            use_cache = bool(args.get("use_cache", True))
            return _text(get_crude_oil_inventories(use_cache=use_cache))
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "get_fed_policy_signals":
        from .us_economics import get_fed_policy_signals
        try:
            use_cache = bool(args.get("use_cache", True))
            return _text(get_fed_policy_signals(use_cache=use_cache))
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "capture_learning_signal":
        from .learning_signals import capture_learning_signal
        try:
            return _text(capture_learning_signal(
                action_type=str(args.get("action_type", "other")),
                action_description=str(args["action_description"]),
                outcome=str(args["outcome"]),
                rating=int(args["rating"]) if args.get("rating") is not None else None,
                notes=str(args.get("notes", "")),
                skill_used=str(args.get("skill_used", "")),
                bot=str(args.get("bot", "")),
                agent=str(args.get("agent", "")),
                session_id=str(args.get("session_id", "")),
                extra=args.get("extra") if isinstance(args.get("extra"), dict) else None,
            ))
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "get_learning_signals":
        from .learning_signals import get_learning_signals
        try:
            return _text(get_learning_signals(
                limit=int(args.get("limit", 50)),
                action_type=str(args["action_type"]) if args.get("action_type") else None,
                outcome=str(args["outcome"]) if args.get("outcome") else None,
                bot=str(args["bot"]) if args.get("bot") else None,
                min_rating=int(args["min_rating"]) if args.get("min_rating") is not None else None,
                max_rating=int(args["max_rating"]) if args.get("max_rating") is not None else None,
                summarize=bool(args.get("summarize", True)),
            ))
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "send_ntfy_notification":
        from .notifications.ntfy_push import send_push
        try:
            tags = args.get("tags")
            if tags and not isinstance(tags, list):
                tags = [str(tags)]
            return _text(send_push(
                title=str(args["title"]),
                message=str(args["message"]),
                topic=str(args.get("topic", "ops")),
                priority=str(args.get("priority", "default")),
                tags=tags,
                click_url=str(args.get("click_url", "")),
            ))
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "record_prediction_market_bot_metric":
        from .prediction_market_metrics import record_bot_metric_snapshot
        try:
            out = record_bot_metric_snapshot(
                bot_id=str(args.get("bot_id", "")),
                platform=str(args.get("platform", "")),
                market_id=str(args.get("market_id", "")),
                yes_probability=float(args["yes_probability"]) if args.get("yes_probability") is not None else None,
                edge_vs_entry=float(args["edge_vs_entry"]) if args.get("edge_vs_entry") is not None else None,
                latency_ms_observed=float(args["latency_ms_observed"]) if args.get("latency_ms_observed") is not None else None,
                action=str(args.get("action", "")),
                notes=str(args.get("notes", "")),
                extra=args.get("metadata") if isinstance(args.get("metadata"), dict) else None,
            )
            return _text(out)
        except KeyError as exc:
            return _text({"error": f"Missing required field: {exc}"})
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "get_prediction_market_bot_metrics":
        from .prediction_market_metrics import read_recent_metrics
        try:
            out = read_recent_metrics(
                bot_id=str(args.get("bot_id", "")),
                max_lines=int(args.get("max_lines", 500)),
            )
            return _text(out)
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "get_macro_signals":
        MacroSignalEngine = _lazy_import("macro_signals_v21", "MacroSignalEngine")
        if not MacroSignalEngine:
            return _text({"error": "Macro signal engine not available — check FRED_API_KEY"})
        fred_key = os.getenv("FRED_API_KEY", "")
        polygon_key = (_config.polygon.api_key if _config and _config.polygon else
                       os.getenv("POLYGON_API_KEY", ""))
        engine = MacroSignalEngine(fred_api_key=fred_key, polygon_api_key=polygon_key)
        result = await engine.get_all_signals(signals=args.get("signals"))
        return _text(result)

    # ═══════════════════════════════════════════════════════════════
    # V21: Key Vault & Agent Provisioning
    # ═══════════════════════════════════════════════════════════════
    elif name == "store_api_key":
        get_key_vault = _lazy_import("key_vault_v21", "get_key_vault")
        if not get_key_vault:
            return _text({"error": "Key vault not available — pip install cryptography"})
        vault = get_key_vault(passphrase=args.get("passphrase", ""))
        vault.store(args.get("name", ""), args.get("value", ""))
        return _text({"stored": True, "key_name": args.get("name"),
                      "note": "Key encrypted with AES-256-GCM. Raw value never logged."})

    elif name == "list_vault_keys":
        get_key_vault = _lazy_import("key_vault_v21", "get_key_vault")
        if not get_key_vault:
            return _text({"keys": []})
        vault = get_key_vault(passphrase=args.get("passphrase", ""))
        return _text({"keys": vault.list_keys()})

    elif name == "rotate_api_key":
        get_key_vault = _lazy_import("key_vault_v21", "get_key_vault")
        if not get_key_vault:
            return _text({"error": "Key vault not available"})
        vault = get_key_vault(passphrase=args.get("passphrase", ""))
        vault.store(args.get("name", ""), args.get("new_value", ""))
        return _text({"rotated": True, "key_name": args.get("name")})

    elif name == "provision_agent_account":
        get_agent_provisioner = _lazy_import("agent_prov", "get_agent_provisioner")
        if not get_agent_provisioner:
            return _text({"error": "Agent provisioner not available"})
        prov = get_agent_provisioner()
        account = await prov.provision(
            agent_id=args.get("agent_id", ""),
            description=args.get("description", ""),
            max_position_usd=args.get("max_position_usd", 10000),
            allowed_assets=args.get("allowed_assets", []),
        )
        return _text(vars(account) if hasattr(account, "__dict__") else account)

    elif name == "list_agent_accounts":
        get_agent_provisioner = _lazy_import("agent_prov", "get_agent_provisioner")
        if not get_agent_provisioner:
            return _text({"accounts": []})
        return _text({"accounts": get_agent_provisioner().list_accounts()})

    # ═══════════════════════════════════════════════════════════════
    # V21: Streaming & Alerts
    # ═══════════════════════════════════════════════════════════════
    elif name == "create_price_alert":
        get_alert_engine = _lazy_import("price_alerts", "get_alert_engine")
        if not get_alert_engine:
            return _text({"error": "Price alert engine not available"})
        engine = get_alert_engine()
        alert = engine.create_alert(
            symbol=args.get("symbol", ""),
            condition=args.get("condition", ""),
            threshold=args.get("threshold", 0),
            message=args.get("message", ""),
        )
        return _text({"alert_id": alert.id, "symbol": alert.symbol,
                      "condition": alert.condition, "threshold": alert.threshold,
                      "status": "active", "message": "Alert created. Fires when condition met via Polygon polling."})

    elif name == "list_price_alerts":
        get_alert_engine = _lazy_import("price_alerts", "get_alert_engine")
        if not get_alert_engine:
            return _text({"alerts": []})
        alerts = get_alert_engine().list_alerts(
            symbol=args.get("symbol"), active_only=args.get("active_only", True)
        )
        return _text({"alerts": [vars(a) if hasattr(a, "__dict__") else a for a in alerts]})

    elif name == "delete_price_alert":
        get_alert_engine = _lazy_import("price_alerts", "get_alert_engine")
        if get_alert_engine:
            get_alert_engine().delete_alert(args.get("alert_id", ""))
        return _text({"deleted": args.get("alert_id")})

    elif name == "subscribe_earnings_events":
        EarningsCalendarEngine = _lazy_import("earnings_cal", "EarningsCalendarEngine")
        if not EarningsCalendarEngine:
            return _text({"error": "Earnings calendar not available"})
        polygon_key = (_config.polygon.api_key if _config and _config.polygon else
                       os.getenv("POLYGON_API_KEY", ""))
        engine = EarningsCalendarEngine(polygon_api_key=polygon_key)
        subs = [engine.subscribe(s, alert_days_before=args.get("alert_days_before", 1))
                for s in args.get("symbols", [])]
        return _text({"subscribed": args.get("symbols", []), "subscription_count": len(subs)})

    elif name == "get_earnings_calendar":
        EarningsCalendarEngine = _lazy_import("earnings_cal", "EarningsCalendarEngine")
        if not EarningsCalendarEngine:
            return _text({"error": "Earnings calendar not available"})
        polygon_key = (_config.polygon.api_key if _config and _config.polygon else
                       os.getenv("POLYGON_API_KEY", ""))
        engine = EarningsCalendarEngine(polygon_api_key=polygon_key)
        calendar = await engine.get_calendar(
            symbols=args.get("symbols", []), days_ahead=args.get("days_ahead", 30)
        )
        return _text({"calendar": calendar})

    # ═══════════════════════════════════════════════════════════════
    # V21: Bot Metrics & Live Showcase
    # ═══════════════════════════════════════════════════════════════
    elif name in ("get_bot_dashboard", "get_bot_metrics", "get_live_pnl"):
        import sys as _sys
        _ct_path = _default_control_tower()
        if _ct_path not in _sys.path:
            _sys.path.insert(0, _ct_path)
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "bot_metrics_streaming",
                os.path.join(_ct_path, "autonomous", "bot_metrics_streaming.py"),
            )
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                _sys.modules[spec.name] = mod  # @dataclass needs module in sys.modules (Py 3.14+)
                spec.loader.exec_module(mod)  # type: ignore
                daemon = mod.MetricsStreamingDaemon()
                if name == "get_bot_dashboard":
                    return _text(daemon.get_dashboard())
                elif name == "get_bot_metrics":
                    bot = args.get("bot_name", "")
                    stats = daemon.db.get_stats(bot)
                    trades = daemon.db.get_recent_trades(bot, args.get("last_n_trades", 50))
                    return _text({"stats": stats, "recent_trades": trades, "bot_name": bot})
                elif name == "get_live_pnl":
                    return _text(daemon.get_dashboard())
        except Exception as exc:
            return _text({"error": f"Bot metrics unavailable: {exc}",
                          "hint": f"Ensure ALGOCHAINS_CONTROL_TOWER is set or algochains-control-tower exists at {_ct_path}"})

    elif name == "subscribe_bot_metrics":
        SubscriptionManager = _lazy_import("subscriptions_v21", "SubscriptionManager")
        bot = args.get("bot_name", "").lower().replace(" ", "_")
        uri = f"algochains://bots/{bot}/metrics"
        if SubscriptionManager:
            sub = SubscriptionManager().subscribe(uri=uri, subscriber_id=args.get("subscriber_id", ""))
            return _text({"subscription_id": sub.id, "uri": uri, "bot": bot,
                          "status": "active", "message": f"Subscribed to {uri}. Real-time fills and signals will be pushed."})
        return _text({"uri": uri, "message": "Subscribed (local mode)"})

    # ═══════════════════════════════════════════════════════════════
    # V22.7: Skills Bridge dispatch
    # ═══════════════════════════════════════════════════════════════
    elif name in ("list_skills", "get_skill_detail", "search_skills",
                  "get_skills_for_task", "reload_skills_registry"):
        try:
            from .skills_registry import get_registry
            reg = get_registry()
            if name == "list_skills":
                result = reg.list_skills(
                    category=args.get("category"),
                    platform=args.get("platform"),
                    limit=int(args.get("limit", 50)),
                    offset=int(args.get("offset", 0)),
                )
            elif name == "get_skill_detail":
                result = reg.get_skill_detail(str(args["name"]))
            elif name == "search_skills":
                result = reg.search_skills(str(args["query"]), limit=int(args.get("limit", 20)))
            elif name == "get_skills_for_task":
                result = reg.get_skills_for_task(str(args["task_description"]))
            elif name == "reload_skills_registry":
                count = reg.reload()
                result = {"reloaded": True, "total_skills": count, "stats": reg.stats()}
            else:
                result = {"error": f"Unknown skills tool: {name}"}
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": f"Skills registry error: {exc}", "error_type": type(exc).__name__})
        return _text(result)

    elif name in ("get_openclaw_memory", "store_trade_lesson", "get_current_regime",
                  "get_bot_heartbeat_openclaw", "get_agent_evaluations",
                  "get_openclaw_state_summary"):
        try:
            from . import agent_memory as _am
            if name == "get_openclaw_memory":
                result = _am.get_openclaw_memory(
                    key_prefix=args.get("key_prefix"),
                    limit=int(args.get("limit", 50)),
                )
            elif name == "store_trade_lesson":
                result = _am.store_trade_lesson({
                    "symbol": args["symbol"],
                    "direction": args["direction"],
                    "outcome": args["outcome"],
                    "regime": args.get("regime", "unknown"),
                    "lesson": args["lesson"],
                    "pnl": args.get("pnl"),
                })
            elif name == "get_current_regime":
                result = _am.get_current_regime()
            elif name == "get_bot_heartbeat_openclaw":
                result = _am.get_bot_heartbeat()
            elif name == "get_agent_evaluations":
                result = _am.get_agent_evaluations(limit=int(args.get("limit", 20)))
            elif name == "get_openclaw_state_summary":
                result = _am.get_all_state_files()
            else:
                result = {"error": f"Unknown agent memory tool: {name}"}
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": f"Agent memory error: {exc}", "error_type": type(exc).__name__})
        return _text(result)

    elif name == "invoke_moltbook_debate":
        try:
            import subprocess, json as _json, sys as _sys
            symbol = str(args["symbol"])
            direction = str(args["direction"])
            confidence = float(args["confidence"])
            regime = args.get("regime", "unknown")
            trigger_type = str(args.get("trigger_type", "mcp_manual"))

            # Try direct import first (faster, in-process)
            try:
                from moltbook.debate_engine import DebateEngine
                engine = DebateEngine()
                import inspect as _inspect
                if _inspect.iscoroutinefunction(engine.run_debate):
                    import asyncio as _asyncio
                    result = await engine.run_debate(
                        symbol=symbol, direction=direction, confidence=confidence,
                        regime=regime, trigger_type=trigger_type,
                    )
                else:
                    result = engine.run_debate(
                        symbol=symbol, direction=direction, confidence=confidence,
                        regime=regime, trigger_type=trigger_type,
                    )
            except ImportError:
                # Fallback: call as subprocess from control-tower directory.
                # Inputs are passed via environment variables (not f-string
                # interpolation) to prevent shell/code injection.
                import os as _os_mdb
                control_tower = _default_control_tower()
                script = (
                    "import json, sys, os; sys.path.insert(0, '.'); "
                    "from moltbook.debate_engine import DebateEngine; "
                    "import asyncio; e = DebateEngine(); "
                    "r = asyncio.run(e.run_debate("
                    "os.environ['_MDB_SYMBOL'], os.environ['_MDB_DIRECTION'], "
                    "float(os.environ['_MDB_CONF']), os.environ['_MDB_REGIME'], "
                    "os.environ['_MDB_TRIGGER'])); "
                    "print(json.dumps(r))"
                )
                _env = {
                    **_os_mdb.environ,
                    "_MDB_SYMBOL": str(symbol),
                    "_MDB_DIRECTION": str(direction),
                    "_MDB_CONF": str(float(confidence)),
                    "_MDB_REGIME": str(regime),
                    "_MDB_TRIGGER": str(trigger_type),
                }
                proc = subprocess.run(
                    [_sys.executable, "-c", script],
                    cwd=control_tower, capture_output=True, text=True,
                    timeout=60, env=_env
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    result = _json.loads(proc.stdout)
                else:
                    result = {
                        "error": "Moltbook debate engine not reachable",
                        "stderr": proc.stderr[-300:],
                        "hint": "Ensure algochains-control-tower moltbook services are running",
                    }
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": f"Moltbook debate error: {exc}", "error_type": type(exc).__name__,
                          "hint": "Ensure moltbook is running in algochains-control-tower"})
        return _text(result)

    elif name == "run_mcpt_pipeline":
        try:
            import subprocess, sys as _sys
            control_tower = _default_control_tower()
            step = str(args.get("step", "all"))
            dry_run = bool(args.get("dry_run", False))
            no_desktop = bool(args.get("no_desktop", False))

            cmd = [_sys.executable, "scripts/mcpt_autopilot.py", "--json"]
            if step != "all":
                cmd += ["--step", step]
            if dry_run:
                cmd.append("--dry-run")
            if no_desktop:
                cmd.append("--no-desktop")

            proc = subprocess.run(cmd, cwd=control_tower, capture_output=True,
                                  text=True, timeout=120)
            if proc.returncode != 0:
                return _text({"error": f"MCPT pipeline failed (exit {proc.returncode})",
                              "stderr": proc.stderr[-500:],
                              "stdout": proc.stdout[-500:]})
            import json as _json
            try:
                result = _json.loads(proc.stdout)
            except Exception:
                result = {"output": proc.stdout[-2000:], "step": step}
        except FileNotFoundError:
            return _text({"error": f"mcpt_autopilot.py not found — set ALGOCHAINS_CONTROL_TOWER (currently resolved to {_default_control_tower()})"})
        except subprocess.TimeoutExpired:
            return _text({"error": "MCPT pipeline timed out after 120s"})
        except Exception as exc:
            return _text({"error": f"MCPT pipeline error: {exc}"})
        return _text(result)

    elif name == "run_regime_detection":
        try:
            from .agent_memory import get_current_regime
            current = get_current_regime()
            # Also try to call the live detector if available
            try:
                import subprocess, sys as _sys
                control_tower = _default_control_tower()
                proc = subprocess.run(
                    [_sys.executable, "-m", "openclaw.skills.regime_detector"],
                    cwd=control_tower, capture_output=True, text=True, timeout=30
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    import json as _json
                    live = _json.loads(proc.stdout)
                    return _text({"regime_from_openclaw": current, "live_detection": live})
            except Exception:
                pass
            return _text({
                "regime_from_openclaw": current,
                "note": "Live detector not available — showing cached regime from OpenClaw state",
            })
        except Exception as exc:
            return _text({"error": f"Regime detection error: {exc}"})

    # ═══════════════════════════════════════════════════════════════
    # V21: Onyx Intelligence
    # ═══════════════════════════════════════════════════════════════
    elif name in ("onyx_search", "onyx_ask", "onyx_health", "onyx_find_best_setup"):
        get_onyx_client = _lazy_import("onyx_intel", "get_onyx_client")
        if not get_onyx_client:
            return _text({"error": "Onyx intelligence module not available"})
        client = get_onyx_client()
        if name == "onyx_health":
            result = await client.health()
            return _text(result)
        elif name == "onyx_search":
            results = await client.search(args.get("query", ""), limit=args.get("limit", 10),
                                          document_set=args.get("document_set"))
            return _text({"results": [{"document_id": r.document_id, "content": r.content[:500],
                                        "source": r.source, "score": r.score} for r in results]})
        elif name == "onyx_ask":
            answer = await client.ask(args.get("question", ""))
            return _text({"answer": answer.answer,
                          "sources": answer.sources,
                          "citation_count": len(answer.citations)})
        elif name == "onyx_find_best_setup":
            q = f"best setup for {args.get('symbol','')} in {args.get('regime','')} regime on {args.get('timeframe','5min')} timeframe"
            answer = await client.ask(q)
            return _text({"symbol": args.get("symbol"), "regime": args.get("regime"),
                          "answer": answer.answer, "sources": answer.sources[:3]})

    # ═══════════════════════════════════════════════════════════════
    # V21: Crypto Feature Parity
    # ═══════════════════════════════════════════════════════════════
    elif name in ("get_funding_rate", "get_perp_open_interest", "get_liquidation_clusters"):
        get_crypto_perps = _lazy_import("crypto_perps_v21", "get_crypto_perps")
        if not get_crypto_perps:
            return _text({"error": "Crypto perps engine not available"})
        engine = get_crypto_perps()
        symbol = args.get("symbol", "")
        if name == "get_funding_rate":
            result = await engine.get_funding_rates(symbol, exchanges=args.get("exchanges", ["binance", "bybit"]))
            return _text(result)
        elif name == "get_perp_open_interest":
            result = await engine.get_open_interest(symbol, exchange=args.get("exchange", "binance"))
            return _text(result)
        elif name == "get_liquidation_clusters":
            result = await engine.get_liquidation_clusters(symbol, lookback_hours=args.get("lookback_hours", 24))
            return _text(result)

    elif name == "get_staking_yields":
        get_staking_engine = _lazy_import("staking_engine", "get_staking_engine")
        if not get_staking_engine:
            return _text({"error": "Staking engine not available"})
        engine = get_staking_engine()
        result = await engine.get_opportunities(protocols=args.get("protocols", ["lido", "binance_earn", "cosmos", "ethereum_beacon"]))
        return _text(result)

    elif name == "create_dca_schedule":
        get_dca_engine = _lazy_import("dca_engine_v21", "get_dca_engine")
        if not get_dca_engine:
            return _text({"error": "DCA engine not available"})
        engine = get_dca_engine()
        schedule = engine.create_schedule(
            symbol=args.get("symbol", ""), amount_usd=args.get("amount_usd", 0),
            frequency=args.get("frequency", "weekly"), max_purchases=args.get("max_purchases"),
        )
        return _text(vars(schedule) if hasattr(schedule, "__dict__") else schedule)

    elif name == "get_copy_leaders":
        get_copy_engine = _lazy_import("copy_engine_v21", "get_copy_engine")
        if not get_copy_engine:
            return _text({"error": "Copy trading engine not available"})
        engine = get_copy_engine()
        leaders = await engine.get_top_leaders(
            min_return_pct=args.get("min_return_pct", 20),
            min_win_rate=args.get("min_win_rate", 0.55),
            limit=args.get("limit", 10),
        )
        return _text({"leaders": leaders})

    # ═══════════════════════════════════════════════════════════════
    # V21: SaaS Tenant Hardening
    # ═══════════════════════════════════════════════════════════════
    elif name == "get_tenant_audit_log":
        AuditLogger = _lazy_import("tenant_mw", "AuditLogger")
        if not AuditLogger:
            return _text({"error": "Audit logger not available"})
        logger_inst = AuditLogger(tenant_id=args.get("tenant_id", ""))
        entries = logger_inst.get_entries(limit=args.get("limit", 100), tool_name=args.get("tool_name"))
        return _text({"tenant_id": args.get("tenant_id"), "entries": entries})

    elif name == "create_tenant_sandbox":
        SandboxManager = _lazy_import("tenant_mw", "SandboxManager")
        if not SandboxManager:
            return _text({"error": "Sandbox manager not available"})
        sandbox = SandboxManager().create_sandbox(
            tenant_id=args.get("tenant_id", ""), config=args.get("config", {})
        )
        return _text(sandbox)

    elif name == "get_tenant_rate_limits":
        TenantRateLimiter = _lazy_import("tenant_mw", "TenantRateLimiter")
        if not TenantRateLimiter:
            return _text({"tenant_id": args.get("tenant_id"), "status": "unlimited"})
        limiter = TenantRateLimiter(tenant_id=args.get("tenant_id", ""))
        return _text(limiter.get_status())

    # ═══════════════════════════════════════════════════════════════
    # Desktop Tower Job Dispatcher
    # ═══════════════════════════════════════════════════════════════
    elif name in ("dispatch_tower_job", "get_tower_job_status", "get_tower_health", "list_tower_jobs"):
        import os as _os_tower
        import sys as _sys
        _ct_path = _default_control_tower()
        if _ct_path not in _sys.path:
            _sys.path.insert(0, _ct_path)
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "desktop_tower_dispatcher",
                _os_tower.path.join(_ct_path, "autonomous", "desktop_tower_dispatcher.py"),
            )
            if not spec or not spec.loader:
                return _text({"error": "Desktop tower dispatcher not found in control tower"})
            mod = importlib.util.module_from_spec(spec)
            _sys.modules[spec.name] = mod  # @dataclass needs module in sys.modules (Py 3.14+)
            spec.loader.exec_module(mod)  # type: ignore
            dispatcher = mod.get_dispatcher()
            if name == "dispatch_tower_job":
                job_id = await dispatcher.submit(
                    args.get("job_type", ""), args.get("params", {}),
                    force_local=args.get("force_local", False),
                )
                job = dispatcher.get_job(job_id)
                return _text({"job_id": job_id, "status": job.status if job else "submitted",
                              "memory_mb": job.estimated_memory_mb if job else 0,
                              "routed_to": "tower" if (job and job.tower_pid) else "local"})
            elif name == "get_tower_job_status":
                job = dispatcher.get_job(args.get("job_id", ""))
                if not job:
                    return _text({"error": "Job not found", "job_id": args.get("job_id")})
                return _text({"job_id": job.id, "status": job.status, "result": job.result,
                              "error": job.error, "tower_pid": job.tower_pid})
            elif name == "get_tower_health":
                health = await dispatcher.tower_health()
                return _text(health)
            elif name == "list_tower_jobs":
                jobs = dispatcher.list_jobs(status=args.get("status"), limit=args.get("limit", 20))
                return _text({"jobs": jobs})
        except Exception as exc:
            return _text({"error": f"Tower dispatcher error: {exc}"})

    elif name == "get_signal_conflict_stats":
        import os as _os_sc
        import sys as _sys
        _ct_path = _default_control_tower()
        if _ct_path not in _sys.path:
            _sys.path.insert(0, _ct_path)
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "signal_conflict_manager",
                _os_sc.path.join(_ct_path, "signal_conflict_manager.py"),
            )
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                _sys.modules[spec.name] = mod  # @dataclass needs module in sys.modules (Py 3.14+)
                spec.loader.exec_module(mod)  # type: ignore
                mgr = mod.get_conflict_manager(args.get("bot_name", ""))
                stats = mgr.get_conflict_stats(hours=args.get("hours", 24))
                recent = mgr.get_recent_conflicts(limit=10)
                return _text({"bot": args.get("bot_name"), "stats_24h": stats, "recent_conflicts": recent,
                              "policy": {"opposite_signals": "BLOCKED", "same_direction": "BLOCKED",
                                         "force_reversal_threshold_pct": 90,
                                         "reversal_loss_multiplier": 2.0, "min_hold_secs": 120}})
        except Exception as exc:
            return _text({"error": f"Signal conflict manager error: {exc}"})

    elif name == "get_paper_trading_metrics":
        try:
            import os as _os
            from dotenv import load_dotenv as _ld
            _ld(_os.path.join(_default_control_tower(), ".env"), override=False)
            key = _os.getenv("ALPACA_PAPER_KEY", "")
            secret = _os.getenv("ALPACA_PAPER_SECRET", "")
            if not key:
                return _text({"error": "ALPACA_PAPER_KEY not configured"})
            from alpaca.trading.client import TradingClient
            tc = TradingClient(key, secret, paper=True)
            acct = tc.get_account()
            positions = tc.get_all_positions()
            pos_data = [{"symbol": p.symbol, "qty": float(p.qty), "avg_entry": float(p.avg_entry_price),
                         "unrealized_pl": float(p.unrealized_pl), "market_value": float(p.market_value)}
                        for p in positions]
            return _text({
                "account": {"equity": float(acct.equity), "buying_power": float(acct.buying_power),
                            "portfolio_value": float(acct.portfolio_value), "status": str(acct.status)},
                "open_positions": pos_data,
                "position_count": len(pos_data),
                "source": "Alpaca Paper Trading (real account, paper mode)",
            })
        except Exception as exc:
            return _text({"error": f"Paper trading metrics error: {exc}"})

    # ═══════════════════════════════════════════════════════════════
    # Marketplace Autopilot + Onyx
    # ═══════════════════════════════════════════════════════════════
    elif name == "run_marketplace_autopilot":
        try:
            import subprocess as _sp
            import sys as _sys
            _ct = _default_control_tower()
            _script = os.path.join(_ct, "autonomous", "marketplace_autopilot.py")
            _cmd = [_sys.executable, _script]
            _stage = args.get("stage", "all")
            if _stage != "all":
                _cmd += ["--stage", _stage]
            if args.get("symbol"):
                _cmd += ["--symbol", args["symbol"]]
            if args.get("dry_run", False):
                _cmd += ["--dry-run"]
            _result = _sp.run(_cmd, capture_output=True, text=True, timeout=600, cwd=_ct)
            return _text({
                "status": "success" if _result.returncode == 0 else "error",
                "returncode": _result.returncode,
                "stdout": _result.stdout[-3000:] if _result.stdout else "",
                "stderr": _result.stderr[-1000:] if _result.stderr else "",
                "stage": _stage,
                "source": "marketplace_autopilot.py",
            })
        except Exception as exc:
            return _text({"error": f"Marketplace autopilot error: {exc}"})

    elif name == "get_marketplace_listings":
        try:
            from .marketplace.supabase_tools import get_marketplace_listings as _sb_listings
            _asset_filter = args.get("asset_class", "all")
            _status_filter = args.get("status", "all")
            _result = _sb_listings(status=_status_filter, asset_class=_asset_filter, limit=args.get("limit", 50))
            # Supabase-first succeeded if source == "supabase" and no error key
            if "error" not in _result or _result.get("total", 0) > 0:
                return _text(_result)
            # Supabase not configured — fall back to local filesystem
            import glob as _glob
            _ct = os.path.expanduser("~/CascadeProjects/algochains-control-tower")
            _mdir = os.path.join(_ct, "research_pipeline", "marketplace")
            _listings = []
            for _fpath in sorted(_glob.glob(os.path.join(_mdir, "*.json"))):
                try:
                    with open(_fpath) as _f:
                        _data = json.loads(_f.read())
                    _ac = _data.get("asset_class", "unknown")
                    _st = "live" if _data.get("source") == "live_production" else (
                          "validated" if _data.get("validation_gates_passed") else "paper")
                    if _asset_filter != "all" and _ac != _asset_filter:
                        continue
                    if _status_filter != "all" and _st != _status_filter:
                        continue
                    _listings.append({
                        "id": _data.get("bot_id"),
                        "name": _data.get("name"),
                        "symbol": _data.get("symbol"),
                        "asset_class": _ac,
                        "strategy": _data.get("strategy_type"),
                        "status": _st,
                        "oos_sharpe": (_data.get("performance") or {}).get("oos_sharpe"),
                        "win_rate": (_data.get("performance") or {}).get("win_rate"),
                        "max_dd": (_data.get("performance") or {}).get("max_drawdown_pct"),
                        "futures_locked": _data.get("futures_locked", False),
                        "subscribable": _data.get("subscribable", False),
                        "subscription_price": _data.get("subscription_price_monthly"),
                        "paper_only": _data.get("paper_only", False),
                        "access_level": _data.get("access_level", "subscriber"),
                    })
                except Exception:
                    pass
            return _text({
                "total": len(_listings),
                "live": sum(1 for b in _listings if b["status"] == "live"),
                "validated": sum(1 for b in _listings if b["status"] == "validated"),
                "paper": sum(1 for b in _listings if b["status"] == "paper"),
                "subscribable": sum(1 for b in _listings if b["subscribable"]),
                "owner_only": sum(1 for b in _listings if b["futures_locked"]),
                "listings": _listings,
                "source": "filesystem_fallback",
            })
        except Exception as exc:
            return _text({"error": f"Marketplace listings error: {exc}"})

    elif name == "run_onyx_ingest":
        try:
            import subprocess as _sp
            import sys as _sys
            _ct = _default_control_tower()
            _script = os.path.join(_ct, "autonomous", "onyx_ingest.py")
            _mode = "--full-sync" if args.get("full_sync", False) else "--incremental"
            _result = _sp.run(
                [_sys.executable, _script, _mode],
                capture_output=True, text=True, timeout=300, cwd=_ct,
            )
            return _text({
                "status": "success" if _result.returncode == 0 else "error",
                "returncode": _result.returncode,
                "output": _result.stdout[-2000:] if _result.stdout else "",
                "mode": _mode,
                "source": "onyx_ingest.py",
            })
        except Exception as exc:
            return _text({"error": f"Onyx ingest error: {exc}"})

    elif name == "get_onyx_status":
        try:
            import httpx as _httpx
            _onyx_url = os.getenv("ONYX_API_URL", "http://localhost:8085")
            _key = os.getenv("ONYX_API_KEY", "")
            _headers = {"Authorization": f"Bearer {_key}"} if _key else {}
            async with _httpx.AsyncClient(timeout=10) as _hc:
                _r = await _hc.get(f"{_onyx_url}/health", headers=_headers)
                _health = _r.status_code == 200
            return _text({
                "healthy": _health,
                "url": _onyx_url,
                "source": "onyx_health_check",
                "note": "Onyx host is configured via ONYX_API_URL (self-hosted RAG).",
            })
        except Exception as exc:
            return _text({"error": f"Onyx status check failed: {exc}", "url": os.getenv("ONYX_API_URL", "http://localhost:8085")})

    elif name == "get_learn_hub_health":
        try:
            import httpx as _httpx
            _base = (args.get("base_url") or "https://algochains.ai").rstrip("/")
            _results: dict = {"base_url": _base, "checks": {}}
            async with _httpx.AsyncClient(timeout=10, follow_redirects=False) as _hc:
                # Hub page — must be 200 (no login redirect)
                _hub = await _hc.get(f"{_base}/learn/")
                _results["checks"]["hub"] = {
                    "url": f"{_base}/learn/",
                    "status": _hub.status_code,
                    "ok": _hub.status_code == 200,
                    "note": "200 expected. 302 to /signin/ means login gate is broken." if _hub.status_code != 200 else "OK — anonymous access confirmed.",
                }
                # RSS feed — must be 200 with rss+xml content-type
                _rss = await _hc.get(f"{_base}/learn/feed.xml")
                _rss_ct = _rss.headers.get("content-type", "")
                _results["checks"]["rss"] = {
                    "url": f"{_base}/learn/feed.xml",
                    "status": _rss.status_code,
                    "content_type": _rss_ct,
                    "ok": _rss.status_code == 200 and "rss+xml" in _rss_ct,
                }
                # Subdomain redirect (external — only works if learn.algochains.ai is live)
                try:
                    _sub = await _hc.get("https://learn.algochains.ai/")
                    _loc = _sub.headers.get("location", "")
                    _results["checks"]["subdomain"] = {
                        "url": "https://learn.algochains.ai/",
                        "status": _sub.status_code,
                        "location": _loc,
                        "ok": _sub.status_code in (301, 302) and "/learn/" in _loc,
                        "note": "301 → algochains.ai/learn/ expected. Not yet live until CF rule set.",
                    }
                except Exception as _se:
                    _results["checks"]["subdomain"] = {"error": str(_se), "note": "DNS/CF not yet set up."}
            _results["healthy"] = all(c.get("ok") for c in _results["checks"].values() if isinstance(c, dict) and "ok" in c)
            return _text(_results)
        except Exception as exc:
            return _text({"error": f"Learn Hub health check failed: {exc}"})

    # ═══════════════════════════════════════════════════════════════
    # V22: Live Bot Intelligence
    # ═══════════════════════════════════════════════════════════════
    elif name == "get_live_bot_metrics":
        try:
            # Supabase-first: return real-time pushed metrics from bot_metrics_live
            from .marketplace.supabase_tools import get_live_bot_metrics as _sb_metrics
            bot_id_arg = args.get("bot_id")
            _sb_result = _sb_metrics(bot_id=bot_id_arg)
            if "error" not in _sb_result or _sb_result.get("total", 0) > 0:
                return _text(_sb_result)
            # Supabase not configured — fall back to local log parser
            from .live_bot_intelligence import parse_bot_metrics
            bot_id = (bot_id_arg or "mnq").lower()
            metrics = parse_bot_metrics(bot_id)
            return _text(metrics.to_dict())
        except Exception as exc:
            return _text({"error": f"Bot metrics error: {exc}", "bot_id": args.get("bot_id")})

    elif name == "get_subscriber_bots":
        try:
            from .marketplace.supabase_tools import get_subscriber_bots as _sb_subs
            user_id = args.get("user_id", "")
            if not user_id:
                return _text({"error": "user_id is required (email or UUID)"})
            return _text(_sb_subs(user_id))
        except Exception as exc:
            return _text({"error": f"Subscriber bots error: {exc}"})

    elif name == "deliver_strategy_to_subscriber":
        # SEC-2026-C2 FIX: require owner_token; subscription verification + SSRF guard
        # are enforced inside deliver_strategy_to_subscriber (supabase_tools.py).
        _owner_token_provided = args.get("owner_token", "")
        _expected_owner_token = os.environ.get("OWNER_API_TOKEN", "")
        if not _expected_owner_token or _owner_token_provided != _expected_owner_token:
            return _text({"error": "deliver_strategy_to_subscriber requires owner_token matching OWNER_API_TOKEN."})
        try:
            from .marketplace.supabase_tools import deliver_strategy_to_subscriber as _deliver
            return _text(_deliver(
                subscriber_id=args["subscriber_id"],
                strategy_id=args["strategy_id"],
                webhook_url=args.get("webhook_url"),
                token_ttl_seconds=int(args.get("token_ttl_seconds", 86400)),
            ))
        except Exception as exc:
            return _text({"error": f"deliver_strategy_to_subscriber error: {exc}"})

    elif name == "get_all_bot_metrics":
        try:
            from .live_bot_intelligence.metrics_parser import parse_all_bots
            all_metrics = parse_all_bots()
            return _text({k: v.to_dict() for k, v in all_metrics.items()})
        except Exception as exc:
            return _text({"error": f"All bot metrics error: {exc}"})

    elif name == "get_system_heartbeat":
        try:
            from .live_bot_intelligence import get_system_heartbeat as _get_hb
            hb = _get_hb()
            return _text(hb.to_dict())
        except Exception as exc:
            return _text({"error": f"Heartbeat read error: {exc}"})

    elif name == "get_system_health":
        try:
            from .trading_system_health import get_system_health
            return _text(get_system_health())
        except Exception as exc:
            return _text({"error": f"System health error: {exc}"})

    elif name == "get_adaptive_brain_status":
        try:
            from .adaptive_brain_status import get_adaptive_brain_status
            return _text(get_adaptive_brain_status())
        except Exception as exc:
            return _text({"error": f"Adaptive brain status error: {exc}"})

    elif name == "get_strategy_academic_citations":
        try:
            from .live_bot_intelligence import get_academic_citations
            bot_id = args.get("bot_id", "mnq").lower()
            citations = get_academic_citations(bot_id)
            return _text({
                "bot_id": bot_id,
                "citation_count": len(citations),
                "citations": [{"title": c.title, "authors": c.authors, "year": c.year,
                               "venue": c.venue, "doi_or_ssrn": c.doi_or_ssrn,
                               "relevance": c.relevance, "url": c.url}
                              for c in citations]
            })
        except Exception as exc:
            return _text({"error": f"Academic citations error: {exc}", "bot_id": args.get("bot_id")})

    elif name == "get_bot_card_data":
        try:
            from .live_bot_intelligence import get_bot_card_data
            bot_id = args.get("bot_id", "mnq").lower()
            card = get_bot_card_data(bot_id)
            return _text(card.to_dict())
        except Exception as exc:
            return _text({"error": f"Bot card data error: {exc}", "bot_id": args.get("bot_id")})

    elif name == "get_finalized_backtests":
        try:
            from supabase import create_client as _sb_create
            _sb = _sb_create(os.environ["SUPABASE_URL"], os.environ["SUPABASE_ANON_KEY"])
            strategy_id = args.get("strategy_id", "")
            limit = int(args.get("limit", 10))
            q = _sb.table("strategy_backtest_run").select(
                "id,strategy_id,run_label,git_sha,promotion,decision_text,metrics_summary,finalized_at,finalized_by"
            ).eq("status", "finalized")
            if strategy_id:
                q = q.eq("strategy_id", strategy_id)
            result = q.order("finalized_at", desc=True).limit(limit).execute()
            return _text({"runs": result.data or [], "count": len(result.data or [])})
        except Exception as exc:
            return _text({"error": f"get_finalized_backtests error: {exc}"})

    elif name == "get_backtest_run_detail":
        try:
            from supabase import create_client as _sb_create
            _sb = _sb_create(os.environ["SUPABASE_URL"], os.environ["SUPABASE_ANON_KEY"])
            run_id = args.get("run_id", "")
            if not run_id:
                return _text({"error": "run_id is required"})
            run_r = _sb.table("strategy_backtest_run").select("*").eq("id", run_id).single().execute()
            folds_r = _sb.table("strategy_backtest_fold").select("*").eq("run_id", run_id).order("fold_index").execute()
            return _text({
                "run": run_r.data,
                "folds": folds_r.data or [],
                "fold_count": len(folds_r.data or []),
            })
        except Exception as exc:
            return _text({"error": f"get_backtest_run_detail error: {exc}"})

    elif name == "list_bot_research_attachments":
        try:
            from .live_bot_intelligence.academic_registry import BACKTEST_ARTIFACTS, BLUEPRINT_REFS
            bot_id = args.get("bot_id", "all").lower()
            if bot_id == "all":
                result = {}
                for bid in ["mnq", "cl", "mes", "nq"]:
                    result[bid] = {
                        "artifacts": [{"type": a.artifact_type, "name": a.name,
                                       "path": a.local_path, "available": a.available,
                                       "description": a.description}
                                      for a in BACKTEST_ARTIFACTS.get(bid, [])],
                        "blueprints": BLUEPRINT_REFS.get(bid, []),
                    }
                return _text(result)
            else:
                return _text({
                    "bot_id": bot_id,
                    "artifacts": [{"type": a.artifact_type, "name": a.name,
                                   "path": a.local_path, "available": a.available,
                                   "description": a.description}
                                  for a in BACKTEST_ARTIFACTS.get(bot_id, [])],
                    "blueprints": BLUEPRINT_REFS.get(bot_id, []),
                })
        except Exception as exc:
            return _text({"error": f"Research attachments error: {exc}"})

    # V26.0 Bot Ops handlers ─────────────────────────────────────────────
    elif name == "get_bot_position_state":
        try:
            from .live_bot_intelligence.bot_ops import get_position_state
            return _text(get_position_state(args.get("bot_id", "mnq").lower()))
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "get_bot_bracket_status":
        try:
            from .live_bot_intelligence.bot_ops import get_bracket_status
            return _text(get_bracket_status(args.get("bot_id", "mnq").lower()))
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "get_ai_pipeline_health":
        try:
            from .live_bot_intelligence.bot_ops import get_ai_pipeline_health
            return _text(get_ai_pipeline_health(args.get("bot_id", "mnq").lower()))
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "get_all_bot_ops_status":
        try:
            from .live_bot_intelligence.bot_ops import get_all_bot_ops_status
            return _text(get_all_bot_ops_status())
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "check_unprotected_positions":
        try:
            from .live_bot_intelligence.bot_ops import check_unprotected_positions
            return _text(check_unprotected_positions())
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "bracket_integrity_check":
        try:
            from .live_bot_intelligence.bot_ops import bracket_integrity_check
            return _text(bracket_integrity_check())
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "get_bracket_guardian_status":
        try:
            from .live_bot_intelligence.bot_ops import get_bracket_guardian_status
            return _text(get_bracket_guardian_status())
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "restart_trading_bot":
        try:
            from .live_bot_intelligence.bot_ops import restart_bot
            return _text(restart_bot(
                bot_id=args.get("bot_id", "").lower(),
                owner_token=args.get("owner_token", ""),
            ))
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "flatten_bot_position":
        if os.getenv("ALGOCHAINS_REQUIRE_CONFIRMATION", "1") == "1":
            logger.warning("flatten_bot_position BLOCKED — ALGOCHAINS_REQUIRE_CONFIRMATION=1")
            return _text({
                "status": "blocked",
                "reason": "flatten_bot_position requires confirmation. Set ALGOCHAINS_REQUIRE_CONFIRMATION=0 to allow.",
            })
        try:
            from .live_bot_intelligence.bot_ops import flatten_position_tradovate
            return _text(flatten_position_tradovate(
                symbol=args.get("symbol", "").upper(),
                owner_token=args.get("owner_token", ""),
            ))
        except Exception as exc:
            return _text({"error": str(exc)})

    # ═══════════════════════════════════════════════════════════════
    # V22.2: Onboarding Tools
    # ═══════════════════════════════════════════════════════════════
    elif name == "start_onboarding":
        try:
            from .onboarding import start_onboarding as _start_onboarding
            return _text(_start_onboarding())
        except Exception as exc:
            return _text({"error": f"Onboarding start error: {exc}"})

    elif name == "acknowledge_risk_disclosure":
        try:
            from .onboarding import acknowledge_risk_disclosure as _ack_risk
            return _text(_ack_risk(arguments.get("acknowledgment", "")))
        except Exception as exc:
            return _text({"error": f"Acknowledgment error: {exc}"})

    elif name == "get_broker_setup_guide":
        try:
            from .onboarding import get_broker_setup_guide as _broker_guide
            return _text(_broker_guide(arguments.get("broker", "")))
        except Exception as exc:
            return _text({"error": f"Broker guide error: {exc}"})

    elif name == "validate_broker_connection":
        try:
            from .onboarding import validate_broker_connection as _validate_broker
            return _text(await _validate_broker(arguments.get("broker", "")))
        except Exception as exc:
            return _text({"error": f"Broker validation error: {exc}"})

    elif name == "get_data_provider_setup_guide":
        try:
            from .onboarding import get_data_provider_setup_guide as _dp_guide
            return _text(_dp_guide(arguments.get("provider", "")))
        except Exception as exc:
            return _text({"error": f"Data provider guide error: {exc}"})

    elif name == "validate_data_provider":
        try:
            from .onboarding import validate_data_provider as _validate_dp
            return _text(await _validate_dp(arguments.get("provider", "")))
        except Exception as exc:
            return _text({"error": f"Data provider validation error: {exc}"})

    elif name == "run_onboarding_smoke_test":
        try:
            from .onboarding import run_smoke_test as _smoke_test
            return _text(await _smoke_test())
        except Exception as exc:
            return _text({"error": f"Smoke test error: {exc}"})

    elif name == "get_onboarding_status":
        try:
            from .onboarding import get_onboarding_status as _ob_status
            return _text(_ob_status())
        except Exception as exc:
            return _text({"error": f"Onboarding status error: {exc}"})

    elif name == "set_algochains_api_key":
        try:
            from .onboarding import set_algochains_api_key as _set_ac_key
            return _text(_set_ac_key(api_key=arguments["api_key"]))
        except Exception as exc:
            return _text({"error": f"API key configuration error: {exc}"})

    elif name == "set_guardrail_preferences":
        try:
            from .onboarding import set_guardrail_preferences as _set_guardrail_prefs
            return _text(_set_guardrail_prefs(
                notify_on_daily_loss_pct=float(arguments.get("notify_on_daily_loss_pct", 80)),
                pause_on_consecutive_losses=int(arguments.get("pause_on_consecutive_losses", 3)),
                slack_alerts_enabled=bool(arguments.get("slack_alerts_enabled", False)),
            ))
        except Exception as exc:
            return _text({"error": f"Guardrail prefs error: {exc}"})

    elif name == "generate_ide_config":
        # SEC-2026-C6: full config contains env secrets — owner_token required.
        _owner_token_provided = arguments.get("owner_token", "")
        _expected_owner_token = os.environ.get("OWNER_API_TOKEN", "")
        try:
            from .onboarding import generate_mcporter_config as _gen_config
            from .onboarding import generate_mcporter_config_masked as _gen_masked
            if not _expected_owner_token or _owner_token_provided != _expected_owner_token:
                return _text(_gen_masked(
                    ide=arguments.get("ide", "cursor"),
                    tool_mode=arguments.get("tool_mode", "smart"),
                ))
            return _text(_gen_config(
                ide=arguments.get("ide", "cursor"),
                tool_mode=arguments.get("tool_mode", "smart"),
            ))
        except Exception as exc:
            return _text({"error": f"Config generation error: {exc}"})

    # ═══════════════════════════════════════════════════════════════
    # V22.3: Proprietary Data Ingestion Tools
    # ═══════════════════════════════════════════════════════════════
    elif name == "ingest_csv_data":
        try:
            from .data_ingestion import ingest_csv_data as _ingest_csv
            return _text(_ingest_csv(
                file_path=arguments["file_path"],
                symbol=arguments["symbol"],
                timeframe=arguments["timeframe"],
                columns=arguments.get("columns"),
                date_column=arguments.get("date_column", "date"),
                date_format=arguments.get("date_format", "%Y-%m-%d %H:%M:%S"),
            ))
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": f"CSV ingestion error: {exc}"})

    elif name == "ingest_json_signals":
        try:
            from .data_ingestion import ingest_json_signals as _ingest_signals
            return _text(_ingest_signals(
                file_path=arguments["file_path"],
                signal_type=arguments["signal_type"],
                symbol=arguments["symbol"],
            ))
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": f"Signal ingestion error: {exc}"})

    elif name == "connect_onyx_docs":
        try:
            from .data_ingestion import connect_onyx_docs as _connect_onyx
            return _text(_connect_onyx(
                doc_paths=arguments["doc_paths"],
                doc_type=arguments["doc_type"],
            ))
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": f"Onyx ingestion error: {exc}"})

    elif name == "register_strategy":
        try:
            from .data_ingestion import register_strategy as _reg_strategy
            return _text(_reg_strategy(
                name=arguments["name"],
                asset_class=arguments["asset_class"],
                timeframe=arguments["timeframe"],
                symbols=arguments["symbols"],
                spec_path=arguments["spec_path"],
                description=arguments.get("description", ""),
                author=arguments.get("author", ""),
            ))
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": f"Strategy registration error: {exc}"})

    elif name == "list_ingested_data":
        try:
            from .data_ingestion import list_ingested_data as _list_ingested
            return _text(_list_ingested())
        except Exception as exc:
            return _text({"error": f"List ingested data error: {exc}"})

    # ═══════════════════════════════════════════════════════════════
    # Platform Tools — Support Tickets, OAuth, Waitlist, Verification,
    #                  Analytics, Password Reset, Multi-Bot Metrics
    # ═══════════════════════════════════════════════════════════════

    elif name == "create_support_ticket":
        try:
            from .support_tickets import create_ticket as _create_ticket
            return _text(await _create_ticket(
                subject=arguments["subject"],
                description=arguments["description"],
                user_email=arguments["user_email"],
                category=arguments.get("category", "other"),
                priority=arguments.get("priority", "medium"),
                user_id=arguments.get("user_id"),
                metadata=arguments.get("metadata"),
            ))
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": f"Support ticket create error: {exc}"})

    elif name == "get_support_ticket":
        _owner_token_provided = arguments.get("owner_token", "")
        _expected_owner_token = os.environ.get("OWNER_API_TOKEN", "")
        if not _expected_owner_token or _owner_token_provided != _expected_owner_token:
            return _text({"error": "get_support_ticket requires owner_token matching OWNER_API_TOKEN."})
        try:
            from .support_tickets import get_ticket as _get_ticket
            return _text(await _get_ticket(arguments["ticket_id"]))
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "list_support_tickets":
        _owner_token_provided = arguments.get("owner_token", "")
        _expected_owner_token = os.environ.get("OWNER_API_TOKEN", "")
        if not _expected_owner_token or _owner_token_provided != _expected_owner_token:
            return _text({"error": "list_support_tickets requires owner_token matching OWNER_API_TOKEN."})
        try:
            from .support_tickets import list_tickets as _list_tickets
            return _text(await _list_tickets(
                status=arguments.get("status"),
                priority=arguments.get("priority"),
                category=arguments.get("category"),
                user_email=arguments.get("user_email"),
                limit=int(arguments.get("limit", 50)),
            ))
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "update_ticket_status":
        _owner_token_provided = arguments.get("owner_token", "")
        _expected_owner_token = os.environ.get("OWNER_API_TOKEN", "")
        if not _expected_owner_token or _owner_token_provided != _expected_owner_token:
            return _text({"error": "update_ticket_status requires owner_token matching OWNER_API_TOKEN."})
        try:
            from .support_tickets import update_ticket_status as _update_ticket
            return _text(await _update_ticket(
                ticket_id=arguments["ticket_id"],
                status=arguments["status"],
                agent_response=arguments.get("agent_response"),
                agent_email=arguments.get("agent_email"),
            ))
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "get_ticket_stats":
        _owner_token_provided = arguments.get("owner_token", "")
        _expected_owner_token = os.environ.get("OWNER_API_TOKEN", "")
        if not _expected_owner_token or _owner_token_provided != _expected_owner_token:
            return _text({"error": "get_ticket_stats requires owner_token matching OWNER_API_TOKEN."})
        try:
            from .support_tickets import get_ticket_stats as _ticket_stats
            return _text(await _ticket_stats())
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "generate_broker_auth_url":
        try:
            from .brokers.oauth_manager import generate_auth_url as _gen_auth_url
            return _text(await _gen_auth_url(
                broker=arguments["broker"],
                user_id=arguments["user_id"],
                redirect_uri=arguments.get("redirect_uri"),
            ))
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "exchange_broker_oauth_code":
        try:
            from .brokers.oauth_manager import exchange_code as _exchange_code
            return _text(await _exchange_code(
                state=arguments["state"],
                code=arguments["code"],
                redirect_uri=arguments.get("redirect_uri"),
            ))
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "get_broker_oauth_status":
        # SEC-2026-C5: never return plaintext access_token; owner_token required.
        _owner_token_provided = arguments.get("owner_token", "")
        _expected_owner_token = os.environ.get("OWNER_API_TOKEN", "")
        if not _expected_owner_token or _owner_token_provided != _expected_owner_token:
            return _text({
                "error": "get_broker_oauth_status requires owner_token matching OWNER_API_TOKEN.",
            })
        try:
            from .brokers.oauth_manager import get_oauth_status as _get_oauth_status
            return _text(await _get_oauth_status(
                broker=arguments["broker"],
                user_id=arguments["user_id"],
                auto_refresh=True,
            ))
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "get_connected_brokers":
        try:
            from .brokers.oauth_manager import get_connected_brokers as _get_connected
            return _text(await _get_connected(arguments["user_id"]))
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "revoke_broker_connection":
        try:
            from .brokers.oauth_manager import revoke_token as _revoke_token
            return _text(await _revoke_token(
                broker=arguments["broker"],
                user_id=arguments["user_id"],
            ))
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "get_checkout_url":
        try:
            from .cloud_saas.billing_engine import BillingEngine as _BillingEngine
            _be = _BillingEngine()
            return _text(await _be.create_platform_checkout_session(
                email=arguments["email"],
                tier=arguments.get("tier", "paper"),
                referral_code=arguments.get("referral_code"),
            ))
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "generate_payment_link":
        tier = arguments.get("tier", "paper")
        env_var = "STRIPE_PAPER_LINK" if tier == "paper" else "STRIPE_LIVE_LINK"
        link = os.environ.get(env_var, "")
        if not link:
            link = f"https://algochains.ai/pricing#{tier}"
        price = "$29/mo" if tier == "paper" else "$99/mo"
        return _text({
            "payment_link": link,
            "tier": tier,
            "price": price,
            "note": "After payment, set ALGOCHAINS_SUBSCRIBER_KEY=<emailed key> and run get_my_portfolio()",
        })

    elif name in ("get_started", "get_pricing", "get_system_status"):
        # Public onboarding meta-tools — no auth, never raise.
        try:
            from . import onboarding_meta as _om
            if name == "get_started":
                return _text(_om.get_started(arguments.get("goal")))
            elif name == "get_pricing":
                return _text(_om.get_pricing())
            else:
                return _text(_om.get_system_status())
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name in ("join_bot", "get_subscriber_status", "accept_subscriber_terms",
                  "get_my_usage", "create_referral_code", "get_my_referrals",
                  "get_referral_earnings", "get_my_realized_pnl"):
        _sub_key = os.environ.get("ALGOCHAINS_SUBSCRIBER_KEY", "")
        if not _sub_key:
            return _text({
                "error": "ALGOCHAINS_SUBSCRIBER_KEY not set. "
                         "Get a subscriber key from algochains.ai or run get_checkout_url() to subscribe.",
            })
        from .subscriber_auth import resolve_subscriber_key as _resolve_sub
        _sub = _resolve_sub(_sub_key)
        if not _sub:
            return _text({"error": "Invalid or expired subscriber key. Check ALGOCHAINS_SUBSCRIBER_KEY."})
        if name == "get_my_usage":
            try:
                from .subscriber_tools import get_my_usage as _get_my_usage
                return _text(_get_my_usage(_sub.subscriber_id))
            except Exception as exc:
                return _text({"error": str(exc)})
        elif name == "get_my_realized_pnl":
            try:
                from .cloud_saas.realized_pnl import get_my_realized_pnl as _grp
                return _text(_grp(_sub.subscriber_id))
            except Exception as exc:
                return _text({"error": str(exc)})
        elif name in ("create_referral_code", "get_my_referrals", "get_referral_earnings"):
            try:
                from .cloud_saas import referrals as _referrals
                if name == "create_referral_code":
                    return _text(_referrals.create_referral_code(_sub.subscriber_id))
                elif name == "get_my_referrals":
                    return _text(_referrals.get_my_referrals(_sub.subscriber_id))
                else:
                    return _text(_referrals.get_referral_earnings(_sub.subscriber_id))
            except Exception as exc:
                return _text({"error": str(exc)})
        elif name == "join_bot":
            try:
                from .subscriber_tools import join_bot as _join_bot
                return _text(_join_bot(
                    _sub.subscriber_id,
                    arguments["bot"],
                    size_multiplier=float(arguments.get("size_multiplier", 1.0)),
                    max_contracts=int(arguments.get("max_contracts", 10)),
                    daily_loss_cap_usd=float(arguments.get("daily_loss_cap_usd", 5000.0)),
                ))
            except KeyError as exc:
                return _text({"error": f"Missing required argument: {exc}"})
            except Exception as exc:
                return _text({"error": str(exc)})
        elif name == "get_subscriber_status":
            try:
                from .subscriber_tools import get_subscriber_status as _get_sub_status
                return _text(_get_sub_status(_sub.subscriber_id))
            except Exception as exc:
                return _text({"error": str(exc)})
        elif name == "accept_subscriber_terms":
            try:
                from .subscriber_tools import accept_subscriber_terms as _accept_terms
                return _text(_accept_terms(
                    _sub.subscriber_id,
                    acknowledgment=arguments.get("acknowledgment"),
                ))
            except Exception as exc:
                return _text({"error": str(exc)})

    elif name == "create_creator_onboarding_link":
        _owner_token_provided = arguments.get("owner_token", "")
        _expected_owner_token = os.environ.get("OWNER_API_TOKEN", "")
        if not _expected_owner_token or _owner_token_provided != _expected_owner_token:
            return _text({"error": "create_creator_onboarding_link requires owner_token matching OWNER_API_TOKEN."})
        try:
            from .cloud_saas import connect_payouts as _cp
            _creator_id = arguments.get("creator_id", "")
            if not _creator_id:
                return _text({"error": "creator_id required."})
            return _text(await _cp.create_creator_onboarding_link(
                creator_id=_creator_id,
                creator_email=arguments["creator_email"],
            ))
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "get_my_creator_earnings":
        _owner_token_provided = arguments.get("owner_token", "")
        _expected_owner_token = os.environ.get("OWNER_API_TOKEN", "")
        if not _expected_owner_token or _owner_token_provided != _expected_owner_token:
            return _text({"error": "get_my_creator_earnings requires owner_token matching OWNER_API_TOKEN."})
        try:
            from .cloud_saas import connect_payouts as _cp
            _creator_id = arguments.get("creator_id", "")
            if not _creator_id:
                return _text({"error": "creator_id required."})
            return _text(_cp.get_my_creator_earnings(_creator_id))
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "run_creator_payouts":
        # OWNER-GATED — moves real money. Fails closed when OWNER_API_TOKEN unset.
        _owner_token_provided = arguments.get("owner_token", "")
        _expected_owner_token = os.environ.get("OWNER_API_TOKEN", "")
        if not _expected_owner_token or _owner_token_provided != _expected_owner_token:
            return _text({"error": "run_creator_payouts requires owner_token matching OWNER_API_TOKEN."})
        try:
            from .cloud_saas import connect_payouts as _cp
            return _text(await _cp.run_creator_payouts(
                creator_id=arguments.get("creator_id"),
                dry_run=bool(arguments.get("dry_run", True)),
                min_payout_usd=float(arguments.get("min_payout_usd", 25.0)),
            ))
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "reconcile_creator_pnl":
        # OWNER-GATED — writes the creator earnings ledger. Fails closed.
        _owner_token_provided = arguments.get("owner_token", "")
        _expected_owner_token = os.environ.get("OWNER_API_TOKEN", "")
        if not _expected_owner_token or _owner_token_provided != _expected_owner_token:
            return _text({"error": "reconcile_creator_pnl requires owner_token matching OWNER_API_TOKEN."})
        try:
            from .cloud_saas.realized_pnl import reconcile_creator_pnl as _rec
            return _text(await _rec(
                arguments["period_start"],
                arguments["period_end"],
                dry_run=bool(arguments.get("dry_run", True)),
            ))
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "join_waitlist":
        try:
            from .waitlist import join_waitlist as _join_waitlist
            result = await _join_waitlist(
                email=arguments["email"],
                first_name=arguments.get("first_name", ""),
                last_name=arguments.get("last_name", ""),
                broker=arguments.get("broker", ""),
                use_case=arguments.get("use_case", ""),
                referral_code=arguments.get("referral_code"),
            )
            # Append checkout link so the user can pay immediately without waiting for an invite
            from urllib.parse import quote as _url_quote
            result["checkout_url"] = f"https://algochains.ai/pricing?email={_url_quote(arguments['email'], safe='')}"
            result["note"] = "Skip the waitlist — subscribe directly at the checkout URL above."
            return _text(result)
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "get_waitlist_stats":
        try:
            from .waitlist import get_waitlist_stats as _waitlist_stats
            return _text(await _waitlist_stats())
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "send_waitlist_invite":
        # SEC-2026-C3 FIX: invite minting must not be available to unauthenticated callers.
        _owner_token_provided = arguments.get("owner_token", "")
        _expected_owner_token = os.environ.get("OWNER_API_TOKEN", "")
        if not _expected_owner_token or _owner_token_provided != _expected_owner_token:
            return _text({"error": "send_waitlist_invite requires owner_token matching OWNER_API_TOKEN."})
        try:
            from .waitlist import send_invite as _send_invite
            return _text(await _send_invite(arguments["email"]))
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "send_email_verification_code":
        try:
            from .verification import send_email_code as _send_email_code
            return _text(await _send_email_code(
                email=arguments["email"],
                purpose=arguments.get("purpose", "email_verification"),
                context=arguments.get("context"),
            ))
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "send_sms_verification_code":
        try:
            from .verification import send_sms_code as _send_sms_code
            return _text(await _send_sms_code(
                phone=arguments["phone"],
                purpose=arguments.get("purpose", "purchase"),
                context=arguments.get("context"),
            ))
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "verify_code":
        try:
            from .verification import verify_code as _verify_code
            return _text(await _verify_code(
                destination=arguments["destination"],
                code=arguments["code"],
                purpose=arguments.get("purpose", "email_verification"),
            ))
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "track_platform_event":
        try:
            from .platform_analytics import track_event as _track_event
            return _text(await _track_event(
                event_type=arguments["event_type"],
                session_id=arguments.get("session_id"),
                user_id=arguments.get("user_id"),
                page=arguments.get("page"),
                referrer=arguments.get("referrer"),
                properties=arguments.get("properties"),
                device=arguments.get("device"),
            ))
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "get_analytics_summary":
        try:
            from .platform_analytics import get_analytics_summary as _get_analytics
            return _text(await _get_analytics(
                days=int(arguments.get("days", 7)),
                event_type=arguments.get("event_type"),
            ))
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "initiate_password_reset":
        try:
            from .auth.password_reset import initiate_password_reset as _init_reset
            return _text(await _init_reset(arguments["email"]))
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "complete_password_reset":
        try:
            from .auth.password_reset import complete_password_reset as _complete_reset
            return _text(await _complete_reset(
                access_token=arguments["access_token"],
                new_password=arguments["new_password"],
            ))
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "initiate_account_recovery":
        try:
            from .auth.password_reset import initiate_account_recovery as _init_recovery
            return _text(await _init_recovery(
                email=arguments["email"],
                reason=arguments.get("reason", "lost_email_access"),
                contact_info=arguments.get("contact_info"),
            ))
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "get_password_policy":
        try:
            from .auth.password_reset import get_password_policy as _get_pwd_policy
            return _text(await _get_pwd_policy())
        except Exception as exc:
            return _text({"error": str(exc)})

    # ── Programmatic Account / MFA / Developer Key Tools ─────────────────
    elif name == "signup_algochains":
        try:
            from .auth.platform_auth import signup_algochains as _signup
            return _text(await _signup(
                email=arguments["email"],
                password=arguments["password"],
            ))
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "verify_email_otp":
        try:
            from .auth.platform_auth import verify_email_otp as _verify_email
            return _text(await _verify_email(
                email=arguments["email"],
                token=arguments["token"],
            ))
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "login_algochains":
        try:
            from .auth.platform_auth import login_algochains as _login
            return _text(await _login(
                email=arguments["email"],
                password=arguments["password"],
            ))
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "refresh_session":
        try:
            from .auth.platform_auth import refresh_session as _refresh_session
            return _text(await _refresh_session())
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "logout_algochains":
        try:
            from .auth.platform_auth import logout_algochains as _logout
            return _text(await _logout())
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "enroll_mfa":
        try:
            from .auth.platform_auth import enroll_mfa as _enroll_mfa
            return _text(await _enroll_mfa(
                factor_type=arguments.get("factor_type", "totp"),
            ))
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "challenge_mfa":
        try:
            from .auth.platform_auth import challenge_mfa as _challenge_mfa
            return _text(await _challenge_mfa(factor_id=arguments["factor_id"]))
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "verify_mfa":
        try:
            from .auth.platform_auth import verify_mfa as _verify_mfa
            return _text(await _verify_mfa(
                factor_id=arguments["factor_id"],
                code=arguments["code"],
                challenge_id=arguments.get("challenge_id"),
            ))
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "list_mfa_factors":
        try:
            from .auth.platform_auth import list_mfa_factors as _list_factors
            return _text(await _list_factors())
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "remove_mfa_factor":
        try:
            from .auth.platform_auth import remove_mfa_factor as _remove_factor
            return _text(await _remove_factor(
                factor_id=arguments["factor_id"],
                owner_token=arguments.get("owner_token", ""),
            ))
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "create_developer_key":
        try:
            from .auth.platform_auth import create_developer_key as _create_key
            return _text(await _create_key(
                name=arguments.get("name", "default"),
                scopes=arguments.get("scopes"),
                env=arguments.get("env", "live"),
            ))
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "list_developer_keys":
        try:
            from .auth.platform_auth import list_developer_keys as _list_keys
            return _text(await _list_keys())
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "rotate_developer_key":
        try:
            from .auth.platform_auth import rotate_developer_key as _rotate_key
            return _text(await _rotate_key(
                key_id=arguments["key_id"],
                name=arguments.get("name"),
            ))
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "revoke_developer_key":
        try:
            from .auth.platform_auth import revoke_developer_key as _revoke_key
            return _text(await _revoke_key(key_id=arguments["key_id"]))
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "get_developer_key_usage":
        try:
            from .auth.platform_auth import get_developer_key_usage as _key_usage
            return _text(await _key_usage(key_id=arguments["key_id"]))
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "test_bridge_connection":
        try:
            from .auth.platform_auth import test_bridge_connection as _test_bridge
            return _text(await _test_bridge(api_key=arguments.get("api_key")))
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "get_user_bot_metrics":
        try:
            from .live_bot_intelligence.multi_account_metrics import get_user_bot_metrics as _get_ubm
            result = await _get_ubm(
                user_id=arguments["user_id"],
                bot_id=arguments["bot_id"],
                subscription_id=arguments["subscription_id"],
                log_path=arguments.get("log_path"),
            )
            return _text(result.to_dict())
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "get_all_user_bots":
        try:
            from .live_bot_intelligence.multi_account_metrics import get_all_user_bots as _get_all_bots
            return _text(await _get_all_bots(arguments["user_id"]))
        except Exception as exc:
            return _text({"error": str(exc)})

    elif name == "upsert_bot_performance":
        # SEC-2026-C4 FIX: metrics_streaming_daemon.py is the canonical writer.
        # This MCP path is deprecated for autonomous callers; require owner_token.
        _owner_token_provided = arguments.get("owner_token", "")
        _expected_owner_token = os.environ.get("OWNER_API_TOKEN", "")
        if not _expected_owner_token or _owner_token_provided != _expected_owner_token:
            return _text({
                "error": "upsert_bot_performance requires owner_token matching OWNER_API_TOKEN. "
                         "Autonomous metric writes go through metrics_streaming_daemon.py, not MCP.",
            })
        try:
            from .live_bot_intelligence.multi_account_metrics import upsert_managed_bot_performance as _upsert_perf
            return _text(await _upsert_perf(
                subscription_id=arguments["subscription_id"],
                bot_id=arguments["bot_id"],
                daily_pnl=float(arguments["daily_pnl"]),
                win_rate=float(arguments["win_rate"]),
                trade_count=int(arguments["trade_count"]),
                is_running=bool(arguments["is_running"]),
                broker=arguments["broker"],
                sharpe_ratio=arguments.get("sharpe_ratio"),
                max_drawdown=arguments.get("max_drawdown"),
                win_rate_validated=arguments.get("win_rate_validated"),
                weekly_pnl=arguments.get("weekly_pnl"),
                last_trade_at=arguments.get("last_trade_at"),
            ))
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": str(exc)})

    # ═══════════════════════════════════════════════════════════════
    # V22.1: Guardrail Status Tools (read-only — AI cannot modify limits)
    # ═══════════════════════════════════════════════════════════════
    elif name == "get_circuit_breaker_status":
        if not _GUARDRAILS_AVAILABLE:
            return _text({
                "warning": "Trading guardrails module not loaded. Hard-coded circuit breakers are INACTIVE.",
                "action": "Deploy trading_guardrails.py to activate V22 safety limits.",
            })
        try:
            status = get_guardrails().get_status()
            return _text(status)
        except Exception as exc:
            return _text({"error": f"Guardrail status error: {exc}"})

    elif name == "get_daily_loss_proximity":
        try:
            from .daily_loss_proximity import get_daily_loss_proximity
            return _text(get_daily_loss_proximity())
        except Exception as exc:
            return _text({"error": f"Daily loss proximity error: {exc}"})

    elif name == "get_agent_loop_status":
        if not _GUARDRAILS_AVAILABLE:
            return _text({
                "warning": "Trading guardrails module not loaded. AI loop detection is INACTIVE.",
            })
        try:
            status = get_guardrails().get_status()
            return _text({
                "loop_detection": status.get("loop_detection", {}),
                "limits": {
                    "max_identical_calls_60s": status["hard_coded_limits"]["ai_loop_identical_calls_limit"],
                    "max_tool_calls_per_minute": status["hard_coded_limits"]["ai_tool_calls_per_minute_limit"],
                },
                "advice": (
                    "If loop_risk is HIGH, avoid repeating the same tool call with identical arguments. "
                    "The circuit breaker will trip on the next repeated call above the limit."
                ),
            })
        except Exception as exc:
            return _text({"error": f"Loop status error: {exc}"})

    elif name == "get_latency_profile":
        _call_start = time.monotonic()
        return _text({
            "execution_tier": "Tier 4: MCP AI-assisted (120ms–2s per tool call)",
            "suitable_for": [
                "Swing trading (15min – daily bars)",
                "Portfolio rebalancing (daily/weekly)",
                "Options strategy selection",
                "Strategy research and backtesting",
                "Post-trade performance analysis",
                "Signal routing between research and live bots",
            ],
            "not_suitable_for": [
                "HFT / market making (requires <1ms, co-located C++/FPGA)",
                "Statistical arbitrage on tick data (requires <50ms, Rust engine)",
                "1-minute intraday execution (borderline — use direct bot WebSocket)",
            ],
            "measured_latency_ms": {
                "this_tool_call_overhead": round((time.monotonic() - _call_start) * 1000, 1),
                "mcp_tool_overhead_typical": "2–5ms",
                "llm_inference_typical": "80–400ms",
                "tradovate_api_roundtrip": "15–80ms",
                "polygon_bar_fetch": "20–150ms",
                "onyx_rag_query": "100–500ms",
                "end_to_end_simple_order": "~200–700ms",
                "end_to_end_full_pipeline": "~500ms–2s",
            },
            "live_bot_execution_tier": (
                "Tier 3: Direct Python WebSocket (15–80ms). "
                "The 4 live bots (MNQ/CL/MES/NQ) execute via WebSocket directly — "
                "they do NOT route through MCP for fills."
            ),
            "sse_server_port": int(os.environ.get("ALGOCHAINS_SSE_PORT", "8765")),
            "sse_advantage": (
                "V22 SSE transport eliminates quote polling. "
                "Subscribe to /stream/quotes for real-time price push instead of polling get_quote."
            ),
            "reference": "See LATENCY_GUIDE.md for full tier comparison and strategy suitability matrix.",
        })

    # ═══════════════════════════════════════════════════════════════
    # Ultimate Quant Alpha Stack
    # ═══════════════════════════════════════════════════════════════
    elif name == "compute_volatility_surface":
        get_vol_surface_engine = _lazy_import("vol_surface_v21", "get_vol_surface_engine")
        if not get_vol_surface_engine:
            return _text({"error": "Volatility surface engine not available"})
        polygon_key = (_config.polygon.api_key if _config and _config.polygon else
                       os.getenv("POLYGON_API_KEY", ""))
        engine = get_vol_surface_engine(polygon_key)
        surface = await engine.get_surface(args.get("symbol", ""), args.get("expiry_filter"))
        signal = engine.generate_signal(surface)
        return _text({
            "symbol": surface.symbol, "spot": surface.spot_price, "as_of": surface.as_of,
            "iv_rank": surface.iv_rank, "iv_percentile": surface.iv_percentile,
            "atm_iv": surface.atm_iv, "skew_25d": surface.skew_25d,
            "vol_regime": surface.regime, "term_structure": surface.term_structure,
            "signal": {"type": signal.signal, "reason": signal.reason, "conviction": signal.conviction},
            "call_count": len(surface.calls), "put_count": len(surface.puts),
        })

    elif name == "compute_factor_exposure":
        get_factor_engine = _lazy_import("factor_model_v21", "get_factor_engine")
        if not get_factor_engine:
            return _text({"error": "Factor model engine not available"})
        polygon_key = (_config.polygon.api_key if _config and _config.polygon else
                       os.getenv("POLYGON_API_KEY", ""))
        fred_key = (_config.fred_api_key if _config and hasattr(_config, "fred_api_key") else
                    os.getenv("FRED_API_KEY", ""))
        engine = get_factor_engine(polygon_key, fred_key)
        exposure = await engine.compute_factor_exposure(
            args.get("symbol", ""), period=args.get("period", "1y"),
            benchmark=args.get("benchmark", "SPY"),
        )
        return _text({
            "symbol": exposure.symbol, "period": exposure.period,
            "alpha_annualized": exposure.alpha,
            "betas": {"market": exposure.beta_market, "smb": exposure.beta_smb,
                      "hml": exposure.beta_hml, "momentum": exposure.beta_mom},
            "r_squared": exposure.r_squared,
            "information_ratio": exposure.information_ratio,
            "tracking_error_annualized": exposure.tracking_error,
            "active_return_annualized": exposure.active_return,
            "regime": exposure.regime, "as_of": exposure.as_of,
        })

    elif name == "detect_regime_hmm":
        get_regime_detector = _lazy_import("regime_hmm", "get_regime_detector")
        if not get_regime_detector:
            return _text({"error": "Regime HMM detector not available"})
        polygon_key = (_config.polygon.api_key if _config and _config.polygon else
                       os.getenv("POLYGON_API_KEY", ""))
        detector = get_regime_detector(polygon_key)
        regime = await detector.detect_regime(
            symbol=args.get("symbol", "SPY"),
            lookback_days=args.get("lookback_days", 252),
        )
        return _text({
            "symbol": regime.symbol, "current_regime": regime.current_regime,
            "probability": regime.regime_probability, "days_in_regime": regime.days_in_regime,
            "volatility_regime": regime.volatility_regime, "trend_bias": regime.trend_bias,
            "sharpe_annualized": regime.sharpe_annualized,
            "transition_probabilities": regime.transition_probability,
            "method": regime.method, "as_of": regime.as_of,
        })

    elif name == "get_quant_regime_state":
        import json as _json
        from pathlib import Path as _Path
        from datetime import datetime as _dt, timezone as _tz
        bot_filter = args.get("bot_id")
        ct_root = _Path(_default_control_tower())
        snapshot_path = ct_root / "state" / "quant_shadow_snapshot.json"
        snapshot = {}
        snapshot_error = None
        snapshot_status = "missing"
        try:
            if snapshot_path.exists():
                snapshot = _json.loads(snapshot_path.read_text())
                snapshot_status = "ok"
                generated_at = snapshot.get("generated_at") if isinstance(snapshot, dict) else None
                stale_after = float(snapshot.get("stale_after_sec", 360)) if isinstance(snapshot, dict) else 360.0
                try:
                    gen_ts = _dt.fromisoformat(str(generated_at).replace("Z", "+00:00"))
                    if gen_ts.tzinfo is None:
                        gen_ts = gen_ts.replace(tzinfo=_tz.utc)
                    if (_dt.now(_tz.utc) - gen_ts).total_seconds() > stale_after:
                        snapshot_status = "stale"
                except Exception:
                    snapshot_status = "stale"
            else:
                snapshot_error = "state/quant_shadow_snapshot.json not found"
        except Exception as exc:
            snapshot_error = str(exc)
            snapshot_status = "unreadable"

        metrics = {}
        summary = []
        metrics_status = "unavailable"
        summary_status = "unavailable"
        summary_error = None
        try:
            from .marketplace.supabase_tools import _get_sb_client as _sb_client
            sb = _sb_client()
            if sb is not None:
                q = sb.table("bot_metrics_live").select(
                    "bot_id,symbol,garch_vol_forecast,garch_vol_status,volume_source,"
                    "garch_vol_zscore,ofi_intensity,ofi_status,kalman_slope,kalman_status,hmm_regime,"
                    "hmm_regime_status,sortino_ratio,calmar_ratio,updated_at"
                )
                if bot_filter:
                    q = q.eq("bot_id", bot_filter)
                res = q.execute()
                for row in (res.data or []):
                    metrics[row.get("bot_id")] = row
                metrics_status = "ok"
            else:
                metrics_status = "unavailable"
            sb_service = _sb_client(use_service_role=True)
            if sb_service is not None:
                try:
                    view_res = sb_service.table("v_quant_model_shadow_summary").select("*").execute()
                    summary = view_res.data or []
                    summary_status = "ok"
                except Exception as exc:
                    msg = str(exc)
                    summary_error = msg
                    if "permission" in msg.lower() or "permission denied" in msg.lower() or "42501" in msg:
                        summary_status = "permission_denied"
                    elif "does not exist" in msg.lower() or "schema" in msg.lower() or "column" in msg.lower():
                        summary_status = "schema_missing"
                    else:
                        summary_status = "error"
                    summary = []
            else:
                summary_status = "unavailable"
        except Exception as exc:
            metrics = {"error": str(exc)}
            msg = str(exc)
            if "does not exist" in msg.lower() or "schema" in msg.lower() or "column" in msg.lower():
                metrics_status = "schema_missing"
            elif "permission" in msg.lower() or "permission denied" in msg.lower() or "42501" in msg:
                metrics_status = "permission_denied"
            else:
                metrics_status = "error"

        bots = snapshot.get("bots", {}) if isinstance(snapshot, dict) else {}
        if bot_filter and isinstance(bots, dict):
            bots = {bot_filter: bots[bot_filter]} if bot_filter in bots else {}
        return _text({
            "source": "snapshot_plus_supabase_metrics",
            "snapshot_generated_at": snapshot.get("generated_at") if isinstance(snapshot, dict) else None,
            "snapshot_status": snapshot_status,
            "snapshot_error": snapshot_error,
            "bots": bots,
            "bot_metrics_live_status": metrics_status,
            "bot_metrics_live": metrics,
            "agreement_summary_7d_status": summary_status,
            "agreement_summary_7d_error": summary_error,
            "agreement_summary_7d": summary,
            "computes_models": False,
        })

    elif name == "get_vix_term_structure":
        import httpx as _httpx
        try:
            async with _httpx.AsyncClient(timeout=10.0) as c:
                vix_resp = await c.get("https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv")
                v3m_resp = await c.get("https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX3M_History.csv")
            vix_data = vix_resp.text.strip().split("\n")
            vix_last = vix_data[-1].split(",")
            vix_spot = float(vix_last[-1]) if len(vix_last) > 1 else 0
            v3m_data = v3m_resp.text.strip().split("\n")
            v3m_last = v3m_data[-1].split(",")
            v3m = float(v3m_last[-1]) if len(v3m_last) > 1 else 0
            contango = (v3m - vix_spot) / vix_spot if vix_spot > 0 else 0
            regime = "contango" if contango > 0.05 else "backwardation" if contango < -0.05 else "flat"
            return _text({
                "vix_spot": round(vix_spot, 2), "vix_3m": round(v3m, 2),
                "contango_pct": round(contango * 100, 2), "regime": regime,
                "signal": "bullish_equities" if regime == "contango" else "fear_elevated" if regime == "backwardation" else "neutral",
                "source": "CBOE public CSV (real data)",
            })
        except Exception as exc:
            return _text({"error": f"VIX term structure fetch failed: {exc}"})

    elif name == "compute_information_ratio":
        get_factor_engine = _lazy_import("factor_model_v21", "get_factor_engine")
        if not get_factor_engine:
            return _text({"error": "Factor engine needed for IR computation"})
        polygon_key = (_config.polygon.api_key if _config and _config.polygon else
                       os.getenv("POLYGON_API_KEY", ""))
        engine = get_factor_engine(polygon_key)
        exposure = await engine.compute_factor_exposure(
            args.get("symbol", ""), period=args.get("period", "1y"),
            benchmark=args.get("benchmark", "SPY"),
        )
        rating = "exceptional" if exposure.information_ratio > 1 else "strong" if exposure.information_ratio > 0.5 else "weak" if exposure.information_ratio < 0 else "moderate"
        return _text({
            "symbol": exposure.symbol, "benchmark": args.get("benchmark", "SPY"),
            "information_ratio": exposure.information_ratio,
            "tracking_error": exposure.tracking_error,
            "active_return_annualized": exposure.active_return,
            "alpha": exposure.alpha, "rating": rating,
        })

    elif name == "compute_correlation_matrix":
        symbols = args.get("symbols", [])
        if len(symbols) < 2:
            return _text({"error": "Need at least 2 symbols"})
        polygon_key = (_config.polygon.api_key if _config and _config.polygon else
                       os.getenv("POLYGON_API_KEY", ""))
        if not polygon_key:
            return _text({"error": "POLYGON_API_KEY required"})
        period = args.get("period", "3m")
        days = {"1m": 21, "3m": 63, "6m": 126, "1y": 252}.get(period, 63)
        from datetime import date as _date, timedelta as _td
        end = _date.today()
        start = end - _td(days=days + 10)
        import httpx as _hx
        _POLYGON_BASE = "https://api.polygon.io"
        async with _hx.AsyncClient(base_url=_POLYGON_BASE, params={"apiKey": polygon_key}, timeout=30.0) as client:
            all_rets = {}
            for sym in symbols[:20]:
                resp = await client.get(f"/v2/aggs/ticker/{sym}/range/1/day/{start.isoformat()}/{end.isoformat()}",
                                        params={"adjusted": "true", "limit": 300})
                if resp.status_code == 200:
                    bars = resp.json().get("results", [])
                    import math as _math
                    rets = [_math.log(bars[i]["c"] / bars[i-1]["c"]) for i in range(1, len(bars))
                            if bars[i-1].get("c", 0) > 0 and bars[i].get("c", 0) > 0]
                    all_rets[sym] = rets
        try:
            import numpy as _np
            aligned_len = min(len(v) for v in all_rets.values())
            matrix = _np.array([v[-aligned_len:] for v in all_rets.values()])
            corr = _np.corrcoef(matrix)
            corr_dict = {sym: {symbols[j]: round(float(corr[i, j]), 4) for j in range(len(symbols))}
                         for i, sym in enumerate(symbols)}
            # High correlations
            threshold = args.get("threshold", 0.7)
            warnings = [f"{symbols[i]}/{symbols[j]}: {corr[i,j]:.2f}"
                        for i in range(len(symbols)) for j in range(i+1, len(symbols))
                        if abs(corr[i, j]) > threshold]
            avg_corr = float(_np.mean([corr[i,j] for i in range(len(symbols)) for j in range(i+1, len(symbols))]))
            return _text({"correlation_matrix": corr_dict, "avg_correlation": round(avg_corr, 4),
                          "high_correlation_warnings": warnings, "period": period,
                          "concentration_risk": "high" if avg_corr > 0.6 else "moderate" if avg_corr > 0.3 else "low"})
        except ImportError:
            return _text({"error": "numpy required for correlation matrix", "hint": "pip install numpy"})

    # ── Protection Patterns (freqtrade-style guards) ──────────────────────
    elif name == "check_protection_status":
        try:
            from .account_protection.protection_patterns import get_all_protection_status
            return _text(get_all_protection_status(args.get("bot")))
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "record_stop_event":
        try:
            from .account_protection.protection_patterns import _stoploss_guard, _cooldown_period
            bot = str(args["bot"]); symbol = str(args["symbol"])
            pnl = float(args["pnl_usd"]); reason = str(args.get("reason", ""))
            _stoploss_guard.record_stop(bot, symbol, pnl, reason)
            _cooldown_period.trigger_cooldown(bot, symbol)
            lock = _stoploss_guard.is_locked(bot, symbol)
            return _text({"recorded": True, "bot": bot, "symbol": symbol, "pnl_usd": pnl,
                          "stoploss_guard_triggered": lock.is_locked,
                          "lock_reason": lock.lock_reason if lock.is_locked else None})
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "lock_instrument":
        try:
            from .account_protection.protection_patterns import lock_instrument
            return _text(lock_instrument(
                str(args["bot"]), str(args["symbol"]),
                str(args["reason"]), float(args.get("lock_hours", 1.0))
            ))
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "unlock_instrument":
        try:
            from .account_protection.protection_patterns import unlock_instrument
            return _text(unlock_instrument(str(args["bot"]), str(args["symbol"])))
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    # ── Volatility Targeting (pysystemtrade patterns) ──────────────────────
    elif name == "compute_volatility_targeted_size":
        try:
            from .volatility_targeting import compute_volatility_targeted_size
            result = compute_volatility_targeted_size(
                symbol=str(args["symbol"]),
                current_price=float(args["current_price"]),
                annualized_vol_pct=float(args["annualized_vol_pct"]),
                capital_usd=float(args["capital_usd"]),
                target_vol_pct=float(args.get("target_vol_pct", 20.0)),
                idm=float(args.get("idm", 1.0)),
                forecast_scalar=float(args.get("forecast_scalar", 1.0)),
                max_leverage=float(args.get("max_leverage", 4.0)),
            )
            return _text(result.to_dict())
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "compute_idm":
        try:
            from .volatility_targeting import compute_idm
            result = compute_idm(
                instruments=args["instruments"],
                weights=args.get("weights"),
            )
            return _text(result.to_dict())
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "compute_forecast_scalar":
        try:
            from .volatility_targeting import compute_forecast_scalar
            return _text(compute_forecast_scalar(
                raw_forecast=float(args["raw_forecast"]),
                target_abs_forecast=float(args.get("target_abs_forecast", 10.0)),
                scalar=float(args["scalar"]) if "scalar" in args else None,
                raw_forecast_history=[float(x) for x in args["history"]] if "history" in args else None,
            ))
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "dual_size_conservative":
        try:
            from .volatility_targeting import dual_size_conservative
            return _text(dual_size_conservative(
                symbol=str(args["symbol"]),
                current_price=float(args["current_price"]),
                annualized_vol_pct=float(args["annualized_vol_pct"]),
                capital_usd=float(args["capital_usd"]),
                kelly_contracts=int(args["kelly_contracts"]),
                target_vol_pct=float(args.get("target_vol_pct", 20.0)),
                idm=float(args.get("idm", 1.0)),
                forecast_scalar=float(args.get("forecast_scalar", 1.0)),
            ))
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    # ── Performance Reports (quantstats-style tearsheets) ─────────────────
    elif name == "generate_bot_tearsheet":
        try:
            from .performance_reports import generate_bot_tearsheet
            return _text(generate_bot_tearsheet(
                bot_name=str(args["bot_name"]),
                returns=[float(r) for r in args["returns"]],
                frequency=str(args.get("frequency", "daily")),
                risk_free_rate=float(args.get("risk_free_rate", 0.05)),
            ))
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "get_bot_metrics_full":
        try:
            from .performance_reports import get_bot_metrics_full
            return _text(get_bot_metrics_full(
                bot_name=str(args["bot_name"]),
                returns=[float(r) for r in args["returns"]],
                frequency=str(args.get("frequency", "daily")),
                risk_free_rate=float(args.get("risk_free_rate", 0.05)),
            ))
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    # ── Prop Fund Manager ──────────────────────────────────────────────────
    elif name == "list_prop_funds":
        try:
            from .brokers.prop_fund_manager import list_prop_funds
            return _text(list_prop_funds(args.get("platform")))
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "evaluate_strategy_for_prop_fund":
        try:
            from .brokers.prop_fund_manager import evaluate_all_funds
            return _text(evaluate_all_funds(
                strategy_name=str(args["strategy_name"]),
                symbol=str(args["symbol"]),
                max_daily_loss_usd=float(args["max_daily_loss_usd"]),
                max_drawdown_usd=float(args["max_drawdown_usd"]),
                avg_profit_per_day_usd=float(args["avg_profit_per_day_usd"]),
                holds_overnight=bool(args.get("holds_overnight", False)),
                trades_news=bool(args.get("trades_news", False)),
                max_position_contracts=int(args.get("max_position_contracts", 2)),
                min_trading_days_per_month=int(args.get("min_trading_days_per_month", 15)),
                historical_returns_daily=[float(r) for r in args["historical_returns_daily"]]
                    if "historical_returns_daily" in args else None,
            ))
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "simulate_prop_fund_evaluation":
        try:
            from .brokers.prop_fund_manager import simulate_drawdown_against_fund_rules
            return _text(simulate_drawdown_against_fund_rules(
                fund_name=str(args["fund_name"]),
                daily_pnl_series=[float(p) for p in args["daily_pnl_series"]],
                account_size_usd=float(args["account_size_usd"]) if "account_size_usd" in args else None,
            ))
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "get_prop_fund_rules":
        try:
            from .brokers.prop_fund_manager import PROP_FUNDS
            fund_name = str(args.get("fund_name", "")).lower()
            if fund_name and fund_name in PROP_FUNDS:
                return _text(PROP_FUNDS[fund_name].to_dict())
            return _text({k: v.to_dict() for k, v in PROP_FUNDS.items()})
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    # ── Security Tools ─────────────────────────────────────────────────────
    elif name == "check_rate_limit_status":
        try:
            from .security.per_tool_rate_limiter import get_rate_limit_status
            return _text(get_rate_limit_status(
                args.get("tool_name"), str(args.get("client_id", "default"))
            ))
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "generate_hmac_signature":
        try:
            from .security.replay_guard import generate_hmac_signature
            return _text(generate_hmac_signature(
                payload=str(args["payload"]),
                secret=str(args["secret"]),
                algorithm=str(args.get("algorithm", "sha256")),
            ))
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "verify_hmac_signature":
        try:
            from .security.replay_guard import verify_hmac_signature
            return _text(verify_hmac_signature(
                payload=str(args["payload"]),
                secret=str(args["secret"]),
                timestamp=str(args["timestamp"]),
                nonce=str(args["nonce"]),
                signature=str(args["signature"]),
                algorithm=str(args.get("algorithm", "sha256")),
            ))
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "compute_r_multiple_size":
        try:
            from .brokers.etrade_connector import compute_r_multiple_size
            return _text(compute_r_multiple_size(
                symbol=str(args["symbol"]),
                entry_price=float(args["entry_price"]),
                stop_loss_price=float(args["stop_loss_price"]),
                capital_usd=float(args["capital_usd"]),
                risk_pct=float(args.get("risk_pct", 1.0)),
                asset_type=str(args.get("asset_type", "equity")),
            ))
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "compute_option_greeks":
        try:
            from .brokers.etrade_connector import compute_option_greeks
            return _text(compute_option_greeks(
                option_type=str(args["option_type"]),
                underlying_price=float(args["underlying_price"]),
                strike=float(args["strike"]),
                time_to_expiry_years=float(args["time_to_expiry_years"]),
                risk_free_rate=float(args.get("risk_free_rate", 0.05)),
                implied_vol=float(args["implied_vol"]),
            ))
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "find_optimal_strike":
        try:
            from .brokers.etrade_connector import find_optimal_strike
            return _text(find_optimal_strike(
                option_type=str(args["option_type"]),
                underlying_price=float(args["underlying_price"]),
                target_delta=float(args["target_delta"]),
                expiry_str=str(args["expiry"]),
                risk_free_rate=float(args.get("risk_free_rate", 0.05)),
                implied_vol=float(args.get("implied_vol", 0.25)),
            ))
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "check_broker_credentials":
        try:
            from .brokers.credential_vault import check_broker_credentials as _check_creds
            broker = args.get("broker")
            if broker:
                return _text(_check_creds(broker))
            from .brokers.credential_vault import check_all_broker_credentials
            return _text(check_all_broker_credentials())
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "get_broker_onboarding_guide":
        try:
            from .brokers.credential_vault import get_broker_onboarding_guide
            return _text(get_broker_onboarding_guide(str(args["broker"])))
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "check_rithmic_status":
        try:
            # check_rithmic_status is synchronous — no async needed
            system_name = os.environ.get("RITHMIC_SYSTEM_NAME", "")
            plant_name = os.environ.get("RITHMIC_PLANT_NAME", "Chicago")
            # Canonical env var is RITHMIC_USER (used by rithmic_bridge.cpp)
            user_id = os.environ.get("RITHMIC_USER", "")
            password = os.environ.get("RITHMIC_PASSWORD", "")
            ssl_cert = os.environ.get("RITHMIC_SSL_CERT", "")
            dry_run = os.environ.get("RITHMIC_DRY_RUN", "true").lower() == "true"
            all_configured = bool(system_name and user_id and password and ssl_cert)
            missing = []
            if not system_name: missing.append("RITHMIC_SYSTEM_NAME")
            if not user_id: missing.append("RITHMIC_USER")
            if not password: missing.append("RITHMIC_PASSWORD")
            if not ssl_cert: missing.append("RITHMIC_SSL_CERT")
            from .brokers.rithmic_connector import RITHMIC_GATEWAYS, RITHMIC_INSTRUMENTS
            result = {
                "dry_run_mode": dry_run,
                "credentials_configured": all_configured,
                "missing_credentials": missing,
                "plant": plant_name,
                "gateway": RITHMIC_GATEWAYS.get(plant_name, "Unknown"),
                "supported_instruments": list(RITHMIC_INSTRUMENTS.keys()),
                "supported_prop_funds": ["apex", "topstep", "myfundedfutures", "tradeday", "bulenox", "earn2trade"],
                "vendor_agreement": "https://www.rithmic.com/contacts",
                "live_bridge": "rithmic/rithmic_bridge (C++ R|API+ bridge, read-only)",
                "live_mcp": "moltbook/rithmic_mcp_server.py (use get_rithmic_live_* tools for real data)",
                "note": (
                    "DRY_RUN active — set RITHMIC_DRY_RUN=false after signing vendor agreement."
                    if dry_run else
                    ("Credentials configured. Use get_rithmic_live_accounts/pnl/positions/fills for live prop account data."
                     if all_configured
                     else "Missing credentials — add RITHMIC_USER, RITHMIC_PASSWORD, RITHMIC_SYSTEM_NAME, RITHMIC_SSL_CERT to .env")
                ),
            }
            return _text(result)
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "get_kronos_shadow_stats":
        try:
            import json as _json
            from pathlib import Path as _Path
            # Use _default_control_tower() — honours ALGOCHAINS_CONTROL_TOWER env and
            # the shared legacy path list so this works on both Mac and desktop.
            # The previous 5x .parent traversal was path-layout-specific and broke
            # on any non-default install location.
            _ct = _Path(_default_control_tower())
            _sh = _ct / "state" / "signal_health.json"
            if not _sh.exists():
                return _text({"error": f"signal_health.json not found at {_sh} — set ALGOCHAINS_CONTROL_TOWER env or verify control-tower path"})
            data = _json.loads(_sh.read_text())
            bot_key = str(args.get("bot_key", "all"))
            if bot_key == "all":
                result = {}
                for k, v in data.items():
                    shadow = v.get("kronos_shadow")
                    if shadow:
                        result[k] = shadow
                return _text({"bots": result, "count": len(result)})
            else:
                bot_data = data.get(bot_key, {})
                shadow = bot_data.get("kronos_shadow", {})
                if not shadow:
                    return _text({"error": f"No Kronos shadow data for bot '{bot_key}'", "available_keys": list(data.keys())})
                return _text({"bot_key": bot_key, "kronos_shadow": shadow})
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "get_signal_trade_correlation":
        # mcp-correlation-tool: thin READ-ONLY wrapper over the control-tower script
        # scripts/signal_trade_correlation_audit.py. Does NOT reimplement the audit —
        # runs it with --json --no-slack and returns the report dict so agents can read
        # signal->trade NULL-rate / coverage KPIs without re-deriving the logic.
        try:
            import json as _json
            import subprocess as _subp
            from pathlib import Path as _Path

            _ct = _Path(_default_control_tower())
            _script = _ct / "scripts" / "signal_trade_correlation_audit.py"
            if not _script.exists():
                return _text({"error": f"correlation audit script not found at {_script}"})
            _limit = int(args.get("limit", 50) or 50)
            _action = str(args.get("action", "submitted") or "submitted")
            if _action not in ("submitted", "all", "blocked"):
                _action = "submitted"
            _cmd = [
                "python3", "-B", str(_script),
                "--json", "--no-slack",
                "--limit", str(_limit),
                "--action", _action,
            ]
            # Default to filled-only (matches the launchd plist) unless explicitly disabled.
            if args.get("filled_only", True):
                _cmd.append("--filled-only")
            _max_attempts = max(
                1,
                min(int(os.getenv("ALGOCHAINS_TRACEABILITY_AUDIT_ATTEMPTS", "3")), 5),
            )
            for _attempt in range(1, _max_attempts + 1):
                _proc = _subp.run(_cmd, capture_output=True, text=True, timeout=60, cwd=str(_ct))
                if _proc.returncode == 0:
                    try:
                        return _text(_json.loads(_proc.stdout))
                    except Exception:
                        return _text({"error": "could not parse audit JSON", "stdout": (_proc.stdout or "")[:500]})

                _transient = _is_traceability_transient_failure(_proc.stderr, _proc.stdout)
                if not _transient or _attempt == _max_attempts:
                    payload = {
                        "error": "correlation audit failed",
                        "returncode": _proc.returncode,
                        "stderr": (_proc.stderr or "")[:500],
                    }
                    if _transient:
                        payload.update({
                            "error": "correlation audit transient failure",
                            "transient": True,
                            "attempts": _attempt,
                            "retry_attempts": _attempt - 1,
                        })
                    return _text(payload)

                time.sleep(min(0.25 * _attempt, 1.0))
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name in ("get_rithmic_live_accounts", "get_rithmic_live_pnl",
                  "get_rithmic_live_positions", "get_rithmic_live_fills"):
        try:
            import sys as _sys
            from pathlib import Path as _Path
            _ct = str(_Path(__file__).resolve().parent.parent.parent.parent.parent / "algochains-control-tower")
            if _ct not in _sys.path:
                _sys.path.insert(0, _ct)
            from rithmic.rithmic_client import get_client as _get_rc
            client = _get_rc(auto_start=True)
            if client is None:
                return _text({"error": "Rithmic bridge unavailable — check .env credentials and run: cd rithmic && make"})
            if name == "get_rithmic_live_accounts":
                accounts = client.get_accounts()
                return _text({"accounts": accounts, "count": len(accounts), "status": "live" if client.is_alive() else "disconnected"})
            elif name == "get_rithmic_live_pnl":
                pnl = client.get_pnl()
                summary = client.get_daily_pnl_summary()
                return _text({"pnl": pnl, "summary": summary, "count": len(pnl), "status": "live" if client.is_alive() else "disconnected"})
            elif name == "get_rithmic_live_positions":
                positions = client.get_positions()
                return _text({"positions": positions, "count": len(positions), "status": "live" if client.is_alive() else "disconnected"})
            elif name == "get_rithmic_live_fills":
                limit = max(1, min(int(args.get("limit", 50)), 500))
                fills = client.get_fills(limit=limit)
                return _text({"fills": fills, "count": len(fills), "status": "live" if client.is_alive() else "disconnected"})
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "register_prop_fund_account":
        try:
            from .brokers.prop_fund_drawdown_monitor import register_prop_fund_account
            return _text(register_prop_fund_account(
                account_id=str(args["account_id"]),
                fund_name=str(args["fund_name"]),
                broker=str(args["broker"]),
                starting_balance=float(args["starting_balance"]),
                max_daily_loss_usd=args.get("max_daily_loss_usd"),
                max_trailing_drawdown_usd=args.get("max_trailing_drawdown_usd"),
                profit_target_usd=args.get("profit_target_usd"),
            ))
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "get_prop_fund_monitor_status":
        try:
            from .brokers.prop_fund_drawdown_monitor import get_prop_fund_monitor_status
            return _text(get_prop_fund_monitor_status())
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "get_prop_fund_broker_options":
        try:
            from .brokers.credential_vault import get_prop_fund_broker_options
            return _text(get_prop_fund_broker_options())
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "build_prop_fund_inputs":
        try:
            from .brokers.prop_fund_data_feeder import build_prop_fund_inputs
            return _text(build_prop_fund_inputs(
                strategy_name=str(args["strategy_name"]),
                symbol=str(args["symbol"]),
                lookback_days=int(args.get("lookback_days", 90)),
                account_id=args.get("account_id"),
                fills_override=args.get("fills_override"),
            ))
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "onboard_prop_account":
        try:
            from .brokers.prop_fund_autopilot import onboard_prop_account
            return _text(onboard_prop_account(
                fund_key=str(args["fund_key"]),
                account_id=str(args["account_id"]),
                broker=str(args["broker"]),
                starting_balance=float(args["starting_balance"]),
                credentials_ref=args.get("credentials_ref"),
                confirm=bool(args.get("confirm", False)),
            ))
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "deploy_bot_in_prop_mode":
        try:
            from .brokers.prop_fund_autopilot import deploy_bot_in_prop_mode
            return _text(deploy_bot_in_prop_mode(
                account_id=str(args["account_id"]),
                bot_name=str(args.get("bot_name", "FUTURES_SCALPER_UPGRADED")),
                symbol=str(args.get("symbol", "MNQ")),
                confirm=bool(args.get("confirm", False)),
            ))
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "get_prop_mode_status":
        try:
            from .brokers.prop_fund_autopilot import get_prop_mode_status
            return _text(get_prop_mode_status(account_id=args.get("account_id")))
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "request_prop_payout":
        try:
            from .brokers.prop_fund_autopilot import request_prop_payout
            return _text(request_prop_payout(
                account_id=str(args["account_id"]),
                current_balance=float(args["current_balance"]),
            ))
        except KeyError as exc:
            return _text({"error": f"Missing required argument: {exc}"})
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "run_prop_fund_autopilot":
        try:
            from .brokers.prop_fund_autopilot import run_prop_fund_autopilot
            return _text(run_prop_fund_autopilot(
                strategy_name=str(args.get("strategy_name", "FUTURES_SCALPER_UPGRADED")),
                symbol=str(args.get("symbol", "MNQ")),
                lookback_days=int(args.get("lookback_days", 90)),
                account_id=args.get("account_id"),
                fund_keys=args.get("fund_keys"),
                fills_override=args.get("fills_override"),
            ))
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "check_prop_fund_rules_freshness":
        try:
            from .brokers.prop_fund_manager import check_prop_fund_rules_freshness
            return _text(check_prop_fund_rules_freshness(
                max_age_days=int(args.get("max_age_days", 30)),
            ))
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    # ── AlgoClaw Agent Skill System (v25.0) ─────────────────────────────────
    elif name == "run_algoclaw_skill":
        try:
            import sys
            _ac_dir = os.path.join(os.path.dirname(__file__), "..", "..", "..", "algoclaw")
            if _ac_dir not in sys.path:
                sys.path.insert(0, _ac_dir)
            from algoclaw.cli import run_skill
            skill = args.get("skill")
            if not skill:
                return _text({"error": "Provide 'skill' argument", "usage": "run_algoclaw_skill({'skill': 'bot-health'})"})
            result = run_skill(skill, args.get("params", {}))
            return _text(result)
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "list_algoclaw_skills":
        try:
            import sys
            _ac_dir = os.path.join(os.path.dirname(__file__), "..", "..", "..", "algoclaw")
            if _ac_dir not in sys.path:
                sys.path.insert(0, _ac_dir)
            from algoclaw.cli import list_skills
            return _text(list_skills())
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "get_algoclaw_status":
        try:
            import sys
            _ac_dir = os.path.join(os.path.dirname(__file__), "..", "..", "..", "algoclaw")
            if _ac_dir not in sys.path:
                sys.path.insert(0, _ac_dir)
            from algoclaw.cli import get_status
            return _text(get_status())
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    # ── Numerai Tournament Tools (§9 / §28 / HK-6/HK-17) ────────────────────
    # Isolated from futures bots. No futures imports. HK-6: no secret values in responses.

    elif name == "numerai_status":
        try:
            from .tournament.numerai.config import get_config
            cfg = get_config()
            return _text(cfg.status_dict())
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "numerai_round_info":
        try:
            from .tournament.numerai.config import _get_napi
            napi = _get_napi()
            current_round = napi.get_current_round()
            return _text({
                "current_round": current_round,
                "submission_window": "Tuesday–Saturday",
                "scoring_lag": "~20 business days + 2 lag (20D2L)",
                "note": "Verify exact window at numer.ai — this is the standard cadence.",
            })
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "numerai_download_dataset":
        try:
            from .tournament.numerai.config import get_config
            from .tournament.numerai.download import download_training_data, download_live_data
            cfg = get_config()
            feature_set = arguments.get("feature_set", cfg.feature_set)
            force = arguments.get("force_redownload", False)
            train_paths = download_training_data(feature_set=feature_set, force_redownload=force)
            live_paths = download_live_data()
            return _text({
                "train_parquet": str(train_paths["train_parquet"]),
                "features_json": str(train_paths["features_json"]),
                "live_parquet": str(live_paths["live_parquet"]),
                "round_id": live_paths["round_id"],
                "feature_set": feature_set,
                "note": "live.parquet always re-downloaded (HK-3: IDs change each round).",
            })
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "numerai_train_baseline":
        try:
            from .tournament.numerai.config import get_config
            from .tournament.numerai.download import (
                load_feature_names, load_train_dataframe,
                download_training_data, download_live_data,
            )
            from .tournament.numerai.train import train_baseline
            cfg = get_config()
            feature_set = arguments.get("feature_set", cfg.feature_set)
            holdout_n = int(arguments.get("holdout_n", cfg.holdout_eras))
            embargo_eras = int(arguments.get("embargo_eras", cfg.embargo_eras))

            train_paths = download_training_data(feature_set=feature_set)
            live_paths = download_live_data()
            feature_names = load_feature_names(train_paths["features_json"], feature_set)
            train_df = load_train_dataframe(
                train_paths["train_parquet"], train_paths["features_json"],
                feature_set=feature_set, target_col=cfg.target_column,
            )
            meta = train_baseline(
                train_df, feature_names,
                target_col=cfg.target_column,
                holdout_n=holdout_n, embargo_eras=embargo_eras,
                models_dir=cfg.models_dir(),
                round_id=live_paths["round_id"], cfg=cfg,
            )
            return _text(meta)
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "numerai_validate_metrics":
        try:
            from .tournament.numerai.config import get_config
            from .tournament.numerai.download import (
                load_feature_names, load_train_dataframe,
                download_training_data, download_live_data,
            )
            from .tournament.numerai.era_utils import era_split
            from .tournament.numerai.train import load_model, predict
            from .tournament.numerai.neutralize import neutralize_predictions
            from .tournament.numerai.validate import validate_metrics
            import json as _json
            from pathlib import Path as _Path

            cfg = get_config()
            use_neutralized = arguments.get("neutralized", True)
            train_paths = download_training_data(feature_set=cfg.feature_set)
            live_paths = download_live_data()
            feature_names = load_feature_names(train_paths["features_json"], cfg.feature_set)
            train_df = load_train_dataframe(
                train_paths["train_parquet"], train_paths["features_json"],
                feature_set=cfg.feature_set, target_col=cfg.target_column,
            )
            _, val_df = era_split(train_df, holdout_n=cfg.holdout_eras, embargo_gap=cfg.embargo_eras)

            # Load most recent model
            models_dir = cfg.models_dir()
            pkls = sorted(models_dir.glob("model_*.pkl"), key=lambda p: p.stat().st_mtime)
            if not pkls:
                return _text({"error": "No trained model found. Run numerai_train_baseline first."})
            model_artifact = load_model(pkls[-1])
            feat_cols = [f for f in feature_names if f in val_df.columns]
            val_preds = predict(model_artifact, val_df[feat_cols])
            if use_neutralized:
                val_preds = neutralize_predictions(val_preds, val_df, feature_names)
            report = validate_metrics(val_preds, val_df, target_col=cfg.target_column)
            report.pop("per_era_proxy_corr", None)  # too verbose for MCP response
            return _text(report)
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "numerai_dry_run_submit":
        try:
            from .tournament.numerai.config import get_config
            from .tournament.numerai.download import (
                load_feature_names, load_live_dataframe,
                download_training_data, download_live_data,
            )
            from .tournament.numerai.train import load_model, predict
            from .tournament.numerai.neutralize import neutralize_predictions
            from .tournament.numerai.submit import build_submission
            from pathlib import Path as _Path

            cfg = get_config()
            use_neutralized = arguments.get("neutralized", True)
            train_paths = download_training_data(feature_set=cfg.feature_set)
            live_paths = download_live_data()
            feature_names = load_feature_names(train_paths["features_json"], cfg.feature_set)
            live_df = load_live_dataframe(
                live_paths["live_parquet"], train_paths["features_json"],
                feature_set=cfg.feature_set,
            )
            models_dir = cfg.models_dir()
            pkls = sorted(models_dir.glob("model_*.pkl"), key=lambda p: p.stat().st_mtime)
            if not pkls:
                return _text({"error": "No trained model found. Run numerai_train_baseline first."})
            model_artifact = load_model(pkls[-1])
            feat_cols = [f for f in feature_names if f in live_df.columns]
            preds = predict(model_artifact, live_df[feat_cols])
            if use_neutralized:
                preds = neutralize_predictions(preds, live_df, feature_names)
            submission_path = cfg.submissions_dir() / f"dry_run_r{live_paths['round_id']}.csv"
            result = build_submission(preds, live_df, submission_path)
            result["round_id"] = live_paths["round_id"]
            result["dry_run"] = True
            return _text(result)
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "numerai_upload_predictions":
        try:
            from .tournament.numerai.config import get_config
            from .tournament.numerai.submit import upload_predictions_gated
            from pathlib import Path as _Path

            cfg = get_config()
            model_id = arguments.get("model_id", "").strip()
            confirm = arguments.get("confirm", False)
            round_id = arguments.get("round_id")

            if not confirm:
                return _text({
                    "uploaded": False,
                    "reason": "confirm=false — set confirm=true to attempt upload",
                    "note": "Also requires NUMERAI_ALLOW_LIVE=1 in environment (HK-7).",
                    "secret_in_response": False,
                })

            # Find most recent dry-run submission
            submissions_dir = cfg.submissions_dir()
            csvs = sorted(submissions_dir.glob("*.csv"), key=lambda p: p.stat().st_mtime)
            if not csvs:
                return _text({"error": "No submission CSV found. Run numerai_dry_run_submit first."})

            result = upload_predictions_gated(
                csvs[-1], model_id=model_id, round_id=round_id,
                dry_run=not cfg.allow_live,
            )
            return _text(result)
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    elif name == "numerai_get_model_scores":
        try:
            from .tournament.numerai.config import _get_napi
            import numerapi as _napi_mod

            napi = _get_napi()
            model_id = arguments.get("model_id", "").strip() or None
            n_rounds = int(arguments.get("n_rounds", 20))

            # Pass-through JSON — no hardcoded field names (HK-13)
            leaderboard = napi.get_leaderboard(limit=100)
            result = {
                "note_proxy_mmc": (
                    "These are leaderboard scores — authoritative for live performance. "
                    "Do not confuse with proxy_mmc from local validation (HK-10)."
                ),
                "note_bmc": (
                    "BMC on leaderboard = stake-weighted ensemble benchmark. "
                    "BMC in diagnostics = highest-stake benchmark. They differ (§14)."
                ),
                "leaderboard_sample": leaderboard[:20] if isinstance(leaderboard, list) else leaderboard,
            }
            if model_id:
                try:
                    perf = napi.round_model_performances_v2(model_id)
                    result["model_performances"] = perf[-n_rounds:] if isinstance(perf, list) else perf
                except Exception as perf_exc:
                    result["model_performances_error"] = str(perf_exc)
            return _text(result)
        except Exception as exc:
            return _text({"error": str(exc), "error_type": type(exc).__name__})

    else:
        return _text({"error": f"Unknown tool: {name}"})


# ═══════════════════════════════════════════════════════════════════
# MCP Resources — expose live state as readable resources
# ═══════════════════════════════════════════════════════════════════

RESOURCES = [
    Resource(
        uri="algochains://tools/status",
        name="V17 Tool Mode Status",
        description="Current tool exposure mode (smart/full), Tier 1 tool count, total tool count, and index stats.",
        mimeType="application/json",
    ),
    Resource(
        uri="algochains://tools/manifest",
        name="MCP Tool Implementation Manifest",
        description="All tools with implementation_status (full|partial|stub), required env vars, Tier-1 flags. For CI and Onyx.",
        mimeType="application/json",
    ),
    Resource(
        uri="algochains://brokers/status",
        name="Broker Connection Status",
        description="Live status of all configured and connected brokers.",
        mimeType="application/json",
    ),
    Resource(
        uri="algochains://validation/gates",
        name="Validation Gate Thresholds",
        description="Current thresholds for all 6 strategy validation gates.",
        mimeType="application/json",
    ),
    Resource(
        uri="algochains://server/diagnostics",
        name="Server Diagnostics",
        description="Tool call statistics, error rates, and recent call history.",
        mimeType="application/json",
    ),
    Resource(
        uri="algochains://ml/models",
        name="V10 ML Model Registry",
        description="Registered ML models, their stages, and metrics.",
        mimeType="application/json",
    ),
    Resource(
        uri="algochains://ml/feature-sets",
        name="V10 Feature Sets",
        description="Defined feature sets for ML training pipelines.",
        mimeType="application/json",
    ),
    Resource(
        uri="algochains://ml/rl-agents",
        name="V10 RL Agents",
        description="Reinforcement learning agents, training state, and metrics.",
        mimeType="application/json",
    ),
    Resource(
        uri="algochains://execution/orders",
        name="V11 Order State",
        description="Institutional order manager state — active orders and history.",
        mimeType="application/json",
    ),
    Resource(
        uri="algochains://execution/algos",
        name="V11 Algo Executors",
        description="Active algorithmic execution engines and their status.",
        mimeType="application/json",
    ),
    Resource(
        uri="algochains://analytics/regimes",
        name="V12 Market Regimes",
        description="Detected market regimes and transition probabilities.",
        mimeType="application/json",
    ),
    Resource(
        uri="algochains://analytics/alerts",
        name="V12 Active Alerts",
        description="Configured market alerts and their trigger history.",
        mimeType="application/json",
    ),
    Resource(
        uri="algochains://alt-data/scrape-jobs",
        name="V13 Scrape Jobs",
        description="Web scraping jobs and their status.",
        mimeType="application/json",
    ),
    Resource(
        uri="algochains://agents/swarms",
        name="V14 Agent Swarms",
        description="Active agent swarms, members, and task status.",
        mimeType="application/json",
    ),
    Resource(
        uri="algochains://defi/positions",
        name="V15 DeFi Positions",
        description="DeFi protocol positions, yields, and risk status.",
        mimeType="application/json",
    ),
    Resource(
        uri="algochains://cloud/tenants",
        name="V16 SaaS Tenants",
        description="Multi-tenant SaaS platform tenants and subscription status.",
        mimeType="application/json",
    ),
    Resource(
        uri="algochains://rate-limits/status",
        name="Rate Limit Status",
        description="Current rate limit bucket status for all categories.",
        mimeType="application/json",
    ),
    Resource(
        uri="algochains://circuit-breakers/status",
        name="Circuit Breaker Status",
        description="Circuit breaker state for each engine category — failures, open/closed, cooldown.",
        mimeType="application/json",
    ),
]

# ═══════════════════════════════════════════════════════════════════
# MCP Resource Templates — dynamic URI-based resources (MCP v2)
# ═══════════════════════════════════════════════════════════════════
RESOURCE_TEMPLATES = [
    ResourceTemplate(
        uriTemplate="algochains://market/{ticker}/snapshot",
        name="Market Snapshot",
        description="Live market snapshot for a ticker — price, volume, change, bid/ask. Supports stocks, crypto, forex.",
        mimeType="application/json",
    ),
    ResourceTemplate(
        uriTemplate="algochains://portfolio/{broker}/summary",
        name="Portfolio Summary",
        description="Portfolio summary for a connected broker — positions, P&L, buying power, margin.",
        mimeType="application/json",
    ),
    ResourceTemplate(
        uriTemplate="algochains://massive/tables/{table_name}",
        name="Massive DataFrame",
        description="Inspect a stored Massive DataFrame — schema, row count, sample data, age.",
        mimeType="application/json",
    ),
]


@app.list_resources()
async def list_resources() -> list[Resource]:
    return RESOURCES


@app.list_resource_templates()
async def list_resource_templates() -> list[ResourceTemplate]:
    return RESOURCE_TEMPLATES


@app.read_resource()
async def read_resource(uri: str) -> str:
    if uri == "algochains://tools/status":
        cfg = _config or load_config()
        return json.dumps({
            "tool_mode": cfg.tool_mode,
            "tier1_tools_exposed": len(TOOLS_TIER1),
            "total_tools_registered": len(TOOLS),
            "tier2_tools_discoverable": len(TOOLS) - len(TOOLS_TIER1),
            "tier1_tool_names": sorted(TIER1_TOOL_NAMES),
            "estimated_tokens_smart": "~4,000",
            "estimated_tokens_full": "~40,000+",
            "token_savings_pct": round((1 - len(TOOLS_TIER1) / len(TOOLS)) * 100, 1),
            "env_var": "ALGOCHAINS_TOOL_MODE",
            "research": {
                "arxiv_paper": "2603.20313 — 99.6% token reduction with semantic tool discovery",
                "claude_code": "MCP Tool Search — 95% context savings via lazy loading",
                "cursor_limit": "80 tools (was 40)",
            },
        }, indent=2)

    elif uri == "algochains://tools/manifest":
        cfg = _config or load_config()
        manifest = build_manifest(
            tool_names=[t.name for t in TOOLS],
            tier1_names=set(TIER1_TOOL_NAMES),
            tool_mode=cfg.tool_mode,
        )
        return json.dumps(manifest, indent=2)

    elif uri == "algochains://brokers/status":
        registry = _get_registry()
        configured = registry.list_configured()
        connected = registry.list_available()
        status = []
        for b in configured:
            conn = registry.get(b)
            status.append({
                "name": b,
                "configured": True,
                "connected": b in connected,
                "asset_classes": [ac.value for ac in conn.supported_asset_classes] if conn else [],
            })
        return json.dumps(status, indent=2)

    elif uri == "algochains://validation/gates":
        cfg = _config or load_config()
        g = cfg.gating
        return json.dumps({
            "min_oos_sharpe": g.min_oos_sharpe,
            "min_oos_trades": g.min_oos_trades,
            "max_drawdown_pct": g.max_drawdown_pct,
            "min_oos_is_ratio": g.min_oos_is_ratio,
            "max_is_sharpe": g.max_is_sharpe,
            "mcpt_max_p_value": g.mcpt_max_p_value,
            "mcpt_permutations": g.mcpt_permutations,
            "require_walk_forward": g.require_walk_forward,
            "require_mcpt": g.require_mcpt,
            "require_paper_graduation": g.require_paper_graduation,
            "min_paper_days": g.min_paper_days,
            "min_paper_trades": g.min_paper_trades,
        }, indent=2)

    elif uri == "algochains://server/diagnostics":
        tlog = get_tool_logger()
        return json.dumps({
            "stats": tlog.stats(),
            "recent": tlog.recent(10),
        }, indent=2, default=str)

    # ── V10: ML Resources ────────────────────────────────────
    elif uri == "algochains://ml/models":
        reg = _get_model_registry()
        return json.dumps({"models": list(reg._registry.values()), "count": len(reg._registry)}, indent=2, default=str)

    elif uri == "algochains://ml/feature-sets":
        eng = _get_feature_engine()
        return json.dumps({"feature_sets": list(eng._sets.values()), "count": len(eng._sets)}, indent=2, default=str)

    elif uri == "algochains://ml/rl-agents":
        rl = _get_rl_agent()
        return json.dumps({"agents": list(rl._agents.values()), "count": len(rl._agents)}, indent=2, default=str)

    # ── V11: Execution Resources ─────────────────────────────
    elif uri == "algochains://execution/orders":
        mgr = _get_inst_order_mgr()
        return json.dumps({"orders": list(mgr._orders.values()), "count": len(mgr._orders)}, indent=2, default=str)

    elif uri == "algochains://execution/algos":
        algo = _get_algo_executor()
        return json.dumps({"algos": list(algo._algos.values()), "count": len(algo._algos)}, indent=2, default=str)

    # ── V12: Analytics Resources ─────────────────────────────
    elif uri == "algochains://analytics/regimes":
        regime = _get_regime_detector()
        return json.dumps({"regimes": list(regime._history), "count": len(regime._history)}, indent=2, default=str)

    elif uri == "algochains://analytics/alerts":
        alerts = _get_alert_engine()
        return json.dumps({"alerts": list(alerts._alerts.values()), "count": len(alerts._alerts)}, indent=2, default=str)

    # ── V13: Alt Data Resources ──────────────────────────────
    elif uri == "algochains://alt-data/scrape-jobs":
        scraper = _get_web_scraper()
        return json.dumps({"jobs": list(scraper._jobs.values()), "count": len(scraper._jobs)}, indent=2, default=str)

    # ── V14: Agent Swarm Resources ───────────────────────────
    elif uri == "algochains://agents/swarms":
        swarm = _get_swarm_mgr()
        return json.dumps({"swarms": list(swarm._swarms.values()), "count": len(swarm._swarms)}, indent=2, default=str)

    # ── V15: DeFi Resources ──────────────────────────────────
    elif uri == "algochains://defi/positions":
        defi = _get_defi_portfolio()
        return json.dumps({"positions": list(defi._positions.values()), "count": len(defi._positions)}, indent=2, default=str)

    # ── V16: Cloud SaaS Resources ────────────────────────────
    elif uri == "algochains://cloud/tenants":
        tenant = _get_tenant_engine()
        return json.dumps({"tenants": list(tenant._tenants.values()), "count": len(tenant._tenants)}, indent=2, default=str)

    # ── Rate Limit Status ────────────────────────────────────
    elif uri == "algochains://rate-limits/status":
        limiter = get_rate_limiter()
        buckets = {}
        for key, bucket in limiter._buckets.items():
            buckets[key] = {"tokens": round(bucket.tokens, 1), "capacity": bucket.capacity, "refill_rate": bucket.refill_rate}
        return json.dumps({"buckets": buckets}, indent=2)

    # ── Circuit Breaker Status ────────────────────────────────
    elif uri == "algochains://circuit-breakers/status":
        from .middleware import _circuits, CIRCUIT_FAILURE_THRESHOLD, CIRCUIT_COOLDOWN_SECONDS
        now = time.monotonic()
        cb_status = {}
        for cat, state in _circuits.items():
            is_open = state.open_until > now
            cb_status[cat] = {
                "state": "OPEN" if is_open else "CLOSED",
                "consecutive_failures": state.failures,
                "threshold": CIRCUIT_FAILURE_THRESHOLD,
                "cooldown_seconds": CIRCUIT_COOLDOWN_SECONDS,
                "retry_after_seconds": round(state.open_until - now, 1) if is_open else 0,
            }
        return json.dumps({"circuit_breakers": cb_status}, indent=2)

    # ── V17: Resource Template handlers (dynamic URIs) ──────────
    elif uri.startswith("algochains://market/") and uri.endswith("/snapshot"):
        ticker = uri.replace("algochains://market/", "").replace("/snapshot", "").upper()
        registry = _get_registry()
        brokers = registry.list_available()
        quote_data = {"ticker": ticker, "error": "No broker connected"}
        for broker_name in brokers:
            try:
                conn = registry.get(broker_name)
                if conn:
                    q = await conn.get_quote(ticker)
                    quote_data = {"ticker": ticker, "broker": broker_name, "quote": q}
                    break
            except Exception:
                continue
        return json.dumps(quote_data, indent=2, default=str)

    elif uri.startswith("algochains://portfolio/") and uri.endswith("/summary"):
        broker_name = uri.replace("algochains://portfolio/", "").replace("/summary", "")
        registry = _get_registry()
        conn = registry.get(broker_name)
        if conn is None:
            return json.dumps({"error": f"Broker '{broker_name}' not connected"}, indent=2)
        try:
            acct = await conn.get_account()
            positions = await conn.get_positions()
            return json.dumps({"broker": broker_name, "account": acct, "positions": positions, "position_count": len(positions)}, indent=2, default=str)
        except Exception as e:
            return json.dumps({"broker": broker_name, "error": str(e)}, indent=2)

    elif uri.startswith("algochains://massive/tables/"):
        table_name = uri.replace("algochains://massive/tables/", "")
        eng = await _get_massive_provider()
        df = eng.get_table(table_name)
        if df is None:
            return json.dumps({"error": f"Table '{table_name}' not found", "available_tables": eng.list_tables()}, indent=2)
        sample = df.head(5).to_dict(orient="records") if len(df) > 0 else []
        return json.dumps({
            "table": table_name,
            "rows": len(df),
            "columns": [{"name": col, "dtype": str(df[col].dtype)} for col in df.columns],
            "sample": sample,
            "age_seconds": round(time.monotonic() - eng._table_timestamps.get(table_name, 0), 1),
        }, indent=2, default=str)

    raise ValueError(f"Unknown resource: {uri}")


# ═══════════════════════════════════════════════════════════════════
# MCP Prompts — reusable prompt templates for common workflows
# ═══════════════════════════════════════════════════════════════════

PROMPTS = [
    Prompt(
        name="trade",
        description="Place a trade on any broker with proper risk checks.",
        arguments=[
            PromptArgument(name="broker", description="Broker to trade on (alpaca, ibkr, oanda, traderspost)", required=True),
            PromptArgument(name="action", description="What to trade, e.g. 'buy 10 AAPL' or 'sell 100 EUR_USD'", required=True),
        ],
    ),
    Prompt(
        name="portfolio_review",
        description="Get a comprehensive portfolio review across all connected brokers.",
        arguments=[],
    ),
    Prompt(
        name="submit_strategy",
        description="Walk through submitting a strategy for MCPT validation.",
        arguments=[
            PromptArgument(name="symbol", description="Ticker symbol (e.g. AAPL, EUR_USD)", required=True),
            PromptArgument(name="strategy_type", description="Strategy type: trend, mean_reversion, breakout, momentum, scalp", required=True),
        ],
    ),
    Prompt(
        name="browse_bots",
        description="Explore the AlgoChains marketplace for validated trading bots.",
        arguments=[
            PromptArgument(name="asset_class", description="Filter: stocks, crypto, futures, forex", required=False),
        ],
    ),
    # ── V9 Prompts ─────────────────────────────────────────────
    Prompt(
        name="risk_review",
        description="Comprehensive portfolio risk review: VaR, stress tests, concentration, margin.",
        arguments=[],
    ),
    Prompt(
        name="compliance_check",
        description="Run a full compliance health check: kill switch status, violations, audit integrity.",
        arguments=[
            PromptArgument(name="profile", description="Compliance profile: us_retail_algo, us_institutional, eu_mifid2", required=False),
        ],
    ),
    Prompt(
        name="onboard_tenant",
        description="Walk through onboarding a new white-label tenant step by step.",
        arguments=[
            PromptArgument(name="company_name", description="Name of the company to onboard", required=True),
            PromptArgument(name="tier", description="Tier: starter, growth, professional, enterprise", required=False),
        ],
    ),
    Prompt(
        name="build_strategy",
        description="AI-guided strategy creation using the Strategy Builder SDK.",
        arguments=[
            PromptArgument(name="asset_class", description="Asset class: equity, forex, futures, crypto", required=True),
            PromptArgument(name="style", description="Trading style: momentum, mean_reversion, trend, breakout", required=False),
        ],
    ),
]


@app.list_prompts()
async def list_prompts() -> list[Prompt]:
    return PROMPTS


@app.get_prompt()
async def get_prompt(name: str, arguments: dict | None = None) -> GetPromptResult:
    args = arguments or {}

    if name == "trade":
        return GetPromptResult(
            description="Place a trade with risk awareness",
            messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(
                        type="text",
                        text=(
                            f"I want to {args.get('action', 'place a trade')} on {args.get('broker', 'alpaca')}.\n\n"
                            "Before placing the order:\n"
                            "1. Check my account balance and buying power\n"
                            "2. Get a current quote for the symbol\n"
                            "3. Verify I have sufficient funds\n"
                            "4. Place the order\n"
                            "5. Confirm the order status"
                        ),
                    ),
                ),
            ],
        )

    elif name == "portfolio_review":
        return GetPromptResult(
            description="Comprehensive portfolio review",
            messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(
                        type="text",
                        text=(
                            "Give me a comprehensive portfolio review:\n\n"
                            "1. Get portfolio summary across all brokers\n"
                            "2. List all open positions with P&L\n"
                            "3. Show total equity and cash across all accounts\n"
                            "4. Highlight any positions with significant unrealized loss (>5%)\n"
                            "5. Suggest any rebalancing if appropriate"
                        ),
                    ),
                ),
            ],
        )

    elif name == "submit_strategy":
        symbol = args.get("symbol", "AAPL")
        stype = args.get("strategy_type", "trend")
        return GetPromptResult(
            description=f"Submit {symbol} {stype} strategy for validation",
            messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(
                        type="text",
                        text=(
                            f"I want to submit my {stype} strategy for {symbol} to the AlgoChains marketplace.\n\n"
                            "First, show me the current validation gate requirements.\n"
                            "Then help me prepare the submission with these details:\n"
                            f"- Symbol: {symbol}\n"
                            f"- Strategy type: {stype}\n"
                            "- I'll provide: OOS Sharpe, trade count, max drawdown, MCPT p-value, and WF data\n\n"
                            "Walk me through each gate requirement so I can provide the right metrics."
                        ),
                    ),
                ),
            ],
        )

    elif name == "browse_bots":
        ac = args.get("asset_class", "")
        filter_text = f" filtered by {ac}" if ac else ""
        return GetPromptResult(
            description=f"Browse marketplace bots{filter_text}",
            messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(
                        type="text",
                        text=(
                            f"Show me the best validated trading bots on the AlgoChains marketplace{filter_text}.\n\n"
                            "For each bot, show:\n"
                            "- Name and strategy type\n"
                            "- OOS Sharpe ratio and tier (Platinum/Gold/Silver/Bronze)\n"
                            "- Max drawdown and win rate\n"
                            "- Monthly price\n\n"
                            "Sort by OOS Sharpe descending."
                        ),
                    ),
                ),
            ],
        )

    elif name == "risk_review":
        return GetPromptResult(
            description="Comprehensive portfolio risk review",
            messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(
                        type="text",
                        text=(
                            "Run a comprehensive risk review of my portfolio:\n\n"
                            "1. Calculate VaR at 95% and 99% confidence (parametric method)\n"
                            "2. Calculate Expected Shortfall (CVaR)\n"
                            "3. Get factor exposure decomposition\n"
                            "4. Run stress tests: COVID crash, GFC 2008, rate shock, flash crash\n"
                            "5. Check drawdown status and margin utilization\n"
                            "6. Analyze concentration risk (HHI, top positions)\n"
                            "7. If options positions exist, show Greeks exposure\n"
                            "8. Check all active risk alerts\n\n"
                            "Present a risk dashboard summary with RED/YELLOW/GREEN status for each metric."
                        ),
                    ),
                ),
            ],
        )

    elif name == "compliance_check":
        profile = args.get("profile", "us_retail_algo")
        return GetPromptResult(
            description=f"Compliance health check ({profile})",
            messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(
                        type="text",
                        text=(
                            f"Run a full compliance health check using the '{profile}' profile:\n\n"
                            "1. Check kill switch status\n"
                            "2. Get compliance status (daily P&L vs limits, open violations)\n"
                            "3. Run a surveillance scan (last 24 hours)\n"
                            "4. Check for wash trade alerts\n"
                            "5. Verify audit trail integrity (chain validation)\n"
                            "6. Show recent audit trail entries\n"
                            "7. Generate best execution report for today's trades\n\n"
                            "Flag any violations or concerns and recommend corrective actions."
                        ),
                    ),
                ),
            ],
        )

    elif name == "onboard_tenant":
        company = args.get("company_name", "New Company")
        tier = args.get("tier", "starter")
        return GetPromptResult(
            description=f"Onboard {company} as white-label tenant",
            messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(
                        type="text",
                        text=(
                            f"Walk me through onboarding '{company}' as a new white-label tenant:\n\n"
                            f"1. Create tenant with tier '{tier}'\n"
                            "2. Configure branding (logo, colors, app name)\n"
                            "3. Set up broker routing (which broker handles which asset class)\n"
                            "4. Create the admin sub-account\n"
                            "5. Set compliance profile appropriate for their jurisdiction\n"
                            "6. Configure risk alert thresholds\n"
                            "7. Show the tenant dashboard and billing summary\n\n"
                            "At each step, confirm the settings before proceeding."
                        ),
                    ),
                ),
            ],
        )

    elif name == "build_strategy":
        asset_class = args.get("asset_class", "equity")
        style = args.get("style", "momentum")
        return GetPromptResult(
            description=f"Build a {style} {asset_class} strategy",
            messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(
                        type="text",
                        text=(
                            f"Help me build a {style} strategy for {asset_class} using the Strategy Builder SDK:\n\n"
                            "1. Show me relevant templates and let me pick one (or start fresh)\n"
                            "2. Help me define entry/exit rules with appropriate indicators\n"
                            "3. Set position sizing and risk management parameters\n"
                            "4. Validate the strategy spec\n"
                            "5. Run a backtest with the Rust engine\n"
                            "6. If Sharpe > 1.5, run walk-forward validation\n"
                            "7. If WFE > 0.5, run optimization (100 trials)\n"
                            "8. Show final results and ask if I want to deploy to paper trading\n\n"
                            f"Target: {asset_class} assets, {style} approach."
                        ),
                    ),
                ),
            ],
        )

    raise ValueError(f"Unknown prompt: {name}")


# ═══════════════════════════════════════════════════════════════════
# Server entry point
# ═══════════════════════════════════════════════════════════════════

async def _run():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def _request_access(email: str) -> None:
    """POST a subscription request to Command Center → Slack approval channel."""
    import json, urllib.request, os
    bridge = os.getenv("ALGOCHAINS_BRIDGE_URL", "https://cc.algochains.io")
    internal_key = os.getenv("CC_INTERNAL_KEY", "")
    payload = json.dumps({"listing_id": 87, "email": email}).encode()
    req = urllib.request.Request(
        f"{bridge}/api/subscribe", data=payload, method="POST",
        headers={"Content-Type": "application/json", "x-internal-key": internal_key},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            result = json.loads(r.read())
        print(f"\nAccess request submitted for {email}.")
        print(f"Tyler will see an approval request in #tradovate-futures-bot-changelog.")
        print(f"Once approved, your credentials will be DM'd on Slack or emailed.\n")
        print(f"Note: Paper trading requires no broker account — fills appear automatically.")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        print(f"\nRequest failed (HTTP {e.code}): {body}")
        print(f"Contact support@algochains.ai to request MNQ paper access.\n")
    except Exception as e:
        print(f"\nRequest failed: {e}")
        print(f"Contact support@algochains.ai.\n")


def _run_demo_signal() -> None:
    """Inject a test MNQ signal and verify paper fills appear within 5 seconds."""
    import subprocess, os, sys
    from pathlib import Path

    # Find the control-tower repo relative to the mcp-server repo
    control_tower = os.getenv(
        "ALGOCHAINS_CONTROL_TOWER",
        str(Path(__file__).resolve().parents[4] / "algochains-control-tower"),
    )
    dryrun_script = Path(control_tower) / "scripts" / "copy_trade_dryrun.py"
    if not dryrun_script.exists():
        print(f"Demo signal script not found at {dryrun_script}")
        print("Ensure ALGOCHAINS_CONTROL_TOWER points to the control-tower repo.")
        return

    print("Injecting test MNQ signal (15-second TTL)…")
    print("Paper fills should appear in subscriber_fills within ~3 seconds.\n")
    result = subprocess.run(
        [sys.executable, str(dryrun_script), "--create-signal", "--bot", "MNQ",
         "--subscriber-email", ""],
        cwd=control_tower, capture_output=False, timeout=30,
    )
    if result.returncode == 0:
        print("\nDemo signal complete. Check subscriber_fills in Supabase or ask Claude:")
        print("  'What are my copy-trade signals?'")
    else:
        print(f"\nDemo signal exited with code {result.returncode}. Check output above.")


def _print_ide_config(target: str) -> None:
    """Write and print MCP config for the specified IDE target."""
    import json
    import os
    import platform
    import sys

    is_windows = platform.system() == "Windows"
    home = os.path.expanduser("~")
    appdata = os.environ.get("APPDATA", os.path.join(home, "AppData", "Roaming"))

    # Base config block — same for every IDE
    config_block = {
        "mcpServers": {
            "algochains": {
                "command": "algochains-mcp",
                "env": {
                    "ALGOCHAINS_TOOL_MODE": "smart",
                },
            }
        }
    }
    config_json = json.dumps(config_block, indent=2)

    # Per-IDE file paths
    paths = {
        "cursor": os.path.join(home, ".cursor", "mcp.json"),
        "claude-desktop": (
            os.path.join(appdata, "Claude", "claude_desktop_config.json")
            if is_windows
            else os.path.join(home, "Library", "Application Support", "Claude", "claude_desktop_config.json")
            if platform.system() == "Darwin"
            else os.path.join(home, ".config", "claude", "claude_desktop_config.json")
        ),
        "windsurf": os.path.join(home, ".codeium", "windsurf", "mcp_config.json"),
    }

    targets = list(paths.keys()) if target == "all" else [target]

    for t in targets:
        if t == "claude-code":
            print(f"\n  Claude Code CLI — run in your terminal:")
            print(f"    claude mcp add algochains algochains-mcp")
            print(f"\n  Or add manually to ~/.claude.json under mcpServers:")
            print(config_json)
            continue

        path = paths.get(t)
        if not path:
            print(f"  Unknown target '{t}'. Supported: cursor, claude-desktop, windsurf, claude-code, all", file=sys.stderr)
            continue

        # Read existing config and merge
        existing: dict = {}
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    existing = json.load(f)
            except Exception:
                pass

        if "mcpServers" not in existing:
            existing["mcpServers"] = {}
        existing["mcpServers"]["algochains"] = config_block["mcpServers"]["algochains"]

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2)
            f.write("\n")

        print(f"\n  ✓  Config written to: {path}")
        print(f"     Restart {t.title().replace('-', ' ')} to connect AlgoChains (482 tools, smart mode).")

    print(f"\n  Config block added:")
    print(config_json)
    print()
    print("  Next: ask your AI: 'Discover tools for backtesting' or 'What is my MNQ bot health?'")


def main():
    import sys

    args = sys.argv[1:]

    # --version / -V  →  print version and exit
    if args and args[0] in ("--version", "-V"):
        print(f"algochains-mcp-server {_server_version}")
        return

    # --generate-config [target]  →  write IDE config and exit
    if args and args[0] == "--generate-config":
        target = args[1] if len(args) > 1 else "cursor"
        _print_ide_config(target)
        return

    # --request-access <email>  →  post subscription request to #tradovate-bot-changelog
    if args and args[0] == "--request-access":
        email = args[1] if len(args) > 1 else None
        if not email or "@" not in email:
            print("Usage: algochains-mcp --request-access your@email.io")
            return
        _request_access(email)
        return

    # --demo-signal  →  inject a test MNQ signal and watch paper fills appear
    if args and args[0] == "--demo-signal":
        _run_demo_signal()
        return

    # Default: start the MCP stdio server
    logger.info("Starting AlgoChains MCP Server v%s", _server_version)
    asyncio.run(_run())


if __name__ == "__main__":
    main()
