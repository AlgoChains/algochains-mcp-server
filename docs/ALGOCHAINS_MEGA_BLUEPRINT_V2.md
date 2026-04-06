# ALGOCHAINS MEGA BLUEPRINT V2 — THE DEFINITIVE SYSTEM PROMPT

> **Purpose:** Single source of truth. Paste to any AI coding agent. Complete system context, architecture, roadmap V10→V18, and the genius-level innovations that separate AlgoChains from every other trading platform.
>
> **Who we are:** AlgoChains (algochains.io / algochains.ai) — AI-native algorithmic trading platform
> **CEO:** Tyler Reynolds | **Web Dev:** RJ Reynolds
> **Launch:** April 14, 2026 | **Fundraise:** $2M SAFE round
> **Current version:** v9.0.0 → targeting v18.0.0
> **Supersedes:** `ALGOCHAINS_MASTER_ROADMAP_MEGA_PROMPT.md` + `V17_MULTI_BROKER_SCALING_BLUEPRINT.md`
> **Parts:** This is Part 1 of 3. See `_V2_PART2.md` and `_V2_PART3.md`.

---

## TABLE OF CONTENTS (Full Document)

**Part 1 (this file):** Sections 1-6 — System Context, Architecture V2, Roadmap, V10-V12
**Part 2:** Sections 7-12 — V13-V18 (Alt Data, Agents, DeFi, Cloud, Multi-Broker, Intent)
**Part 3:** Sections 13-18 — Genius Layer, Implementation, Rules, Gates, Deployment, Mega Prompt

---

## 1. SYSTEM CONTEXT — What We Have Today

You are building for **AlgoChains**, an AI-native algorithmic trading platform. MUST build on top of this — never recreate what already works.

### 1.1 Core Products

| Product | Description |
|---------|-------------|
| **MCP Server** | 51 tools, 8 prompts, 14 engine modules. Production-hardened: per-tool timeouts, concurrency semaphores, circuit breakers, response size guards. Python async. |
| **Control Tower** | 4 live futures bots (MNQ, CL, MES, NQ), Tradovate WebSocket, 3-tier Token Guardian, institutional flow, GPU optimization, priority-based failover (Mac→Desktop→VPS→Cloud). |
| **AlgoChains.io** | Next.js on Vercel. Marketplace UI, strategy builder, dashboards. Built by RJ Reynolds. |
| **Rust Engine v2** | 4 strategy binaries (rsi/bb/swing/scalper). Per-bar equity-curve Sharpe. Walk-forward + MCPT. 172 validated marketplace bots. |
| **OpenClaw Gateway** | 80+ skills, 372 cron jobs, 9 crew agents. Groq LLaMA 3.3 70B primary, Claude/GPT fallback. |

### 1.2 Live Bots (Production — DO NOT CHANGE parameters without backtest)

| Bot | Asset | Key Params |
|-----|-------|-----------|
| MNQ Scalper | Micro Nasdaq | Vol 3.02x, Mom 39.01%, Stop 5t, Target 65t, Kelly half |
| CL Scalper | Crude Oil | Vol spike + momentum, trailing stop (50% risk at 1.5x, BE at 2x) |
| MES Swing | Micro S&P | Swing with trailing stop |
| NQ Swing | Nasdaq 100 | Swing with trailing stop |

ML override requires: volume > 50% avg AND RSI 30-70. Timeframe confluence: 2/3 agreement.

### 1.3 MCP Tool Inventory (v9.0.0 — 51 tools)

- **V1-V7 Core (32):** Broker mgmt, trading, market data, portfolio, marketplace, BYOK, notifications, datasets
- **V8 Strategy (8):** create/validate/backtest/optimize/walk_forward/deploy/templates/fork
- **V8 Social (6):** follow/unfollow/leader_stats/copy_status/copy_params/become_leader
- **V8 Signals (5):** publish/subscribe/verify/accuracy/consensus
- **V9 Risk (10):** VaR, ES, factor exposure, stress test, drawdown, margin, greeks, concentration, alerts
- **V9 Compliance (11):** pre_trade, surveillance, audit, kill_switch, profiles, best_exec, wash_trade, restricted
- **V9 Multi-Tenant (10):** tenant CRUD, sub-accounts, broker routing, billing, dashboard, permissions

### 1.4 Production Hardening (Deployed in middleware.py)

| Feature | Implementation |
|---------|---------------|
| Per-tool timeouts | `asyncio.wait_for()` 30-120s by category |
| Concurrency semaphores | 3-10 bounded parallel by engine type |
| Circuit breaker | 5 failures → open 60s cooldown |
| Response size guard | 1MB max, truncate with warning |
| Rate limiting | Token bucket per broker + category |
| Input validation | String/list truncation, whitespace strip |

**Pipeline:** Sanitize → Circuit check → Rate limit → Semaphore → Execute with timeout → Size guard → Record

### 1.5 Module Map

```
src/algochains_mcp/
├── strategy_builder/     # StrategyEngine, WalkForwardEngine, TemplateManager
├── social_trading/       # SocialTradingEngine
├── community_signals/    # CommunitySignalEngine
├── risk_dashboard/       # RiskDashboardEngine
├── compliance/           # ComplianceEngine
├── multi_tenant/         # MultiTenantEngine
├── brokers/              # Alpaca, IBKR, Oanda, TradersPost, QuantConnect
│   └── base.py           # BrokerConnector ABC — THE normalization layer
├── marketplace/          # MarketplaceBridge, StrategyValidator (6-gate MCPT)
├── data_providers/       # Polygon, Finnhub, AlphaVantage, TwelveData, Yahoo
├── datasets/             # DatasetBuilder
├── byok/                 # KeyOrchestrator, ProviderRegistry
├── auth/                 # APIKeys, SupabaseSSO
├── server.py             # Main: tool registration, dispatch, resources, prompts
├── middleware.py          # Rate limit, timeouts, circuit breakers, concurrency
├── config.py | errors.py
```

### 1.6 Infrastructure

| Component | Technology |
|-----------|-----------|
| Dev Machine | Mac M3 Max (Priority 1) |
| GPU Server | Desktop Ubuntu/WSL2, RTX 5080, 16.3GB VRAM (100.99.127.119, Priority 2) |
| GPU Services | ml_enhancer:8001, finbert:8002, vLLM Qwen2.5-7B:8003, Ollama deepseek-r1:11434 |
| Futures Broker | Tradovate (LIVE account, WebSocket + REST) |
| Equities | Alpaca REST | Forex: Oanda REST | Multi-asset: IBKR TWS |
| Market Data | Databento (ticks), Polygon.io (aggs via Massive MCP) |
| Database | PostgreSQL (Supabase) + SQLite (local) |
| AI Gateway | OpenClaw: Groq LLaMA 3.3 70B (FREE), Claude 3.5, GPT-4o fallback |
| Networking | Tailscale mesh VPN |
| Monitoring | Slack (8 channels, severity-routed) |
| Failover | Priority: Mac → Desktop → VPS → Cloud, auto-handback |

### 1.7 Data Assets

- MNQ tick data: 46 months (2020-2025, gap 2023), ~5-7 GB pkl.gz
- Forex: 13 pairs x 4 timeframes, ~2 GB Parquet
- Stocks: 17 tickers x 5 timeframes, ~3 GB Parquet
- 172 validated marketplace bots (RSI/BB/EMA/Swing)
- Sharpe gates: daily max 5.0, hourly 7.0, 15min 10.0, 5min 12.0

### 1.8 Database Schema (20+ tables, RLS-ready)

Strategy: `strategy_specs`, `backtest_runs`, `optimization_runs`, `walk_forward_runs`, `deployments`
Social: `leaders`, `copy_relationships`
Signals: `community_signals`, `signal_subscriptions`, `user_accuracy`
Risk: `risk_snapshots`, `risk_alerts`, `risk_alert_rules`, `stress_test_results`
Compliance: `compliance_profiles`, `audit_trail`, `compliance_violations`, `pre_trade_checks`
Tenant: `tenants`, `sub_accounts`, `broker_routing`, `tenant_billing`, `usage_meters`

---

## 2. ARCHITECTURE V2 — The New Brain

### 2.1 Why V2

V1 hits three walls:
1. **IDE wall:** Cursor caps at 40 tools. 69% of our tools invisible. 55K+ tokens wasted.
2. **Broker wall:** Only 5 brokers. Missing Tradovate (our main!), all crypto, Tastytrade, etc.
3. **State wall:** Snapshots only. No cross-broker aggregation. No session journal. No market context.

### 2.2 V2 Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                    IDE / AI Client                               │
│  (Cursor, Windsurf, Claude Desktop, Claude Code, VS Code)       │
└───────────────────────┬────────────────────────────────────────┘
                        │ MCP Protocol
┌───────────────────────▼────────────────────────────────────────┐
│              DYNAMIC TOOL GATEWAY (V17)                          │
│  3 meta-tools: search_tools | describe_tools | execute_tool     │
│  Tool Registry: 200+ tools indexed, only 3 exposed to LLM      │
│  Semantic search + category index + state context injection     │
│  Token: ~1,500 (3 tools) vs ~65,000 (129 tools) = 97.7%        │
└──┬──────────┬──────────┬──────────┬──────────┬─────────────────┘
   │          │          │          │          │
┌──▼──┐  ┌───▼──┐  ┌───▼──┐  ┌───▼──┐  ┌───▼──┐  ┌────────┐
│Broker│  │ML/AI │  │Exec  │  │Alt   │  │Cloud │  │Intent  │
│GW   │  │V10   │  │V11   │  │Data  │  │V16   │  │V18     │
│V17  │  │      │  │      │  │V13   │  │      │  │        │
└──┬──┘  └──────┘  └──────┘  └──────┘  └──────┘  └────────┘
   │
┌──▼───────────────────────────────────────────────────────────┐
│  NORMALIZED MULTI-BROKER LAYER (BrokerConnector ABC)          │
│  Alpaca | IBKR | Oanda | Tradovate | Schwab | Tastytrade     │
│  TradeStation | CCXT(100+ crypto) | Trade-It(eTrade,Webull)   │
└──┬───────────────────────────────────────────────────────────┘
   │
┌──▼───────────────────────────────────────────────────────────┐
│  UNIFIED STATE ENGINE                                         │
│  portfolio/unified | session/journal | market/context          │
│  brokers/capabilities | admin/tool-analytics | shadow/portfolio│
└──────────────────────────────────────────────────────────────┘
```

### 2.3 Strategic Vision

```
V1-V9 (DONE):   "Connect and Trade"     → Brokers, tools, marketplace, risk, compliance
V10-V11:         "Intelligent Edge"      → ML strategies, institutional execution
V12-V13:         "Platform Expansion"    → Real-time, mobile, alternative data
V14-V15:         "Autonomous Future"     → Agent swarms, DeFi, cross-chain
V16:             "Enterprise Cloud"      → Full SaaS, multi-region, enterprise
V17:             "Universal Brokerage"   → Any broker, any IDE, full state awareness
V18:             "Intent Intelligence"   → Think, don't click. Trade by intent.
```

### 2.4 Revenue Progression

| Version | New Revenue Stream | Price Point |
|---------|-------------------|-------------|
| V8-V9 | Marketplace fees + Subscriptions | 30% cut + $49-$499/mo |
| V10 | ML model marketplace | Creators sell trained models |
| V11 | Institutional license | $2K-$10K/mo per seat |
| V12 | Mobile premium + API tiers | $9.99/mo + tiered |
| V13 | Alt data marketplace | 30% cut on data sales |
| V14 | Agent compute fees | Per-hour billing |
| V15 | DeFi protocol fees | 0.1% per tx |
| V16 | Enterprise SaaS | $50K-$500K/yr |
| V17 | Broker-agnostic premium | $99/mo |
| V18 | Intent autopilot | $199/mo |

---

## 3. ROADMAP OVERVIEW — V10 through V18

| Version | Name | New Tools | Lines | Key Deliverable |
|---------|------|-----------|-------|-----------------|
| **V10** | ML/AI Strategy Engine | 12 | ~3,500 | GPU ML models, RL agents, LLM strategy gen |
| **V11** | Institutional Execution | 10 | ~3,000 | FIX protocol, SOR, dark pools, TWAP/VWAP |
| **V12** | Real-Time + Mobile | 8 | ~2,500 | WebSocket streaming, push, REST API |
| **V13** | Alt Data Marketplace | 10 | ~3,000 | Sentiment, satellite, SEC NLP, options flow |
| **V14** | Agent Swarm | 8 | ~2,500 | Multi-agent orchestration, strategy evolution |
| **V15** | DeFi Cross-Chain | 10 | ~2,500 | DEX, MEV protection, yield farming |
| **V16** | Cloud SaaS | 6 | ~3,000 | K8s, global edge, usage billing |
| **V17** | Multi-Broker + IDE | 6 | ~4,000 | 20+ brokers, dynamic toolsets, state engine |
| **V18** | Intent Intelligence | 8 | ~3,500 | Intent trading, shadow portfolios, strategy DNA |

**Totals:** 78 new tools (51→129), 20 new modules (14→34), ~27,500 lines, 9 releases

---

## 4. V10: ML/AI-Native Strategy Engine

> **Module:** `src/algochains_mcp/ml_engine/` | **Tools:** 12 | **Lines:** ~3,500

### 4.1 Architecture

```
Feature Engineering → Model Training → Model Registry → GPU Dispatcher
         │                  │                │                │
    OHLCV, indicators,  XGBoost, LSTM,   Version control,  Mac MPS (local)
    order flow, sent,   Transformer,     A/B testing,      Desktop CUDA
    calendar            Ensemble, RL     OOS Sharpe gates  (100.99.127.119)
```

### 4.2 Tools (12)

| Tool | Description |
|------|-------------|
| `create_feature_set` | Define feature engineering pipeline |
| `train_model` | Train ML model, dispatch to GPU |
| `evaluate_model` | OOS walk-forward evaluation |
| `predict` | Live predictions from deployed model |
| `list_models` | List models with status + metrics |
| `promote_model` | dev → staging → production with gates |
| `create_rl_agent` | Create PPO/SAC reinforcement learning agent |
| `train_rl_agent` | Train RL on historical with GPU |
| `evaluate_rl_agent` | Backtest RL on unseen data |
| `generate_strategy_llm` | Natural language → StrategySpec via LLM |
| `explain_model` | SHAP/LIME explainability |
| `compare_models` | Side-by-side OOS comparison |

### 4.3 Classes

```python
class FeatureEngine:
    async def create_feature_set(self, symbols, timeframe, features, target, horizon) -> dict
    async def compute_features(self, feature_set_id, data_range) -> dict

class ModelTrainer:
    async def train(self, feature_set_id, model_type, hyperparams, train_range, test_range) -> dict
    async def evaluate(self, model_id, eval_range, metrics) -> dict
    async def predict(self, model_id, symbol, as_of=None) -> dict
    async def explain(self, model_id, sample_range=None, top_features=10) -> dict

class ModelRegistry:
    async def register(self, model_id, metadata) -> dict
    async def promote(self, model_id, target_stage, reason) -> dict
    async def list_models(self, status=None) -> list[dict]
    async def compare(self, model_ids, eval_range, metrics) -> dict

class RLAgentEngine:
    async def create_agent(self, env_config, algo, reward, episodes) -> dict
    async def train(self, agent_id, train_range, episodes, checkpoint_every=100) -> dict
    async def evaluate(self, agent_id, eval_range) -> dict

class GPUDispatcher:
    async def dispatch(self, task_type, payload) -> dict  # auto-selects Mac MPS or Desktop CUDA
    async def check_gpu_status(self) -> dict
    async def transfer_data(self, source, dest, files) -> dict  # rsync ONLY, never SSHFS

class LLMStrategyGenerator:
    async def generate(self, description, asset_class, risk_tolerance, constraints=None) -> dict
    async def refine(self, spec_id, feedback) -> dict
```

### 4.4 GPU Protocol

```python
GPU_DISPATCH_CONFIG = {
    "mac": {"device": "mps", "max_batch": 4096, "models": ["xgboost", "ensemble"]},
    "desktop": {
        "host": "100.99.127.119", "device": "cuda", "max_batch": 32768,
        "models": ["lstm", "transformer", "rl"],
        "transfer": "rsync -avz --progress",  # NEVER SSHFS/NFS
    }
}
```

### 4.5 Tables

```sql
CREATE TABLE IF NOT EXISTS feature_sets (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, symbols TEXT NOT NULL,
    timeframe TEXT NOT NULL, features TEXT NOT NULL, target TEXT NOT NULL,
    horizon TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ml_models (
    id TEXT PRIMARY KEY, feature_set_id TEXT REFERENCES feature_sets(id),
    model_type TEXT NOT NULL, hyperparams TEXT,
    stage TEXT DEFAULT 'dev' CHECK (stage IN ('dev','staging','production','archived')),
    train_range TEXT, test_range TEXT, metrics TEXT, artifact_path TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, promoted_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS rl_agents (
    id TEXT PRIMARY KEY, env_config TEXT NOT NULL, algo TEXT NOT NULL,
    reward_fn TEXT NOT NULL, episodes_trained INTEGER DEFAULT 0,
    best_reward REAL, metrics TEXT, checkpoint_path TEXT, stage TEXT DEFAULT 'dev',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ml_predictions (
    id TEXT PRIMARY KEY, model_id TEXT REFERENCES ml_models(id),
    symbol TEXT NOT NULL, predicted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    prediction TEXT NOT NULL, actual TEXT, correct BOOLEAN
);
```

---

## 5. V11: Institutional-Grade Execution

> **Module:** `src/algochains_mcp/execution_engine/` | **Tools:** 10 | **Lines:** ~3,000

### 5.1 Architecture

```
Order Manager → Smart Order Router → Venue Selection
                     │
         ┌───────────┼───────────┐
         ▼           ▼           ▼
    Lit Venues   Dark Pools   Algo Execution
    NYSE/NASDAQ  SIGMA-X      TWAP/VWAP/Iceberg
         │           │           │
         └───────────┼───────────┘
                     ▼
              FIX Gateway (4.2/4.4/5.0)
                     │
              TCA Engine (slippage, impact, shortfall)
```

### 5.2 Tools (10)

`execute_algo_order`, `get_algo_order_status`, `cancel_algo_order`, `analyze_market_impact`, `get_tca_report`, `configure_sor`, `list_execution_venues`, `create_fix_session`, `send_fix_message`, `get_execution_analytics`

### 5.3 Classes

```python
class InstitutionalOrderManager  # Validate + risk check + compliance
class SmartOrderRouter           # Split across venues, minimize impact
class AlgoExecutor               # TWAP, VWAP, Iceberg, Sniper
class FIXGateway                 # FIX 4.2/4.4/5.0 sessions
class TCAEngine                  # Slippage, impact, implementation shortfall
class VenueManager               # Venue registry + latency/fee tracking
```

### 5.4 Tables

```sql
CREATE TABLE IF NOT EXISTS algo_orders (
    id TEXT PRIMARY KEY, algo_type TEXT NOT NULL, symbol TEXT NOT NULL,
    side TEXT NOT NULL, total_qty REAL NOT NULL, filled_qty REAL DEFAULT 0,
    avg_fill_price REAL, status TEXT DEFAULT 'active', params TEXT NOT NULL,
    child_orders TEXT, started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS execution_venues (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, venue_type TEXT,
    avg_latency_ms REAL, maker_fee REAL, taker_fee REAL, status TEXT DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS tca_records (
    id TEXT PRIMARY KEY, order_id TEXT NOT NULL, symbol TEXT NOT NULL,
    slippage_bps REAL, market_impact_bps REAL, implementation_shortfall_bps REAL,
    venue TEXT, filled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS fix_sessions (
    id TEXT PRIMARY KEY, sender_comp_id TEXT NOT NULL, target_comp_id TEXT NOT NULL,
    fix_version TEXT DEFAULT '4.4', host TEXT NOT NULL, port INTEGER NOT NULL,
    status TEXT DEFAULT 'disconnected'
);
```

---

## 6. V12: Real-Time Analytics & Mobile API

> **Module:** `src/algochains_mcp/realtime/` | **Tools:** 8 | **Lines:** ~2,500

### 6.1 Tools (8)

`subscribe_stream`, `unsubscribe_stream`, `list_active_streams`, `configure_push_notification`, `get_push_history`, `create_api_token`, `get_api_usage`, `create_webhook`

### 6.2 Classes

```python
class EventBus              # Redis pub/sub
class StreamManager         # Per-user WebSocket rooms
class PushNotificationService  # Firebase + APNs
class APIGateway            # FastAPI REST, OpenAPI 3.0, JWT auth
class WebhookManager        # Outbound webhooks with retry
```

### 6.3 Tables

```sql
CREATE TABLE IF NOT EXISTS stream_subscriptions (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
    stream_type TEXT NOT NULL CHECK (stream_type IN ('portfolio','market','risk','signals','bots')),
    filters TEXT, status TEXT DEFAULT 'active', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS push_devices (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, device_token TEXT NOT NULL UNIQUE,
    platform TEXT CHECK (platform IN ('ios','android','web')), rules TEXT
);

CREATE TABLE IF NOT EXISTS api_tokens (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL,
    token_hash TEXT NOT NULL, scopes TEXT NOT NULL, requests_count INTEGER DEFAULT 0,
    expires_at TIMESTAMP, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS webhooks (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, url TEXT NOT NULL,
    events TEXT NOT NULL, secret_hash TEXT, status TEXT DEFAULT 'active',
    deliveries_total INTEGER DEFAULT 0, deliveries_failed INTEGER DEFAULT 0
);
```

---

> **END OF PART 1** — Continue to `ALGOCHAINS_MEGA_BLUEPRINT_V2_PART2.md`
