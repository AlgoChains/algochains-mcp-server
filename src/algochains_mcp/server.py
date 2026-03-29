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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("algochains_mcp.server")

app = Server("algochains-mcp-server")

_config: ServerConfig | None = None
_registry: BrokerRegistry | None = None
_validator: StrategyValidator | None = None
_bridge: MarketplaceBridge | None = None


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

    raise ValueError(f"Unknown prompt: {name}")


# ═══════════════════════════════════════════════════════════════════
# Server entry point
# ═══════════════════════════════════════════════════════════════════

async def _run():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def main():
    logger.info("Starting AlgoChains MCP Server v0.1.0")
    asyncio.run(_run())


if __name__ == "__main__":
    main()
