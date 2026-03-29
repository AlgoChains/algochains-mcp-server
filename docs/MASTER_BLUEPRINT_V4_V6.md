# AlgoChains MCP Server — Master Blueprint V4-V6

## Executive Summary

The AlgoChains MCP Server is the **universal AI-to-broker trading protocol** that lets any AI agent (Claude, GPT, Gemini, Llama) execute trades, manage portfolios, and access market data through a single standardized interface.

**Version 6.0.0** completes the full roadmap:

| Version | Feature | Status |
|---------|---------|--------|
| V1 | Core broker connectors + MCPT validation | ✅ Shipped |
| V2 | Marketplace bridge + diagnostics + prompts | ✅ Shipped |
| V3 | Auth (Supabase SSO), deployment modes, metrics, IDE configs | ✅ Shipped |
| **V4** | **WebSocket streaming (real-time P&L, fills, positions)** | ✅ **Shipped** |
| **V5** | **Multi-strategy portfolio optimizer, risk parity allocation** | ✅ **Shipped** |
| **V6** | **Mobile companion hooks, push notifications on fills** | ✅ **Shipped** |
| **Data** | **Pluggable data provider connectors (Polygon, Yahoo, etc.)** | ✅ **Shipped** |

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    AI Agent (Claude, GPT, etc.)              │
│                                                             │
│  "Place a buy order for AAPL" / "Optimize my portfolio"     │
│  "Stream my P&L" / "Get NVDA news from Polygon"             │
└─────────────────────┬───────────────────────────────────────┘
                      │ MCP Protocol (stdio/SSE)
                      ▼
┌─────────────────────────────────────────────────────────────┐
│              AlgoChains MCP Server v6.0.0                    │
│                                                             │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────────┐  │
│  │ Trading  │ │Portfolio │ │Streaming │ │ Data Providers│  │
│  │  Tools   │ │Optimizer │ │ Manager  │ │   Registry    │  │
│  │ (V1-V3)  │ │  (V5)    │ │  (V4)    │ │  (Optional)   │  │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └──────┬────────┘  │
│       │             │            │               │           │
│  ┌────┴─────┐ ┌────┴─────┐ ┌───┴────┐  ┌──────┴────────┐  │
│  │Marketplace│ │Notifier  │ │  Auth  │  │  Middleware    │  │
│  │  Bridge   │ │  (V6)    │ │(V3 SSO)│  │ (Rate/Retry)  │  │
│  └────┬─────┘ └────┬─────┘ └───┬────┘  └──────┬────────┘  │
│       │             │            │               │           │
└───────┼─────────────┼────────────┼───────────────┼───────────┘
        │             │            │               │
        ▼             ▼            ▼               ▼
   ┌─────────┐  ┌──────────┐  ┌────────┐   ┌──────────────┐
   │ Brokers │  │ Channels │  │Supabase│   │Data Providers│
   │         │  │          │  │  JWT   │   │              │
   │ Alpaca  │  │ Slack    │  └────────┘   │ Polygon.io   │
   │ IBKR    │  │ Discord  │               │ Yahoo Finance│
   │ Oanda   │  │ Telegram │               │ Alpha Vantage│
   │ TradersP│  │ Email    │               │ Finnhub      │
   │ QuantC  │  │ FCM/APNS │               │ Twelve Data  │
   └─────────┘  └──────────┘               └──────────────┘
```

---

## V4: Real-Time Streaming Module

### Purpose
Provide AI agents with live market and portfolio data streams so they can make real-time decisions without polling.

### Module: `src/algochains_mcp/streaming/manager.py`

### Topics
| Topic | Description | Use Case |
|-------|-------------|----------|
| `pnl` | Real-time P&L updates | "How much am I up today?" |
| `fills` | Order fill confirmations | "Was my AAPL order filled?" |
| `positions` | Position changes | "What positions do I have open?" |
| `quotes` | Live quotes | "What's the current price of TSLA?" |
| `trades` | Trade execution events | "Show me recent trades" |
| `risk_alerts` | Risk threshold breaches | "Alert me if drawdown exceeds 5%" |
| `order_updates` | Order status changes | "Is my limit order still pending?" |

### MCP Tools
- **`stream_subscribe`** — Subscribe to a topic with optional symbol/broker filters
- **`stream_snapshot`** — Get latest N events from any topic
- **`get_realtime_pnl`** — Live P&L across all brokers + streaming data
- **`stream_stats`** — System diagnostics (buffer sizes, subscription counts)

### Implementation Details
- Ring buffer per topic (configurable size, default 1000 events)
- Callback-based subscription system
- Filter by symbols and/or brokers
- Thread-safe event publishing via asyncio locks
- JSON-serializable events with timestamps

---

## V5: Multi-Strategy Portfolio Optimizer

### Purpose
Automatically allocate capital across marketplace bot subscriptions using quantitative methods.

### Module: `src/algochains_mcp/portfolio/optimizer.py`

### Allocation Methods
| Method | Description | Best For |
|--------|-------------|----------|
| `equal_weight` | 1/N allocation | Baseline comparison |
| `risk_parity` | Weight inversely by volatility | Balanced risk contribution |
| `mean_variance` | Markowitz optimal frontier | Maximum expected utility |
| `kelly` | Kelly criterion sizing | Aggressive growth |
| `max_sharpe` | Maximize risk-adjusted return | Best Sharpe ratio |
| `min_variance` | Minimize portfolio variance | Conservative investors |

### MCP Tools
- **`optimize_portfolio`** — Run a single allocation method
- **`compare_allocations`** — Compare ALL methods side-by-side, ranked by Sharpe

### Input: Bot Metrics
```json
{
  "slug": "mnq-momentum-5min",
  "name": "MNQ Momentum Scalper",
  "oos_sharpe": 3.5,
  "annual_return": 0.45,
  "annual_volatility": 0.12,
  "max_drawdown": 0.08,
  "win_rate": 0.62,
  "avg_trade_pnl": 25.50
}
```

### Output: Allocation Result
```json
{
  "method": "risk_parity",
  "total_capital": 50000,
  "portfolio_sharpe": 2.85,
  "portfolio_return": 0.32,
  "portfolio_volatility": 0.09,
  "portfolio_max_drawdown": 0.065,
  "diversification_score": 78.5,
  "allocations": [
    {"slug": "mnq-momentum-5min", "weight": 0.35, "dollar_amount": 17500},
    {"slug": "cl-breakout-15min", "weight": 0.40, "dollar_amount": 20000},
    {"slug": "spy-swing-daily", "weight": 0.25, "dollar_amount": 12500}
  ]
}
```

### Key Features
- Automatic drawdown constraint enforcement
- Diversification score (0-100)
- Correlation-aware (when data available)
- Tier weighting (Diamond > Gold > Silver > Bronze)

---

## V6: Notification System

### Purpose
Push real-time notifications to any channel when trading events occur.

### Module: `src/algochains_mcp/notifications/push.py`

### Channels
| Channel | Delivery | Auth |
|---------|----------|------|
| Slack | Webhook | `SLACK_WEBHOOK_URL` |
| Discord | Webhook | `DISCORD_WEBHOOK_URL` |
| Telegram | Bot API | `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` |
| Email | SendGrid/Resend | `EMAIL_API_KEY` |
| FCM | Firebase | `FCM_SERVER_KEY` |
| APNS | Apple Push | Certificate |
| WebSocket | In-process | None (always available) |

### Events
| Event | Priority | Use Case |
|-------|----------|----------|
| `order_fill` | High | "Your AAPL buy order filled at $195.50" |
| `daily_pnl` | Medium | "Daily P&L: +$450 (+2.3%)" |
| `drawdown_alert` | Critical | "Drawdown exceeded 5% threshold!" |
| `bot_status` | Low | "MNQ bot is running normally" |
| `margin_warning` | Critical | "Margin call warning: 85% utilized" |
| `risk_alert` | High | "VIX spike detected: 35+" |
| `rebalance_needed` | Medium | "Portfolio drift >5%, rebalance suggested" |

### MCP Tools
- **`configure_notifications`** — Set up any channel
- **`send_notification`** — Send a notification (auto-routes to configured channels)
- **`get_notification_history`** — View past notifications
- **`notification_stats`** — System metrics

---

## Data Providers (Optional Connectors)

### Purpose
Let users plug in their preferred data source for market data, news, fundamentals, and symbol search. All providers implement the same `DataProvider` interface.

### Module: `src/algochains_mcp/data_providers/`

### Available Providers
| Provider | Free Tier | Key Features | Env Var |
|----------|-----------|-------------|---------|
| **Polygon.io** | 5/min | Real-time, WebSocket, options, news | `POLYGON_API_KEY` |
| **Yahoo Finance** | Unlimited | No key needed, fundamentals | None |
| **Alpha Vantage** | 25/day | 110+ technicals, fundamentals | `ALPHA_VANTAGE_API_KEY` |
| **Finnhub** | 60/min | News sentiment, SEC filings, earnings | `FINNHUB_API_KEY` |
| **Twelve Data** | 800/day | 800+ exchanges, WebSocket | `TWELVE_DATA_API_KEY` |

### Auto-Discovery
The `DataProviderRegistry` automatically discovers providers based on environment variables. If you set `POLYGON_API_KEY`, the Polygon provider is available. Yahoo Finance always loads (no key needed).

### MCP Tools
- **`list_data_providers`** — Show configured + all available providers
- **`get_market_data`** — Fetch OHLCV bars from any provider
- **`get_realtime_quote`** — Real-time quote
- **`get_news`** — Financial news (Polygon, Finnhub)
- **`get_fundamentals`** — P/E, EPS, market cap, etc.
- **`search_symbols`** — Find tickers across providers
- **`data_provider_health`** — Health check all providers

### Fallback Chain
If you configure multiple providers, the system uses a priority order:
1. Polygon (paid, real-time)
2. Twelve Data (paid, 800+ exchanges)
3. Finnhub (free, 60/min)
4. Alpha Vantage (free, 25/day)
5. Yahoo Finance (free, no key)

---

## Complete Tool Inventory (43 Tools)

### Trading (V1) — 7 tools
`place_order`, `cancel_order`, `close_position`, `get_account`, `get_positions`, `get_quote`, `get_order_history`

### Broker Management (V1) — 2 tools
`list_brokers`, `connect_broker`

### Marketplace (V2) — 3 tools
`browse_marketplace`, `subscribe_to_bot`, `get_bot_details`

### Strategy Validation (V2) — 3 tools
`submit_strategy`, `validate_strategy`, `get_validation_gates`

### Diagnostics (V3) — 1 tool
`server_diagnostics`

### Streaming (V4) — 4 tools
`stream_subscribe`, `stream_snapshot`, `get_realtime_pnl`, `stream_stats`

### Portfolio Optimizer (V5) — 2 tools
`optimize_portfolio`, `compare_allocations`

### Notifications (V6) — 4 tools
`configure_notifications`, `send_notification`, `get_notification_history`, `notification_stats`

### Data Providers — 7 tools
`list_data_providers`, `get_market_data`, `get_realtime_quote`, `get_news`, `get_fundamentals`, `search_symbols`, `data_provider_health`

### Prompts — 4 prompts
`browse_bots`, `plan_trade`, `risk_check`, `strategy_report`

### Resources — 4 resources
`algochains://portfolio`, `algochains://marketplace`, `algochains://brokers`, `algochains://risk`

---

## Installation & Quick Start

### Minimal (Trading Only)
```bash
pip install algochains-mcp-server
```

### With Data Providers
```bash
pip install "algochains-mcp-server[polygon,yahoo]"
# Or install all data providers:
pip install "algochains-mcp-server[data-all]"
```

### With Everything
```bash
pip install "algochains-mcp-server[auth,data-all,notifications]"
```

### Claude Desktop Config
```json
{
  "mcpServers": {
    "algochains": {
      "command": "algochains-mcp",
      "env": {
        "ALPACA_API_KEY": "your-key",
        "ALPACA_SECRET_KEY": "your-secret",
        "POLYGON_API_KEY": "your-polygon-key",
        "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/..."
      }
    }
  }
}
```

---

## File Structure

```
algochains-mcp-server/
├── src/algochains_mcp/
│   ├── server.py                  # Main MCP server (43 tools, 4 prompts, 4 resources)
│   ├── config.py                  # Configuration dataclasses
│   ├── errors.py                  # Error hierarchy
│   ├── middleware.py              # Rate limiting, retry, logging
│   ├── auth/
│   │   ├── supabase_sso.py       # V3: Supabase JWT auth
│   │   └── api_keys.py           # V3: API key validation
│   ├── brokers/
│   │   ├── registry.py           # Broker connection manager
│   │   ├── alpaca_conn.py        # Alpaca connector
│   │   ├── ibkr_conn.py          # Interactive Brokers
│   │   ├── oanda_conn.py         # Oanda (forex)
│   │   ├── traderspost_conn.py   # TradersPost webhook router
│   │   └── quantconnect_conn.py  # QuantConnect LEAN
│   ├── marketplace/
│   │   ├── bridge.py             # Django API bridge
│   │   └── validator.py          # 6-gate MCPT validation
│   ├── streaming/                # V4: Real-time streaming
│   │   └── manager.py            # StreamManager + ring buffers
│   ├── portfolio/                # V5: Portfolio optimization
│   │   └── optimizer.py          # 6 allocation methods
│   ├── notifications/            # V6: Push notifications
│   │   └── push.py               # Multi-channel dispatcher
│   └── data_providers/           # Optional data connectors
│       ├── base.py               # DataProvider ABC + types
│       ├── registry.py           # Auto-discovery registry
│       ├── polygon_provider.py   # Polygon.io
│       ├── yahoo_provider.py     # Yahoo Finance
│       ├── alpha_vantage_provider.py  # Alpha Vantage
│       ├── finnhub_provider.py   # Finnhub
│       └── twelve_data_provider.py    # Twelve Data
├── tests/
│   ├── test_validator.py
│   ├── test_errors.py
│   ├── test_middleware.py
│   └── test_bridge.py
├── docs/
│   ├── MASTER_BLUEPRINT_V4_V6.md # This document
│   ├── COMMS_STRATEGY.md
│   └── INTEGRATION_README.md
├── pyproject.toml                # v6.0.0
├── README.md
└── .env.example
```

---

## Competitive Positioning

### vs. Other Trading MCP Servers
| Feature | AlgoChains | Generic MCP | Manual Broker SDK |
|---------|-----------|------------|-------------------|
| Multi-broker | ✅ 5 brokers | ❌ 1 broker | ❌ Manual |
| Portfolio optimizer | ✅ 6 methods | ❌ | ❌ |
| Strategy validation | ✅ 6-gate MCPT | ❌ | ❌ |
| Marketplace | ✅ 172+ bots | ❌ | ❌ |
| Data providers | ✅ 5 providers | ❌ | ❌ |
| Streaming | ✅ 7 topics | ❌ | ❌ |
| Notifications | ✅ 7 channels | ❌ | ❌ |
| Auth | ✅ SSO + API keys | ❌ | ❌ |
| IDE integration | ✅ All major IDEs | Partial | ❌ |

### Unique Value
1. **Only MCP server with integrated marketplace** — browse, validate, subscribe to algo strategies
2. **Only one with portfolio optimization** — AI agents can auto-allocate capital
3. **Most broker coverage** — 5 direct + TradersPost routes to 9 more
4. **Pluggable data providers** — use free or premium data, your choice
5. **Production-grade** — rate limiting, retry, structured errors, auth

---

## Next Steps (Post-V6)

### V7 (Planned)
- **Backtesting engine** — Run backtests through MCP
- **Strategy builder** — AI-assisted strategy creation
- **Social trading** — Follow top traders' signals

### V8 (Planned)
- **Risk management dashboard** — Real-time risk monitoring
- **Compliance module** — Regulatory reporting
- **Multi-tenant** — Team/org support

---

*Document generated: AlgoChains MCP Server v6.0.0*
*GitHub: https://github.com/AlgoChains/algochains-mcp-server*
