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
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .brokers.base import OrderSide, OrderType
from .brokers.registry import BrokerRegistry
from .config import ServerConfig, load_config
from .marketplace.validator import StrategyValidator

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("algochains_mcp.server")

app = Server("algochains-mcp-server")

_config: ServerConfig | None = None
_registry: BrokerRegistry | None = None
_validator: StrategyValidator | None = None


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


def _text(data: Any) -> list[TextContent]:
    if isinstance(data, (dict, list)):
        return [TextContent(type="text", text=json.dumps(data, indent=2, default=str))]
    return [TextContent(type="text", text=str(data))]


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
]


@app.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    registry = _get_registry()

    try:
        # ── Trading ──────────────────────────────────────────────
        if name == "place_order":
            conn = registry.get(arguments["broker"])
            if not conn:
                return _text({"error": f"Broker '{arguments['broker']}' not connected. Run connect_broker first."})
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
            conn = registry.get(arguments["broker"])
            if not conn:
                return _text({"error": f"Broker '{arguments['broker']}' not connected"})
            ok = await conn.cancel_order(arguments["order_id"])
            return _text({"cancelled": ok, "order_id": arguments["order_id"]})

        elif name == "close_position":
            conn = registry.get(arguments["broker"])
            if not conn:
                return _text({"error": f"Broker '{arguments['broker']}' not connected"})
            order = await conn.close_position(arguments["symbol"])
            return _text(order.to_dict() if order else {"error": f"No position in {arguments['symbol']}"})

        elif name == "close_all_positions":
            conn = registry.get(arguments["broker"])
            if not conn:
                return _text({"error": f"Broker '{arguments['broker']}' not connected"})
            orders = await conn.close_all_positions()
            return _text({"closed": len(orders), "orders": [o.to_dict() for o in orders]})

        # ── Portfolio ────────────────────────────────────────────
        elif name == "get_account":
            conn = registry.get(arguments["broker"])
            if not conn:
                return _text({"error": f"Broker '{arguments['broker']}' not connected"})
            acct = await conn.get_account()
            return _text(acct.to_dict())

        elif name == "get_positions":
            conn = registry.get(arguments["broker"])
            if not conn:
                return _text({"error": f"Broker '{arguments['broker']}' not connected"})
            positions = await conn.get_positions()
            return _text([p.to_dict() for p in positions])

        elif name == "get_orders":
            conn = registry.get(arguments["broker"])
            if not conn:
                return _text({"error": f"Broker '{arguments['broker']}' not connected"})
            orders = await conn.get_orders(arguments.get("status"))
            return _text([o.to_dict() for o in orders])

        elif name == "get_portfolio_summary":
            summary = {"brokers": {}, "total_equity": 0, "total_positions": 0}
            for broker_name in registry.list_available():
                conn = registry.get(broker_name)
                try:
                    acct = await conn.get_account()
                    positions = await conn.get_positions()
                    summary["brokers"][broker_name] = {
                        "equity": acct.equity,
                        "cash": acct.cash,
                        "positions": len(positions),
                        "unrealized_pnl": sum(p.unrealized_pnl for p in positions),
                    }
                    summary["total_equity"] += acct.equity
                    summary["total_positions"] += len(positions)
                except Exception as e:
                    summary["brokers"][broker_name] = {"error": str(e)}
            return _text(summary)

        # ── Market Data ─────────────────────────────────────────
        elif name == "get_quote":
            conn = registry.get(arguments["broker"])
            if not conn:
                return _text({"error": f"Broker '{arguments['broker']}' not connected"})
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
            return _text({"error": f"Broker '{broker_name}' not configured. Set environment variables."})

        elif name == "broker_health_check":
            health = await registry.health_check_all()
            return _text(health)

        # ── Marketplace ─────────────────────────────────────────
        elif name == "browse_marketplace":
            import httpx
            cfg = _config or load_config()
            url = f"{cfg.marketplace.django_url}/marketplace/"
            return _text({
                "marketplace_url": url,
                "note": "Browse the marketplace at this URL. API listing endpoint coming soon.",
                "filters": {k: v for k, v in arguments.items() if v},
            })

        elif name == "get_listing_detail":
            cfg = _config or load_config()
            url = f"{cfg.marketplace.django_url}/bots/{arguments['slug']}/"
            return _text({"listing_url": url, "slug": arguments["slug"]})

        elif name == "subscribe_to_bot":
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

        else:
            return _text({"error": f"Unknown tool: {name}"})

    except Exception as e:
        logger.error("Tool %s failed: %s", name, e, exc_info=True)
        return _text({"error": str(e), "tool": name})


async def _run():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def main():
    logger.info("Starting AlgoChains MCP Server v0.1.0")
    asyncio.run(_run())


if __name__ == "__main__":
    main()
