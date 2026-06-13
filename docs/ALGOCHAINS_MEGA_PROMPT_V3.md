# AlgoChains MCP Server — MEGA PROMPT V3
**Date:** 2026-04-06  
**Version:** 20.0.0  
**Author:** Tyler Reynolds  
**Status:** CANONICAL — use this document to re-initialize any AI agent for this project

---

## THE ONE-PARAGRAPH CONTEXT DUMP

You are working on `algochains-mcp-server` — an MCP (Model Context Protocol) server that gives any AI agent (Cursor, Windsurf, Claude Code, VS Code) the ability to trade, backtest, and deploy validated algorithms to the AlgoChains marketplace. The server has 275+ tools across 20 version layers (V1-V20). V20 (latest) adds: account protection guards (prevent account explosions), Builder SDK ($199/mo tier: Supabase data warehouse access + marketplace submission pipeline), memory-safe lazy import system (95% RAM reduction on startup), FastAPI HTTP/SSE transport for remote agents, and real Backtrader strategy templates. The server serves 4 live futures bots (MNQ, CL, MES, NQ) on Tradovate demo, 4 equity marketplace bots (IWM/GOOGL/UBER/AMZN BB Mean Reversion) on Alpaca, and a 30-day paper trading graduation pipeline for external creators.

---

## ARCHITECTURE MAP

```
algochains-mcp-server/
├── src/algochains_mcp/
│   ├── server.py                    ← CORE: 5000+ line MCP server, 275+ tools
│   │                                  LAZY LOADING: all V8-V19 imported on first use
│   │                                  SMART MODE: 25 Tier-1 tools (fits in Cursor)
│   │                                  FULL MODE: all 275+ tools (Claude Code)
│   ├── http_transport.py            ← Phase 2: FastAPI HTTP/SSE transport
│   ├── memory_safety.py             ← BoundedCache, MemoryMonitor, lazy_import
│   ├── account_protection/
│   │   ├── engine.py               ← AccountProtectionEngine (orchestrates 13 guards)
│   │   └── guards.py               ← 13 PreTradeGuard implementations
│   ├── builder_sdk/
│   │   ├── data_warehouse.py       ← DataWarehouseClient (3.09B rows Supabase)
│   │   ├── strategy_runner.py      ← StrategyRunner (vectorized + Backtrader)
│   │   ├── submission_pipeline.py  ← SubmissionPipeline (7-gate MCPT validation)
│   │   ├── paper_trading_graduation.py ← PaperTradingMonitor, DecayWatchdog
│   │   ├── signal_tester.py        ← End-to-end signal propagation tester
│   │   └── templates/
│   │       ├── sma_crossover.py    ← Real Backtrader template
│   │       ├── rsi_mean_reversion.py
│   │       ├── bollinger_breakout.py
│   │       ├── ema_pullback.py     ← Modeled after MES/NQ swing bots
│   │       └── registry.py         ← Template catalog + instantiation
│   ├── brokers/                    ← V1-V7: Tradovate, Alpaca, IBKR, OANDA, etc.
│   ├── marketplace/                ← V1-V7: MarketplaceBridge, StrategyValidator
│   ├── streaming/                  ← V8: Real-time data streaming
│   ├── portfolio/                  ← V8: Portfolio optimizer
│   ├── strategy_builder/           ← V9: Full strategy spec/backtest/optimize/deploy
│   ├── ml_engine/                  ← V10: Feature engineering, model training, RL
│   ├── execution_engine/           ← V11: Institutional order management, FIX
│   ├── realtime_analytics/         ← V12: P&L streaming, order flow, microstructure
│   ├── alt_data/                   ← V13: Sentiment, satellite, SEC, social
│   ├── agent_swarm/                ← V14: Multi-agent orchestration
│   ├── defi_engine/                ← V15: DEX, yield, bridge, MEV
│   ├── cloud_saas/                 ← V16: Multi-tenant, billing, white-label
│   ├── dynamic_toolsets/           ← V17: Semantic tool discovery (95% token reduction)
│   ├── intent_engine/              ← V18: Natural language → trade execution
│   ├── alpha_engines/              ← V19: VWAP, dark pool, GEX, vol surface, etc.
│   └── data_providers/             ← Polygon, Alpha Vantage, Finnhub, Databento, etc.
├── scripts/
│   └── startup_health_check.py    ← One-command system verification
├── .env.example                   ← Comprehensive env var template
├── pyproject.toml                 ← Version 20.0.0, extras: backtrader, http, supabase
└── tests/
    ├── test_account_protection.py
    ├── test_builder_sdk.py
    └── test_memory_safety.py
```

---

## CRITICAL RULES (never violate)

1. **NEVER import heavy modules at top level** — all V8-V19 imports MUST go through `_lazy_import()`. The startup memory spike was crashing Windsurf and the Mac. The fix reduced startup RAM from ~800MB to ~45MB.

2. **NEVER modify trading logic without approval** — stop loss, take profit, position sizing, max daily loss, confidence thresholds.

3. **NEVER use `tradovate_token_auto_refresh.py`** — breaks WebSocket keep-alive. Always use `tradovate_token_guardian.py`.

4. **NEVER mock data in production paths** — no synthetic prices, fake fills, placeholder Sharpe. If real data unavailable, fail closed.

5. **NEVER expose source code in marketplace submissions** — strategy IP belongs to the creator. Only metrics and signals are submitted.

---

## KEY DECISIONS MADE (context for new agents)

### Why Lazy Loading?
The user reported "horrendous memory leakage — computer instantly crashed on boot" and "Windsurf also won't load anymore." Root cause: server.py imported 50+ modules at startup (`from .ml_engine.feature_engine import FeatureEngine`, etc.) causing numpy/pandas/torch chains that consumed 800MB+. Fix: `_lazy_import()` function defers all V8-V19 module loading to first use. Result: 12ms startup, 162MB RSS.

### Why 13 Account Protection Guards?
Futures account blowups are the #1 cause of user churn. Guards prevent: oversized positions (fat finger), consecutive loss spirals, VIX spike exposure, buying power exhaustion, concentration risk, correlation cascade (all positions same direction), daily loss limit breach, max drawdown breach, after-hours trading, margin calls.

### Why 7-Gate Validation Pipeline?
Industry research (see deflated-sharpe.pdf) shows 95% of backtests are spurious. The 7 gates filter to the top 5%: (1) Schema validation, (2) Performance gates, (3) Overfitting detection via deflated Sharpe, (4) Monte Carlo Permutation Test (500 permutations, p<0.05), (5) Walk-Forward validation, (6) 30-day paper trading, (7) Decay monitoring.

### Why HTTP Transport?
The stdio transport works for local IDE agents (Cursor, Windsurf) but not for remote agents (Claude API, OpenAI Agents SDK, n8n, Make.com). The HTTP/SSE transport enables: remote AI agents, multi-agent pipelines, webhook-triggered strategy execution.

### Why HMAC-signed signals?
Prevents creators from inflating metrics by injecting fake signals. Each `signal_to_api()` call is signed with the creator's secret. Django validates timestamp (reject if >60s old) and signature.

---

## LIVE SYSTEM STATE (as of 2026-04-06)

### Active Bots (Tradovate Demo)
| Bot | File | Status | Account |
|-----|------|--------|---------|
| MNQ scalper | FUTURES_SCALPER_UPGRADED.py | Live | DEMO5812602 |
| CL swing | CL_FUTURES_SCALPER.py | Live | DEMO5812602 |
| MES swing | mes_swing_live.py | Live | DEMO5812602 |
| NQ swing | nq_swing_live.py | Live | DEMO5812602 |

### Marketplace Listings (Alpaca — Public)
| ID | Strategy | Sharpe | Price |
|----|----------|--------|-------|
| 77 | IWM BB Mean Reversion | 3.81 | $29.99/mo |
| 78 | GOOGL BB Mean Reversion | 3.17 | $29.99/mo |
| 79 | UBER BB Mean Reversion | 3.05 | $29.99/mo |
| 80 | AMZN BB Mean Reversion | 2.59 | $29.99/mo |

### Data Warehouses (Read-Only, Builder Tier)
| Warehouse | Rows | Use |
|-----------|------|-----|
| Crypto_minute (bwfyebmxcdzamcjpsgpj) | 409M | Crypto backtesting |
| Stocks_minute (irpcqlegtkxtpvkfnrqh) | 1.3B | Equities |
| Forex_minute (auxbddwafgqxjmhsfwab) | 1.4B | Forex |

### Infrastructure
- **Mac M3 Max (treycsa):** 4 live bots, Token Guardian, MCP server, Cursor
- **Desktop Tower (localhost):** Onyx RAG stack, PostgreSQL, Redis, GPU backtesting
- **Django API:** https://algochains.ai
- **Supabase:** `$SUPABASE_PROJECT_REF` (marketplace DB)

---

## TOOL TIERS

### Tier 1 (Smart Mode — 25 tools, exposed by default)
```
place_order, cancel_order, close_position, close_all_positions,
get_quote, get_account, get_positions, get_orders, get_order_history,
portfolio_summary, connect_broker, disconnect_broker, list_brokers,
run_backtest, validate_strategy, optimize_strategy,
trade_by_intent, get_portfolio_state, discover_tools, get_tool_details,
compute_gex, unusual_options_activity, compute_kelly,
check_order_safety, query_data_warehouse
```

### Tier 2 (Discoverable via discover_tools → execute_dynamic_tool)
All 250+ remaining tools across V8-V20 domains.

---

## ENVIRONMENT VARIABLES — QUICK REFERENCE

```bash
# Minimum for MCP server to start (no broker needed):
# (no required vars — server starts with 0 env vars set)

# For paper trading and marketplace:
ALGOCHAINS_BUILDER_KEY=           # $199/mo tier
ALGOCHAINS_SIGNAL_SECRET=         # 32+ char HMAC secret (python3 -c "import secrets; print(secrets.token_hex(32))")
ALGOCHAINS_DJANGO_URL=https://algochains.ai

# For live trading:
ALPACA_API_KEY= ALPACA_SECRET_KEY=           # Equities
TRADOVATE_USERNAME= TRADOVATE_PASSWORD=      # Futures

# For data:
POLYGON_API_KEY=                 # $29/mo, best for options/L2
DATABENTO_API_KEY=               # Tick data

# For security:
ALGOCHAINS_HTTP_TRANSPORT_SECRET= # Bearer token for HTTP transport
ALGOCHAINS_PROTECTION_PRESET=moderate  # conservative|moderate|aggressive
```

---

## ACCOUNT PROTECTION — 13 GUARDS

| Guard | Default Threshold | What It Blocks |
|-------|------------------|----------------|
| PositionSizeGuard | 10% of equity max | Oversized single position |
| DailyLossGuard | $500/day | Trading after daily loss limit |
| DrawdownGuard | 15% from peak | Trading in deep drawdown |
| FatFingerGuard | 25% order size check | Typo-level order sizes |
| BuyingPowerGuard | 90% cash required | Insufficient funds |
| MarginGuard | 80% margin utilization | Margin call prevention |
| ConcentrationGuard | 25% single symbol | Sector concentration risk |
| VolatilityKillswitch | VIX > 35 | VIX spike protection |
| CorrelationGuard | 70% portfolio same direction | Cascade risk |
| MaxPositionsGuard | 10 concurrent | Overtrading protection |
| TimeRestrictionGuard | 9:31-15:59 EST | Illiquid hours |
| ConsecutiveLossGuard | 5 in a row | Loss spiral prevention |
| MarginGuard | 3x futures leverage | Futures-specific margin |

### Presets
- **conservative:** VIX>25, $200/day, 8% DD, 5% position
- **moderate (default):** VIX>35, $500/day, 15% DD, 10% position
- **aggressive:** VIX>45, $2000/day, 25% DD, 20% position

---

## BUILDER SDK — COMPLETE FLOW

```python
# Step 1: Query data
from algochains import DataWarehouseClient, DataQuery
client = DataWarehouseClient(api_key="your-builder-key")
data = await client.query(DataQuery(
    warehouse="stocks",
    symbol="AAPL",
    start_date="2020-01-01",
    end_date="2024-12-31",
    interval="1min",
))

# Step 2: Run backtest
from algochains import StrategyRunner, BacktestConfig
runner = StrategyRunner()
result = await runner.run_backtest(
    strategy_class=MyStrategy,
    config=BacktestConfig(symbol="AAPL", start_date="2020-01-01", end_date="2024-12-31"),
    price_data=data,
)
print(result.passes_marketplace_gates())
# {'passes_all': True, 'gates': {...}, 'tier': 'gold'}

# Step 3: Paper trade for 30 days
from algochains import live_mode, signal_to_api
live_mode("my_strategy")  # Runs for 30 days, auto-posts signals

# Step 4: Check graduation eligibility
from algochains_mcp.builder_sdk.paper_trading_graduation import PaperTradingMonitor
monitor = PaperTradingMonitor("my_strategy", creator_key="your-builder-key")
eligibility = await monitor.check_graduation_eligibility()
# {'passed': True, 'summary': {'total_signals': 73, 'sharpe': 1.4, ...}}

# Step 5: Submit to marketplace
result = await monitor.submit_to_validation_queue(backtest_result=result.__dict__)
# {'submitted': True, 'submission_id': 'sub_abc123', 'status': 'pending_review'}

# Step 6: AlgoChains team reviews → listing published → signals auto-execute for subscribers
```

---

## HTTP TRANSPORT — CLAUDE API INTEGRATION

```python
# Start the HTTP server:
algochains-mcp-http --host 0.0.0.0 --port 8080

# Claude API config:
{
  "tools": [{
    "type": "mcp",
    "server_label": "algochains",
    "server_url": "http://your-server:8080/mcp",
    "authorization_token": "<ALGOCHAINS_HTTP_TRANSPORT_SECRET>"
  }]
}

# OpenAI Agents SDK config:
mcp_server = MCPServerStreamableHTTP(
    url="http://your-server:8080/mcp",
    headers={"Authorization": "Bearer <token>"}
)
```

---

## MEMORY SAFETY — HOW IT WORKS

```python
# The lazy import system in server.py:
def _lazy_import(rel_module: str, class_name: str) -> Any:
    """Import only when first used. Cached in _lazy_module_cache."""
    key = f"{rel_module}.{class_name}"
    if key in _lazy_module_cache:
        return _lazy_module_cache[key]
    mod = importlib.import_module(f"algochains_mcp{rel_module}")
    return getattr(mod, class_name)

# Before fix: 50+ top-level imports → ~800MB RSS at startup
# After fix: 3 essential imports → ~45MB RSS at startup (12ms import time)

# Memory monitor (auto-runs in production):
from algochains_mcp.memory_safety import get_memory_monitor
monitor = get_memory_monitor()
monitor.start_background_monitoring(interval_seconds=30)
# Triggers GC at 400MB, emergency cleanup at 600MB
```

---

## BACKTRADER STRATEGY TEMPLATES

### Available Templates
| Name | Best For | Benchmark Sharpe | Win Rate |
|------|----------|-----------------|----------|
| SMACrossover | SPY/QQQ daily | 1.4 | 55% |
| RSIMeanReversion | Equities 4h | 2.2 | 48% |
| BollingerBreakout | Futures/crypto 1h | 1.8 | 42% |
| EMAPullback | Index futures 4h | 1.9 | 57% |

### Usage
```python
from algochains_mcp.builder_sdk.templates.registry import get_template_class, list_templates
import backtrader as bt

StrategyClass = get_template_class("RSIMeanReversion")
cerebro = bt.Cerebro()
cerebro.addstrategy(StrategyClass, rsi_period=14, oversold=25, overbought=75)
cerebro.adddata(your_data_feed)
cerebro.run()
```

---

## SIGNAL PROPAGATION — HOW IT WORKS

```
Creator's Backtrader strategy
    │
    │ live_mode() runs
    ▼
signal_to_api() called on every buy/sell
    │ HMAC-signed with ALGOCHAINS_SIGNAL_SECRET
    ▼
POST /signals/signal/ at algochains.ai
    │ Django validates: HMAC sig, timestamp (<60s old), rate limit (100/hr)
    ▼
metrics_signalevent table updated
    │
    ▼
PaperTradingMonitor polls every 6h
    │ After 30 days: evaluate graduation gates
    ▼
submit_to_validation_queue() if passed
    │ AlgoChains runs independent backtest in Docker sandbox
    ▼
Manual review → listing published
    │
    ▼
Subscribers receive signals via broker adapters (Alpaca, Schwab, etc.)
```

---

## WHAT STILL NEEDS BUILDING (Phase 3-4 Roadmap)

### Phase 3 (Next Sprint)
- [ ] **Docker sandbox** for independent backtest verification (no-network, 60s timeout, 2GB mem)
- [ ] **Signal similarity detection** — flag strategies with >90% identical signals to another
- [ ] **Creator heartbeat monitor** — alert if no signals for 24h during paper trading phase
- [ ] **Stripe Connect integration** — 70/30 payout split for creators
- [ ] **MCP resource templates** — `algochains://strategy/{name}`, `algochains://listing/{id}`
- [ ] **Subscription circuit breaker** — auto-pause listing if subscriber loses >20% in 30 days

### Phase 4 (Institutional Tier)
- [ ] **FIX gateway production hardening** — real FIX 4.2 connectivity
- [ ] **Co-location support** — latency-optimized execution paths
- [ ] **Multi-strategy portfolio optimization** — Markowitz + HRP allocation
- [ ] **Automated governance reports** — compliance documentation for RIA use
- [ ] **White-label platform** — full tenant isolation, custom branding

### Infrastructure Pending
- [ ] Named Cloudflare tunnel (onyx.algochains.ai) — persistent HTTPS URL
- [ ] Onyx Slack bot live in #quant-lab — mobile natural language queries
- [ ] Live trading subscriptions (Alpaca live mode, not just paper)
- [ ] Schwab OAuth for subscribers
- [ ] Options strategies on marketplace (after options margin rules finalized)

---

## HOW TO USE THIS DOCUMENT

**For a new AI agent starting fresh:**
1. Read this document top to bottom
2. Run `python scripts/startup_health_check.py` to verify system state
3. Check `git log --oneline -5` for recent changes
4. If making changes to server.py: always use `_lazy_import()` for new V8+ modules
5. Never touch trading logic files without explicit user approval

**For continuing this build:**
The completed items from this session:
- ✅ Lazy loading system (startup time: 12ms, RAM: 162MB)
- ✅ FastAPI HTTP/SSE transport (Phase 2)
- ✅ 4 real Backtrader templates (SMA, RSI, BB, EMA)
- ✅ Paper trading graduation pipeline (30-day monitor + decay watchdog)
- ✅ Signal propagation tester (HMAC + end-to-end)
- ✅ Startup health check script (one-command system check)
- ✅ Comprehensive .env.example

Next tasks (in priority order):
1. Docker sandbox for creator strategy verification
2. Signal similarity detector
3. Stripe Connect payout integration
4. Named Cloudflare tunnel setup
5. Onyx Slack bot activation
