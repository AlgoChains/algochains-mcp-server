<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://algochains.ai/logo-white.svg">
  <source media="(prefers-color-scheme: light)" srcset="https://algochains.ai/logo-dark.svg">
  <img alt="AlgoChains" src="https://algochains.ai/logo-dark.svg" width="420">
</picture>

<br />

# MCP Server for Algorithmic Trading

**Give any AI agent the ability to trade, build datasets, and deploy validated strategies — across 12+ brokers, 10+ data providers, and every major IDE.**

<br />

[![PyPI](https://img.shields.io/pypi/v/algochains-mcp-server?color=%236C3AED&label=PyPI)](https://pypi.org/project/algochains-mcp-server/)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)
[![MCP](https://img.shields.io/badge/MCP-v1.0-7C3AED?logo=data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMjAiIGhlaWdodD0iMjAiIHZpZXdCb3g9IjAgMCAyMCAyMCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48Y2lyY2xlIGN4PSIxMCIgY3k9IjEwIiByPSI4IiBmaWxsPSJ3aGl0ZSIvPjwvc3ZnPg==)](https://modelcontextprotocol.io)
[![Tests](https://img.shields.io/badge/tests-40%20passing-brightgreen)](#development)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

[Get Started](#get-started) ·
[Why AlgoChains](#why-algochains) ·
[Tools Reference](#tool-reference) ·
[Marketplace](#strategy-marketplace) ·
[Data Providers](#data-providers) ·
[Docs](https://algochains.ai/docs)

</div>

<br />

## Get Started

### Install

```bash
pip install algochains-mcp-server
```

Or with broker SDKs and data provider clients:

```bash
pip install "algochains-mcp-server[all,data-all,datasets]"
```

### Add to your IDE

<details open>
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
        "POLYGON_API_KEY": "..."
      }
    }
  }
}
```

</details>

<details>
<summary><strong>Cursor</strong></summary>

Add to `~/.cursor/mcp.json`:

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

<details>
<summary><strong>Claude Desktop</strong></summary>

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

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

<details>
<summary><strong>VS Code (Copilot / Continue)</strong></summary>

Add to `.vscode/mcp.json`:

```json
{
  "servers": {
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

### Start trading

Once installed, ask your AI agent:

```text
"Buy 10 shares of AAPL on Alpaca"
"What does my portfolio look like across all brokers?"
"Gather my API keys and show me what data I can access"
"Build a daily OHLCV dataset for NVDA with RSI and regime labels"
"Show me the top validated bots on the marketplace"
"Submit my momentum strategy for MCPT validation"
```

<br />

---

## Why AlgoChains

Most MCP servers give AI agents access to a single service. AlgoChains gives them access to the **entire algorithmic trading stack** — broker execution, market data ingestion, strategy validation, portfolio optimization, and a two-sided marketplace — through one local process.

### The problem

Building an AI-powered trading system today means stitching together broker APIs, market data providers, backtesting engines, validation frameworks, and deployment infrastructure. Each has its own SDK, auth model, and failure modes. AI agents can write code that calls these APIs, but they can't **natively interact** with them in real time.

### What this solves

AlgoChains MCP Server exposes **54 tools** across 9 domains that any MCP-compatible AI agent can call directly:

| Domain | What the agent can do | Tools |
|---|---|---|
| **Order Execution** | Place, cancel, and close trades on any connected broker | 4 |
| **Portfolio Intelligence** | View positions, balances, and P&L across all brokers | 4 |
| **Market Data** | Fetch quotes, bars, and news from 10+ providers | 6 |
| **BYOK Key Orchestrator** | Auto-discover, validate, and provision data provider API keys | 6 |
| **Dataset Builder** | Build, enrich, and export ML-ready proprietary datasets | 5 |
| **Strategy Validation** | Submit strategies through 6-gate MCPT institutional validation | 3 |
| **Marketplace** | Browse, verify, subscribe to, and deploy 172+ validated bots | 5 |
| **Streaming** | Subscribe to real-time P&L, fills, positions, and risk events | 4 |
| **Portfolio Optimization** | Run risk parity, Kelly, mean-variance, and max-Sharpe allocation | 6 |
| **Notifications** | Push alerts to Slack, Discord, Telegram, email, and mobile | 7 |
| **Diagnostics** | Server health, connected brokers, error rates | 4 |

> **54 tools · 4 prompts · 4 resources · 39 modules · 7,200+ lines of production code**

<br />

### How it compares

**Broker connectivity:**

No other MCP server connects to more than one broker. AlgoChains normalizes 12+ brokers — including Alpaca, Interactive Brokers, Oanda, Schwab, Robinhood, Tastytrade, and more — behind a single interface.

**BYOK Key Orchestrator (V7):**

No platform — not Composio, Nango, Arcade, or Merge — offers autonomous discovery of existing API keys. They all require manual entry. AlgoChains scans your environment variables, `.env` files, IDE configs, shell profiles, and `~/.config/` directories automatically. It validates each key with live API calls to check permissions, rate limits, and plan tiers — then shows exactly what data you can access and what you're missing.

| Capability | Composio | Nango | Arcade | **AlgoChains** |
|---|---|---|---|---|
| Key entry | Manual | Manual | Manual | **Autonomous** |
| Auto-discovery | No | No | No | **Yes** |
| Deep validation | No | Basic | No | **Live API calls** |
| Gap analysis | No | No | No | **Yes** |
| Dataset pipeline | No | No | No | **Yes** |
| ML integration | No | No | No | **Yes** |

**Strategy validation:**

Every bot on the AlgoChains marketplace passes a 6-gate institutional-grade validation pipeline: schema checks, performance thresholds, overfitting detection, MCPT permutation testing (p < 0.05), walk-forward consistency, and 30-day paper trading. No other marketplace enforces this level of statistical rigor.

<br />

---

## Supported Brokers

| Broker | Connection | Asset Classes |
|---|---|---|
| **Alpaca** | REST API | Stocks, ETFs, Crypto, Options |
| **Interactive Brokers** | TWS Gateway | Stocks, Futures, Options, Forex |
| **Oanda** | REST v20 | Forex (70+ pairs) |
| **Schwab** | via TradersPost | Stocks, Options |
| **Robinhood** | via TradersPost | Stocks, Crypto, Options |
| **Tastytrade** | via TradersPost | Stocks, Options, Futures |
| **TradeStation** | via TradersPost | Stocks, Futures, Options |
| **Tradier** | via TradersPost | Stocks, Options |
| **Coinbase** | via TradersPost | Crypto |
| **Kraken** | via TradersPost | Crypto |
| **QuantConnect** | LEAN API | All (algo deployment) |

> **TradersPost** acts as a webhook router — connect once and reach Schwab, Robinhood, Tastytrade, TradeStation, Tradier, Coinbase, and Kraken through a single integration.

<br />

## Data Providers

AlgoChains pulls market data from 10+ providers. The BYOK Key Orchestrator auto-discovers which keys you already have and validates them with live API calls.

| Provider | Data Types | Free Tier |
|---|---|---|
| **Polygon.io** | Bars, quotes, trades, news, fundamentals | 5 calls/min |
| **Alpha Vantage** | Bars, quotes, fundamentals, forex, crypto | 25 calls/day |
| **Finnhub** | Bars, quotes, news, sentiment, insider trades | 60 calls/min |
| **Twelve Data** | Bars, quotes, 800+ technical indicators | 8 calls/min |
| **Yahoo Finance** | Bars, quotes, fundamentals | No key needed |
| **Databento** | Tick data, L2 order book, trades | Paid |
| **Unusual Whales** | Options flow, dark pool, GEX | Paid |
| **Intrinio** | Fundamentals, options chains, institutional holdings | Trial |
| **Nasdaq / Quandl** | Economic indicators, macro, alternative data | 50 calls/day |
| **OpenBB** | Aggregated multi-source | Free |

**Quick start:** Say `"gather my keys"` and the agent will scan your environment, show what you have, and guide you to fill gaps — starting with free tiers.

<br />

## Tool Reference

### Order Execution

| Tool | Description |
|---|---|
| `place_order` | Execute a trade on any connected broker. Supports market, limit, stop, stop-limit, and trailing stop orders. |
| `cancel_order` | Cancel an open order by ID. |
| `close_position` | Close entire position in a symbol. |
| `close_all_positions` | Emergency: close all open positions on a broker. |

<details>
<summary>Example: <code>place_order</code></summary>

```json
{
  "broker": "alpaca",
  "symbol": "AAPL",
  "side": "buy",
  "qty": 10,
  "order_type": "limit",
  "limit_price": 185.50,
  "time_in_force": "day"
}
```

</details>

### Portfolio & Account

| Tool | Description |
|---|---|
| `get_account` | Account balances, equity, buying power for a specific broker. |
| `get_positions` | Current open positions with unrealized P&L. |
| `get_orders` | Recent order history with fill status. |
| `get_portfolio_summary` | Aggregated view across all connected brokers. |

### Market Data

| Tool | Description |
|---|---|
| `get_quote` | Real-time bid/ask/last for any symbol. |
| `list_data_providers` | Show all available data providers and their status. |
| `provider_health_check` | Test connectivity and rate limit status for each provider. |
| `get_bars` | Historical OHLCV bars from the best available provider. |
| `get_news` | Market news and sentiment for a symbol. |
| `search_symbols` | Symbol lookup across all connected providers. |

### BYOK Key Orchestrator

| Tool | Description |
|---|---|
| `discover_keys` | Scan env vars, `.env` files, IDE configs, shell profiles, and `~/.config/` for existing API keys. |
| `validate_keys` | Deep-validate all discovered keys with live API calls. Returns permissions, rate limits, plan tier. |
| `key_gap_analysis` | What you have, what you're missing, what each missing key unlocks, and signup URLs. |
| `provision_key` | Write a new key to `.env` and validate it. |
| `key_health` | Real-time health check of all configured keys. |
| `export_config` | Export validated key config as `.env`, JSON, or IDE MCP config format. |

### Dataset Builder

| Tool | Description |
|---|---|
| `build_dataset` | Build a proprietary dataset for a symbol/timeframe using all available providers. |
| `list_datasets` | Inventory of built datasets with row count, columns, date range, and sources. |
| `dataset_status` | Coverage map — what you can build vs. what requires additional keys. |
| `enrich_dataset` | Add technical indicators, sentiment, regime labels, or volume profile to an existing dataset. |
| `export_dataset` | Export as ML-ready train/test split with anti-leakage guarantees. |

### Strategy Marketplace

| Tool | Description |
|---|---|
| `browse_marketplace` | Search validated bots by asset class, Sharpe ratio, tier, or strategy type. |
| `get_listing_detail` | Full details for a specific bot including backtest results and live metrics. |
| `subscribe_to_bot` | Subscribe to a bot and deploy it locally or in the cloud. |
| `verify_bot_metrics` | Audit a bot's performance claims — returns SHA-256 hashes and broker fill IDs. |
| `get_live_performance` | Real-time P&L and trade log for a live bot. |

### Strategy Validation

| Tool | Description |
|---|---|
| `submit_strategy` | Submit a strategy for 6-gate MCPT validation. |
| `check_validation_status` | Check progress through the validation pipeline. |
| `get_validation_gates` | View gate requirements and which ones have passed. |

### Streaming (V4)

| Tool | Description |
|---|---|
| `subscribe_pnl` | Real-time P&L stream via WebSocket. |
| `subscribe_fills` | Live fill notifications as orders execute. |
| `subscribe_positions` | Position change events. |
| `subscribe_risk_alerts` | Drawdown, margin, and risk threshold alerts. |

### Portfolio Optimization (V5)

| Tool | Description |
|---|---|
| `optimize_portfolio` | Run portfolio optimization across multiple strategies. |
| `get_efficient_frontier` | Compute the efficient frontier for a set of strategies. |
| `risk_parity_allocation` | Equal risk contribution across strategies. |
| `kelly_allocation` | Kelly criterion sizing based on historical edge. |
| `max_sharpe_allocation` | Maximize portfolio Sharpe ratio. |
| `min_variance_allocation` | Minimize portfolio variance. |

### Notifications (V6)

| Tool | Description |
|---|---|
| `send_notification` | Push a message to any configured channel. |
| `configure_alerts` | Set up alert rules (drawdown, fill, P&L threshold). |
| `list_channels` | Show configured notification channels and their status. |

### Diagnostics

| Tool | Description |
|---|---|
| `server_diagnostics` | Server health, uptime, connected brokers, error rates. |
| `list_brokers` | Show all configured brokers and connection status. |
| `connect_broker` | Establish connection to a new broker. |
| `broker_health_check` | Test connectivity and auth for a specific broker. |

<br />

---

## Strategy Marketplace

AlgoChains operates a **two-sided marketplace** for validated algorithmic trading strategies.

### For developers (creators)

Build a strategy → backtest with our Rust engine → pass 6-gate MCPT validation → paper trade for 30 days → list on the marketplace → earn revenue from subscribers.

### For subscribers

Browse the marketplace → verify metrics on-chain → subscribe → deploy locally or in the cloud → connect your broker → auto-trade.

### Validation pipeline

Every strategy listed on the marketplace must pass all applicable gates:

| Gate | Check | Threshold |
|---|---|---|
| **Schema** | Required fields present and correctly typed | Pass/fail |
| **Performance** | Out-of-sample Sharpe, trade count, drawdown | Sharpe ≥ 1.0, trades ≥ 50, DD ≤ 40% |
| **Overfitting** | In-sample vs. out-of-sample ratio | IS/OOS ≥ 0.5, IS Sharpe ≤ 8.0 |
| **MCPT** | Permutation test on returns | p-value ≤ 0.05 (1,000 permutations) |
| **Walk-Forward** | Consistency across time folds | ≥ 3 folds with positive OOS Sharpe |
| **Paper Trading** | Forward test on live market data | 30 days, ≥ 50 trades |

Strategies that pass are classified into tiers:

| Tier | Score | What it means |
|---|---|---|
| **Platinum** | ≥ 90 | All 6 gates + live-verified broker fill IDs + SHA-256 trade log |
| **Gold** | ≥ 70 | Gates 1–5 + paper trading complete |
| **Silver** | ≥ 50 | Gates 1–4, walk-forward optional |
| **Bronze** | ≥ 30 | Gates 1–2, MCPT recommended |

<br />

---

## Security

AlgoChains is designed around one principle: **your credentials never leave your machine.**

- **Broker API keys** are stored locally in `.env` and used by the local MCP process only. They are never transmitted to AlgoChains servers.
- **Data provider keys** discovered by the BYOK orchestrator are masked in all output (first 4 + last 4 characters only) and never logged or transmitted.
- **Metrics only** — the only data sent to AlgoChains cloud is opt-in performance metrics (P&L, Sharpe, trade count) signed with SHA-256, used to verify marketplace listings.
- **Authentication** uses Supabase with Google SSO. Row-level security ensures users see only their own data.
- **Transport** is HTTPS/TLS 1.3 for all cloud communication. The MCP server itself communicates with your IDE over local stdio — no network involved.

<br />

---

## Deployment Modes

### Local execution

Run the bot on your own machine. Your broker keys stay local. Only signed performance metrics are optionally sent to the marketplace.

```bash
pip install "algochains-mcp-server[all]"
cp .env.example .env   # Add your broker credentials
algochains-mcp          # Start the MCP server
```

### Cloud deployment

For users who don't want to manage infrastructure. AlgoChains runs the bot in an isolated Kubernetes pod. Broker credentials are stored in a managed secret vault (GCP Secret Manager) and encrypted at rest.

### Hybrid (recommended for creators)

Run bots locally with full control. Publish verified metrics to the marketplace so subscribers can audit your track record.

<br />

---

## Development

```bash
git clone https://github.com/AlgoChains/algochains-mcp-server.git
cd algochains-mcp-server
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,all,data-all,datasets]"
cp .env.example .env
```

### Run tests

```bash
pytest                   # 40 tests
pytest -v --tb=short     # verbose output
ruff check src/          # lint
```

### Run locally

```bash
algochains-mcp                       # via entry point
python -m algochains_mcp.server      # or directly
```

### Project layout

```text
src/algochains_mcp/
├── server.py                 # MCP entry point — 54 tools, prompts, resources
├── config.py                 # Environment and broker configuration
├── errors.py                 # Typed error hierarchy (15 classes)
├── middleware.py              # Rate limiting, retry with backoff, structured logging
├── auth/                     # Supabase SSO + API key scoping
├── brokers/                  # Alpaca, IBKR, Oanda, TradersPost, QuantConnect
├── marketplace/              # Django bridge + 6-gate MCPT validator
├── data_providers/           # Polygon, Yahoo, Alpha Vantage, Finnhub, Twelve Data
├── byok/                     # Key Orchestrator — discovery, validation, provisioning
├── datasets/                 # Dataset Builder — ingest, normalize, enrich, export
├── streaming/                # WebSocket streaming — P&L, fills, positions, risk
├── portfolio/                # Portfolio optimizer — risk parity, Kelly, mean-variance
└── notifications/            # Push notifications — Slack, Discord, Telegram, email, FCM
```

<br />

---

## Roadmap

| Version | Status | What shipped |
|---|---|---|
| **V1** | ✅ Shipped | Core broker connectors (Alpaca, IBKR, Oanda, TradersPost, QuantConnect) + MCPT strategy validation |
| **V2** | ✅ Shipped | Marketplace bridge, server diagnostics, AI prompts |
| **V3** | ✅ Shipped | Supabase SSO, deployment modes, metrics verification, IDE configs |
| **V4** | ✅ Shipped | WebSocket streaming — real-time P&L, fills, positions, risk alerts |
| **V5** | ✅ Shipped | Portfolio optimizer — risk parity, Kelly, mean-variance, max Sharpe, min variance |
| **V6** | ✅ Shipped | Push notifications — Slack, Discord, Telegram, email, Firebase Cloud Messaging |
| **V7** | ✅ Shipped | BYOK Key Orchestrator + Proprietary Dataset Builder |
| **V8** | 🔧 Next | **Strategy Builder SDK** — natural language → StrategySpec → backtest → deploy pipeline (8 tools). **Social Trading** — copy-trading with proportional scaling, leader ranking, multi-broker mirroring (6 tools). **Community Signals** — pub/sub signal bus with verified trades, consensus engine, accuracy scoring (5 tools). [Blueprint →](docs/V8_STRATEGY_BUILDER_SOCIAL_TRADING_BLUEPRINT.md) |
| **V9** | 📋 Planned | **Risk Dashboard** — VaR (parametric, historical, Monte Carlo), expected shortfall, Barra-style factor decomposition, 7 pre-built stress scenarios, real-time drawdown/margin/Greeks monitoring (10 tools). **Compliance Module** — SEC/FINRA pre-trade checks, wash trade detection, kill switch, immutable audit trail, best execution reporting (8 tools). **Multi-Tenant White-Label** — tenant-isolated infrastructure with RLS, sub-account management, branded experiences, Stripe billing, broker routing (7 tools). [Blueprint →](docs/V9_RISK_DASHBOARD_COMPLIANCE_WHITELABEL_BLUEPRINT.md) |

<br />

---

## Contributing

We welcome contributions. Please open an issue first to discuss what you'd like to change.

1. Fork the repository
2. Create a feature branch: `git checkout -b feat/your-feature`
3. Make your changes and add tests
4. Run `pytest && ruff check src/`
5. Open a pull request

<br />

## License

MIT — see [LICENSE](LICENSE) for details.

<br />

---

<div align="center">

**[algochains.ai](https://algochains.ai)** · [Marketplace](https://algochains.ai/marketplace) · [Documentation](https://algochains.ai/docs) · [Discord](https://discord.gg/algochains)

<sub>Built in San Francisco · Real strategies, real fills, real metrics.</sub>

</div>
