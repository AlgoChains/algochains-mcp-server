# AlgoChains MCP Server v21.3

> **The institutional-grade Model Context Protocol server for autonomous trading systems.**  
> Drop this into Claude, Cursor, or any MCP-compatible AI — and you get a full autonomous trading desk.

[![MCP](https://img.shields.io/badge/MCP-2025--11--25-blue?style=flat-square)](https://modelcontextprotocol.io)
[![Tools](https://img.shields.io/badge/tools-338-green?style=flat-square)](#tool-categories)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue?style=flat-square)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-purple?style=flat-square)](LICENSE)
[![Brokers](https://img.shields.io/badge/brokers-Tradovate%20%7C%20Alpaca%20%7C%20OANDA-orange?style=flat-square)](#supported-brokers)
[![Bots](https://img.shields.io/badge/live%20bots-4%20futures%20%2B%20equities%2Fcrypto-red?style=flat-square)](#live-bot-showcase)
[![Onyx](https://img.shields.io/badge/Onyx-RAG%20Knowledge%20Brain-8b5cf6?style=flat-square)](#onyx-intelligence)

---

## What This Is

AlgoChains MCP Server exposes a **live, institutional-grade trading platform** as a set of AI-callable tools — compatible with Claude Desktop, Cursor, VS Code, and any MCP 2025-11-25-compliant client.

**No paper data. No synthetic fills. No placeholder values. No Vertex AI — Onyx on-prem replaces it all.**  
Every tool connects to real brokers, real market data feeds (Databento tick-level, Polygon, FRED, CBOE), and real strategy execution infrastructure.

```
Your AI assistant                    AlgoChains MCP Server v21.3
  Claude / Cursor         ←MCP→      338 tools across 19 domains
  ChatGPT / Copilot                  ↕                    ↕
                                     Real Brokers       Onyx Knowledge Brain
                                     Tradovate (Futures)  400+ research docs
                                     Alpaca (Equities)    80+ skills indexed
                                     OANDA (Forex)        Live bot logs
                                     Binance (Crypto)     Blueprints & audits
                                                          ↕
                                     Autonomous Pipeline
                                     Research → Backtest → MCPT → Marketplace
```

### V21.3 Highlights

- **Autonomous Marketplace Pipeline** — `run_marketplace_autopilot` scans strategy research, runs Rust tick backtests, applies 5-gate MCPT validation, stages passing strategies as subscribable marketplace bots — zero human intervention required
- **Live Futures Showcase** — 4 live bots (MNQ Sharpe 4.61, CL, MES, NQ) with real fill metrics, locked to owner only
- **Subscribable Equities & Crypto** — Alpaca paper trader running 16 equities + 8 crypto pairs with real fills, subscribable at $9-29/mo
- **Onyx Intelligence Brain** — Replaces Vertex AI RAG. Self-hosted on desktop GPU (100.89.114.31:8085), daily sync at 3am PT
- **Signal Conflict Manager** — Formalized buy/sell overlap policy with SQLite audit log, backtested design
- **Desktop Tower Dispatcher** — Auto-routes heavy Optuna/backtest jobs to Windows GPU tower via Tailscale SSH

---

## 60-Second Quickstart

```bash
# 1. Install
pip install algochains-mcp-server

# 2. Set credentials (copy from .env.example)
export TRADOVATE_USERNAME=...
export ALPACA_API_KEY=...
export POLYGON_API_KEY=...

# 3. Add to Claude Desktop  (~/.claude/claude_desktop_config.json)
{
  "mcpServers": {
    "algochains": {
      "command": "uvx",
      "args": ["algochains-mcp-server"],
      "env": {
        "TRADOVATE_USERNAME": "your-username",
        "TRADOVATE_PASSWORD": "your-password",
        "ALPACA_API_KEY": "your-key",
        "POLYGON_API_KEY": "your-key"
      }
    }
  }
}

# 4. Ask Claude:
# "What's my current MNQ position P&L?"
# "Run the AlphaLoop evolution cycle on my CL scalper"
# "Show me dark pool prints on NVDA in the last hour"
# "Compute gamma exposure for SPY options chain"
```

---

## Live Bot Showcase — Real Autonomous Futures Trading

AlgoChains runs **4 live futures bots** on Tradovate, tracked in real-time through this MCP server. These can be subscribed to on the marketplace:

| Bot | Symbol | Strategy | Timeframe | AI Stack |
|-----|--------|----------|-----------|----------|
| `MNQ_Upgraded_Scalper` | MNQ | 7-AI ensemble scalper | 5-min | GPT-4o, Claude 3.7, Gemini Pro, Llama 3.3 debate engine |
| `CL_Swing_Scalper` | CL | FinBERT sentiment + momentum | Swing | FinBERT NLP, COT positioning, dark pool |
| `MES_EMA_Swing` | MES | EMA pullback with regime detection | Daily | Multi-agent pipeline, ADDM drift detection |
| `NQ_EMA_Swing` | NQ | Trend following + foundation model | Swing | Foundation model shadow runtime, DaveWang signals |

Subscribe to live bot metrics via MCP:
```python
# In your agent session
subscribe_bot_metrics(bot_name="MNQ_Upgraded_Scalper", subscriber_id="your-id")
# → Streams live: entry price, P&L, signal confidence, regime state
```

---

## Tool Categories

### 1. Account & Portfolio (28 tools)
```
get_account_balance      get_positions         get_orders
get_working_orders       get_fills             cancel_order
flatten_all_positions    get_risk_parameters   daily_pnl_summary
```

### 2. Market Data — Tick to Macro (41 tools)
```
get_quote                get_ohlcv             get_tick_data
stream_quotes            get_options_chain     get_futures_curve
get_macro_signals        get_vix_term_structure get_yield_curve
get_dxy_regime           get_credit_spreads    get_pmi_data
```

### 3. Order Flow & Institutional (34 tools)
```
get_footprint_chart      compute_cumulative_delta  get_volume_profile
get_dark_pool_volume     get_dark_pool_prints       detect_absorption
compute_vwap             compute_twap               get_order_book_imbalance
```

### 4. Signal Generation & AI Ensemble (29 tools)
```
generate_signal          run_ai_debate          get_ensemble_vote
compute_confidence       get_regime_state       compute_gex
unusual_options_activity read_tape              pair_trade_signal
```

### 5. Strategy Building & Backtesting (38 tools)
```
build_strategy           run_backtest           validate_strategy
optimize_strategy        run_mcpt_validation    submit_to_marketplace
compute_sharpe           run_walk_forward       analyze_overfitting
```

### 6. AlphaLoop — Autonomous Self-Improvement (18 tools)
```
run_evolution_cycle      get_evolution_status   list_evolved_strategies
rollback_evolution       record_trade_episode   query_trade_memory
get_lessons_learned      inject_session_context get_strategy_rankings
```

### 7. Order Execution (22 tools)
```
place_order              place_bracket_order    place_oco_order
modify_order             cancel_order           get_execution_quality
compute_slippage         get_fill_analysis      smart_route_order
```

### 8. Risk Management (19 tools)
```
compute_kelly            get_position_sizing    check_circuit_breaker
get_max_drawdown         get_var               compute_correlation_risk
event_risk_check         check_vix_gate         get_daily_loss_proximity
```

### 9. Market Intelligence (24 tools)
```
get_earnings_catalyst    get_prediction_markets get_congressional_trades
get_insider_activity     get_dark_pool_prints   get_alt_data_signals
get_news_sentiment       get_economic_releases  get_cot_report
```

### 10. MCP Spec 2025-11-25 — Elicitation + Tasks (12 tools)
```
request_trade_confirmation  submit_long_running_task  get_task_status
cancel_task                 list_active_tasks         subscribe_resource
list_subscriptions          notify_resource_update    get_sampling_config
```

### 11. Crypto & DeFi (31 tools)
```
get_crypto_quote         get_funding_rate          get_open_interest
get_liquidation_clusters get_staking_yields        create_dca_schedule
get_copy_trade_leaders   subscribe_copy_trading    get_nft_portfolio
```

### 12. Security & Key Management (11 tools)
```
store_api_key            rotate_api_key            check_key_health
provision_agent_account  list_agent_accounts       audit_access_log
```

### 13. SaaS & Marketplace (28 tools)
```
create_strategy_listing  get_marketplace_strategies subscribe_strategy
get_subscriber_metrics   process_payment           get_revenue_report
create_sandbox           destroy_sandbox            get_tenant_audit_log
```

### 14. Onyx Intelligence (8 tools)
```
onyx_search              onyx_ask                  onyx_health
onyx_ingest_document     onyx_search_strategies    onyx_query_bot_history
onyx_find_best_setup     onyx_get_lessons
```

### 15. Streaming & Alerts (16 tools)
```
create_price_alert       list_alerts               delete_alert
subscribe_earnings       get_earnings_calendar     stream_bot_metrics
get_bot_dashboard        subscribe_bot_metrics     get_live_pnl
```

### 16. Autonomous Agents (14 tools)
```
run_watchdog_check       get_adaptive_brain_status run_token_guardian
get_system_health        run_morning_scan          get_incident_report
```

### 17. White-Label & BYOK (17 tools)
```
configure_white_label    get_white_label_config    test_white_label_connection
set_byok_key             get_byok_status           list_byok_providers
```

### 18. Strategy Research & Evolution (22 tools)
```
search_ssrn_strategies   replicate_paper           compute_dcf
get_factor_exposures     run_sensitivity_sweep     scan_regime_alpha
```

**Total: 350+ tools across 18 domains**

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    AlgoChains MCP Server v21                    │
│                                                                 │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────────────┐  │
│  │ MCP 2025-11 │  │ AlphaLoop    │  │ 7-AI Debate Engine    │  │
│  │ Compliance  │  │ Self-Improve │  │ GPT4o·Claude·Gemini   │  │
│  │ Elicitation │  │ Trade Memory │  │ Llama·Mistral·Qwen    │  │
│  │ Tasks+SSE   │  │ RL Reward    │  │ Foundation Model RAG  │  │
│  └─────────────┘  └──────────────┘  └───────────────────────┘  │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                  Data Provider Stack                       │  │
│  │  Databento(tick) │ Polygon(bars) │ FRED │ CBOE │ FINRA   │  │
│  │  SEC EDGAR │ Polymarket │ Kalshi │ Binance │ Hyperliquid  │  │
│  │  Lido │ Cosmos │ Ethereum Beacon │ Massive.com (white-label) │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                   Broker Layer                             │  │
│  │        Tradovate (futures) │ Alpaca (equities/crypto)     │  │
│  │        OANDA (forex) │ Binance │ Bybit │ Hyperliquid       │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────────────┐  │
│  │ Key Vault   │  │ Onyx RAG KB  │  │ Stripe Connect SaaS   │  │
│  │ AES-256-GCM │  │ 400+ docs    │  │ Per-tenant isolation  │  │
│  │ scrypt KDF  │  │ Semantic QA  │  │ Immutable audit log   │  │
│  └─────────────┘  └──────────────┘  └───────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Real Data Policy

**This server enforces a strict real-data-only policy across every tool.**

- ❌ No synthetic fills
- ❌ No placeholder P&L
- ❌ No mock market data
- ❌ No hardcoded fallback prices
- ✅ All data from live brokers, real APIs, real tick feeds
- ✅ If a source is unavailable → tool fails closed with explicit error
- ✅ Every tool documents its real data source

---

## Account Protection System (12 Guards)

AlgoChains ships with 12 hardened trading safeguards:

| Guard | Trigger | Action |
|-------|---------|--------|
| **Daily Loss Limit** | Net P&L < -$500 | Block all new trades |
| **VIX Gate** | VIX > 35 | Flatten positions + halt |
| **Max Position Count** | > 3 concurrent | Reject new orders |
| **Fat Finger** | Size > 3x normal | Require confirmation |
| **Bracket Integrity** | Stop or target missing | Auto-attach bracket |
| **Flash Crash** | Price move > 2% in 60s | Emergency flatten |
| **Drawdown Circuit Breaker** | Drawdown > 15% | Halt trading |
| **Correlation Exposure** | Portfolio corr > 0.85 | Reduce size |
| **Consecutive Loss Guard** | 3 losses in a row | Force cooldown |
| **Daily Loss Proximity** | Within 80% of limit | Reduce position sizes |
| **Cancel-on-Disconnect** | WebSocket drops | Auto-cancel working orders |
| **Earnings Event Risk** | Earnings within 24h | Restrict position size |

---

## MCP 2025-11-25 Spec Compliance

AlgoChains implements the full MCP 2025-11-25 specification:

### Elicitation — Structured User Confirmation
```python
# High-value trades require explicit confirmation before execution
result = request_trade_confirmation(
    symbol="MNQ",
    side="BUY",
    quantity=4,
    estimated_notional=97600
)
# → MCP client shows a structured form; execution waits for approval
```

### Durable Tasks — Long-Running Operations
```python
task = submit_long_running_task(
    operation="full_backtest",
    strategy_id="my-mnq-scalper",
    date_range={"start": "2023-01-01", "end": "2025-12-31"}
)
# → Returns task ID immediately; runs in background; notifies on completion
status = get_task_status(task_id=task["task_id"])
```

### Resource Subscriptions — Real-Time Push
```python
subscribe_resource(uri="algochains://bots/mnq/metrics")
# → Client receives push notifications on every fill, signal, and P&L update

subscribe_resource(uri="algochains://alerts/price")
# → Client notified when price alerts trigger
```

### OpenID Connect Discovery
```
GET /.well-known/openid-configuration
→ Returns OIDC metadata for enterprise SSO integration
```

---

## AlphaLoop — Autonomous Strategy Evolution

AlgoChains implements a 4-stage self-improvement cycle inspired by DeepMind AlphaLoop:

```
SCAN  →  MUTATE  →  VALIDATE  →  PROMOTE
  ↑                                 |
  └──────── (if better) ────────────┘
```

1. **SCAN**: Identifies underperforming strategies (Sharpe < 1.5 or win rate < 50%)
2. **MUTATE**: Uses Optuna to generate parameter variations (TP/SL ratios, thresholds)
3. **VALIDATE**: Tests mutations against real trade history via RL reward model
4. **PROMOTE**: Swaps in better-performing variant; keeps rollback checkpoint

```python
# Trigger an evolution cycle
result = run_evolution_cycle(
    strategy_id="mnq-upgraded-scalper",
    generations=5,
    min_trades_required=20
)
# → Returns promoted variant or keeps current if no improvement
```

---

## Order Flow Intelligence

Real institutional-grade microstructure analysis:

```python
# Footprint chart — bid/ask volume at each price level
chart = get_footprint_chart(symbol="MNQ", timeframe="5min", bars=20)
# → Detects absorption (sellers absorbed at support), imbalances, delta exhaustion

# Dark pool volume — real FINRA ATS + Polygon off-exchange data  
dp = get_dark_pool_volume(symbol="NVDA", date="2026-04-05")
# → Returns actual dark pool % from FINRA ATS weekly reports + Polygon trade conditions

# Cumulative delta with divergence detection
cd = compute_cumulative_delta(symbol="MNQ", timeframe="5min")
# → Real-time bid/ask pressure with bearish/bullish divergence alerts
```

---

## Macro Signal Fabric

Pre-computed macro alpha signals from real sources:

| Signal | Source | Update Frequency |
|--------|--------|-----------------|
| Yield Curve (2y-10y spread) | FRED API | Daily |
| Credit Spreads (HY-IG) | FRED API | Daily |
| DXY Momentum | Polygon | Real-time |
| PMI Regime | FRED API | Monthly |
| VIX Term Structure | CBOE public CSV | Daily |
| Congressional Trades | Capitol Trades / SEC | Weekly |
| Prediction Market Odds | Polymarket + Kalshi | Real-time |

```python
signals = get_macro_signals()
# → Returns composite regime score, trend bias, risk-on/off state
```

---

## Onyx Intelligence — Semantic Strategy Search

AlgoChains integrates with a self-hosted Onyx knowledge base for natural language strategy research:

```python
# Ask natural language questions about your trading system
answer = onyx_ask("What's the best CL swing setup from the last 90 days?")
# → Searches 400+ strategy research JSONs, returns cited answer

answer = onyx_ask("How do I configure the Token Guardian for Tradovate?")
# → Searches blueprints, skills, runbooks; returns step-by-step answer

results = onyx_search("MNQ regime detection CUSUM", limit=5)
# → Returns top matching documents with relevance scores
```

**Knowledge base includes:**
- 400+ strategy research JSONs from AlphaLoop runs
- 45+ implementation blueprints
- 126 OpenClaw skills with full context
- 7 days of live bot logs (rolling window)
- Marketplace strategy listings and audit reports

---

## Supported Brokers

| Broker | Asset Classes | Status |
|--------|--------------|--------|
| **Tradovate** | Futures (MNQ, CL, MES, NQ, ES, GC, SI) | ✅ Live |
| **Alpaca** | Equities, Options, Crypto | ✅ Live |
| **OANDA** | Forex (50+ pairs) | ✅ Live |
| **Binance** | Crypto Spot + Perpetuals | ✅ Live |
| **Bybit** | Crypto Perpetuals | ✅ Live |
| **Hyperliquid** | Decentralized Perpetuals | ✅ Live |

---

## Configuration Reference

```bash
# Required: at least one broker
TRADOVATE_USERNAME=...        TRADOVATE_PASSWORD=...
TRADOVATE_APP_ID=...          TRADOVATE_APP_SECRET=...
ALPACA_API_KEY=...            ALPACA_SECRET_KEY=...

# Market data (real data sources)
POLYGON_API_KEY=...           DATABENTO_API_KEY=...
FRED_API_KEY=...

# Strategy intelligence
ONYX_API_URL=http://your-onyx:8085    ONYX_API_KEY=...

# Streaming & notifications
SLACK_WEBHOOK_URL=...         SIGNAL_URL=...
METRICS_INGEST_API_KEY=...

# AI ensemble
OPENAI_API_KEY=...            ANTHROPIC_API_KEY=...
CEREBRAS_API_KEY=...          GROQ_API_KEY=...

# Payments (marketplace)
STRIPE_SECRET_KEY=...         STRIPE_WEBHOOK_SECRET=...

# Security
KEY_VAULT_PASSPHRASE=...      # AES-256-GCM vault master key
```

---

## Quick Test

```bash
# Verify all modules load correctly
python -m pytest tests/test_tool_registration.py -v

# Check broker connectivity
python -c "
from algochains_mcp import server
print(f'Tools registered: {len(server._get_registered_tools())}')
"

# Start HTTP server with SSE streaming
python -m algochains_mcp.http_transport --port 8765

# Run single MCP query via CLI
echo '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_account_balance","arguments":{}}}' \
  | uvx algochains-mcp-server
```

---

## What Makes This Different

| Feature | AlgoChains MCP | Generic trading APIs |
|---------|---------------|---------------------|
| **MCP 2025-11-25 spec** | ✅ Full (Elicitation, Tasks, SSE) | ❌ None |
| **Self-improving strategies** | ✅ AlphaLoop 4-stage evolution | ❌ Static |
| **Order flow data** | ✅ Footprint, delta, dark pool | ❌ OHLCV only |
| **Multi-broker** | ✅ 6 brokers + DeFi | ❌ 1 broker |
| **Macro signals** | ✅ FRED, CBOE, Polymarket | ❌ None |
| **Institutional AI** | ✅ 7-model debate ensemble | ❌ Single model |
| **Real data policy** | ✅ Enforced, fails closed | ⚠️ Often mock |
| **Account guards** | ✅ 12 hardened protections | ⚠️ Basic limits |
| **Semantic search** | ✅ Onyx RAG knowledge base | ❌ None |
| **Private marketplace** | ✅ Creator + subscriber model | ❌ None |

---

## Developer Guide

### Adding a New Tool

```python
# In server.py, add to _TOOL_MAP:
"my_new_tool": {
    "description": "What it does and what real data source it uses",
    "inputSchema": {
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "Ticker symbol"},
        },
        "required": ["symbol"],
    },
}

# In _dispatch_tool():
if name == "my_new_tool":
    # Always use real API, never return synthetic data
    result = await real_api_client.fetch(args["symbol"])
    if result is None:
        raise ToolError("Real data unavailable — check API key")
    return result
```

### Builder SDK — Create Strategy Templates

```python
from algochains_mcp.builder_sdk import StrategyTemplate

template = StrategyTemplate(
    name="My EMA Breakout",
    symbol="MNQ",
    timeframe="5min",
    indicators=["EMA(9)", "EMA(21)", "ATR(14)", "Volume(20)"],
    entry_logic="ema9 > ema21 AND volume > 1.5x AND momentum > 0",
    exit_logic="stop_atr_2x OR target_atr_4x OR 60min_max",
    risk_pct=0.01,
)
template.validate()
template.submit_to_marketplace()
```

---

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for the full history.

**v21.0.0** (2026-04-06) — The "Holy Fuck Factor" Release
- MCP 2025-11-25 full spec compliance (Elicitation, Tasks, SSE, OIDC)
- AlphaLoop self-improving strategy evolution daemon (real RL reward model)
- Order flow stack: footprint charts, cumulative delta, volume profile, dark pool
- Earnings NLP: SEC EDGAR + FinBERT sentiment pipeline
- Prediction markets: Polymarket + Kalshi integration
- Macro signal fabric: FRED, CBOE, Polygon
- Encrypted local key vault (AES-256-GCM + scrypt)
- Per-agent sub-account provisioning (Alpaca Broker API)
- Price alert engine with Polygon real-time polling
- Earnings event subscription system
- Onyx intelligence layer (self-hosted RAG knowledge base)
- Crypto parity: copy trading, staking, DCA, perp futures
- Stripe Connect billing engine (real payments, 70/30 split)
- Per-tenant rate limiting + immutable audit log
- Registry.json for MCP marketplace discovery
- Bot metrics streaming daemon for live showcase

---

## License

MIT — use freely, build great things.

---

<div align="center">

**Built for traders who demand real data, real execution, and real autonomy.**

[GitHub](https://github.com/AlgoChains/algochains-mcp-server) · [Docs](https://docs.algochains.ai) · [Marketplace](https://algochains.ai/marketplace) · [Discord](https://discord.gg/algochains)

</div>
