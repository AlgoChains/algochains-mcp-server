# V17: Multi-Broker Expansion + IDE Scaling + State Awareness Blueprint

> **Date**: March 30, 2026
> **Status**: BLUEPRINT — Ready for implementation
> **Scope**: Expand brokerage coverage to 20+ brokers, solve the 100+ tool IDE problem, deepen state awareness

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [Research Findings](#2-research-findings)
3. [Architecture: The Three Pillars](#3-architecture-the-three-pillars)
4. [Pillar 1: Multi-Broker Expansion](#4-pillar-1-multi-broker-expansion)
5. [Pillar 2: IDE Scaling — Dynamic Toolsets](#5-pillar-2-ide-scaling--dynamic-toolsets)
6. [Pillar 3: Deep State Awareness](#6-pillar-3-deep-state-awareness)
7. [Existing MCP Servers to Integrate](#7-existing-mcp-servers-to-integrate)
8. [Normalized API Contract](#8-normalized-api-contract)
9. [Implementation Plan](#9-implementation-plan)
10. [Mega Prompt](#10-mega-prompt)

---

## 1. Problem Statement

### Three critical gaps in the current AlgoChains MCP Server:

**Gap 1: Limited Brokerage Coverage**
- Currently supports: Alpaca, IBKR, Oanda, TradersPost (Schwab/Robinhood proxy), QuantConnect
- Missing: Tradovate (futures — we USE this for live bots!), Coinbase, Binance, Kraken, Tastytrade, TradeStation, Webull, eTrade, Bybit, dYdX, and 10+ others
- Each broker has different auth (OAuth2, API keys, session tokens), order types, WebSocket protocols, and asset class support

**Gap 2: 100+ Tools Overwhelm IDEs**
- Our server exposes 100+ tools across V1-V16 engines
- Cursor hard-caps at 40 MCP tools. Beyond that, tools are INVISIBLE to the LLM
- Claude Desktop / Windsurf: tool definitions consume 20-55K+ tokens (22-27% of context window) before a single user message
- GitHub Copilot caps at 128 tools
- More tools = worse LLM reasoning, slower responses, higher costs

**Gap 3: Shallow State Awareness**
- Current MCP Resources expose snapshots but not live streaming state
- No cross-broker aggregated view (unified portfolio across all brokers)
- No market regime context injected into tool selection
- No session-level state (what did the user trade today? what's their P&L?)

---

## 2. Research Findings

### 2a. Existing Brokerage MCP Servers (Found 25+)

| Broker | MCP Server | Stars | Language | Status | GitHub URL |
|--------|-----------|-------|----------|--------|------------|
| **Alpaca** | alpacahq/alpaca-mcp-server (OFFICIAL) | 591 | Python | Production (V2) | https://github.com/alpacahq/alpaca-mcp-server |
| **IBKR** | rcontesti/IB_MCP | 101 | Python | Active | https://github.com/rcontesti/ib_mcp |
| **IBKR** | seriallazer/ibkr-mcp-server | 56 | Python | Active | https://github.com/seriallazer/ibkr-mcp-server |
| **IBKR** | happy-shine/ibkr_mcp | 18 | Python | Active | https://github.com/happy-shine/ibkr_mcp |
| **IBKR** | xiao81/IBKR-MCP-Server | 18 | Python | Active | https://github.com/xiao81/IBKR-MCP-Server |
| **IBKR** | GaoChX/ibkr-mcp-server | 8 | Python | Active (FastMCP 2.0) | https://github.com/GaoChX/ibkr-mcp-server |
| **IBKR** | code-rabi/interactive-brokers-mcp | — | Python | Active | https://github.com/code-rabi/interactive-brokers-mcp |
| **Tradovate** | 0xjmp/mcp-tradovate | — | — | Active | https://github.com/0xjmp/mcp-tradovate |
| **Tradovate** | alexanimal/tradovate-mcp-server | — | — | Active | https://github.com/alexanimal/tradovate-mcp-server |
| **Robinhood** | rohitsingh-iitd/robinhood-mcp-server | — | Python | Active (crypto) | https://github.com/rohitsingh-iitd/robinhood-mcp-server |
| **Robinhood** | verygoodplugins/robinhood-mcp | 2 | Python | Read-only | https://github.com/verygoodplugins/robinhood-mcp |
| **Tastytrade** | ferdousbhai/tasty-agent | — | Python | Active | https://github.com/ferdousbhai/tasty-agent |
| **TradeStation** | theelderwand/tradestation-mcp | — | — | Active | https://github.com/theelderwand/tradestation-mcp |
| **TradeStation** | Frederick-G764/brokers-mcp | — | — | Active | https://github.com/Frederick-G764/brokers-mcp |
| **Multi-Broker** | trade-it-inc/trade-it-mcp | — | — | Remote (Schwab, eTrade, Webull, Tastytrade, Coinbase, Kraken) | https://github.com/trade-it-inc/trade-it-mcp |
| **Multi-Broker** | Open-Agent-Tools/open-stocks-mcp | 4 | Python | Active (Robinhood, Schwab) | https://github.com/Open-Agent-Tools/open-stocks-mcp |
| **CCXT (100+ crypto)** | doggybee/mcp-server-ccxt | 131 | TypeScript | Active | https://github.com/doggybee/mcp-server-ccxt |
| **CCXT** | Obinox04/ccxt-mcp | — | — | Active | https://github.com/Obinox04/ccxt-mcp |
| **CCXT** | lazy-dinosaur/ccxt-mcp | — | — | Active | https://github.com/lazy-dinosaur/ccxt-mcp |
| **Coinbase** | zhangzhongnan928/mcp-coinbase-commerce | — | — | Active | (awesome-mcp-servers list) |
| **Binance** | ForgeTrade MCP | — | — | Active | https://www.pulsemcp.com/servers/forgetrade-binance-trader |
| **Forex** | Forex-GPT MCP | — | — | Active (127+ instruments) | https://www.pulsemcp.com/servers/forex-gpt |
| **Market Data** | financial-datasets/mcp-server | — | — | Active | https://github.com/financial-datasets/mcp-server |
| **Market Data** | Massive MCP | — | — | Active (Polygon.io) | Already integrated in our system |

### 2b. IDE Token Problem — Hard Data

| Client | Tool Limit | What Happens Over Limit |
|--------|-----------|------------------------|
| **Cursor** | 40 tools max | Silently drops tools beyond 40. Remaining tools INVISIBLE |
| **GitHub Copilot** | 128 tools | Hard cap, tools beyond 128 ignored |
| **Claude Desktop** | ~120 tools practical | Tool definitions consume 22-27% of 200K context window |
| **Claude Code** | No hard limit | But 130 tools = 52K tokens = 26% of context consumed before first message |
| **Windsurf** | No published limit | Same context pressure as Claude |

**Real numbers**: GitHub MCP server with 66 tools consumes 46K additional tokens. One user reported going from 34K to 80K tokens just by adding GitHub MCP. Our 100+ tool server would consume **55-70K tokens** of context just for tool definitions.

### 2c. Proven Scaling Patterns

| Pattern | Token Reduction | Source | Tradeoff |
|---------|----------------|--------|----------|
| **Dynamic Toolsets** (Speakeasy) | 96% input, 90% total | speakeasy.com/blog | 2-3x more tool calls, ~50% slower first call |
| **Registry Pattern** (Harness) | 94% (130 → 11 tools) | harness.io/blog | Requires generic verb design |
| **Gateway/Hub Pattern** (Arcade) | Configurable per-client | arcade.dev/blog | Extra infra layer |
| **Tool-RAG** (ApX) | Variable | apxml.com/posts | Embedding quality dependent |
| **Split Servers** (Manual) | Linear with split count | Best practice guides | Config management overhead |
| **Anthropic Tool Search** | Progressive (deferred) | Anthropic SDK | Requires `defer_loading: true` |

---

## 3. Architecture: The Three Pillars

```
┌─────────────────────────────────────────────────────────────────────┐
│                        IDE / AI Client                               │
│  (Cursor, Windsurf, Claude Desktop, Claude Code, VS Code)           │
└────────────────────────────┬────────────────────────────────────────┘
                             │ MCP Protocol (stdio or HTTP+SSE)
                             │
┌────────────────────────────▼────────────────────────────────────────┐
│              PILLAR 2: Dynamic Tool Gateway                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐  │
│  │ search_tools  │  │describe_tools│  │ execute_tool              │  │
│  │ (semantic +   │  │ (lazy schema │  │ (dispatch to any backend  │  │
│  │  categories)  │  │  loading)    │  │  server/engine)           │  │
│  └──────────────┘  └──────────────┘  └──────────────────────────┘  │
│                                                                      │
│  Tool Registry: 200+ tools indexed, only 3 exposed to LLM           │
│  Categories: trading, portfolio, market-data, ml, analytics,         │
│              execution, alt-data, agents, defi, cloud, admin         │
└────────┬──────────┬──────────┬──────────┬──────────┬───────────────┘
         │          │          │          │          │
    ┌────▼───┐ ┌───▼────┐ ┌───▼───┐ ┌───▼────┐ ┌───▼────┐
    │Broker  │ │ML/AI   │ │Exec   │ │Alt     │ │Cloud   │
    │Gateway │ │Engine  │ │Engine │ │Data    │ │SaaS    │
    │(V17)   │ │(V10)   │ │(V11)  │ │(V13)   │ │(V16)   │
    └───┬────┘ └────────┘ └───────┘ └────────┘ └────────┘
        │
   PILLAR 1: Normalized Multi-Broker Layer
        │
   ┌────▼─────────────────────────────────────────────┐
   │              BrokerConnector ABC                   │
   │  connect() | place_order() | get_positions()      │
   │  cancel_order() | get_quote() | get_account()     │
   │  get_orders() | close_position() | health_check() │
   │  + NEW: get_historical() | stream_quotes()        │
   │  + NEW: get_option_chain() | get_order_book()     │
   └──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬────┘
      │  │  │  │  │  │  │  │  │  │  │  │  │  │  │
      ▼  ▼  ▼  ▼  ▼  ▼  ▼  ▼  ▼  ▼  ▼  ▼  ▼  ▼  ▼
   Alpaca IBKR Oanda Tradovate Schwab Tastytrade
   TradeStation Coinbase Binance Kraken Webull
   eTrade Bybit dYdX Robinhood ...
        │
   PILLAR 3: Deep State Awareness
        │
   ┌────▼─────────────────────────────────────────────┐
   │           Unified State Engine                     │
   │  • Cross-broker aggregated portfolio               │
   │  • Real-time P&L across all accounts               │
   │  • Session trade journal (what happened today)     │
   │  • Market regime context injection                 │
   │  • Broker health dashboard                         │
   │  • Rate limit + circuit breaker status             │
   └──────────────────────────────────────────────────┘
```

---

## 4. Pillar 1: Multi-Broker Expansion

### 4a. Current State

Our `BrokerConnector` ABC in `brokers/base.py` already defines a solid normalized interface:
- `connect()`, `disconnect()`, `get_account()`, `get_positions()`, `get_orders()`
- `place_order()`, `cancel_order()`, `get_quote()`, `close_position()`, `close_all_positions()`, `health_check()`
- Data models: `Order`, `Position`, `AccountInfo`, `Quote` with `to_dict()` serialization
- Enums: `OrderSide`, `OrderType`, `OrderStatus`, `AssetClass`

Existing connectors: `alpaca_connector.py`, `ibkr_connector.py`, `oanda_connector.py`, `traderspost_connector.py`, `quantconnect_connector.py`

### 4b. New Connectors to Build (Priority Order)

#### Tier 1 — We actively use these / highest demand
| Broker | Asset Classes | Auth | Complexity | Approach |
|--------|-------------|------|------------|----------|
| **Tradovate** | Futures | OAuth2 + WebSocket | HIGH | We have `tradovate_client.py` in control-tower. Port to connector. Reference: `0xjmp/mcp-tradovate`, `alexanimal/tradovate-mcp-server` |
| **Coinbase Advanced** | Crypto | API Key + HMAC | MEDIUM | Reference: `ccxt` library, Coinbase Advanced Trade API v3 |
| **Schwab** (direct) | Stocks, Options, ETFs | OAuth2 (weekly re-auth) | HIGH | Reference: `Open-Agent-Tools/open-stocks-mcp`, Schwab API docs. Currently proxied via TradersPost |

#### Tier 2 — High community demand
| Broker | Asset Classes | Auth | Complexity | Approach |
|--------|-------------|------|------------|----------|
| **Tastytrade** | Stocks, Options, Crypto | OAuth2 (client_secret + refresh_token) | MEDIUM | Reference: `ferdousbhai/tasty-agent` — has working MCP server. Port API client to our connector |
| **Binance** | Crypto, Futures | API Key + HMAC | MEDIUM | Use `ccxt` library as adapter. Reference: `doggybee/mcp-server-ccxt` |
| **Kraken** | Crypto | API Key + nonce | MEDIUM | Use `ccxt` library as adapter |
| **TradeStation** | Stocks, Options, Futures | OAuth2 | MEDIUM | Reference: `theelderwand/tradestation-mcp`, `areed1192/tradestation-python-api` |

#### Tier 3 — Nice to have
| Broker | Asset Classes | Auth | Complexity | Approach |
|--------|-------------|------|------------|----------|
| **Webull** | Stocks, Options, Crypto | OAuth2 | HIGH (unofficial API) | Reference: `trade-it-inc/trade-it-mcp` (remote, supports Webull) |
| **eTrade** | Stocks, Options | OAuth1a (!!!) | HIGH | Morgan Stanley API, legacy auth. Reference: `trade-it-inc/trade-it-mcp` |
| **Bybit** | Crypto, Derivatives | API Key + HMAC | MEDIUM | Use `ccxt` library |
| **dYdX** | Crypto Perps | API Key | MEDIUM | Use `ccxt` or direct REST |
| **Robinhood** | Stocks, Options, Crypto | Session-based (no official API) | VERY HIGH | Reference: `rohitsingh-iitd/robinhood-mcp-server` (crypto only). Unofficial `robin_stocks` library |

#### Tier 4 — Aggregate via adapters
| Broker | Approach |
|--------|----------|
| **All 100+ crypto exchanges** | Single `ccxt_connector.py` adapter wrapping the `ccxt` library. One connector, 100+ exchanges |
| **Trade-It supported brokers** | Single `tradeit_connector.py` wrapping Trade-It's remote MCP (Schwab, eTrade, Webull, Public, Tastytrade, Coinbase, Kraken) |

### 4c. Extended BrokerConnector ABC

Add optional methods to `base.py` for advanced features:

```python
# === NEW optional abstract methods (default NotImplementedError) ===

async def get_historical(
    self, symbol: str, interval: str = "1d",
    start: Optional[datetime] = None, end: Optional[datetime] = None,
) -> list[dict]:
    """Get historical OHLCV bars."""
    raise NotImplementedError(f"{self.name} does not support historical data")

async def stream_quotes(self, symbols: list[str]) -> AsyncIterator[Quote]:
    """Stream real-time quotes via WebSocket. Yields Quote objects."""
    raise NotImplementedError(f"{self.name} does not support streaming")

async def get_option_chain(
    self, symbol: str, expiration: Optional[str] = None,
) -> list[dict]:
    """Get options chain for a symbol."""
    raise NotImplementedError(f"{self.name} does not support options")

async def get_order_book(self, symbol: str, depth: int = 10) -> dict:
    """Get Level 2 order book."""
    raise NotImplementedError(f"{self.name} does not support order book")

async def get_transactions(
    self, start: Optional[datetime] = None, end: Optional[datetime] = None,
) -> list[dict]:
    """Get account transaction history."""
    raise NotImplementedError(f"{self.name} does not support transactions")

# === NEW: Capabilities declaration ===
@property
def capabilities(self) -> dict:
    """Declare what this broker supports."""
    return {
        "streaming": False,
        "options": False,
        "futures": False,
        "order_book": False,
        "historical": False,
        "paper_trading": False,
        "bracket_orders": False,
        "fractional_shares": False,
    }
```

### 4d. CCXT Meta-Connector Pattern

For crypto, build ONE connector that wraps `ccxt`:

```python
class CCXTConnector(BrokerConnector):
    """Universal crypto connector via ccxt — supports 100+ exchanges."""

    def __init__(self, exchange_id: str, api_key: str, secret: str, **kwargs):
        self.exchange_id = exchange_id
        self.name = f"ccxt_{exchange_id}"  # e.g. "ccxt_binance"
        self.supported_asset_classes = [AssetClass.CRYPTO]
        self._exchange = getattr(ccxt, exchange_id)({
            'apiKey': api_key,
            'secret': secret,
            **kwargs,
        })

    async def place_order(self, symbol, side, qty, order_type=OrderType.MARKET, **kw):
        # Normalize to ccxt format
        ccxt_type = order_type.value  # "market", "limit", etc.
        raw = await asyncio.to_thread(
            self._exchange.create_order, symbol, ccxt_type, side.value, qty,
            kw.get('limit_price'),
        )
        return self._normalize_order(raw)
```

---

## 5. Pillar 2: IDE Scaling — Dynamic Toolsets

### 5a. The Problem (Numbers)

Our server currently has ~110 tools. At ~400-600 tokens per tool definition:
- **Total tool token overhead**: ~44,000–66,000 tokens
- **Cursor**: Can only see 40 of our 110 tools (64% INVISIBLE)
- **Claude Desktop**: 22-33% of context window consumed by tool definitions alone
- **Impact**: Worse reasoning, missed tools, higher API costs, slower responses

### 5b. The Solution: Three-Tool Gateway

Replace 110+ tools with **exactly 3 meta-tools** exposed to the LLM:

```
┌─────────────────────────────────────────────────────────┐
│  LLM sees ONLY 3 tools:                                  │
│                                                           │
│  1. search_tools(query, category?, limit?)                │
│     → Returns matching tool names + short descriptions    │
│     → Uses semantic embeddings + category index           │
│                                                           │
│  2. describe_tools(tool_names: list[str])                 │
│     → Returns full JSON schema for requested tools        │
│     → Lazy-loads only what LLM needs                      │
│                                                           │
│  3. execute_tool(tool_name, arguments)                    │
│     → Dispatches to the real tool implementation          │
│     → All existing middleware applies (timeout, circuit    │
│       breaker, rate limit, concurrency, size guard)       │
└─────────────────────────────────────────────────────────┘
```

**Token impact**: ~1,500 tokens for 3 tool definitions vs ~55,000 for 110 tools = **97% reduction**

### 5c. Tool Registry Design

```python
@dataclass
class ToolEntry:
    name: str
    category: str          # "trading", "ml", "analytics", etc.
    description: str       # Short (one line)
    full_description: str  # Detailed (for describe_tools)
    input_schema: dict     # JSON Schema
    tags: list[str]        # For filtering: ["order", "futures", "alpaca"]
    embedding: list[float] # Pre-computed semantic embedding

TOOL_REGISTRY: dict[str, ToolEntry] = {}

# Categories with display labels
TOOL_CATEGORIES = {
    "trading":      "Trading — place/cancel/close orders on any broker",
    "portfolio":    "Portfolio — positions, P&L, account info across brokers",
    "market_data":  "Market Data — quotes, bars, snapshots, order books",
    "ml":           "ML/AI — train models, feature engineering, predictions",
    "execution":    "Execution — institutional order routing, algos, TCA",
    "analytics":    "Analytics — P&L streams, order flow, regime detection",
    "alt_data":     "Alt Data — sentiment, satellite, SEC filings, social",
    "agents":       "Agent Swarm — autonomous agent orchestration",
    "defi":         "DeFi — swaps, liquidity, yields, MEV protection",
    "cloud":        "Cloud SaaS — tenants, billing, white-label",
    "strategy":     "Strategy — backtesting, optimization, walk-forward",
    "broker_mgmt":  "Broker Management — connect, health check, configure",
    "admin":        "Admin — rate limits, circuit breakers, system status",
}
```

### 5d. search_tools Implementation

```python
@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "search_tools":
        query = arguments.get("query", "")
        category = arguments.get("category")
        limit = arguments.get("limit", 10)

        # Category filter
        candidates = TOOL_REGISTRY.values()
        if category:
            candidates = [t for t in candidates if t.category == category]

        # Semantic search via embeddings
        query_embedding = embed(query)
        scored = [(t, cosine_sim(query_embedding, t.embedding)) for t in candidates]
        scored.sort(key=lambda x: x[1], reverse=True)
        top = scored[:limit]

        results = [{"name": t.name, "category": t.category,
                     "description": t.description, "relevance": round(s, 3)}
                    for t, s in top]

        # Include category overview for discoverability
        return _text({
            "available_categories": TOOL_CATEGORIES,
            "results": results,
            "total_tools": len(TOOL_REGISTRY),
            "tip": "Use describe_tools to get full schemas before execute_tool"
        })

    elif name == "describe_tools":
        tool_names = arguments.get("tool_names", [])
        schemas = {}
        for tn in tool_names:
            entry = TOOL_REGISTRY.get(tn)
            if entry:
                schemas[tn] = {
                    "description": entry.full_description,
                    "input_schema": entry.input_schema,
                    "category": entry.category,
                    "tags": entry.tags,
                }
        return _text(schemas)

    elif name == "execute_tool":
        real_tool_name = arguments.get("tool_name")
        real_arguments = arguments.get("arguments", {})
        # Delegate to existing dispatch pipeline with ALL middleware
        return await _full_dispatch(real_tool_name, real_arguments)
```

### 5e. Dual-Mode Support

Support both modes for backward compatibility:

```python
# Environment variable controls mode
TOOL_MODE = os.environ.get("ALGOCHAINS_TOOL_MODE", "dynamic")
# "dynamic" = 3 meta-tools (default, recommended)
# "static"  = all 110+ tools exposed directly (for debugging or simple clients)

# Optionally, subset mode
ALGOCHAINS_TOOLSETS = os.environ.get("ALGOCHAINS_TOOLSETS", "all")
# "all" | "trading,portfolio,market_data" | "ml,analytics" | etc.
```

### 5f. Alternative: Split Server Architecture

If dynamic toolsets feel too complex, split into focused servers:

```json
{
  "mcpServers": {
    "algochains-trading": {
      "command": "uvx",
      "args": ["algochains-mcp", "--toolset", "trading,portfolio,market_data,broker_mgmt"]
    },
    "algochains-ml": {
      "command": "uvx",
      "args": ["algochains-mcp", "--toolset", "ml,analytics,strategy"]
    },
    "algochains-defi": {
      "command": "uvx",
      "args": ["algochains-mcp", "--toolset", "defi,alt_data,agents,cloud"]
    }
  }
}
```

Each sub-server exposes ~30-40 tools (within Cursor's limit).

---

## 6. Pillar 3: Deep State Awareness

### 6a. Current State Resources (Already Built)

- `algochains://brokers/status` — Connected brokers
- `algochains://validation/gates` — MCPT gate thresholds
- `algochains://ml/models` — ML model registry
- `algochains://execution/orders` — Order state
- `algochains://analytics/regimes` — Market regime detection
- `algochains://rate-limits/status` — Rate limiter state
- `algochains://circuit-breakers/status` — Circuit breaker state
- Plus 8 more V10-V16 engine state resources

### 6b. New State Resources to Add

#### Cross-Broker Aggregated State
```
algochains://portfolio/unified
→ {
    "total_equity": 245000.00,
    "total_cash": 52000.00,
    "total_unrealized_pnl": 3200.00,
    "total_realized_pnl_today": 850.00,
    "accounts": [
        {"broker": "alpaca", "equity": 100000, "positions": 5},
        {"broker": "tradovate", "equity": 85000, "positions": 2},
        {"broker": "ibkr", "equity": 60000, "positions": 8}
    ],
    "cross_broker_positions": [
        {"symbol": "AAPL", "total_qty": 150, "brokers": ["alpaca", "ibkr"]},
        {"symbol": "MNQM6", "total_qty": 2, "brokers": ["tradovate"]}
    ]
  }
```

#### Session Trade Journal
```
algochains://session/journal
→ {
    "session_start": "2026-03-30T06:30:00-07:00",
    "trades_today": 12,
    "realized_pnl_today": 850.00,
    "wins": 8, "losses": 4,
    "win_rate": 0.667,
    "largest_win": 420.00,
    "largest_loss": -180.00,
    "trades": [
        {"time": "09:32", "symbol": "AAPL", "side": "buy", "qty": 50, "pnl": 230.00},
        ...
    ]
  }
```

#### Market Context
```
algochains://market/context
→ {
    "regime": "trending_up",
    "vix": 18.5,
    "market_status": "open",
    "next_close": "2026-03-30T16:00:00-04:00",
    "sector_leaders": ["XLK", "XLF"],
    "sector_laggards": ["XLE", "XLU"],
    "economic_calendar_today": [
        {"time": "10:00", "event": "Consumer Confidence", "impact": "high"}
    ]
  }
```

#### Broker Capabilities Matrix
```
algochains://brokers/capabilities
→ {
    "alpaca":     {"stocks": true, "options": true, "crypto": true, "futures": false, "streaming": true, "paper": true},
    "tradovate":  {"stocks": false, "options": false, "crypto": false, "futures": true, "streaming": true, "paper": true},
    "ibkr":       {"stocks": true, "options": true, "crypto": false, "futures": true, "streaming": true, "paper": true},
    "ccxt_binance": {"stocks": false, "options": false, "crypto": true, "futures": true, "streaming": true, "paper": true},
    ...
  }
```

#### Tool Usage Analytics
```
algochains://admin/tool-analytics
→ {
    "session_tool_calls": 47,
    "most_used": ["place_order", "get_positions", "get_quote"],
    "slowest_avg_ms": [{"tool": "train_model", "avg_ms": 8500}],
    "errors_today": [{"tool": "scrape_web", "count": 3, "last_error": "timeout"}],
    "circuit_breakers_tripped": ["v13_alt_data"]
  }
```

### 6c. Context Injection for Tool Selection

When using Dynamic Toolsets, inject relevant state into `search_tools` results:

```python
# If user asks "buy some AAPL", inject:
# - Which brokers support stocks (alpaca, ibkr, schwab)
# - Current AAPL position across brokers (if any)
# - Current buying power per broker
# - Market status (open/closed)

# This helps the LLM make informed tool selections without
# needing to call 5 tools to gather context first
```

---

## 7. Existing MCP Servers to Integrate

### Strategy: Absorb vs Proxy vs Reference

| Server | Strategy | Reasoning |
|--------|----------|-----------|
| **Alpaca Official** | **Reference** — port API patterns to our connector | Their V2 is well-built; study their toolset filtering pattern |
| **rcontesti/IB_MCP** | **Reference** — study their TWS API wrapper | Most popular IBKR MCP (101 stars) |
| **0xjmp/mcp-tradovate** | **Absorb** — we need Tradovate desperately | Port their API client into our connector pattern |
| **ferdousbhai/tasty-agent** | **Absorb** — clean Python MCP, easy to port | Study their auth flow (client_secret + refresh_token) |
| **doggybee/mcp-server-ccxt** | **Reference** — study their ccxt wrapper pattern | Build our own Python ccxt connector using their patterns |
| **trade-it-inc/trade-it-mcp** | **Proxy** — remote server, no local code | Add as external MCP server for Schwab/eTrade/Webull |
| **theelderwand/tradestation-mcp** | **Reference** — study their API patterns | Build our own connector |
| **Open-Agent-Tools/open-stocks-mcp** | **Reference** — multi-broker patterns | Study their normalization approach |
| **Massive MCP** | **Already integrated** | Continue using for Polygon.io market data |

---

## 8. Normalized API Contract

### 8a. What Makes Normalization Hard

| Dimension | Challenge | Our Solution |
|-----------|-----------|-------------|
| **Auth flows** | OAuth2 (Alpaca, Schwab), API Key+HMAC (Binance), Session tokens (Tradovate), OAuth1a (eTrade) | Auth adapter per connector; unified `connect()` / `is_connected()` interface |
| **Order types** | IBKR has 60+ order types. Most brokers have 5. | Core set in enum (market/limit/stop/stop_limit/trailing_stop) + `extra_params: dict` for broker-specific |
| **Asset classes** | Some brokers support 1 class (Tradovate = futures only), some support 5+ | `supported_asset_classes` list per connector + `capabilities` property |
| **Symbology** | AAPL vs AAPL.US vs XNAS:AAPL vs US.AAPL | Symbol normalizer utility per connector; user always uses simple symbols |
| **WebSocket vs REST** | Alpaca/IBKR/Tradovate use WS for streaming. Some are REST-only | `stream_quotes()` optional method; REST polling fallback |
| **Paper/Live** | Different endpoints, different API keys | `paper: bool` config per connector; same interface |
| **Fractional shares** | Alpaca yes, IBKR no, Tradovate N/A | `capabilities["fractional_shares"]` flag |
| **Rate limits** | Vary wildly per broker | Per-broker rate limit config in middleware |

### 8b. The Normalized Interface (Already 90% There)

Our `BrokerConnector` ABC is the right foundation. We extend it with:

1. **Capabilities property** — declare what each broker supports
2. **Optional advanced methods** — `get_historical()`, `stream_quotes()`, `get_option_chain()`, `get_order_book()`
3. **Symbol normalizer** — per-connector `normalize_symbol()` / `denormalize_symbol()`
4. **Auth lifecycle** — `connect()` already exists; add `refresh_auth()` and `is_authenticated()` for long-running sessions
5. **Error normalization** — all connectors raise `BrokerError` with broker-specific details in `.raw`

---

## 9. Implementation Plan

### Phase 1: Tradovate Connector (Week 1) — HIGHEST PRIORITY
- [ ] Port `tradovate_client.py` from control-tower to `brokers/tradovate_connector.py`
- [ ] Implement full `BrokerConnector` interface (place_order, positions, quotes, etc.)
- [ ] Handle OAuth2 token lifecycle (refresh, WebSocket keep-alive)
- [ ] Handle futures symbology (MNQ → MNQM6, CL → CLK6, etc.)
- [ ] Add to `registry.py` auto-detection
- [ ] Test with paper account

### Phase 2: CCXT Crypto Meta-Connector (Week 1-2)
- [ ] Build `ccxt_connector.py` wrapping the `ccxt` Python library
- [ ] Support top 10 exchanges: Binance, Coinbase, Kraken, Bybit, OKX, dYdX, KuCoin, Gate.io, Bitfinex, Gemini
- [ ] Normalize crypto symbols (BTC/USDT → consistent format)
- [ ] Handle exchange-specific order types

### Phase 3: Dynamic Toolset Gateway (Week 2-3)
- [ ] Build `tool_registry.py` with `ToolEntry` dataclass + category index
- [ ] Populate registry from existing TOOLS list automatically
- [ ] Implement `search_tools` with category browse + semantic search
- [ ] Implement `describe_tools` with lazy schema loading
- [ ] Implement `execute_tool` delegating to existing `_dispatch_tool`
- [ ] Add `ALGOCHAINS_TOOL_MODE` env var (dynamic/static)
- [ ] Add `ALGOCHAINS_TOOLSETS` env var for subset filtering
- [ ] Benchmark: measure token overhead reduction

### Phase 4: Tastytrade + TradeStation Connectors (Week 3)
- [ ] Port auth patterns from `ferdousbhai/tasty-agent`
- [ ] Build `tastytrade_connector.py`
- [ ] Build `tradestation_connector.py`
- [ ] Both implement full BrokerConnector interface

### Phase 5: Deep State Resources (Week 3-4)
- [ ] Build `state_engine.py` for cross-broker aggregation
- [ ] Implement `algochains://portfolio/unified` resource
- [ ] Implement `algochains://session/journal` resource
- [ ] Implement `algochains://market/context` resource
- [ ] Implement `algochains://brokers/capabilities` resource
- [ ] Implement `algochains://admin/tool-analytics` resource
- [ ] Wire context injection into `search_tools`

### Phase 6: Schwab Direct + Remaining Connectors (Week 4+)
- [ ] Build `schwab_connector.py` (OAuth2 weekly re-auth challenge)
- [ ] Build `webull_connector.py` (unofficial API)
- [ ] Evaluate Trade-It proxy for eTrade/Public

---

## 10. Mega Prompt

```
You are implementing the V17 Multi-Broker Expansion + IDE Scaling update for the
AlgoChains MCP Server. This server is a production financial trading system that
connects AI agents (via Cursor, Windsurf, Claude Desktop, VS Code) to 20+ brokerages
through a normalized API layer.

=== CODEBASE CONTEXT ===

Repository: /Users/treycsa/CascadeProjects/algochains-mcp-server/
Main files:
  - src/algochains_mcp/server.py — Main MCP server, tool definitions, dispatch, resources
  - src/algochains_mcp/middleware.py — Rate limiting, timeouts, circuit breakers, concurrency
  - src/algochains_mcp/brokers/base.py — BrokerConnector ABC (THE normalization layer)
  - src/algochains_mcp/brokers/registry.py — Broker registry (auto-discovery, get/list)
  - src/algochains_mcp/brokers/alpaca_connector.py — Reference implementation
  - src/algochains_mcp/brokers/ibkr_connector.py — IBKR connector
  - src/algochains_mcp/brokers/oanda_connector.py — Oanda forex connector
  - src/algochains_mcp/errors.py — Structured error hierarchy

=== THE THREE TASKS ===

TASK 1: NEW BROKER CONNECTORS
- Each connector goes in src/algochains_mcp/brokers/{name}_connector.py
- Each MUST implement the full BrokerConnector ABC from base.py
- Study the existing alpaca_connector.py as the reference pattern
- Normalized data models: Order, Position, AccountInfo, Quote (all in base.py)
- Every connector must handle auth, rate limits, symbol normalization, error mapping
- Priority: Tradovate (futures) → CCXT (100+ crypto) → Tastytrade → TradeStation → Schwab
- For Tradovate: We have a working client at /Users/treycsa/CascadeProjects/algochains-control-tower/tradovate_client.py — port it
- For crypto: Build a single CCXTConnector wrapping the ccxt library
- For each broker: READ THEIR OFFICIAL API DOCS first. Do NOT guess at endpoints

TASK 2: DYNAMIC TOOLSET GATEWAY (IDE SCALING)
- Our server has 110+ tools. Cursor only supports 40. This must be solved.
- Implement a 3-tool gateway pattern: search_tools, describe_tools, execute_tool
- Build a ToolRegistry that indexes all 110+ tools with categories + embeddings
- search_tools returns names + short descriptions (not full schemas)
- describe_tools returns full JSON schemas only for requested tools (lazy loading)
- execute_tool delegates to the real tool via the existing _dispatch_tool pipeline
- All existing middleware (timeouts, circuit breakers, rate limiting, concurrency semaphores) MUST apply to execute_tool
- Support env var ALGOCHAINS_TOOL_MODE=dynamic|static
- Support env var ALGOCHAINS_TOOLSETS for subset filtering (like Alpaca's pattern)
- This should reduce token overhead from ~55K to ~1.5K (97% reduction)

TASK 3: DEEP STATE AWARENESS
- Build a UnifiedStateEngine that aggregates state across all connected brokers
- New MCP Resources:
  * algochains://portfolio/unified — cross-broker aggregated portfolio
  * algochains://session/journal — today's trades, P&L, win rate
  * algochains://market/context — regime, VIX, market status, calendar
  * algochains://brokers/capabilities — what each broker supports
  * algochains://admin/tool-analytics — tool usage stats, errors, performance
- Inject relevant context into search_tools results so the LLM can make smart tool choices

=== CONSTRAINTS ===

1. REAL DATA ONLY — no mocks, no stubs, no fake responses in production code
2. Every broker connector MUST read real API documentation — do NOT guess endpoints
3. Maintain backward compatibility — static mode must still work exactly as before
4. All new code goes through the existing middleware pipeline (rate limit, timeout, circuit breaker)
5. Use existing error hierarchy from errors.py — all broker errors → BrokerError
6. Use existing data models from base.py — Order, Position, AccountInfo, Quote
7. Python 3.10+ with type hints, async/await throughout
8. No new dependencies without justification (ccxt is justified for crypto)

=== REFERENCE IMPLEMENTATIONS TO STUDY ===

Before building each connector, study these:
- Alpaca: https://github.com/alpacahq/alpaca-mcp-server (V2 pattern, toolset filtering)
- IBKR: https://github.com/rcontesti/ib_mcp (TWS API wrapper)
- Tradovate: https://github.com/0xjmp/mcp-tradovate + our own tradovate_client.py
- Tastytrade: https://github.com/ferdousbhai/tasty-agent
- CCXT: https://github.com/doggybee/mcp-server-ccxt (TypeScript, study the pattern)
- Dynamic Toolsets: https://www.speakeasy.com/blog/how-we-reduced-token-usage-by-100x-dynamic-toolsets-v2
- Registry Pattern: https://www.harness.io/blog/harness-mcp-server-redesign
- Gateway Pattern: https://www.arcade.dev/blog/mcp-gateway-pattern/

=== QUALITY GATES ===

Before marking any task complete:
1. Connector passes health_check() against paper/sandbox account
2. Can place + cancel a paper order through the normalized interface
3. Dynamic toolset mode: search_tools finds correct tools for natural language queries
4. No lint errors, no type errors
5. Token overhead measured and documented (before/after for dynamic toolsets)
6. All MCP Resources return valid JSON
7. Circuit breakers, timeouts, and rate limits work for new connectors
```

---

## Appendix A: Token Math

### Current (Static Mode)
- 110 tools × ~500 tokens/tool = **55,000 tokens** consumed by tool definitions
- Cursor sees: 40/110 tools (36%)
- Context remaining for actual work: ~145K of 200K (27.5% wasted)

### After Dynamic Toolsets
- 3 meta-tools × ~500 tokens/tool = **1,500 tokens** consumed
- Cursor sees: 3/3 tools (100%)
- Context remaining: ~198.5K of 200K (0.75% overhead)
- **Token reduction: 97.3%**
- **Tradeoff**: 2-3 extra tool calls per task (~50% slower first call, amortizes over session)

## Appendix B: Auth Flow Summary

| Broker | Auth Type | Token Lifetime | Refresh Pattern | Sandbox |
|--------|----------|---------------|-----------------|---------|
| Alpaca | API Key + Secret | Permanent | N/A | Separate keys |
| IBKR | OAuth2 or TWS Gateway | Session | TWS auto-reconnect | Paper account |
| Tradovate | OAuth2 + WebSocket | 30 min access, refresh token | WebSocket keep-alive + Token Guardian | Demo account |
| Schwab | OAuth2 | 30 min access, 7 day refresh | Weekly manual re-auth (!!) | Paper account |
| Tastytrade | OAuth2 (client_secret) | Configurable | Refresh token | Paper account |
| TradeStation | OAuth2 | 20 min access | Refresh token | Simulated |
| Coinbase Advanced | API Key + HMAC | Permanent | N/A | Sandbox mode |
| Binance | API Key + HMAC | Permanent | N/A | Testnet |
| Kraken | API Key + Nonce | Permanent | N/A | No sandbox (!) |
| Oanda | API Key | Permanent | N/A | Practice account |

## Appendix C: File Structure After V17

```
src/algochains_mcp/
├── brokers/
│   ├── base.py                    # Extended BrokerConnector ABC
│   ├── registry.py                # Auto-discovery + capabilities
│   ├── alpaca_connector.py        # ✅ Existing
│   ├── ibkr_connector.py          # ✅ Existing
│   ├── oanda_connector.py         # ✅ Existing
│   ├── traderspost_connector.py   # ✅ Existing
│   ├── quantconnect_connector.py  # ✅ Existing
│   ├── tradovate_connector.py     # 🆕 Phase 1
│   ├── ccxt_connector.py          # 🆕 Phase 2 (100+ crypto exchanges)
│   ├── tastytrade_connector.py    # 🆕 Phase 4
│   ├── tradestation_connector.py  # 🆕 Phase 4
│   ├── schwab_connector.py        # 🆕 Phase 6
│   └── symbol_normalizer.py       # 🆕 Cross-broker symbol mapping
├── tool_gateway/
│   ├── registry.py                # 🆕 ToolEntry + ToolRegistry
│   ├── search.py                  # 🆕 Semantic search + category index
│   ├── embeddings.py              # 🆕 Tool description embeddings
│   └── gateway.py                 # 🆕 search_tools/describe_tools/execute_tool
├── state_engine/
│   ├── unified_portfolio.py       # 🆕 Cross-broker aggregation
│   ├── session_journal.py         # 🆕 Trade journal for current session
│   ├── market_context.py          # 🆕 Regime + VIX + calendar
│   └── tool_analytics.py          # 🆕 Usage stats + error tracking
├── middleware.py                   # ✅ Existing (hardened)
├── server.py                      # Modified: dual-mode + new resources
└── errors.py                      # ✅ Existing
```
