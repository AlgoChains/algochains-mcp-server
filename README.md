# AlgoChains MCP Server

> Universal broker connectors, marketplace integration, and AI strategy hosting via the Model Context Protocol.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org)
[![MCP](https://img.shields.io/badge/protocol-MCP-purple.svg)](https://modelcontextprotocol.io)

## What This Does

This MCP server lets **any AI agent** (Claude, GPT, Gemini, Cascade, Devin, etc.) trade on **any broker** through a single normalized interface, submit strategies for MCPT validation, and publish validated bots to the AlgoChains marketplace.

### Supported Brokers

| Broker | Type | Asset Classes | Connection |
|--------|------|---------------|------------|
| **Alpaca** | Direct API | Stocks, ETFs, Crypto, Options | API Key |
| **Interactive Brokers** | TWS/Gateway | Stocks, Futures, Options, Forex | TWS Connection |
| **Oanda** | REST v20 | Forex | Access Token |
| **TradersPost.io** | Webhook Router | Everything below | Webhook URL |
| ↳ Schwab | via TradersPost | Stocks, Options | |
| ↳ Robinhood | via TradersPost | Stocks, Crypto, Options | |
| ↳ Tastytrade | via TradersPost | Stocks, Options, Futures | |
| ↳ TradeStation | via TradersPost | Stocks, Futures, Options | |
| ↳ Tradier | via TradersPost | Stocks, Options | |
| ↳ Coinbase | via TradersPost | Crypto | |
| ↳ Kraken | via TradersPost | Crypto | |
| **QuantConnect** | LEAN API | All (algo deployment) | API Token |

### 18 MCP Tools Across 5 Domains

- **Trading** — `place_order`, `cancel_order`, `close_position`, `close_all_positions`
- **Portfolio** — `get_account`, `get_positions`, `get_orders`, `get_portfolio_summary`
- **Market Data** — `get_quote`
- **Broker Mgmt** — `list_brokers`, `connect_broker`, `broker_health_check`
- **Marketplace** — `browse_marketplace`, `get_listing_detail`, `subscribe_to_bot`
- **Strategy** — `submit_strategy`, `check_validation_status`, `get_validation_gates`

## Quick Start

```bash
pip install algochains-mcp-server
# or with all broker SDKs:
pip install "algochains-mcp-server[all]"
```

Create a `.env` file with your broker credentials (see `.env.example`), then:

```json
{
  "mcpServers": {
    "algochains": {
      "command": "algochains-mcp",
      "env": {
        "ALPACA_API_KEY": "your_key",
        "ALPACA_SECRET_KEY": "your_secret"
      }
    }
  }
}
```

## Strategy Submission (6 Validation Gates)

```
Gate 1: Schema       — Required fields present and valid
Gate 2: Performance  — OOS Sharpe >= 1.0, trades >= 50, drawdown <= 40%
Gate 3: Overfitting  — IS/OOS ratio >= 0.5, IS Sharpe <= 8.0
Gate 4: MCPT         — Permutation test p-value <= 0.05
Gate 5: Walk-Forward — Minimum 3 folds with consistent OOS performance
Gate 6: Paper Trading — 30 days, 50+ trades on paper before live
```

Strategies that pass are tiered: **Platinum** (>=90), **Gold** (>=70), **Silver** (>=50), **Bronze** (>=30).

## Architecture

```
AI Agent (Claude, GPT, Cascade, Devin, etc.)
        |  MCP Protocol (stdio)
        v
AlgoChains MCP Server
  |-- Trading Tools -----> Normalized Broker Interface
  |-- Portfolio Tools         |
  |-- Market Data             +-- Alpaca (direct API)
  |-- Strategy Validation     +-- IBKR (TWS/Gateway)
  |-- Marketplace Tools       +-- Oanda (REST v20)
                              +-- TradersPost.io (webhook)
                              |     +-- Schwab, Robinhood
                              |     +-- Tastytrade, TradeStation
                              |     +-- Tradier, Coinbase, Kraken
                              +-- QuantConnect (LEAN API)
        |
        v
AlgoChains Marketplace (algochains.ai)
  Listings -> Subscriptions -> Deployments -> Metrics
```

## Development

```bash
git clone https://github.com/AlgoChains/algochains-mcp-server.git
cd algochains-mcp-server
pip install -e ".[dev]"
pytest
```

## License

MIT
