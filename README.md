<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://algochains.ai/logo-white.svg">
  <source media="(prefers-color-scheme: light)" srcset="https://algochains.ai/logo-dark.svg">
  <img alt="AlgoChains" src="https://algochains.ai/logo-dark.svg" width="420">
</picture>

<br />

# MCP Server for Algorithmic Trading

**Give any AI agent the ability to trade, build strategies, and deploy validated algos to the marketplace — across 15+ brokers, 10+ data providers, and every major IDE.**

**V20.0: Account Protection + Builder SDK + Marketplace Pipeline** — 275+ tools. NEW: 13 pre-trade safety guards (VIX killswitch, drawdown circuit breakers, fat finger detection, concentration limits), Builder SDK with 3.09B+ row data warehouse access, end-to-end marketplace submission pipeline with 7-gate MCPT validation, memory-safe architecture preventing OOM crashes, and Backtrader integration. Plus all V19 features: alpha engines, intent-based trading, shadow portfolios, genetic strategy evolution.

<br />

[![PyPI](https://img.shields.io/pypi/v/algochains-mcp-server?color=%236C3AED&label=PyPI)](https://pypi.org/project/algochains-mcp-server/)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)
[![MCP](https://img.shields.io/badge/MCP-v1.0-7C3AED)](https://modelcontextprotocol.io)
[![Tests](https://img.shields.io/badge/tests-passing-brightgreen)](#development)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

[Get Started](#get-started) ·
[Account Protection](#account-protection) ·
[Builder SDK](#builder-sdk) ·
[Marketplace](#strategy-marketplace) ·
[Tools Reference](#tool-reference) ·
[Data Providers](#data-providers) ·
[Docs](https://algochains.ai/docs)

</div>

<br />

## Why AlgoChains

| Problem | Before | After |
|---------|--------|-------|
| AI-to-broker connectivity | 5 agents × 15 brokers = 75 integrations | 5 agents × 1 MCP server = **5 connections** |
| Strategy validation | "Trust me, bro" metrics | **7-gate MCPT validation** (peer-reviewed rigor) |
| Account safety | Hope for the best | **13 pre-trade guards** (VIX killswitch, drawdown breakers) |
| Market data | Different API per source | **3.09B+ rows** via single data warehouse query |
| Strategy marketplace | Manual uploads | **End-to-end pipeline** from backtest to live listing |

## Get Started

### Install

```bash
pip install algochains-mcp-server
```

With broker SDKs, data providers, and backtesting:

```bash
pip install "algochains-mcp-server[all,data-all,datasets,backtrader]"
```

### Add to Your IDE

<details open>
<summary><strong>Cursor</strong></summary>

Add to `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "algochains": {
      "command": "algochains-mcp",
      "env": {
        "ALPACA_API_KEY": "...",
        "ALPACA_SECRET_KEY": "...",
        "ALGOCHAINS_TOOL_MODE": "smart"
      }
    }
  }
}
```

> **Cursor has an 80-tool limit.** Smart mode (default) exposes ~29 core tools. Use `discover_tools` to access 245+ more on demand.

</details>

<details>
<summary><strong>Claude Desktop</strong></summary>

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "algochains": {
      "command": "algochains-mcp",
      "args": ["--transport", "stdio"],
      "env": {
        "ALPACA_API_KEY": "...",
        "ALPACA_SECRET_KEY": "...",
        "ALGOCHAINS_TOOL_MODE": "smart"
      }
    }
  }
}
```

</details>

<details>
<summary><strong>Windsurf / Cascade</strong></summary>

Add to `~/.windsurf/mcp-config.json`:

```json
{
  "mcpServers": {
    "algochains": {
      "command": "algochains-mcp",
      "env": {
        "ALPACA_API_KEY": "...",
        "ALPACA_SECRET_KEY": "...",
        "ALGOCHAINS_TOOL_MODE": "smart"
      }
    }
  }
}
```

</details>

<details>
<summary><strong>VS Code / Codex</strong></summary>

Add to `.vscode/mcp.json`:

```json
{
  "mcpServers": {
    "algochains": {
      "command": "algochains-mcp",
      "env": {
        "ALPACA_API_KEY": "...",
        "ALPACA_SECRET_KEY": "..."
      }
    }
  }
}
```

</details>

---

## Account Protection

**Never blow up an account again.** 13 pre-trade safety guards run before every order:

| Guard | What It Does | Default |
|-------|-------------|---------|
| **VIX Killswitch** | Blocks all trades when VIX > threshold | Block at 35, flatten at 50 |
| **Daily Loss** | Stops trading after daily loss % limit | 2% soft, 5% hard |
| **Drawdown** | Blocks trades during drawdown from peak | 10% block, 15% flatten |
| **Buying Power** | Verifies sufficient buying power | Must have funds |
| **Margin** | Caps margin utilization | 70% warn, 80% block |
| **Time Restriction** | Blocks first/last minutes of session | 5 min each |
| **Consecutive Loss** | Cooldown after losing streak | 5 losses → 30 min pause |
| **Fat Finger** | Detects abnormal order sizes | 10x median, $100K notional |
| **Position Size** | Limits single position % of equity | 10% warn, 25% block |
| **Max Positions** | Caps total open positions | 20 positions |
| **Concentration** | Prevents over-concentration | 20% single, 50% sector |
| **Correlation** | Detects correlated exposure | Sector-based grouping |
| **Max Positions** | Limits number of open positions | 20 maximum |

### Protection Presets

```
"conservative"  — Tight limits for small/new accounts ($25K max notional, 1% daily loss)
"moderate"      — Balanced for typical retail (default)
"aggressive"    — Wider limits for experienced traders ($500K notional, 5% daily loss)
```

### Usage

Ask your AI: *"Check if buying 100 shares of NVDA is safe for my account"*

The agent will call `check_order_safety` which runs all 13 guards and returns ALLOW or BLOCK with specific reasons.

---

## Builder SDK

**For $199/mo Builder tier subscribers.** Everything you need to build, validate, and publish trading strategies.

### Data Warehouse Access

Query 3.09 billion+ rows of historical market data:

| Warehouse | Rows | Coverage |
|-----------|------|----------|
| Crypto | 409M | BTC, ETH, SOL, DOGE + more |
| Stocks | 1.3B | All US equities |
| Forex | 1.4B | 70+ currency pairs |

Schema: `ticker, open, high, low, close, volume, window_start, transactions`

Ask your AI: *"Query the last 30 days of AAPL minute data from the stocks warehouse"*

### Strategy Submission Pipeline

End-to-end flow from backtest to live marketplace listing:

```
1. Build strategy (your code, never uploaded)
2. Run backtest → get metrics
3. Submit to marketplace via MCP
4. 7-gate validation runs automatically:
   - Gate 1: Schema validation
   - Gate 2: Performance (Sharpe ≥ 1.0, trades ≥ 50, MaxDD ≤ 40%)
   - Gate 3: Overfitting detection (OOS/IS ratio ≥ 0.5)
   - Gate 4: MCPT significance (p-value < 0.05)
   - Gate 5: Walk-forward consistency (3+ folds)
   - Gate 6: Paper trading (30 days)
   - Gate 7: Decay monitoring (ongoing)
5. Tier assigned: Platinum / Gold / Silver / Bronze
6. Live on marketplace → subscribers auto-deploy
```

### IP Protection

| What | Paper Tier ($30/mo) | Builder Tier ($199/mo) |
|------|--------------------|-----------------------|
| Signal alerts | Yes | Yes |
| Paper trading | Yes | Yes |
| Live trading | No | Yes |
| Source code | **Never** | **Never** |
| Strategy parameters | No | Summary only |
| Data warehouse | No | 3.09B+ rows (read-only) |
| Build & submit algos | No | Yes |

Your source code, algorithm logic, and parameters are **never uploaded, never exposed, never shared**. Signal payloads contain only: direction, symbol, qty, entry, stop, target.

---

## Strategy Marketplace

### Published Bots

| Strategy | Symbol | OOS Sharpe | Win Rate | Max DD | Price |
|----------|--------|-----------|----------|--------|-------|
| NVDA BB Mean Reversion | NVDA | 4.17 | 50.6% | 6.3% | $29.99/mo |
| IWM BB Mean Reversion | IWM | 3.81 | 46.9% | 20.3% | $29.99/mo |
| SHOP BB Mean Reversion | SHOP | 3.34 | 39.1% | 18.2% | $29.99/mo |
| GOOGL BB Mean Reversion | GOOGL | 3.17 | 39.0% | 26.1% | $29.99/mo |
| MNQ Upgraded Scalper | MNQ | 4.40 | — | — | Internal |

### Revenue Model

- **70% to creators** / 30% to AlgoChains (highest in industry)
- **$50-100/mo** for Platinum tier strategies
- **$30-50/mo** for Gold tier
- **$15-30/mo** for Silver tier
- **$5-15/mo** for Bronze tier

### Signal Flow

```
Bot generates signal → Tradovate/Alpaca fills your account
                     ↓
              HMAC-signed POST to Django signal router
                     ↓
              Query subscriber preferences (position types,
              confidence min, portfolio %, time restrictions)
                     ↓
              Fan-out to subscribers via their connected brokers
              (Alpaca, Schwab, Tradovate, OANDA, etc.)
```

---

## Broker Support

| Broker | Connection | Assets | Status |
|--------|-----------|--------|--------|
| **Alpaca** | REST API | Stocks, Options, Crypto | Direct |
| **Interactive Brokers** | TWS/Gateway | Stocks, Options, Futures, Forex | Direct |
| **OANDA** | REST v20 | Forex (70+ pairs) | Direct |
| **Tradovate** | OAuth2 + REST | Futures | Direct |
| **QuantConnect** | LEAN API | All (via LEAN) | Direct |
| **TradersPost** | Webhook | Schwab, Robinhood, Tastytrade, TradeStation, Tradier, Coinbase, Kraken, Bitget, ByBit, IBKR | Via webhook |

**15+ brokers through 1 interface.** This is the USB-C for trading AI.

---

## Tool Reference

### Smart Mode (Default) — ~29 Tier 1 Tools

| Tool | Description |
|------|-------------|
| `place_order` | Place order on any connected broker |
| `cancel_order` | Cancel order by ID |
| `close_position` | Close position in symbol |
| `get_account` | Account equity, cash, buying power |
| `get_positions` | Open positions across brokers |
| `get_orders` | Order history |
| `get_quote` | Real-time bid/ask/last |
| `connect_broker` | Connect to a broker |
| `get_portfolio_summary` | Cross-broker unified view |
| `backtest_strategy` | Run backtest with Rust engine (7ms/run) |
| `validate_strategy` | 7-gate validation pipeline |
| `optimize_strategy` | Bayesian optimization (Optuna) |
| `deploy_strategy` | Deploy to paper or live |
| `execute_intent` | Natural language trading ("buy $1000 of tech stocks") |
| `approve_intent` | Approve/reject AI-generated trade plans |
| `check_order_safety` | **NEW** Run 13 pre-trade safety guards |
| `get_protection_config` | **NEW** View/modify account protection settings |
| `query_data_warehouse` | **NEW** Query 3.09B+ rows of market data |
| `submit_to_marketplace` | **NEW** Submit strategy to marketplace |
| `discover_tools` | Find 245+ additional tools by category |
| `execute_dynamic_tool` | Run any discovered tool |
| `massive_search_endpoints` | Search 700+ Massive API endpoints |
| `massive_call_api` | Call any Massive endpoint |
| `massive_query_data` | Query stored Massive data |
| `massive_run_pipeline` | Composable data pipeline |

### Full Mode — 275+ Tools Across 14 Domains

| Domain | Tools | Examples |
|--------|-------|---------|
| Trading | 8 | place_order, cancel, close, bracket |
| Portfolio | 6 | positions, account, P&L, history |
| Market Data | 10 | quotes, bars, options chain, streaming |
| Strategy | 12 | backtest, optimize, walk-forward, deploy |
| Marketplace | 8 | browse, submit, subscribe, publish |
| Account Protection | 4 | safety check, config, audit, presets |
| Builder SDK | 5 | data warehouse, backtest, submit, guide |
| ML/AI | 20 | train, predict, features, RL agents |
| Execution | 18 | smart routing, FIX, TCA, algos |
| Analytics | 15 | P&L streaming, order flow, regime |
| Alt Data | 17 | sentiment, SEC filings, social |
| Agent Swarm | 15 | orchestration, consensus, memory |
| DeFi | 15 | DEX, yield, bridges, MEV protection |
| Alpha Engines | 20 | VWAP, GEX, dark pools, Kelly, tape |
| Cloud SaaS | 5 | multi-tenant, billing, white-label |
| Memory | 1 | get_memory_status |

---

## Data Providers

| Provider | Type | Env Var |
|----------|------|---------|
| Massive (700+ endpoints) | Universal | `MASSIVE_API_KEY` |
| Polygon | Stocks/Options/Crypto | `POLYGON_API_KEY` |
| Yahoo Finance | Free OHLCV | Built-in |
| Alpha Vantage | Fundamentals | `ALPHA_VANTAGE_KEY` |
| Finnhub | News/Sentiment | `FINNHUB_KEY` |
| Twelve Data | Multi-asset | `TWELVE_DATA_KEY` |

---

## Memory Safety

V20 includes memory-safe architecture to prevent OOM crashes:

- **Process memory monitoring** — configurable hard limit (default 1GB)
- **Bounded LRU caches** — automatic eviction when full
- **Periodic garbage collection** — runs every 60s
- **Response size guards** — truncates outputs over 1MB
- **Lazy imports** — heavy libs loaded only when needed
- **Concurrency semaphores** — limits parallel tool executions

Set max memory: `ALGOCHAINS_MAX_MEMORY_MB=1024`

---

## Configuration

All settings via environment variables:

```bash
# Broker credentials
ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...
ALPACA_PAPER=true

OANDA_ACCOUNT_ID=...
OANDA_ACCESS_TOKEN=...

IBKR_HOST=127.0.0.1
IBKR_PORT=7497

TRADERSPOST_WEBHOOK_URL=https://traderspost.io/trading/webhook/xxx

# Builder tier
ALGOCHAINS_BUILDER_KEY=...     # $199/mo API key for data warehouse
ALGOCHAINS_GATEWAY_URL=https://algochains.ai/api/v1/data

# Marketplace
LISTING_API_KEY=...
SIGNAL_SECRET=...
ALGOCHAINS_DJANGO_URL=https://algochains.ai

# Server
ALGOCHAINS_TOOL_MODE=smart     # smart (29 tools) or full (275+)
ALGOCHAINS_MAX_MEMORY_MB=1024  # Memory limit
MCP_LOG_LEVEL=INFO
```

---

## Development

### Run from source

```bash
git clone https://github.com/AlgoChains/algochains-mcp-server.git
cd algochains-mcp-server
pip install -e ".[dev,all]"
algochains-mcp
```

### Run tests

```bash
pytest tests/ -v
```

### Project structure

```
src/algochains_mcp/
├── server.py                  # Main MCP server (275+ tools)
├── config.py                  # Configuration
├── errors.py                  # Error hierarchy
├── middleware.py               # Rate limiting, circuit breakers
├── memory_safety.py            # NEW: Memory monitoring & safety
├── account_protection/         # NEW: 13 pre-trade safety guards
│   ├── engine.py              # Guard orchestration & presets
│   └── guards.py             # Individual guard implementations
├── builder_sdk/                # NEW: Builder tier ($199/mo)
│   ├── data_warehouse.py      # Supabase data access (3.09B+ rows)
│   ├── strategy_runner.py     # Backtest engine (Backtrader + built-in)
│   └── submission_pipeline.py # 7-gate marketplace submission
├── brokers/                    # 6 broker connectors
│   ├── alpaca_connector.py
│   ├── ibkr_connector.py
│   ├── oanda_connector.py
│   ├── traderspost_connector.py
│   ├── quantconnect_connector.py
│   └── tradovate.py
├── marketplace/                # Validation & bridge
├── strategy_builder/           # Rust-backed backtesting
├── alpha_engines/              # VWAP, GEX, dark pools, Kelly
├── intent_engine/              # Natural language trading
├── ml_engine/                  # ML model training & inference
├── execution_engine/           # Smart order routing, FIX
├── streaming/                  # Real-time data streams
├── portfolio/                  # Portfolio optimization
└── ... (14 domains total)
```

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│              AI AGENTS                           │
│  Claude · GPT · Gemini · Cursor · Codex · Any   │
│                                                  │
│           MCP Protocol (JSON-RPC 2.0)           │
└──────────────────┬──────────────────────────────┘
                   │
        ┌──────────▼──────────┐
        │  ALGOCHAINS MCP     │
        │  SERVER v20.0       │
        │  275+ tools         │
        │                     │
        │  ┌───────────────┐  │
        │  │ Account       │  │  ← 13 safety guards
        │  │ Protection    │  │
        │  └───────────────┘  │
        │  ┌───────────────┐  │
        │  │ Builder SDK   │  │  ← 3.09B+ rows
        │  │ + Marketplace │  │    7-gate validation
        │  └───────────────┘  │
        │  ┌───────────────┐  │
        │  │ 6 Broker      │  │  ← 15+ brokers
        │  │ Connectors    │  │
        │  └───────────────┘  │
        │  ┌───────────────┐  │
        │  │ Memory Safety │  │  ← OOM prevention
        │  └───────────────┘  │
        └──────────┬──────────┘
                   │
     ┌─────────────┼──────────────┐
     │             │              │
     ▼             ▼              ▼
  Alpaca      TradersPost     OANDA
  IBKR        (10+ brokers    Tradovate
  QuantConn    via webhook)
```

---

## Competitive Advantages

| Feature | AlgoChains | Collective2 | QuantConnect | TradersPost |
|---------|-----------|-------------|--------------|-------------|
| AI access | MCP (universal) | None | LEAN only | Webhooks |
| Brokers | 15+ | 5 | 3 | 10+ |
| Validation | 7-gate MCPT | None | Basic | None |
| Account protection | 13 guards | None | None | None |
| Marketplace | Full | Yes | Yes | No |
| Backtest speed | 7ms (Rust) | — | Seconds | — |
| Open source | MIT | No | Partial | No |

**The moat is the validation pipeline.** Nobody else has MCPT with 1000+ permutations, Deflated Sharpe Ratio correction, walk-forward with 3+ folds, 30-day paper trading graduation, and continuous decay monitoring with auto-delist.

---

## Academic References

1. Bailey & López de Prado (2014). "The Deflated Sharpe Ratio." *J. Portfolio Management*
2. Pardo (2008). *The Evaluation and Optimization of Trading Strategies*. Wiley
3. López de Prado (2018). *Advances in Financial Machine Learning*. Wiley
4. Harvey & Liu (2015). "Backtesting." *J. Portfolio Management*
5. Kelly (1956). "A New Interpretation of Information Rate." *Bell System Technical J.*

---

## License

MIT — see [LICENSE](LICENSE)

</div>
