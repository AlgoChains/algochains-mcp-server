"""
AlgoChains MCP Server — the main entry point.

Exposes 25+ tools across 5 domains:
  1. Trading    — place/cancel/close orders on any connected broker
  2. Portfolio  — positions, account info, P&L across all brokers
  3. Market     — quotes, snapshots
  4. Marketplace — browse/publish/subscribe to AlgoChains bot listings
  5. Strategy   — submit strategies for MCPT validation, check gate status

Start with:  algochains-mcp  (or python -m algochains_mcp.server)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    GetPromptResult,
    Prompt,
    PromptArgument,
    PromptMessage,
    Resource,
    TextContent,
    Tool,
)

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
from .middleware import get_rate_limiter, get_tool_logger
from .streaming.manager import StreamManager, StreamTopic
from .portfolio.optimizer import AllocationMethod, BotMetrics, PortfolioOptimizer
from .notifications.push import (
    Notification, NotificationChannel, NotificationDispatcher,
    NotificationEvent, NotificationPriority,
)
from .data_providers.registry import DataProviderRegistry
from .data_providers.base import Interval
from .byok.key_orchestrator import KeyOrchestrator
from .datasets.builder import DatasetBuilder, DatasetRequest
from .strategy_builder.spec import StrategySpec, StrategySpecValidator
from .strategy_builder.backtest_runner import BacktestRunner
from .strategy_builder.optimizer import StrategyOptimizer
from .strategy_builder.walk_forward import WalkForwardEngine
from .strategy_builder.deployer import StrategyDeployer
from .strategy_builder.template_manager import TemplateManager
from .social_trading.engine import SocialTradingEngine
from .community_signals.engine import CommunitySignalEngine
from .risk_dashboard.engine import RiskDashboardEngine
from .compliance.engine import ComplianceEngine
from .multi_tenant.engine import MultiTenantEngine
# V10: ML/AI-Native Strategy Engine
from .ml_engine.feature_engine import FeatureEngine
from .ml_engine.model_trainer import ModelTrainer
from .ml_engine.model_registry import ModelRegistry
from .ml_engine.rl_agent import RLAgentEngine
from .ml_engine.gpu_dispatcher import GPUDispatcher
from .ml_engine.llm_strategy_gen import LLMStrategyGenerator
# V11: Institutional-Grade Execution
from .execution_engine.order_manager import InstitutionalOrderManager
from .execution_engine.smart_order_router import SmartOrderRouter
from .execution_engine.algo_executor import AlgoExecutor
from .execution_engine.fix_gateway import FIXGateway
from .execution_engine.tca_engine import TCAEngine
from .execution_engine.venue_manager import VenueManager
# V12: Real-Time Analytics
from .realtime_analytics.pnl_streamer import PnLStreamer
from .realtime_analytics.order_flow_analyzer import OrderFlowAnalyzer
from .realtime_analytics.microstructure import MicrostructureEngine
from .realtime_analytics.regime_detector import RegimeDetector
from .realtime_analytics.alert_engine import AlertEngine
# V13: Alternative Data Marketplace
from .alt_data.sentiment_engine import SentimentEngine
from .alt_data.satellite_engine import SatelliteDataEngine
from .alt_data.web_scraper import WebScraperEngine
from .alt_data.sec_filing_engine import SECFilingEngine
from .alt_data.social_media_engine import SocialMediaEngine
from .alt_data.alt_data_marketplace import AltDataMarketplace
# V14: Autonomous Agent Swarm
from .agent_swarm.agent_orchestrator import AgentOrchestrator
from .agent_swarm.task_planner import TaskPlanner
from .agent_swarm.agent_memory import AgentMemory
from .agent_swarm.tool_router import ToolRouter
from .agent_swarm.consensus_engine import ConsensusEngine
from .agent_swarm.agent_monitor import AgentMonitor
# V15: DeFi & Cross-Chain
from .defi_engine.dex_aggregator import DEXAggregator
from .defi_engine.yield_optimizer import YieldOptimizer
from .defi_engine.bridge_engine import BridgeEngine
from .defi_engine.mev_protector import MEVProtector
from .defi_engine.governance_engine import GovernanceEngine
from .defi_engine.defi_risk_engine import DeFiRiskEngine
# V16: Cloud SaaS
from .cloud_saas.tenant_manager import TenantManager
from .cloud_saas.billing_engine import BillingEngine
from .cloud_saas.strategy_marketplace import StrategyMarketplace
from .cloud_saas.white_label_engine import WhiteLabelEngine
from .cloud_saas.api_gateway import APIGateway

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("algochains_mcp.server")

app = Server("algochains-mcp-server")

_config: ServerConfig | None = None
_registry: BrokerRegistry | None = None
_validator: StrategyValidator | None = None
_bridge: MarketplaceBridge | None = None
_stream_manager: StreamManager | None = None
_portfolio_optimizer: PortfolioOptimizer | None = None
_notifier: NotificationDispatcher | None = None
_data_registry: DataProviderRegistry | None = None
_key_orchestrator: KeyOrchestrator | None = None
_dataset_builder: DatasetBuilder | None = None
_spec_validator: StrategySpecValidator | None = None
_backtest_runner: BacktestRunner | None = None
_strategy_optimizer: StrategyOptimizer | None = None
_walk_forward: WalkForwardEngine | None = None
_deployer: StrategyDeployer | None = None
_template_mgr: TemplateManager | None = None
_social_engine: SocialTradingEngine | None = None
_signal_engine: CommunitySignalEngine | None = None
_risk_engine: RiskDashboardEngine | None = None
_compliance_engine: ComplianceEngine | None = None
_tenant_engine: MultiTenantEngine | None = None
# V10 singletons
_feature_engine: FeatureEngine | None = None
_model_trainer: ModelTrainer | None = None
_model_registry: ModelRegistry | None = None
_rl_agent: RLAgentEngine | None = None
_gpu_dispatcher: GPUDispatcher | None = None
_llm_strategy_gen: LLMStrategyGenerator | None = None
# V11 singletons
_inst_order_mgr: InstitutionalOrderManager | None = None
_smart_router: SmartOrderRouter | None = None
_algo_executor: AlgoExecutor | None = None
_fix_gateway: FIXGateway | None = None
_tca_engine: TCAEngine | None = None
_venue_manager: VenueManager | None = None
# V12 singletons
_pnl_streamer: PnLStreamer | None = None
_order_flow: OrderFlowAnalyzer | None = None
_microstructure: MicrostructureEngine | None = None
_regime_detector: RegimeDetector | None = None
_alert_engine: AlertEngine | None = None
# V13 singletons
_sentiment_engine: SentimentEngine | None = None
_satellite_engine: SatelliteDataEngine | None = None
_web_scraper: WebScraperEngine | None = None
_sec_filing: SECFilingEngine | None = None
_social_media: SocialMediaEngine | None = None
_alt_data_market: AltDataMarketplace | None = None
# V14 singletons
_agent_orchestrator: AgentOrchestrator | None = None
_task_planner: TaskPlanner | None = None
_agent_memory: AgentMemory | None = None
_tool_router: ToolRouter | None = None
_consensus_engine: ConsensusEngine | None = None
_agent_monitor: AgentMonitor | None = None
# V15 singletons
_dex_aggregator: DEXAggregator | None = None
_yield_optimizer: YieldOptimizer | None = None
_bridge_engine: BridgeEngine | None = None
_mev_protector: MEVProtector | None = None
_governance_engine: GovernanceEngine | None = None
_defi_risk: DeFiRiskEngine | None = None
# V16 singletons
_saas_tenant_mgr: TenantManager | None = None
_billing_engine: BillingEngine | None = None
_strategy_market: StrategyMarketplace | None = None
_white_label: WhiteLabelEngine | None = None
_api_gateway: APIGateway | None = None


def _get_registry() -> BrokerRegistry:
    global _config, _registry
    if _registry is None:
        _config = load_config()
        _registry = BrokerRegistry(_config)
    return _registry


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


def _get_stream_manager() -> StreamManager:
    global _stream_manager
    if _stream_manager is None:
        _stream_manager = StreamManager()
    return _stream_manager


def _get_portfolio_optimizer() -> PortfolioOptimizer:
    global _portfolio_optimizer
    if _portfolio_optimizer is None:
        _portfolio_optimizer = PortfolioOptimizer()
    return _portfolio_optimizer


def _get_notifier() -> NotificationDispatcher:
    global _notifier
    if _notifier is None:
        _notifier = NotificationDispatcher()
    return _notifier


def _get_data_registry() -> DataProviderRegistry:
    global _data_registry
    if _data_registry is None:
        _data_registry = DataProviderRegistry()
    return _data_registry


def _get_key_orchestrator() -> KeyOrchestrator:
    global _key_orchestrator
    if _key_orchestrator is None:
        _key_orchestrator = KeyOrchestrator()
    return _key_orchestrator


def _get_dataset_builder() -> DatasetBuilder:
    global _dataset_builder
    if _dataset_builder is None:
        _dataset_builder = DatasetBuilder()
    return _dataset_builder


def _get_spec_validator() -> StrategySpecValidator:
    global _spec_validator
    if _spec_validator is None:
        _spec_validator = StrategySpecValidator()
    return _spec_validator


def _get_backtest_runner() -> BacktestRunner:
    global _backtest_runner
    if _backtest_runner is None:
        _backtest_runner = BacktestRunner()
    return _backtest_runner


def _get_strategy_optimizer() -> StrategyOptimizer:
    global _strategy_optimizer
    if _strategy_optimizer is None:
        _strategy_optimizer = StrategyOptimizer(_get_backtest_runner())
    return _strategy_optimizer


def _get_walk_forward() -> WalkForwardEngine:
    global _walk_forward
    if _walk_forward is None:
        _walk_forward = WalkForwardEngine(_get_backtest_runner())
    return _walk_forward


def _get_deployer() -> StrategyDeployer:
    global _deployer
    if _deployer is None:
        _deployer = StrategyDeployer()
    return _deployer


def _get_template_mgr() -> TemplateManager:
    global _template_mgr
    if _template_mgr is None:
        _template_mgr = TemplateManager()
    return _template_mgr


def _get_social_engine() -> SocialTradingEngine:
    global _social_engine
    if _social_engine is None:
        _social_engine = SocialTradingEngine()
    return _social_engine


def _get_signal_engine() -> CommunitySignalEngine:
    global _signal_engine
    if _signal_engine is None:
        _signal_engine = CommunitySignalEngine()
    return _signal_engine


def _get_risk_engine() -> RiskDashboardEngine:
    global _risk_engine
    if _risk_engine is None:
        _risk_engine = RiskDashboardEngine()
    return _risk_engine


def _get_compliance_engine() -> ComplianceEngine:
    global _compliance_engine
    if _compliance_engine is None:
        _compliance_engine = ComplianceEngine()
    return _compliance_engine


def _get_tenant_engine() -> MultiTenantEngine:
    global _tenant_engine
    if _tenant_engine is None:
        _tenant_engine = MultiTenantEngine()
    return _tenant_engine


# ── V10 getters ──────────────────────────────────────────────
def _get_feature_engine() -> FeatureEngine:
    global _feature_engine
    if _feature_engine is None:
        _feature_engine = FeatureEngine()
    return _feature_engine

def _get_model_trainer() -> ModelTrainer:
    global _model_trainer
    if _model_trainer is None:
        _model_trainer = ModelTrainer()
    return _model_trainer

def _get_model_registry() -> ModelRegistry:
    global _model_registry
    if _model_registry is None:
        _model_registry = ModelRegistry()
    return _model_registry

def _get_rl_agent() -> RLAgentEngine:
    global _rl_agent
    if _rl_agent is None:
        _rl_agent = RLAgentEngine()
    return _rl_agent

def _get_gpu_dispatcher() -> GPUDispatcher:
    global _gpu_dispatcher
    if _gpu_dispatcher is None:
        _gpu_dispatcher = GPUDispatcher()
    return _gpu_dispatcher

def _get_llm_strategy_gen() -> LLMStrategyGenerator:
    global _llm_strategy_gen
    if _llm_strategy_gen is None:
        _llm_strategy_gen = LLMStrategyGenerator()
    return _llm_strategy_gen

# ── V11 getters ──────────────────────────────────────────────
def _get_inst_order_mgr() -> InstitutionalOrderManager:
    global _inst_order_mgr
    if _inst_order_mgr is None:
        _inst_order_mgr = InstitutionalOrderManager()
    return _inst_order_mgr

def _get_smart_router() -> SmartOrderRouter:
    global _smart_router
    if _smart_router is None:
        _smart_router = SmartOrderRouter()
    return _smart_router

def _get_algo_executor() -> AlgoExecutor:
    global _algo_executor
    if _algo_executor is None:
        _algo_executor = AlgoExecutor()
    return _algo_executor

def _get_fix_gateway() -> FIXGateway:
    global _fix_gateway
    if _fix_gateway is None:
        _fix_gateway = FIXGateway()
    return _fix_gateway

def _get_tca_engine() -> TCAEngine:
    global _tca_engine
    if _tca_engine is None:
        _tca_engine = TCAEngine()
    return _tca_engine

def _get_venue_manager() -> VenueManager:
    global _venue_manager
    if _venue_manager is None:
        _venue_manager = VenueManager()
    return _venue_manager

# ── V12 getters ──────────────────────────────────────────────
def _get_pnl_streamer() -> PnLStreamer:
    global _pnl_streamer
    if _pnl_streamer is None:
        _pnl_streamer = PnLStreamer()
    return _pnl_streamer

def _get_order_flow() -> OrderFlowAnalyzer:
    global _order_flow
    if _order_flow is None:
        _order_flow = OrderFlowAnalyzer()
    return _order_flow

def _get_microstructure() -> MicrostructureEngine:
    global _microstructure
    if _microstructure is None:
        _microstructure = MicrostructureEngine()
    return _microstructure

def _get_regime_detector() -> RegimeDetector:
    global _regime_detector
    if _regime_detector is None:
        _regime_detector = RegimeDetector()
    return _regime_detector

def _get_alert_engine() -> AlertEngine:
    global _alert_engine
    if _alert_engine is None:
        _alert_engine = AlertEngine()
    return _alert_engine

# ── V13 getters ──────────────────────────────────────────────
def _get_sentiment_engine() -> SentimentEngine:
    global _sentiment_engine
    if _sentiment_engine is None:
        _sentiment_engine = SentimentEngine()
    return _sentiment_engine

def _get_satellite_engine() -> SatelliteDataEngine:
    global _satellite_engine
    if _satellite_engine is None:
        _satellite_engine = SatelliteDataEngine()
    return _satellite_engine

def _get_web_scraper() -> WebScraperEngine:
    global _web_scraper
    if _web_scraper is None:
        _web_scraper = WebScraperEngine()
    return _web_scraper

def _get_sec_filing() -> SECFilingEngine:
    global _sec_filing
    if _sec_filing is None:
        _sec_filing = SECFilingEngine()
    return _sec_filing

def _get_social_media() -> SocialMediaEngine:
    global _social_media
    if _social_media is None:
        _social_media = SocialMediaEngine()
    return _social_media

def _get_alt_data_market() -> AltDataMarketplace:
    global _alt_data_market
    if _alt_data_market is None:
        _alt_data_market = AltDataMarketplace()
    return _alt_data_market

# ── V14 getters ──────────────────────────────────────────────
def _get_agent_orchestrator() -> AgentOrchestrator:
    global _agent_orchestrator
    if _agent_orchestrator is None:
        _agent_orchestrator = AgentOrchestrator()
    return _agent_orchestrator

def _get_task_planner() -> TaskPlanner:
    global _task_planner
    if _task_planner is None:
        _task_planner = TaskPlanner()
    return _task_planner

def _get_agent_memory() -> AgentMemory:
    global _agent_memory
    if _agent_memory is None:
        _agent_memory = AgentMemory()
    return _agent_memory

def _get_tool_router() -> ToolRouter:
    global _tool_router
    if _tool_router is None:
        _tool_router = ToolRouter()
    return _tool_router

def _get_consensus_engine() -> ConsensusEngine:
    global _consensus_engine
    if _consensus_engine is None:
        _consensus_engine = ConsensusEngine()
    return _consensus_engine

def _get_agent_monitor() -> AgentMonitor:
    global _agent_monitor
    if _agent_monitor is None:
        _agent_monitor = AgentMonitor()
    return _agent_monitor

# ── V15 getters ──────────────────────────────────────────────
def _get_dex_aggregator() -> DEXAggregator:
    global _dex_aggregator
    if _dex_aggregator is None:
        _dex_aggregator = DEXAggregator()
    return _dex_aggregator

def _get_yield_optimizer() -> YieldOptimizer:
    global _yield_optimizer
    if _yield_optimizer is None:
        _yield_optimizer = YieldOptimizer()
    return _yield_optimizer

def _get_bridge_engine() -> BridgeEngine:
    global _bridge_engine
    if _bridge_engine is None:
        _bridge_engine = BridgeEngine()
    return _bridge_engine

def _get_mev_protector() -> MEVProtector:
    global _mev_protector
    if _mev_protector is None:
        _mev_protector = MEVProtector()
    return _mev_protector

def _get_governance_engine() -> GovernanceEngine:
    global _governance_engine
    if _governance_engine is None:
        _governance_engine = GovernanceEngine()
    return _governance_engine

def _get_defi_risk() -> DeFiRiskEngine:
    global _defi_risk
    if _defi_risk is None:
        _defi_risk = DeFiRiskEngine()
    return _defi_risk

# ── V16 getters ──────────────────────────────────────────────
def _get_saas_tenant_mgr() -> TenantManager:
    global _saas_tenant_mgr
    if _saas_tenant_mgr is None:
        _saas_tenant_mgr = TenantManager()
    return _saas_tenant_mgr

def _get_billing_engine() -> BillingEngine:
    global _billing_engine
    if _billing_engine is None:
        _billing_engine = BillingEngine()
    return _billing_engine

def _get_strategy_market() -> StrategyMarketplace:
    global _strategy_market
    if _strategy_market is None:
        _strategy_market = StrategyMarketplace()
    return _strategy_market

def _get_white_label() -> WhiteLabelEngine:
    global _white_label
    if _white_label is None:
        _white_label = WhiteLabelEngine()
    return _white_label

def _get_api_gateway() -> APIGateway:
    global _api_gateway
    if _api_gateway is None:
        _api_gateway = APIGateway()
    return _api_gateway


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
            },
            "required": ["broker", "symbol", "side", "qty"],
        },
    ),
    Tool(
        name="cancel_order",
        description="Cancel an open order by ID on a specific broker.",
        inputSchema={
            "type": "object",
            "properties": {
                "broker": {"type": "string"},
                "order_id": {"type": "string"},
            },
            "required": ["broker", "order_id"],
        },
    ),
    Tool(
        name="close_position",
        description="Close an entire position in a symbol on a specific broker.",
        inputSchema={
            "type": "object",
            "properties": {
                "broker": {"type": "string"},
                "symbol": {"type": "string"},
            },
            "required": ["broker", "symbol"],
        },
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
    ),
    Tool(
        name="get_portfolio_summary",
        description="Get a unified portfolio summary across ALL connected brokers — total equity, positions, and P&L.",
        inputSchema={"type": "object", "properties": {}},
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
    ),
    # ── Broker Management ───────────────────────────────────────
    Tool(
        name="list_brokers",
        description="List all configured and connected brokers with their status and supported asset classes.",
        inputSchema={"type": "object", "properties": {}},
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
    ),
    Tool(
        name="broker_health_check",
        description="Run health check on all connected brokers.",
        inputSchema={"type": "object", "properties": {}},
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
    ),
    Tool(
        name="subscribe_to_bot",
        description="Subscribe to a marketplace bot listing for paper or live trading.",
        inputSchema={
            "type": "object",
            "properties": {
                "slug": {"type": "string"},
                "broker": {"type": "string", "description": "Which broker to deploy on"},
                "mode": {"type": "string", "enum": ["paper", "live"], "default": "paper"},
            },
            "required": ["slug", "broker"],
        },
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
    ),
    Tool(
        name="get_validation_gates",
        description="Get the current validation gate thresholds and requirements for strategy submissions.",
        inputSchema={"type": "object", "properties": {}},
    ),
    # ── Diagnostics ────────────────────────────────────────────
    Tool(
        name="server_diagnostics",
        description="Get AlgoChains MCP server diagnostics: tool call statistics, error rates, recent call history, and broker connection status.",
        inputSchema={"type": "object", "properties": {}},
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
    ),
    Tool(
        name="get_realtime_pnl",
        description="Get real-time P&L snapshot across all connected brokers with live equity, unrealized P&L, and daily change.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="stream_stats",
        description="Get streaming system statistics: buffer sizes, active subscriptions, callback counts.",
        inputSchema={"type": "object", "properties": {}},
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
    ),
    Tool(
        name="notification_stats",
        description="Get notification system statistics: configured channels, send counts by event and priority.",
        inputSchema={"type": "object", "properties": {}},
    ),
    # ── Data Providers (Optional) ─────────────────────────────
    Tool(
        name="list_data_providers",
        description="List all available and configured data providers (Polygon, Yahoo Finance, Alpha Vantage, Finnhub, Twelve Data, etc.).",
        inputSchema={"type": "object", "properties": {}},
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
    ),
    Tool(
        name="data_provider_health",
        description="Run health checks on all configured data providers.",
        inputSchema={"type": "object", "properties": {}},
    ),
    # ── V7: BYOK Key Orchestrator ──────────────────────────────
    Tool(
        name="discover_keys",
        description="Autonomously scan your environment for existing API keys across 10+ data providers. Checks env vars, .env files, IDE configs, shell profiles, and config directories. Say 'gather my keys' to trigger.",
        inputSchema={"type": "object", "properties": {}},
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
    ),
    Tool(
        name="key_gap_analysis",
        description="Show what data providers you're missing, what each unlocks, signup URLs, free tier availability, and a quick-win recommendation.",
        inputSchema={"type": "object", "properties": {}},
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
    ),
    Tool(
        name="key_health",
        description="Real-time health check of all configured API keys. Shows which are valid, expired, rate-limited, or invalid.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="export_config",
        description="Export your validated key configuration in various formats: env, json, mcp_windsurf, mcp_cursor, mcp_vscode.",
        inputSchema={
            "type": "object",
            "properties": {
                "format": {"type": "string", "enum": ["env", "json", "mcp_windsurf", "mcp_cursor", "mcp_vscode"], "default": "env"},
            },
        },
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
    ),
    Tool(
        name="list_datasets",
        description="List all built proprietary datasets with metadata (rows, columns, date range, sources, size).",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="dataset_status",
        description="Show what data you CAN build vs what you're missing based on your available API keys.",
        inputSchema={"type": "object", "properties": {}},
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
    ),
    # ═══════════════════════════════════════════════════════════════
    # V8: Strategy Builder SDK (8 tools)
    # ═══════════════════════════════════════════════════════════════
    Tool(name="create_strategy", description="Create a new AI-native declarative strategy specification (StrategySpec). Define indicators, entry/exit rules, position sizing in JSON.",
         inputSchema={"type": "object", "properties": {"name": {"type": "string"}, "symbols": {"type": "array", "items": {"type": "string"}}, "timeframe": {"type": "string"}, "asset_class": {"type": "string", "enum": ["equity", "forex", "crypto", "futures"]}, "indicators": {"type": "array"}, "entry_rules": {"type": "object"}, "exit_rules": {"type": "object"}, "position_sizing": {"type": "object"}}, "required": ["name", "symbols", "timeframe", "indicators", "entry_rules", "exit_rules"]}),
    Tool(name="validate_strategy", description="Validate a StrategySpec for schema correctness, parameter ranges, and internal consistency.",
         inputSchema={"type": "object", "properties": {"spec": {"type": "object", "description": "Full StrategySpec object to validate"}}, "required": ["spec"]}),
    Tool(name="backtest_strategy", description="Run a backtest on a StrategySpec using the Rust engine. Returns Sharpe, drawdown, win rate, P&L.",
         inputSchema={"type": "object", "properties": {"spec": {"type": "object"}, "capital": {"type": "number", "default": 10000}}, "required": ["spec"]}),
    Tool(name="optimize_strategy", description="Run Optuna-based parameter optimization on a StrategySpec. Finds best params across n_trials.",
         inputSchema={"type": "object", "properties": {"spec": {"type": "object"}, "n_trials": {"type": "integer", "default": 100}, "metric": {"type": "string", "default": "sharpe"}}, "required": ["spec"]}),
    Tool(name="walk_forward_test", description="Run K-fold walk-forward validation on a strategy. Tests robustness across time periods.",
         inputSchema={"type": "object", "properties": {"spec": {"type": "object"}, "n_folds": {"type": "integer", "default": 5}, "train_pct": {"type": "number", "default": 0.70}}, "required": ["spec"]}),
    Tool(name="deploy_strategy", description="Deploy a validated strategy to paper or live trading on a connected broker.",
         inputSchema={"type": "object", "properties": {"spec": {"type": "object"}, "broker": {"type": "string"}, "mode": {"type": "string", "enum": ["paper", "live"], "default": "paper"}, "capital": {"type": "number", "default": 10000}}, "required": ["spec", "broker"]}),
    Tool(name="list_templates", description="Browse pre-built strategy templates (RSI Momentum, BB Mean Reversion, EMA Crossover, etc).",
         inputSchema={"type": "object", "properties": {"category": {"type": "string", "enum": ["momentum", "mean_reversion", "trend", "breakout", "pairs"]}, "asset_class": {"type": "string"}}}),
    Tool(name="fork_template", description="Fork a strategy template into your own editable StrategySpec with custom parameters.",
         inputSchema={"type": "object", "properties": {"template_id": {"type": "string"}, "new_name": {"type": "string"}, "symbols": {"type": "array", "items": {"type": "string"}}, "overrides": {"type": "object"}}, "required": ["template_id"]}),
    # ═══════════════════════════════════════════════════════════════
    # V8: Social Trading (6 tools)
    # ═══════════════════════════════════════════════════════════════
    Tool(name="become_leader", description="Register as a copy-trading leader. Requires 90+ day track record, 50+ trades, Sharpe ≥ 1.0.",
         inputSchema={"type": "object", "properties": {"user_id": {"type": "string"}, "handle": {"type": "string"}, "track_record": {"type": "object"}}, "required": ["user_id", "handle"]}),
    Tool(name="get_leader_stats", description="Get a leader's full performance stats, followers, and recent signals.",
         inputSchema={"type": "object", "properties": {"leader_id": {"type": "string"}}, "required": ["leader_id"]}),
    Tool(name="follow_leader", description="Start copy-trading a leader with configurable scaling and risk limits.",
         inputSchema={"type": "object", "properties": {"follower_id": {"type": "string"}, "leader_id": {"type": "string"}, "config": {"type": "object"}}, "required": ["follower_id", "leader_id"]}),
    Tool(name="unfollow_leader", description="Stop copy-trading a leader. Optionally close all copied positions.",
         inputSchema={"type": "object", "properties": {"follower_id": {"type": "string"}, "leader_id": {"type": "string"}, "close_positions": {"type": "boolean", "default": false}}, "required": ["follower_id", "leader_id"]}),
    Tool(name="get_copy_status", description="Get status of all copy-trading relationships for a follower.",
         inputSchema={"type": "object", "properties": {"follower_id": {"type": "string"}}, "required": ["follower_id"]}),
    Tool(name="set_copy_parameters", description="Update copy-trading parameters (scaling, risk limits, allowed assets).",
         inputSchema={"type": "object", "properties": {"follower_id": {"type": "string"}, "leader_id": {"type": "string"}, "config_updates": {"type": "object"}}, "required": ["follower_id", "leader_id", "config_updates"]}),
    # ═══════════════════════════════════════════════════════════════
    # V8: Community Signals (5 tools)
    # ═══════════════════════════════════════════════════════════════
    Tool(name="publish_signal", description="Publish a trading signal to the community feed with optional trade verification.",
         inputSchema={"type": "object", "properties": {"user_id": {"type": "string"}, "symbol": {"type": "string"}, "direction": {"type": "string", "enum": ["long", "short"]}, "timeframe": {"type": "string"}, "entry_price": {"type": "number"}, "stop_loss": {"type": "number"}, "take_profit": {"type": "number"}, "confidence": {"type": "number"}, "rationale": {"type": "string"}, "trade_hash": {"type": "string"}}, "required": ["user_id", "symbol", "direction"]}),
    Tool(name="subscribe_signals", description="Subscribe to community signals with filters (symbol, category, min accuracy).",
         inputSchema={"type": "object", "properties": {"user_id": {"type": "string"}, "filters": {"type": "object"}}, "required": ["user_id"]}),
    Tool(name="verify_signal", description="Verify a signal with trade proof from broker (order ID, fill price, fill time).",
         inputSchema={"type": "object", "properties": {"signal_id": {"type": "string"}, "trade_proof": {"type": "object"}}, "required": ["signal_id", "trade_proof"]}),
    Tool(name="get_consensus", description="Get community consensus for a symbol — weighted by publisher accuracy scores.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "timeframe": {"type": "string", "default": "1h"}}, "required": ["symbol"]}),
    Tool(name="get_signal_accuracy", description="Get a user's signal accuracy score and history.",
         inputSchema={"type": "object", "properties": {"user_id": {"type": "string"}}, "required": ["user_id"]}),
    # ═══════════════════════════════════════════════════════════════
    # V9: Risk Dashboard (10 tools)
    # ═══════════════════════════════════════════════════════════════
    Tool(name="calculate_var", description="Calculate Value-at-Risk (parametric, historical, or Monte Carlo) at given confidence level.",
         inputSchema={"type": "object", "properties": {"portfolio": {"type": "object"}, "method": {"type": "string", "enum": ["parametric", "historical", "monte_carlo"], "default": "parametric"}, "confidence": {"type": "number", "default": 0.95}, "horizon_days": {"type": "integer", "default": 1}}, "required": ["portfolio"]}),
    Tool(name="calculate_expected_shortfall", description="Calculate Expected Shortfall (CVaR) — average loss in tail scenarios beyond VaR.",
         inputSchema={"type": "object", "properties": {"portfolio": {"type": "object"}, "confidence": {"type": "number", "default": 0.95}, "horizon_days": {"type": "integer", "default": 1}}, "required": ["portfolio"]}),
    Tool(name="get_factor_exposure", description="Analyze portfolio factor exposures (Market, Size, Value, Momentum, Volatility, Quality).",
         inputSchema={"type": "object", "properties": {"portfolio": {"type": "object"}}, "required": ["portfolio"]}),
    Tool(name="run_stress_test", description="Run historical or custom stress tests (COVID, GFC, Flash Crash, etc) on portfolio.",
         inputSchema={"type": "object", "properties": {"portfolio": {"type": "object"}, "scenario": {"type": "string"}, "custom_shocks": {"type": "object"}}, "required": ["portfolio"]}),
    Tool(name="get_drawdown_monitor", description="Monitor current drawdown vs peak, with estimated recovery time.",
         inputSchema={"type": "object", "properties": {"portfolio": {"type": "object"}}, "required": ["portfolio"]}),
    Tool(name="get_margin_utilization", description="Check margin utilization, buffer to margin call, and status.",
         inputSchema={"type": "object", "properties": {"account": {"type": "object"}}, "required": ["account"]}),
    Tool(name="get_greeks_exposure", description="Get aggregate portfolio Greeks (delta, gamma, theta, vega, rho) for options positions.",
         inputSchema={"type": "object", "properties": {"portfolio": {"type": "object"}}, "required": ["portfolio"]}),
    Tool(name="configure_risk_alert", description="Set up risk alert rules (drawdown, VaR breach, margin, concentration, loss limit).",
         inputSchema={"type": "object", "properties": {"alert_type": {"type": "string", "enum": ["drawdown", "var_breach", "margin", "concentration", "loss_limit"]}, "threshold": {"type": "number"}, "action": {"type": "string", "default": "notify"}, "channels": {"type": "array", "items": {"type": "string"}}}, "required": ["alert_type", "threshold"]}),
    Tool(name="check_risk_alerts", description="Evaluate all active risk alert rules against current portfolio state.",
         inputSchema={"type": "object", "properties": {"portfolio": {"type": "object"}}, "required": ["portfolio"]}),
    Tool(name="get_concentration_risk", description="Analyze portfolio concentration (HHI index, top holdings weight, diversification assessment).",
         inputSchema={"type": "object", "properties": {"portfolio": {"type": "object"}}, "required": ["portfolio"]}),
    # ═══════════════════════════════════════════════════════════════
    # V9: Compliance Module (8 tools)
    # ═══════════════════════════════════════════════════════════════
    Tool(name="pre_trade_check", description="Run compliance pre-trade checks (position limits, order size, daily loss, restricted list, wash trade).",
         inputSchema={"type": "object", "properties": {"order": {"type": "object"}, "account": {"type": "object"}, "profile_id": {"type": "string"}}, "required": ["order", "account"]}),
    Tool(name="post_trade_surveillance", description="Run post-trade surveillance for layering, spoofing, and momentum ignition patterns.",
         inputSchema={"type": "object", "properties": {"trades": {"type": "array", "items": {"type": "object"}}}, "required": ["trades"]}),
    Tool(name="get_audit_trail", description="Retrieve tamper-proof blockchain-style audit trail with chain integrity verification.",
         inputSchema={"type": "object", "properties": {"limit": {"type": "integer", "default": 50}, "action_filter": {"type": "string"}}}),
    Tool(name="activate_kill_switch", description="Activate trading kill switch — immediately halts all order submission.",
         inputSchema={"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}),
    Tool(name="deactivate_kill_switch", description="Deactivate trading kill switch and resume normal operations.",
         inputSchema={"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}),
    Tool(name="set_compliance_profile", description="Set or update a compliance profile with custom trading limits.",
         inputSchema={"type": "object", "properties": {"profile_id": {"type": "string"}, "limits": {"type": "object"}}, "required": ["profile_id", "limits"]}),
    Tool(name="get_compliance_profile", description="Retrieve a compliance profile's current limits and settings.",
         inputSchema={"type": "object", "properties": {"profile_id": {"type": "string"}}, "required": ["profile_id"]}),
    Tool(name="best_execution_report", description="Generate best execution analysis — slippage, venue quality, fill assessment.",
         inputSchema={"type": "object", "properties": {"trades": {"type": "array", "items": {"type": "object"}}}, "required": ["trades"]}),
    Tool(name="get_wash_trade_alerts", description="List potential wash trade violations detected across recent trades.",
         inputSchema={"type": "object", "properties": {"days": {"type": "integer", "default": 30}}}),
    Tool(name="set_restricted_list", description="Update restricted securities, sectors, or countries for a compliance profile.",
         inputSchema={"type": "object", "properties": {"profile_id": {"type": "string"}, "symbols": {"type": "array", "items": {"type": "string"}}, "sectors": {"type": "array", "items": {"type": "string"}}, "countries": {"type": "array", "items": {"type": "string"}}}, "required": ["profile_id"]}),
    Tool(name="run_surveillance_scan", description="Trigger on-demand post-trade surveillance scan for layering, spoofing, wash trades.",
         inputSchema={"type": "object", "properties": {"lookback_hours": {"type": "integer", "default": 24}}}),
    Tool(name="get_compliance_status", description="Current compliance state: daily P&L vs limits, violations, kill switch status.",
         inputSchema={"type": "object", "properties": {"account": {"type": "object"}, "profile_id": {"type": "string"}}, "required": ["account"]}),
    # ═══════════════════════════════════════════════════════════════
    # V9: Multi-Tenant White-Label (10 tools)
    # ═══════════════════════════════════════════════════════════════
    Tool(name="create_tenant", description="Create a new white-label tenant with tier, branding, and API key.",
         inputSchema={"type": "object", "properties": {"name": {"type": "string"}, "admin_email": {"type": "string"}, "tier": {"type": "string", "enum": ["starter", "growth", "professional", "enterprise"], "default": "starter"}, "branding": {"type": "object"}}, "required": ["name", "admin_email"]}),
    Tool(name="get_tenant", description="Retrieve tenant details including sub-account count and configuration.",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}}, "required": ["tenant_id"]}),
    Tool(name="update_tenant", description="Update tenant name, branding, tier, or status.",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}, "updates": {"type": "object"}}, "required": ["tenant_id", "updates"]}),
    Tool(name="create_sub_account", description="Create a sub-account under a tenant with role-based permissions.",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}, "user_id": {"type": "string"}, "name": {"type": "string"}, "permissions": {"type": "array", "items": {"type": "string"}}}, "required": ["tenant_id", "user_id", "name"]}),
    Tool(name="list_sub_accounts", description="List all sub-accounts for a tenant.",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}}, "required": ["tenant_id"]}),
    Tool(name="configure_broker_routing", description="Configure broker routing rules for a tenant (which broker handles which asset class).",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}, "broker_config": {"type": "object"}}, "required": ["tenant_id", "broker_config"]}),
    Tool(name="get_billing_summary", description="Get billing summary for a tenant (tier, usage, estimated monthly cost).",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}}, "required": ["tenant_id"]}),
    Tool(name="get_tenant_dashboard", description="Aggregate metrics for a tenant: AUM, active accounts, daily P&L, usage stats.",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}}, "required": ["tenant_id"]}),
    Tool(name="get_sub_account_status", description="Detailed status of a sub-account: positions, P&L, compliance state, recent trades.",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}, "sub_account_id": {"type": "string"}}, "required": ["tenant_id", "sub_account_id"]}),
    Tool(name="set_sub_account_permissions", description="Update sub-account permissions: trade limits, asset classes, marketplace access.",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}, "sub_account_id": {"type": "string"}, "permissions": {"type": "object"}}, "required": ["tenant_id", "sub_account_id", "permissions"]}),
    # ═══════════════════════════════════════════════════════════════
    # V10: ML/AI-Native Strategy Engine (20 tools)
    # ═══════════════════════════════════════════════════════════════
    Tool(name="create_feature_set", description="Create a named feature set with indicator definitions for ML model training.",
         inputSchema={"type": "object", "properties": {"name": {"type": "string"}, "features": {"type": "array", "items": {"type": "object"}}, "target": {"type": "string"}}, "required": ["name", "features"]}),
    Tool(name="compute_features", description="Compute feature values for a symbol over a date range using a saved feature set.",
         inputSchema={"type": "object", "properties": {"feature_set_id": {"type": "string"}, "symbol": {"type": "string"}, "start_date": {"type": "string"}, "end_date": {"type": "string"}}, "required": ["feature_set_id", "symbol"]}),
    Tool(name="list_feature_sets", description="List all saved feature sets with metadata.",
         inputSchema={"type": "object", "properties": {}}),
    Tool(name="get_feature_importance", description="Get feature importance rankings for a trained model's feature set.",
         inputSchema={"type": "object", "properties": {"feature_set_id": {"type": "string"}, "model_id": {"type": "string"}}, "required": ["feature_set_id"]}),
    Tool(name="train_model", description="Train an ML model (XGBoost, LSTM, transformer) on a feature set with train/test split.",
         inputSchema={"type": "object", "properties": {"feature_set_id": {"type": "string"}, "model_type": {"type": "string", "enum": ["xgboost", "lstm", "transformer", "random_forest", "lightgbm"]}, "hyperparameters": {"type": "object"}, "train_split": {"type": "number", "default": 0.8}}, "required": ["feature_set_id", "model_type"]}),
    Tool(name="evaluate_model", description="Evaluate a trained model on held-out test data with comprehensive metrics.",
         inputSchema={"type": "object", "properties": {"model_id": {"type": "string"}, "test_data_id": {"type": "string"}}, "required": ["model_id"]}),
    Tool(name="predict", description="Run inference on a trained model for a symbol to get signal predictions.",
         inputSchema={"type": "object", "properties": {"model_id": {"type": "string"}, "symbol": {"type": "string"}, "features": {"type": "object"}}, "required": ["model_id", "symbol"]}),
    Tool(name="explain_prediction", description="Get SHAP-based explanation for a model prediction.",
         inputSchema={"type": "object", "properties": {"model_id": {"type": "string"}, "prediction_id": {"type": "string"}}, "required": ["model_id", "prediction_id"]}),
    Tool(name="register_model", description="Register a trained model in the model registry with version and metadata.",
         inputSchema={"type": "object", "properties": {"model_id": {"type": "string"}, "name": {"type": "string"}, "version": {"type": "string"}, "metrics": {"type": "object"}, "tags": {"type": "array", "items": {"type": "string"}}}, "required": ["model_id", "name"]}),
    Tool(name="promote_model", description="Promote a model to a target stage (staging, production, archived).",
         inputSchema={"type": "object", "properties": {"registry_id": {"type": "string"}, "stage": {"type": "string", "enum": ["staging", "production", "archived"]}}, "required": ["registry_id", "stage"]}),
    Tool(name="list_models", description="List all models in the registry with optional stage filter.",
         inputSchema={"type": "object", "properties": {"stage": {"type": "string"}, "name_filter": {"type": "string"}}}),
    Tool(name="compare_models", description="Compare two or more models side-by-side on key metrics.",
         inputSchema={"type": "object", "properties": {"model_ids": {"type": "array", "items": {"type": "string"}}}, "required": ["model_ids"]}),
    Tool(name="archive_model", description="Archive a model, removing it from active use.",
         inputSchema={"type": "object", "properties": {"registry_id": {"type": "string"}, "reason": {"type": "string"}}, "required": ["registry_id"]}),
    Tool(name="create_rl_agent", description="Create a reinforcement learning trading agent with environment and reward config.",
         inputSchema={"type": "object", "properties": {"name": {"type": "string"}, "algorithm": {"type": "string", "enum": ["ppo", "dqn", "a2c", "sac"]}, "environment": {"type": "object"}, "reward_config": {"type": "object"}}, "required": ["name", "algorithm"]}),
    Tool(name="train_rl_agent", description="Train an RL agent on historical or simulated market data.",
         inputSchema={"type": "object", "properties": {"agent_id": {"type": "string"}, "episodes": {"type": "integer", "default": 1000}, "symbol": {"type": "string"}}, "required": ["agent_id"]}),
    Tool(name="evaluate_rl_agent", description="Evaluate RL agent performance with episode statistics.",
         inputSchema={"type": "object", "properties": {"agent_id": {"type": "string"}, "episodes": {"type": "integer", "default": 100}}, "required": ["agent_id"]}),
    Tool(name="get_rl_agent_state", description="Get current state and policy of an RL agent.",
         inputSchema={"type": "object", "properties": {"agent_id": {"type": "string"}}, "required": ["agent_id"]}),
    Tool(name="dispatch_gpu_task", description="Route a compute task to Mac M3 Max or Desktop RTX GPU.",
         inputSchema={"type": "object", "properties": {"task_type": {"type": "string", "enum": ["training", "inference", "optimization", "backtest"]}, "payload": {"type": "object"}, "prefer_gpu": {"type": "string", "enum": ["mac_m3", "desktop_rtx", "auto"]}}, "required": ["task_type", "payload"]}),
    Tool(name="gpu_status", description="Get status of all available GPU compute nodes.",
         inputSchema={"type": "object", "properties": {}}),
    Tool(name="generate_strategy_spec", description="Use LLM to generate a complete strategy specification from natural language.",
         inputSchema={"type": "object", "properties": {"description": {"type": "string"}, "asset_class": {"type": "string"}, "risk_tolerance": {"type": "string", "enum": ["conservative", "moderate", "aggressive"]}}, "required": ["description"]}),
    # ═══════════════════════════════════════════════════════════════
    # V11: Institutional-Grade Execution (18 tools)
    # ═══════════════════════════════════════════════════════════════
    Tool(name="validate_institutional_order", description="Validate an order against institutional compliance rules and limits.",
         inputSchema={"type": "object", "properties": {"order": {"type": "object"}, "account_id": {"type": "string"}}, "required": ["order"]}),
    Tool(name="submit_institutional_order", description="Submit an institutional order with full audit trail and compliance checks.",
         inputSchema={"type": "object", "properties": {"order": {"type": "object"}, "account_id": {"type": "string"}, "compliance_override": {"type": "boolean", "default": False}}, "required": ["order"]}),
    Tool(name="get_order_status", description="Get detailed status of an institutional order including fill reports.",
         inputSchema={"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]}),
    Tool(name="route_order", description="Smart-route an order across venues for best execution.",
         inputSchema={"type": "object", "properties": {"order": {"type": "object"}, "routing_strategy": {"type": "string", "enum": ["best_price", "lowest_latency", "dark_pool_first", "split"]}, "max_venues": {"type": "integer", "default": 5}}, "required": ["order"]}),
    Tool(name="get_venue_analytics", description="Get execution analytics per venue: fill rates, latency, slippage.",
         inputSchema={"type": "object", "properties": {"venue_id": {"type": "string"}, "lookback_days": {"type": "integer", "default": 30}}}),
    Tool(name="start_algo_execution", description="Start an algorithmic execution strategy (TWAP, VWAP, iceberg, sniper).",
         inputSchema={"type": "object", "properties": {"algo_type": {"type": "string", "enum": ["twap", "vwap", "iceberg", "sniper", "pov"]}, "order": {"type": "object"}, "parameters": {"type": "object"}}, "required": ["algo_type", "order"]}),
    Tool(name="stop_algo_execution", description="Stop a running algo execution and report fills.",
         inputSchema={"type": "object", "properties": {"execution_id": {"type": "string"}}, "required": ["execution_id"]}),
    Tool(name="get_algo_execution_status", description="Get real-time status of an algo execution (progress, fills, slippage).",
         inputSchema={"type": "object", "properties": {"execution_id": {"type": "string"}}, "required": ["execution_id"]}),
    Tool(name="connect_fix_session", description="Establish a FIX protocol session to an execution venue.",
         inputSchema={"type": "object", "properties": {"venue": {"type": "string"}, "sender_comp_id": {"type": "string"}, "target_comp_id": {"type": "string"}, "config": {"type": "object"}}, "required": ["venue", "sender_comp_id", "target_comp_id"]}),
    Tool(name="disconnect_fix_session", description="Gracefully disconnect a FIX session.",
         inputSchema={"type": "object", "properties": {"session_id": {"type": "string"}}, "required": ["session_id"]}),
    Tool(name="get_fix_session_status", description="Get FIX session health: heartbeat, sequence numbers, message counts.",
         inputSchema={"type": "object", "properties": {"session_id": {"type": "string"}}, "required": ["session_id"]}),
    Tool(name="run_tca", description="Run transaction cost analysis on completed trades.",
         inputSchema={"type": "object", "properties": {"trades": {"type": "array", "items": {"type": "object"}}, "benchmark": {"type": "string", "enum": ["vwap", "twap", "arrival_price", "close"], "default": "vwap"}}, "required": ["trades"]}),
    Tool(name="get_tca_report", description="Get a comprehensive TCA report for a time period.",
         inputSchema={"type": "object", "properties": {"start_date": {"type": "string"}, "end_date": {"type": "string"}, "account_id": {"type": "string"}}, "required": ["start_date", "end_date"]}),
    Tool(name="get_implementation_shortfall", description="Calculate implementation shortfall for a set of orders.",
         inputSchema={"type": "object", "properties": {"orders": {"type": "array", "items": {"type": "object"}}}, "required": ["orders"]}),
    Tool(name="register_venue", description="Register a new execution venue in the venue registry.",
         inputSchema={"type": "object", "properties": {"name": {"type": "string"}, "venue_type": {"type": "string", "enum": ["exchange", "dark_pool", "ats", "otc"]}, "config": {"type": "object"}}, "required": ["name", "venue_type"]}),
    Tool(name="list_venues", description="List all registered execution venues with health status.",
         inputSchema={"type": "object", "properties": {}}),
    Tool(name="get_venue_status", description="Get detailed status of a specific venue.",
         inputSchema={"type": "object", "properties": {"venue_id": {"type": "string"}}, "required": ["venue_id"]}),
    Tool(name="set_venue_priority", description="Set routing priority for a venue.",
         inputSchema={"type": "object", "properties": {"venue_id": {"type": "string"}, "priority": {"type": "integer"}}, "required": ["venue_id", "priority"]}),
    # ═══════════════════════════════════════════════════════════════
    # V12: Real-Time Analytics (15 tools)
    # ═══════════════════════════════════════════════════════════════
    Tool(name="start_pnl_stream", description="Start real-time P&L streaming for an account or portfolio.",
         inputSchema={"type": "object", "properties": {"account_id": {"type": "string"}, "symbols": {"type": "array", "items": {"type": "string"}}}, "required": ["account_id"]}),
    Tool(name="get_pnl_snapshot", description="Get current P&L snapshot across all tracked positions.",
         inputSchema={"type": "object", "properties": {"account_id": {"type": "string"}}, "required": ["account_id"]}),
    Tool(name="get_pnl_history", description="Get historical P&L time series for charting.",
         inputSchema={"type": "object", "properties": {"account_id": {"type": "string"}, "interval": {"type": "string", "enum": ["1m", "5m", "1h", "1d"], "default": "1h"}, "lookback": {"type": "string", "default": "24h"}}, "required": ["account_id"]}),
    Tool(name="analyze_order_flow", description="Analyze order flow for a symbol: buy/sell pressure, large trades, imbalances.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "lookback_minutes": {"type": "integer", "default": 60}}, "required": ["symbol"]}),
    Tool(name="get_order_flow_heatmap", description="Get order flow heatmap data for price levels.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "levels": {"type": "integer", "default": 20}}, "required": ["symbol"]}),
    Tool(name="get_volume_profile", description="Get volume profile analysis for a symbol.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "lookback_days": {"type": "integer", "default": 5}}, "required": ["symbol"]}),
    Tool(name="analyze_microstructure", description="Analyze market microstructure: bid-ask spread, depth, tick patterns.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}),
    Tool(name="get_toxicity_score", description="Get order flow toxicity score (VPIN) for a symbol.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "window": {"type": "integer", "default": 50}}, "required": ["symbol"]}),
    Tool(name="detect_regime", description="Detect current market regime using statistical methods.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "method": {"type": "string", "enum": ["hmm", "threshold", "ml"], "default": "hmm"}}, "required": ["symbol"]}),
    Tool(name="get_regime_history", description="Get historical regime classifications for a symbol.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "lookback_days": {"type": "integer", "default": 90}}, "required": ["symbol"]}),
    Tool(name="get_regime_transition_matrix", description="Get regime transition probability matrix.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}),
    Tool(name="create_alert", description="Create a real-time alert with conditions and actions.",
         inputSchema={"type": "object", "properties": {"name": {"type": "string"}, "condition": {"type": "object"}, "actions": {"type": "array", "items": {"type": "object"}}, "channels": {"type": "array", "items": {"type": "string"}}}, "required": ["name", "condition"]}),
    Tool(name="list_alerts", description="List all configured alerts with their status.",
         inputSchema={"type": "object", "properties": {"active_only": {"type": "boolean", "default": True}}}),
    Tool(name="delete_alert", description="Delete an alert by ID.",
         inputSchema={"type": "object", "properties": {"alert_id": {"type": "string"}}, "required": ["alert_id"]}),
    Tool(name="get_alert_history", description="Get alert trigger history.",
         inputSchema={"type": "object", "properties": {"alert_id": {"type": "string"}, "limit": {"type": "integer", "default": 50}}}),
    # ═══════════════════════════════════════════════════════════════
    # V13: Alternative Data Marketplace (18 tools)
    # ═══════════════════════════════════════════════════════════════
    Tool(name="analyze_sentiment", description="Run NLP sentiment analysis on text or news for a symbol.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "source": {"type": "string", "enum": ["news", "twitter", "reddit", "earnings_call", "custom"]}, "text": {"type": "string"}}, "required": ["symbol"]}),
    Tool(name="get_sentiment_history", description="Get historical sentiment scores for a symbol.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "source": {"type": "string"}, "lookback_days": {"type": "integer", "default": 30}}, "required": ["symbol"]}),
    Tool(name="get_sentiment_signal", description="Get aggregated sentiment signal (bullish/bearish/neutral) for a symbol.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}),
    Tool(name="analyze_satellite", description="Analyze satellite imagery data for economic activity signals.",
         inputSchema={"type": "object", "properties": {"location": {"type": "string"}, "data_type": {"type": "string", "enum": ["parking_lots", "shipping", "agriculture", "construction", "nightlights"]}, "symbol": {"type": "string"}}, "required": ["location", "data_type"]}),
    Tool(name="get_satellite_timeseries", description="Get time series of satellite-derived metrics.",
         inputSchema={"type": "object", "properties": {"location_id": {"type": "string"}, "metric": {"type": "string"}, "lookback_days": {"type": "integer", "default": 90}}, "required": ["location_id", "metric"]}),
    Tool(name="scrape_web_data", description="Scrape structured data from web sources for trading signals.",
         inputSchema={"type": "object", "properties": {"url": {"type": "string"}, "selectors": {"type": "object"}, "schedule": {"type": "string"}}, "required": ["url"]}),
    Tool(name="list_scrape_jobs", description="List all configured web scrape jobs.",
         inputSchema={"type": "object", "properties": {}}),
    Tool(name="get_scrape_results", description="Get results from a web scrape job.",
         inputSchema={"type": "object", "properties": {"job_id": {"type": "string"}, "limit": {"type": "integer", "default": 100}}, "required": ["job_id"]}),
    Tool(name="analyze_sec_filing", description="Analyze an SEC filing (10-K, 10-Q, 8-K) for trading signals.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "filing_type": {"type": "string", "enum": ["10-K", "10-Q", "8-K", "13-F", "S-1"]}, "filing_url": {"type": "string"}}, "required": ["symbol", "filing_type"]}),
    Tool(name="get_insider_trades", description="Get recent insider trading activity for a symbol.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "days": {"type": "integer", "default": 90}}, "required": ["symbol"]}),
    Tool(name="get_institutional_holdings", description="Get institutional holdings changes (13-F) for a symbol.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "quarter": {"type": "string"}}, "required": ["symbol"]}),
    Tool(name="analyze_social_media", description="Analyze social media signals (Twitter, Reddit, StockTwits) for a symbol.",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "platform": {"type": "string", "enum": ["twitter", "reddit", "stocktwits", "all"]}, "lookback_hours": {"type": "integer", "default": 24}}, "required": ["symbol"]}),
    Tool(name="get_social_momentum", description="Get social momentum score for a symbol (trending vs fading).",
         inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}),
    Tool(name="get_social_sentiment_feed", description="Get real-time social sentiment feed for monitored symbols.",
         inputSchema={"type": "object", "properties": {"symbols": {"type": "array", "items": {"type": "string"}}, "limit": {"type": "integer", "default": 50}}}),
    Tool(name="browse_alt_datasets", description="Browse available alternative datasets in the marketplace.",
         inputSchema={"type": "object", "properties": {"category": {"type": "string"}, "min_quality": {"type": "number", "default": 0.7}}}),
    Tool(name="subscribe_alt_dataset", description="Subscribe to an alternative dataset for signal generation.",
         inputSchema={"type": "object", "properties": {"dataset_id": {"type": "string"}, "config": {"type": "object"}}, "required": ["dataset_id"]}),
    Tool(name="get_alt_dataset_sample", description="Get a sample of data from an alternative dataset.",
         inputSchema={"type": "object", "properties": {"dataset_id": {"type": "string"}, "limit": {"type": "integer", "default": 100}}, "required": ["dataset_id"]}),
    Tool(name="get_alt_data_quality", description="Get quality metrics for an alternative dataset.",
         inputSchema={"type": "object", "properties": {"dataset_id": {"type": "string"}}, "required": ["dataset_id"]}),
    # ═══════════════════════════════════════════════════════════════
    # V14: Autonomous Agent Swarm (18 tools)
    # ═══════════════════════════════════════════════════════════════
    Tool(name="spawn_agent", description="Spawn a new autonomous trading agent with a specific role and strategy.",
         inputSchema={"type": "object", "properties": {"name": {"type": "string"}, "role": {"type": "string", "enum": ["researcher", "trader", "risk_manager", "analyst", "executor"]}, "strategy": {"type": "object"}, "capital_allocation": {"type": "number"}}, "required": ["name", "role"]}),
    Tool(name="list_agents", description="List all active agents in the swarm with their status.",
         inputSchema={"type": "object", "properties": {"role_filter": {"type": "string"}}}),
    Tool(name="get_agent_detail", description="Get detailed info about a specific agent: state, P&L, decisions.",
         inputSchema={"type": "object", "properties": {"agent_id": {"type": "string"}}, "required": ["agent_id"]}),
    Tool(name="terminate_agent", description="Terminate an agent and close its positions.",
         inputSchema={"type": "object", "properties": {"agent_id": {"type": "string"}, "reason": {"type": "string"}}, "required": ["agent_id"]}),
    Tool(name="create_task_plan", description="Create a task plan that decomposes a trading goal into agent subtasks.",
         inputSchema={"type": "object", "properties": {"goal": {"type": "string"}, "constraints": {"type": "object"}, "deadline": {"type": "string"}}, "required": ["goal"]}),
    Tool(name="get_task_plan", description="Get a task plan and its execution status.",
         inputSchema={"type": "object", "properties": {"plan_id": {"type": "string"}}, "required": ["plan_id"]}),
    Tool(name="store_agent_memory", description="Store a memory/observation in shared agent memory.",
         inputSchema={"type": "object", "properties": {"agent_id": {"type": "string"}, "memory_type": {"type": "string", "enum": ["observation", "decision", "outcome", "insight"]}, "content": {"type": "object"}}, "required": ["agent_id", "memory_type", "content"]}),
    Tool(name="query_agent_memory", description="Query shared agent memory for relevant past observations.",
         inputSchema={"type": "object", "properties": {"query": {"type": "string"}, "memory_type": {"type": "string"}, "agent_id": {"type": "string"}, "limit": {"type": "integer", "default": 20}}, "required": ["query"]}),
    Tool(name="get_memory_stats", description="Get agent memory usage statistics.",
         inputSchema={"type": "object", "properties": {"agent_id": {"type": "string"}}}),
    Tool(name="route_tool_call", description="Route a tool call from an agent to the appropriate MCP tool.",
         inputSchema={"type": "object", "properties": {"agent_id": {"type": "string"}, "tool_name": {"type": "string"}, "arguments": {"type": "object"}}, "required": ["agent_id", "tool_name", "arguments"]}),
    Tool(name="get_tool_permissions", description="Get tool access permissions for an agent.",
         inputSchema={"type": "object", "properties": {"agent_id": {"type": "string"}}, "required": ["agent_id"]}),
    Tool(name="request_consensus", description="Request multi-agent consensus on a trading decision.",
         inputSchema={"type": "object", "properties": {"proposal": {"type": "object"}, "agent_ids": {"type": "array", "items": {"type": "string"}}, "method": {"type": "string", "enum": ["majority", "weighted", "unanimous"], "default": "weighted"}}, "required": ["proposal", "agent_ids"]}),
    Tool(name="get_consensus_result", description="Get the result of a consensus request.",
         inputSchema={"type": "object", "properties": {"consensus_id": {"type": "string"}}, "required": ["consensus_id"]}),
    Tool(name="get_consensus_history", description="Get history of consensus decisions.",
         inputSchema={"type": "object", "properties": {"limit": {"type": "integer", "default": 20}}}),
    Tool(name="get_agent_health", description="Get health metrics for an agent: uptime, error rate, latency.",
         inputSchema={"type": "object", "properties": {"agent_id": {"type": "string"}}, "required": ["agent_id"]}),
    Tool(name="get_swarm_dashboard", description="Get aggregate swarm dashboard: active agents, total P&L, task status.",
         inputSchema={"type": "object", "properties": {}}),
    Tool(name="get_agent_performance", description="Get detailed performance metrics for an agent over time.",
         inputSchema={"type": "object", "properties": {"agent_id": {"type": "string"}, "lookback_days": {"type": "integer", "default": 30}}, "required": ["agent_id"]}),
    Tool(name="set_agent_parameters", description="Update an agent's strategy parameters at runtime.",
         inputSchema={"type": "object", "properties": {"agent_id": {"type": "string"}, "parameters": {"type": "object"}}, "required": ["agent_id", "parameters"]}),
    # ═══════════════════════════════════════════════════════════════
    # V15: DeFi & Cross-Chain (20 tools)
    # ═══════════════════════════════════════════════════════════════
    Tool(name="get_dex_quote", description="Get best swap quote across decentralized exchanges.",
         inputSchema={"type": "object", "properties": {"token_in": {"type": "string"}, "token_out": {"type": "string"}, "amount": {"type": "string"}, "chain": {"type": "string", "enum": ["ethereum", "polygon", "arbitrum", "optimism", "base", "solana"]}}, "required": ["token_in", "token_out", "amount"]}),
    Tool(name="execute_swap", description="Execute a token swap on the best DEX route.",
         inputSchema={"type": "object", "properties": {"quote_id": {"type": "string"}, "slippage_tolerance": {"type": "number", "default": 0.005}, "deadline_minutes": {"type": "integer", "default": 20}}, "required": ["quote_id"]}),
    Tool(name="get_dex_liquidity", description="Get liquidity depth across DEXes for a token pair.",
         inputSchema={"type": "object", "properties": {"token_in": {"type": "string"}, "token_out": {"type": "string"}, "chain": {"type": "string"}}, "required": ["token_in", "token_out"]}),
    Tool(name="scan_yield_opportunities", description="Scan DeFi protocols for yield farming opportunities.",
         inputSchema={"type": "object", "properties": {"min_apy": {"type": "number", "default": 5.0}, "max_risk_score": {"type": "number", "default": 7}, "chains": {"type": "array", "items": {"type": "string"}}}}),
    Tool(name="deploy_yield_strategy", description="Deploy capital to a yield farming strategy.",
         inputSchema={"type": "object", "properties": {"opportunity_id": {"type": "string"}, "amount": {"type": "string"}, "auto_compound": {"type": "boolean", "default": True}}, "required": ["opportunity_id", "amount"]}),
    Tool(name="get_yield_positions", description="Get all active yield farming positions.",
         inputSchema={"type": "object", "properties": {}}),
    Tool(name="withdraw_yield", description="Withdraw from a yield farming position.",
         inputSchema={"type": "object", "properties": {"position_id": {"type": "string"}, "amount": {"type": "string"}}, "required": ["position_id"]}),
    Tool(name="bridge_tokens", description="Bridge tokens across chains via cross-chain bridge.",
         inputSchema={"type": "object", "properties": {"token": {"type": "string"}, "amount": {"type": "string"}, "from_chain": {"type": "string"}, "to_chain": {"type": "string"}, "bridge_protocol": {"type": "string"}}, "required": ["token", "amount", "from_chain", "to_chain"]}),
    Tool(name="get_bridge_status", description="Get status of a cross-chain bridge transfer.",
         inputSchema={"type": "object", "properties": {"transfer_id": {"type": "string"}}, "required": ["transfer_id"]}),
    Tool(name="list_bridge_routes", description="List available bridge routes between chains for a token.",
         inputSchema={"type": "object", "properties": {"token": {"type": "string"}, "from_chain": {"type": "string"}, "to_chain": {"type": "string"}}, "required": ["token", "from_chain", "to_chain"]}),
    Tool(name="check_mev_risk", description="Check MEV risk for a pending transaction.",
         inputSchema={"type": "object", "properties": {"transaction": {"type": "object"}, "chain": {"type": "string"}}, "required": ["transaction"]}),
    Tool(name="submit_protected_tx", description="Submit a transaction with MEV protection (Flashbots/private mempool).",
         inputSchema={"type": "object", "properties": {"transaction": {"type": "object"}, "protection_type": {"type": "string", "enum": ["flashbots", "private_mempool", "backrun_protection"]}}, "required": ["transaction"]}),
    Tool(name="get_mev_analytics", description="Get MEV analytics: sandwich attacks, front-running stats for monitored wallets.",
         inputSchema={"type": "object", "properties": {"wallet": {"type": "string"}, "lookback_days": {"type": "integer", "default": 7}}}),
    Tool(name="get_governance_proposals", description="Get active governance proposals for a DAO/protocol.",
         inputSchema={"type": "object", "properties": {"protocol": {"type": "string"}, "status": {"type": "string", "enum": ["active", "passed", "rejected", "all"], "default": "active"}}, "required": ["protocol"]}),
    Tool(name="vote_on_proposal", description="Cast a vote on a DAO governance proposal.",
         inputSchema={"type": "object", "properties": {"proposal_id": {"type": "string"}, "vote": {"type": "string", "enum": ["for", "against", "abstain"]}, "reason": {"type": "string"}}, "required": ["proposal_id", "vote"]}),
    Tool(name="get_governance_power", description="Get voting power and delegation status for a wallet.",
         inputSchema={"type": "object", "properties": {"protocol": {"type": "string"}, "wallet": {"type": "string"}}, "required": ["protocol"]}),
    Tool(name="assess_defi_risk", description="Assess risk of a DeFi protocol: smart contract, liquidity, governance.",
         inputSchema={"type": "object", "properties": {"protocol": {"type": "string"}, "chain": {"type": "string"}}, "required": ["protocol"]}),
    Tool(name="get_defi_portfolio_risk", description="Get aggregate risk assessment for all DeFi positions.",
         inputSchema={"type": "object", "properties": {}}),
    Tool(name="monitor_liquidation_risk", description="Monitor liquidation risk for lending/borrowing positions.",
         inputSchema={"type": "object", "properties": {"position_id": {"type": "string"}}, "required": ["position_id"]}),
    Tool(name="get_defi_insurance_options", description="Get DeFi insurance options for protocol risk coverage.",
         inputSchema={"type": "object", "properties": {"protocol": {"type": "string"}, "coverage_amount": {"type": "string"}}, "required": ["protocol"]}),
    # ═══════════════════════════════════════════════════════════════
    # V16: Cloud SaaS Platform (17 tools)
    # ═══════════════════════════════════════════════════════════════
    Tool(name="create_saas_tenant", description="Create a new SaaS tenant with subscription plan and configuration.",
         inputSchema={"type": "object", "properties": {"company_name": {"type": "string"}, "admin_email": {"type": "string"}, "plan": {"type": "string", "enum": ["free", "starter", "professional", "enterprise"]}, "config": {"type": "object"}}, "required": ["company_name", "admin_email"]}),
    Tool(name="get_saas_tenant", description="Get SaaS tenant details, usage, and subscription status.",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}}, "required": ["tenant_id"]}),
    Tool(name="update_saas_tenant", description="Update SaaS tenant settings, plan, or configuration.",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}, "updates": {"type": "object"}}, "required": ["tenant_id", "updates"]}),
    Tool(name="get_usage_metrics", description="Get detailed usage metrics for billing (API calls, compute, storage).",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}, "period": {"type": "string", "default": "current_month"}}, "required": ["tenant_id"]}),
    Tool(name="get_invoice", description="Get invoice details for a billing period.",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}, "invoice_id": {"type": "string"}}, "required": ["tenant_id"]}),
    Tool(name="list_invoices", description="List all invoices for a tenant.",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}, "status": {"type": "string", "enum": ["paid", "pending", "overdue", "all"]}}, "required": ["tenant_id"]}),
    Tool(name="update_payment_method", description="Update payment method for a tenant.",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}, "payment_method": {"type": "object"}}, "required": ["tenant_id", "payment_method"]}),
    Tool(name="publish_strategy_to_marketplace", description="Publish a validated strategy to the SaaS marketplace.",
         inputSchema={"type": "object", "properties": {"strategy_id": {"type": "string"}, "pricing": {"type": "object"}, "description": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}}}, "required": ["strategy_id", "pricing"]}),
    Tool(name="browse_strategy_marketplace", description="Browse the SaaS strategy marketplace with filters.",
         inputSchema={"type": "object", "properties": {"category": {"type": "string"}, "min_sharpe": {"type": "number"}, "max_price": {"type": "number"}, "sort_by": {"type": "string", "enum": ["sharpe", "subscribers", "newest", "price"], "default": "sharpe"}}}),
    Tool(name="subscribe_to_strategy", description="Subscribe a tenant to a marketplace strategy.",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}, "strategy_id": {"type": "string"}, "allocation": {"type": "number"}}, "required": ["tenant_id", "strategy_id"]}),
    Tool(name="configure_white_label", description="Configure white-label branding for a tenant.",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}, "branding": {"type": "object"}}, "required": ["tenant_id", "branding"]}),
    Tool(name="get_white_label_config", description="Get current white-label configuration for a tenant.",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}}, "required": ["tenant_id"]}),
    Tool(name="generate_api_key", description="Generate an API key for tenant programmatic access.",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}, "name": {"type": "string"}, "permissions": {"type": "array", "items": {"type": "string"}}, "rate_limit": {"type": "integer", "default": 1000}}, "required": ["tenant_id", "name"]}),
    Tool(name="list_api_keys", description="List all API keys for a tenant.",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}}, "required": ["tenant_id"]}),
    Tool(name="revoke_api_key", description="Revoke an API key.",
         inputSchema={"type": "object", "properties": {"key_id": {"type": "string"}}, "required": ["key_id"]}),
    Tool(name="get_api_usage", description="Get API usage statistics and rate limit status.",
         inputSchema={"type": "object", "properties": {"tenant_id": {"type": "string"}, "key_id": {"type": "string"}}, "required": ["tenant_id"]}),
    Tool(name="get_platform_health", description="Get overall SaaS platform health: uptime, latency, error rates.",
         inputSchema={"type": "object", "properties": {}}),
]


@app.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    registry = _get_registry()
    limiter = get_rate_limiter()
    tlog = get_tool_logger()
    t0 = time.monotonic()

    try:
        # Rate-limit broker calls
        broker_name = arguments.get("broker", "")
        if broker_name:
            await limiter.acquire(broker_name)

        result = await _dispatch_tool(name, arguments, registry)
        tlog.log_call(name, arguments, duration_ms=(time.monotonic() - t0) * 1000)
        return result

    except AlgoChainsError as e:
        tlog.log_call(name, arguments, error=str(e), duration_ms=(time.monotonic() - t0) * 1000)
        return _error_text(e)
    except Exception as e:
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

    # ── Trading ──────────────────────────────────────────────
    if name == "place_order":
        conn = _require_broker(registry, arguments["broker"])
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
        return _text(order.to_dict())

    elif name == "cancel_order":
        conn = _require_broker(registry, arguments["broker"])
        ok = await conn.cancel_order(arguments["order_id"])
        return _text({"cancelled": ok, "order_id": arguments["order_id"]})

    elif name == "close_position":
        conn = _require_broker(registry, arguments["broker"])
        order = await conn.close_position(arguments["symbol"])
        return _text(order.to_dict() if order else {"error": f"No position in {arguments['symbol']}"})

    elif name == "close_all_positions":
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

    elif name == "get_portfolio_summary":
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

    # ── Broker Management ───────────────────────────────────
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
        try:
            listings = await bridge.browse_listings(
                asset_class=arguments.get("asset_class"),
                strategy_type=arguments.get("strategy_type"),
                min_sharpe=arguments.get("min_sharpe"),
                limit=arguments.get("limit", 20),
            )
            return _text({"count": len(listings), "listings": listings})
        except Exception:
            cfg = _config or load_config()
            return _text({
                "marketplace_url": f"{cfg.marketplace.django_url}/marketplace/",
                "note": "API not reachable — browse the marketplace at this URL.",
                "filters": {k: v for k, v in arguments.items() if v},
            })

    elif name == "get_listing_detail":
        bridge = _get_bridge()
        try:
            listing = await bridge.get_listing(arguments["slug"])
            return _text(listing)
        except Exception:
            cfg = _config or load_config()
            return _text({
                "listing_url": f"{cfg.marketplace.django_url}/bots/{arguments['slug']}/",
                "slug": arguments["slug"],
            })

    elif name == "subscribe_to_bot":
        bridge = _get_bridge()
        try:
            result = await bridge.subscribe(
                slug=arguments["slug"],
                broker=arguments["broker"],
                mode=arguments.get("mode", "paper"),
            )
            return _text(result)
        except Exception:
            cfg = _config or load_config()
            return _text({
                "action": "subscribe",
                "slug": arguments["slug"],
                "broker": arguments["broker"],
                "mode": arguments.get("mode", "paper"),
                "subscribe_url": f"{cfg.marketplace.django_url}/bots/{arguments['slug']}/subscribe/",
                "note": "Subscription requires authentication on algochains.ai",
            })

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

    elif name == "check_validation_status":
        return _text({
            "submission_id": arguments["submission_id"],
            "status": "pending_review",
            "note": "Validation results are returned immediately from submit_strategy.",
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
        return _text({
            "tool_call_stats": tlog.stats(),
            "recent_calls": tlog.recent(10),
            "configured_brokers": registry.list_configured(),
            "connected_brokers": registry.list_available(),
        })

    # ── V4: Streaming ────────────────────────────────────────
    elif name == "stream_subscribe":
        from .streaming.manager import Subscription
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
        orch = _get_key_orchestrator()
        if not orch._discovered:
            await orch.discover_keys()
        result = await orch.export_config(format=arguments.get("format", "env"))
        return _text(result)

    # ── V7: Proprietary Dataset Builder ────────────────────────
    elif name == "build_dataset":
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
        spec = StrategySpec.from_dict(arguments)
        validator = _get_spec_validator()
        validation = validator.validate(spec)
        return _text({"spec": spec.to_dict(), "validation": validation})

    elif name == "validate_strategy":
        spec = StrategySpec.from_dict(arguments["spec"])
        validator = _get_spec_validator()
        return _text(validator.validate(spec))

    elif name == "backtest_strategy":
        spec = StrategySpec.from_dict(arguments["spec"])
        runner = _get_backtest_runner()
        result = await runner.run(spec, capital=arguments.get("capital", 10000))
        return _text(result)

    elif name == "optimize_strategy":
        spec = StrategySpec.from_dict(arguments["spec"])
        optimizer = _get_strategy_optimizer()
        result = await optimizer.optimize(spec, n_trials=arguments.get("n_trials", 100), metric=arguments.get("metric", "sharpe"))
        return _text(result)

    elif name == "walk_forward_test":
        spec = StrategySpec.from_dict(arguments["spec"])
        wf = _get_walk_forward()
        result = await wf.run(spec, n_folds=arguments.get("n_folds", 5), train_pct=arguments.get("train_pct", 0.70))
        return _text(result)

    elif name == "deploy_strategy":
        spec = StrategySpec.from_dict(arguments["spec"])
        deployer = _get_deployer()
        result = await deployer.deploy(spec, broker=arguments["broker"], mode=arguments.get("mode", "paper"), capital=arguments.get("capital", 10000))
        return _text(result)

    elif name == "list_templates":
        mgr = _get_template_mgr()
        return _text(mgr.list_templates(category=arguments.get("category"), asset_class=arguments.get("asset_class")))

    elif name == "fork_template":
        mgr = _get_template_mgr()
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
        eng = _get_regime_detector()
        return _text(await eng.detect(symbol=arguments["symbol"], method=arguments.get("method", "hmm")))

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

    else:
        return _text({"error": f"Unknown tool: {name}"})


# ═══════════════════════════════════════════════════════════════════
# MCP Resources — expose live state as readable resources
# ═══════════════════════════════════════════════════════════════════

RESOURCES = [
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
]


@app.list_resources()
async def list_resources() -> list[Resource]:
    return RESOURCES


@app.read_resource()
async def read_resource(uri: str) -> str:
    if uri == "algochains://brokers/status":
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
            "min_paper_days": g.min_paper_days,
            "min_paper_trades": g.min_paper_trades,
        }, indent=2)

    elif uri == "algochains://server/diagnostics":
        tlog = get_tool_logger()
        return json.dumps({
            "stats": tlog.stats(),
            "recent": tlog.recent(10),
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


def main():
    logger.info("Starting AlgoChains MCP Server v16.0.0")
    asyncio.run(_run())


if __name__ == "__main__":
    main()
