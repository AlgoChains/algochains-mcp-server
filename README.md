<div align="center">

# AlgoChains MCP Server

### The universal AI-to-broker trading protocol

**Trade on any broker. Validate any strategy. Deploy from any IDE.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org)
[![MCP](https://img.shields.io/badge/protocol-MCP-purple.svg)](https://modelcontextprotocol.io)
[![Tests](https://img.shields.io/badge/tests-40%2F40-brightgreen.svg)]()
[![Version](https://img.shields.io/badge/version-3.0.0-blue.svg)]()

[Quick Start](#quick-start) · [IDE Setup](#ide-integration) · [Architecture](#architecture) · [Deployment Modes](#deployment-modes) · [Marketplace](#marketplace) · [Authentication](#authentication) · [API Reference](#api-reference)

</div>

---

## What Is This?

AlgoChains MCP Server is a **Model Context Protocol server** that gives any AI agent — Claude, GPT, Gemini, Cascade, Devin, Copilot, or your own — the ability to:

1. **Trade on 12+ brokers** through a single normalized interface
2. **Submit strategies** for institutional-grade MCPT validation (6 gates)
3. **Browse and subscribe** to validated bots on the AlgoChains marketplace
4. **Verify bot performance** with cryptographically auditable metrics
5. **Deploy bots locally** on your machine or to AlgoChains cloud

This is the **connective tissue** between AI coding assistants and live financial markets.

---

## Quick Start

### Install

```bash
pip install algochains-mcp-server
# or with all broker SDKs:
pip install "algochains-mcp-server[all]"
```

### Configure Your IDE

<details>
<summary><b>Windsurf / Cascade</b></summary>

Add to `~/.windsurf/mcp-config.json`:
```json
{
  "mcpServers": {
    "algochains": {
      "command": "algochains-mcp",
      "env": {
        "ALGOCHAINS_API_KEY": "your_key_from_algochains_ai",
        "ALPACA_API_KEY": "your_alpaca_key",
        "ALPACA_SECRET_KEY": "your_alpaca_secret"
      }
    }
  }
}
```
</details>

<details>
<summary><b>Cursor</b></summary>

Add to `~/.cursor/mcp.json`:
```json
{
  "mcpServers": {
    "algochains": {
      "command": "algochains-mcp",
      "env": {
        "ALGOCHAINS_API_KEY": "your_key_from_algochains_ai",
        "ALPACA_API_KEY": "your_alpaca_key",
        "ALPACA_SECRET_KEY": "your_alpaca_secret"
      }
    }
  }
}
```
</details>

<details>
<summary><b>Claude Desktop</b></summary>

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):
```json
{
  "mcpServers": {
    "algochains": {
      "command": "algochains-mcp",
      "env": {
        "ALGOCHAINS_API_KEY": "your_key_from_algochains_ai",
        "ALPACA_API_KEY": "your_alpaca_key",
        "ALPACA_SECRET_KEY": "your_alpaca_secret"
      }
    }
  }
}
```
</details>

<details>
<summary><b>VS Code (Copilot / Continue)</b></summary>

Add to `.vscode/mcp.json` in your workspace:
```json
{
  "servers": {
    "algochains": {
      "command": "algochains-mcp",
      "env": {
        "ALGOCHAINS_API_KEY": "your_key_from_algochains_ai"
      }
    }
  }
}
```
</details>

### Try It

Once configured, ask your AI assistant:

```
"Show me the best validated trading bots on AlgoChains"
"Place a paper trade: buy 10 shares of AAPL on Alpaca"
"Submit my RSI strategy for MCPT validation"
"What's my portfolio summary across all brokers?"
```

---

## Supported Brokers

| Broker | Type | Asset Classes | Connection | Rate Limit |
|--------|------|---------------|------------|------------|
| **Alpaca** | Direct API | Stocks, ETFs, Crypto, Options | API Key | 200/min |
| **Interactive Brokers** | TWS/Gateway | Stocks, Futures, Options, Forex | TWS Connection | 50/min |
| **Oanda** | REST v20 | Forex (70+ pairs) | Access Token | 120/min |
| **TradersPost.io** | Webhook Router | Everything below | Webhook URL | 30/min |
| ↳ Schwab | via TradersPost | Stocks, Options | | |
| ↳ Robinhood | via TradersPost | Stocks, Crypto, Options | | |
| ↳ Tastytrade | via TradersPost | Stocks, Options, Futures | | |
| ↳ TradeStation | via TradersPost | Stocks, Futures, Options | | |
| ↳ Tradier | via TradersPost | Stocks, Options | | |
| ↳ Coinbase | via TradersPost | Crypto | | |
| ↳ Kraken | via TradersPost | Crypto | | |
| **QuantConnect** | LEAN API | All (algo deployment) | API Token | 20/min |

---

## IDE Integration

The AlgoChains MCP Server runs as a **local process** on the developer's or subscriber's machine. It communicates with AI agents via the MCP stdio protocol — no cloud intermediary for the agent-to-server path.

### How It Works in Your IDE

```
┌──────────────────────────────────────────────────────┐
│  YOUR IDE (Windsurf / Cursor / Claude Desktop)       │
│                                                      │
│  AI Agent  ──── MCP stdio ────  algochains-mcp       │
│  (Claude,       (local)         (local process)      │
│   GPT, etc.)                         │               │
│                                      │               │
│                          ┌───────────┼───────────┐   │
│                          │           │           │   │
│                          ▼           ▼           ▼   │
│                     Your Broker  AlgoChains   Metrics │
│                     (Alpaca,     Cloud API    Verify  │
│                      IBKR,etc)  (algochains   (proof  │
│                                  .ai)         chain)  │
└──────────────────────────────────────────────────────┘
```

### What the AI Can Do

| Domain | Tools | Description |
|--------|-------|-------------|
| **Trading** | `place_order`, `cancel_order`, `close_position`, `close_all_positions` | Execute trades on any connected broker |
| **Portfolio** | `get_account`, `get_positions`, `get_orders`, `get_portfolio_summary` | View holdings across all brokers |
| **Market Data** | `get_quote` | Real-time bid/ask/last quotes |
| **Broker Mgmt** | `list_brokers`, `connect_broker`, `broker_health_check` | Connection management |
| **Marketplace** | `browse_marketplace`, `get_listing_detail`, `subscribe_to_bot` | Browse and subscribe to validated bots |
| **Strategy** | `submit_strategy`, `check_validation_status`, `get_validation_gates` | Submit strategies for MCPT validation |
| **Verification** | `verify_bot_metrics`, `get_live_performance` | Cryptographic performance proof |
| **Deployment** | `deploy_bot_local`, `deploy_bot_cloud` | Deploy subscribed bots |
| **Auth** | `authenticate`, `get_session` | Supabase SSO + API key auth |
| **Diagnostics** | `server_diagnostics` | Health, stats, error rates |

---

## Architecture

### System Overview

```
                        ┌─────────────────────────────────────┐
                        │     AlgoChains Cloud (algochains.ai)│
                        │                                     │
                        │  ┌──────────┐  ┌────────────────┐  │
                        │  │ Supabase │  │ Django REST API │  │
                        │  │ Auth/SSO │  │  /api/v1/...    │  │
                        │  └──────────┘  └────────────────┘  │
                        │  ┌──────────┐  ┌────────────────┐  │
                        │  │ Metrics  │  │  Marketplace   │  │
                        │  │ Ingest   │  │  Listings DB   │  │
                        │  └──────────┘  └────────────────┘  │
                        └────────────┬────────────────────────┘
                                     │ HTTPS (authenticated)
          ┌──────────────────────────┼──────────────────────────┐
          │                          │                          │
    ┌─────┴─────┐            ┌───────┴──────┐          ┌───────┴──────┐
    │ DEVELOPER │            │  SUBSCRIBER  │          │  SUBSCRIBER  │
    │ (Creator) │            │  (Local Box) │          │   (Cloud)    │
    │           │            │              │          │              │
    │ IDE +     │            │ IDE +        │          │ AlgoChains   │
    │ MCP Server│            │ MCP Server   │          │ Managed      │
    │ + Broker  │            │ + Broker     │          │ Deployment   │
    │ Keys      │            │ Keys         │          │              │
    └───────────┘            └──────────────┘          └──────────────┘
```

### Two-Sided Marketplace Model

**Creators (Developers)** build, validate, and publish strategies:
```
Build Strategy → Rust Backtest Engine → 6-Gate MCPT Validation
    → Paper Trading (30 days) → Marketplace Listing → Earn Revenue
```

**Subscribers (Consumers)** discover, verify, and deploy bots:
```
Browse Marketplace → Verify Metrics (auditable) → Subscribe
    → Deploy (local or cloud) → Connect Broker → Auto-Trade
```

### Infrastructure Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **AI Protocol** | MCP (stdio) | Agent ↔ Server communication |
| **Server Runtime** | Python 3.11+ / asyncio | MCP tool handling |
| **Broker Layer** | httpx + broker SDKs | Normalized broker interface |
| **Auth** | Supabase (Google SSO + JWT) | User identity + API keys |
| **Backend** | Django REST Framework | Marketplace API + listings DB |
| **Database** | PostgreSQL (via Supabase) | Users, listings, subscriptions, metrics |
| **Backtest Engine** | Rust (compiled native) | High-performance strategy validation |
| **Monitoring** | OpenClaw + Slack | Autonomous health + alerts |
| **CI/CD** | GitHub Actions | Test, lint, build, publish to PyPI |

---

## Deployment Modes

AlgoChains supports three deployment modes for subscribed bots:

### Mode 1: Local Execution (Customer's Machine)

The subscriber runs the bot on their own hardware. **Their broker API keys never leave their machine.**

```
┌─ Subscriber's Machine ──────────────────────────┐
│                                                  │
│  algochains-mcp (MCP Server)                     │
│     │                                            │
│     ├── Bot Runtime (subscribed strategy)        │
│     │     └── Signal → Order → Broker API        │
│     │                                            │
│     ├── Metrics Reporter                         │
│     │     └── SHA-256 signed P&L → Cloud         │
│     │                                            │
│     └── .env (broker keys — LOCAL ONLY)          │
│                                                  │
└──────────────────────────────────────────────────┘
         │
         │ HTTPS (metrics only, no keys)
         ▼
   algochains.ai/api/v1/metrics/ingest/
```

**Setup:**
```bash
pip install "algochains-mcp-server[all]"
cp .env.example .env
# Edit .env with your broker credentials
algochains-mcp  # starts local MCP server
```

### Mode 2: AlgoChains Cloud Deployment

For subscribers who don't want to run infrastructure. AlgoChains manages the bot process in our cloud — subscriber provides broker OAuth tokens via secure vault.

```
┌─ AlgoChains Cloud ──────────────────────────────┐
│                                                  │
│  Kubernetes Pod (isolated per subscriber)        │
│     ├── Bot Runtime                              │
│     ├── Broker Connector (encrypted creds)       │
│     └── Metrics Reporter (real-time)             │
│                                                  │
│  Secret Vault (HashiCorp / GCP Secret Manager)   │
│     └── Broker OAuth tokens (encrypted at rest)  │
│                                                  │
└──────────────────────────────────────────────────┘
```

### Mode 3: Hybrid (Recommended for Developers)

Developers run bots locally with full control, while publishing verified metrics to the marketplace for subscribers.

```
┌─ Developer's Machine ───────────────────────────┐
│  Live Bot Process → Real Fills → Real P&L       │
│     │                                            │
│     └── Metrics Pusher (every 60s)               │
│           └── SHA-256 hash of trade log          │
│           └── Broker-confirmed fill IDs          │
│           └── POST /api/v1/metrics/ingest/       │
└──────────────────────────────────────────────────┘
```

---

## Marketplace

### Featured Bot Listings

These are **live, validated bots** running real capital with verified metrics:

| Bot | Asset | Strategy | OOS Sharpe | Tier | Status |
|-----|-------|----------|-----------|------|--------|
| **MNQ Momentum Scalper** | MNQ (Micro Nasdaq) | Momentum + Volume | 4.61 | Platinum | Live |
| **CL Energy Scalper** | CL (Crude Oil) | Breakout + Order Flow | 3.12 | Gold | Live |
| **MES Swing Trader** | MES (Micro S&P) | Trend Following | 2.88 | Gold | Live |
| **NQ Swing Trader** | NQ (Nasdaq 100) | Multi-Timeframe | 2.65 | Gold | Live |
| **GBPUSD Forex** | GBPUSD | Breakout + Macro | 1.84 | Silver | Live |
| **QQQ Equity Momentum** | QQQ | RSI + Momentum | 1.63 | Silver | Live |

### How Metrics Are Verified

Every metric on the marketplace is backed by a **verification chain**:

```
1. Rust Backtest Engine runs strategy on historical data
   └── Walk-forward validated across 4+ time periods
   └── MCPT permutation test (1000+ permutations, p < 0.05)

2. Paper Trading phase (30 days, 50+ trades)
   └── Real market conditions, simulated fills
   └── Tracked in Supabase with timestamps

3. Live Trading (optional, highest tier)
   └── Broker-confirmed fill IDs
   └── SHA-256 hash of trade log
   └── Hourly metrics push to marketplace API
```

### Strategy Validation Gates

```
Gate 1: Schema       — Required fields present and valid
Gate 2: Performance  — OOS Sharpe ≥ 1.0, trades ≥ 50, drawdown ≤ 40%
Gate 3: Overfitting  — IS/OOS ratio ≥ 0.5, IS Sharpe ≤ 8.0
Gate 4: MCPT         — Permutation test p-value ≤ 0.05 (1000 permutations)
Gate 5: Walk-Forward — Minimum 3 folds with consistent OOS performance
Gate 6: Paper Trading — 30 days, 50+ trades on paper before live listing
```

**Tier Classification:**

| Tier | Score | Requirements |
|------|-------|-------------|
| **Platinum** | ≥ 90 | All 6 gates pass + live verified metrics |
| **Gold** | ≥ 70 | Gates 1-5 pass + paper trading complete |
| **Silver** | ≥ 50 | Gates 1-4 pass, WF optional |
| **Bronze** | ≥ 30 | Gates 1-2 pass, MCPT recommended |

---

## Authentication

AlgoChains uses **Supabase** for authentication with **Google SSO** as the primary flow:

### For Subscribers (End Users)

```
1. Visit algochains.ai → "Sign in with Google"
2. Supabase issues JWT
3. JWT is used for:
   - Browsing marketplace
   - Subscribing to bots
   - Viewing personal dashboard
4. On subscribe, user gets ALGOCHAINS_API_KEY
5. API key goes into MCP server .env
```

### For Developers (Creators)

```
1. Visit algochains.ai → "Sign in with Google" → Enable Developer Mode
2. Get additional credentials:
   - LISTING_API_KEY (publish listings)
   - METRICS_INGEST_API_KEY (push live metrics)
3. Set ALGOCHAINS_CREATOR_USERNAME in .env
4. Submit strategies via MCP server tools
```

### API Key Scopes

| Key | Scope | Who Gets It |
|-----|-------|-------------|
| `ALGOCHAINS_API_KEY` | Read marketplace, subscribe, deploy | All authenticated users |
| `LISTING_API_KEY` | Create/update listings, publish strategies | Developers only |
| `METRICS_INGEST_API_KEY` | Push live metrics, update performance | Developers only |

### Security Model

- **Broker credentials** stay on user's local machine (never sent to AlgoChains cloud)
- **Supabase JWT** for web UI + marketplace browsing
- **API keys** for server-to-server (MCP → Django REST)
- **All transport** over HTTPS/TLS 1.3
- **Row-level security** in Supabase (users see only their data)

---

## API Reference

### Trading Tools

<details>
<summary><code>place_order</code> — Execute a trade on any broker</summary>

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

Supports: `market`, `limit`, `stop`, `stop_limit`, `trailing_stop`
</details>

<details>
<summary><code>cancel_order</code> — Cancel an open order</summary>

```json
{ "broker": "alpaca", "order_id": "abc-123" }
```
</details>

<details>
<summary><code>close_position</code> — Close entire position in a symbol</summary>

```json
{ "broker": "alpaca", "symbol": "AAPL" }
```
</details>

<details>
<summary><code>close_all_positions</code> — Emergency close all positions</summary>

```json
{ "broker": "alpaca" }
```
</details>

### Portfolio Tools

<details>
<summary><code>get_portfolio_summary</code> — Cross-broker portfolio view</summary>

Returns equity, cash, positions, unrealized P&L across all connected brokers.
</details>

### Marketplace Tools

<details>
<summary><code>browse_marketplace</code> — Find validated bots</summary>

```json
{
  "asset_class": "futures",
  "min_sharpe": 2.0,
  "limit": 10
}
```
</details>

<details>
<summary><code>subscribe_to_bot</code> — Subscribe and deploy</summary>

```json
{
  "slug": "mnq-momentum-scalper",
  "broker": "alpaca",
  "mode": "paper"
}
```
</details>

### Strategy Validation

<details>
<summary><code>submit_strategy</code> — Submit for MCPT validation</summary>

```json
{
  "symbol": "AAPL",
  "strategy_type": "momentum",
  "timeframe": "hour",
  "oos_sharpe": 2.15,
  "oos_trades": 156,
  "max_drawdown_pct": 12.5,
  "is_sharpe": 2.8,
  "mcpt": { "p_value": 0.012, "permutations": 1000 },
  "walk_forward": { "folds": 5, "avg_oos_sharpe": 1.95 }
}
```
</details>

### Verification Tools

<details>
<summary><code>verify_bot_metrics</code> — Audit a bot's performance claims</summary>

```json
{ "slug": "mnq-momentum-scalper" }
```

Returns: Verification chain, SHA-256 hashes, broker fill IDs, methodology.
</details>

---

## Environment Variables

```bash
# ═══════════════════════════════════════════════════════════
# AlgoChains Platform (required for marketplace features)
# ═══════════════════════════════════════════════════════════
ALGOCHAINS_API_KEY=              # Your AlgoChains API key (from algochains.ai dashboard)
ALGOCHAINS_DJANGO_URL=https://algochains.ai
ALGOCHAINS_CREATOR_USERNAME=     # Your marketplace username (developers only)
LISTING_API_KEY=                 # Publishing key (developers only)
METRICS_INGEST_API_KEY=          # Metrics push key (developers only)
SUPABASE_URL=                    # Auto-configured by AlgoChains
SUPABASE_ANON_KEY=               # Auto-configured by AlgoChains

# ═══════════════════════════════════════════════════════════
# Broker Credentials (YOUR keys, stay on YOUR machine)
# ═══════════════════════════════════════════════════════════

# Alpaca (Direct API — stocks, crypto, options)
ALPACA_API_KEY=
ALPACA_SECRET_KEY=
ALPACA_BASE_URL=https://paper-api.alpaca.markets  # or https://api.alpaca.markets

# Interactive Brokers (TWS must be running)
IBKR_HOST=127.0.0.1
IBKR_PORT=7497
IBKR_CLIENT_ID=1

# Oanda (Forex — 70+ pairs)
OANDA_ACCOUNT_ID=
OANDA_ACCESS_TOKEN=
OANDA_ENVIRONMENT=practice  # or live

# TradersPost.io (Webhook → Schwab, Robinhood, Tastytrade, etc.)
TRADERSPOST_WEBHOOK_URL=
TRADERSPOST_API_KEY=

# QuantConnect (LEAN algo platform)
QUANTCONNECT_USER_ID=
QUANTCONNECT_API_TOKEN=
```

---

## Development

### Local Setup

```bash
git clone https://github.com/AlgoChains/algochains-mcp-server.git
cd algochains-mcp-server
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,all]"
cp .env.example .env
# Edit .env with your credentials
```

### Run Tests

```bash
pytest                    # 40/40 tests pass
pytest -v --tb=short      # verbose
ruff check src/           # lint
```

### Run the Server

```bash
algochains-mcp            # via entry point
# or
python -m algochains_mcp.server
```

### Project Structure

```
algochains-mcp-server/
├── src/algochains_mcp/
│   ├── server.py              # MCP server entry (25 tools, resources, prompts)
│   ├── config.py              # Environment config (all broker + marketplace)
│   ├── errors.py              # Typed error hierarchy (15 error classes)
│   ├── middleware.py           # Rate limiting, retry, request logging
│   ├── auth/
│   │   ├── supabase_sso.py    # Google SSO via Supabase
│   │   └── api_keys.py        # API key validation + scoping
│   ├── brokers/
│   │   ├── base.py            # Abstract broker interface
│   │   ├── registry.py        # Broker connection registry
│   │   ├── alpaca_conn.py     # Alpaca implementation
│   │   ├── ibkr_conn.py       # IBKR implementation
│   │   ├── oanda_conn.py      # Oanda implementation
│   │   ├── traderspost_conn.py # TradersPost webhook
│   │   └── quantconnect_conn.py # QuantConnect LEAN
│   └── marketplace/
│       ├── bridge.py          # Django REST HTTP client
│       └── validator.py       # 6-gate MCPT strategy validator
├── tests/                     # 40 tests (errors, middleware, bridge, validator)
├── docs/
│   ├── INTEGRATION_README.md  # Backend/frontend integration guide
│   └── COMMS_STRATEGY.md      # Waitlist onboarding playbook
├── .env.example
├── pyproject.toml
└── README.md
```

---

## AlgoChains Infrastructure

For those integrating with the broader AlgoChains ecosystem:

### Backend Services

| Service | URL | Purpose |
|---------|-----|---------|
| **Marketplace Web** | `https://algochains.ai` | Public marketplace + dashboards |
| **REST API** | `https://algochains.ai/api/v1/` | Listings, subscriptions, metrics |
| **Supabase Auth** | `https://<project>.supabase.co` | Google SSO, JWT, user management |
| **Dev Environment** | `http://172.238.57.39:8000` | Roo's Django dev server |

### Live Bot Processes (Tyler's Infra)

| Process | File | Asset | Broker |
|---------|------|-------|--------|
| `FUTURES_SCALPER_UPGRADED.py` | MNQ Scalper | Micro Nasdaq | Tradovate |
| `CL_FUTURES_SCALPER.py` | CL Scalper | Crude Oil | Tradovate |
| `mes_swing_live.py` | MES Swing | Micro S&P | Tradovate |
| `nq_swing_live.py` | NQ Swing | Nasdaq 100 | Tradovate |

### Monitoring Stack

| Component | Frequency | Channel |
|-----------|-----------|---------|
| **Bot Health Monitor** | 5 min | #incident-response |
| **Token Guardian** | 30 min | auto (launchd) |
| **Kill Switch** | 1 min | #incident-response |
| **Bracket Verifier** | 5 min | #incident-response |
| **Flash Crash Detector** | 1 min | #incident-response |
| **Metrics Push** | 60 min | algochains.ai API |

---

## Roadmap

- [x] V1: Core broker connectors + MCPT validation
- [x] V2: Marketplace bridge + diagnostics + prompts
- [x] V3: Auth (Supabase SSO), deployment modes, metrics verification, IDE configs
- [ ] V4: WebSocket streaming (real-time P&L), portfolio analytics
- [ ] V5: Multi-strategy portfolio optimizer, risk parity allocation
- [ ] V6: Mobile companion app, push notifications on fills

---

## Contributing

1. Fork the repo
2. Create a feature branch: `git checkout -b feat/your-feature`
3. Run tests: `pytest`
4. Submit a PR

---

## License

MIT — see [LICENSE](LICENSE) for details.

---

<div align="center">

**Built by [AlgoChains](https://algochains.ai)** · [Marketplace](https://algochains.ai/marketplace) · [Docs](https://algochains.ai/docs) · [Discord](https://discord.gg/algochains)

</div>
