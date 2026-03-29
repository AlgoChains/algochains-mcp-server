# V8 Blueprint — Strategy Builder SDK, Social Trading & Community Signals

> **Status:** Next · **Target:** Q3 2026 · **Owner:** AlgoChains Core Team

---

## Executive Summary

V8 transforms AlgoChains from a tool that *deploys* strategies into a platform that *creates* them. By combining an AI-native Strategy Builder SDK, a social/copy-trading layer, and a community signal bus, V8 closes the loop: users go from idea → backtest → validation → live trading → sharing — all within one MCP server session, orchestrated by an AI agent.

### What ships in V8

| Component | Description | New MCP Tools |
|---|---|---|
| **Strategy Builder SDK** | AI-assisted strategy creation with natural language → code → backtest → deploy pipeline | 8 |
| **Social Trading** | Copy-trading engine with proportional position mirroring, leader/follower model | 6 |
| **Community Signals** | Pub/sub signal bus where creators broadcast trade signals and followers consume them | 5 |

**Total new tools: 19** (bringing the server from 54 → 73 tools)

---

## Part 1 — Strategy Builder SDK

### The Problem

Today, building an algorithmic trading strategy requires:
1. Choosing a backtesting framework (QuantConnect LEAN, Backtrader, VectorBT, Zipline)
2. Writing indicator logic, entry/exit rules, position sizing, and risk management in code
3. Running backtests across multiple timeframes and assets
4. Validating results against overfitting (IS/OOS splits, walk-forward, MCPT)
5. Deploying to paper, then live

Each step uses a different tool, format, and mental model. AI agents can write strategy code, but they can't natively test, validate, and deploy it without human glue.

### The Solution

A **declarative strategy definition format** (StrategySpec) that AI agents can generate from natural language, backtest through our Rust engine, validate through 6-gate MCPT, and deploy — all via MCP tool calls.

### Architecture

```text
User: "Build me a mean-reversion strategy for AAPL using RSI and Bollinger Bands,
       15-min bars, with ATR-based stops"
                    │
                    ▼
┌─────────────────────────────────────────────────────────┐
│  AI Agent (Claude, GPT, Cascade, etc.)                  │
│                                                         │
│  1. generate_strategy_spec  → StrategySpec JSON         │
│  2. validate_strategy_spec  → Schema check              │
│  3. backtest_strategy       → Rust engine results       │
│  4. optimize_parameters     → Optuna parameter search   │
│  5. run_walk_forward        → Multi-fold OOS results    │
│  6. submit_strategy         → 6-gate MCPT (existing)    │
│  7. deploy_strategy         → Paper or live             │
│  8. list_strategy_templates → Browse starter templates  │
└─────────────────────────────────────────────────────────┘
```

### StrategySpec Format

```json
{
  "name": "AAPL Mean Reversion RSI+BB",
  "version": "1.0.0",
  "author": "user_abc123",
  "description": "Mean reversion on AAPL 15-min bars using RSI oversold + BB lower band touch",

  "universe": {
    "symbols": ["AAPL"],
    "asset_class": "equity",
    "timeframe": "15min",
    "data_range": {
      "train": ["2022-01-01", "2024-06-30"],
      "test": ["2024-07-01", "2025-12-31"]
    }
  },

  "indicators": [
    { "name": "rsi", "period": 14, "source": "close" },
    { "name": "bbands", "period": 20, "std_dev": 2.0, "source": "close" },
    { "name": "atr", "period": 14 }
  ],

  "entry_rules": {
    "long": {
      "conditions": [
        { "indicator": "rsi", "operator": "<", "value": 30 },
        { "indicator": "close", "operator": "<", "ref": "bbands.lower" }
      ],
      "logic": "AND"
    }
  },

  "exit_rules": {
    "stop_loss": { "type": "atr_multiple", "multiplier": 1.5 },
    "take_profit": { "type": "atr_multiple", "multiplier": 3.0 },
    "trailing_stop": { "type": "atr_multiple", "multiplier": 2.0, "activation": 1.5 },
    "time_exit": { "bars": 20 }
  },

  "position_sizing": {
    "method": "risk_pct",
    "risk_per_trade": 0.01,
    "max_positions": 3,
    "max_portfolio_risk": 0.06
  },

  "filters": {
    "volume_min": 100000,
    "spread_max_pct": 0.1,
    "regime": { "enabled": true, "avoid": ["high_vol"] }
  }
}
```

### New MCP Tools

| Tool | Description |
|---|---|
| `generate_strategy_spec` | Convert natural language strategy description into a StrategySpec JSON. AI agent creates the spec based on user intent. |
| `validate_strategy_spec` | Schema validation + sanity checks (valid indicators, realistic parameters, correct date ranges). |
| `backtest_strategy` | Run the StrategySpec through the Rust backtest engine. Returns Sharpe, drawdown, trade count, equity curve. |
| `optimize_parameters` | Optuna-based parameter search over the StrategySpec's indicator and exit rule parameters. Returns top N parameter sets. |
| `run_walk_forward` | Execute K-fold walk-forward validation. Returns per-fold OOS Sharpe, consistency score, and walk-forward efficiency. |
| `deploy_strategy` | Deploy a validated strategy to paper or live trading on a connected broker. |
| `list_strategy_templates` | Browse pre-built strategy templates (momentum, mean reversion, breakout, pairs, etc.) as starting points. |
| `fork_strategy` | Clone an existing strategy (from marketplace or templates) and modify parameters. |

### Implementation Modules

```text
src/algochains_mcp/strategy_builder/
├── __init__.py
├── spec.py                 # StrategySpec Pydantic model + validation
├── codegen.py              # StrategySpec → Rust engine config translation
├── backtest_runner.py      # Subprocess call to Rust engine with result parsing
├── optimizer.py            # Optuna integration for parameter search
├── walk_forward.py         # K-fold walk-forward orchestration
├── templates/              # Pre-built StrategySpec templates
│   ├── momentum_rsi.json
│   ├── mean_reversion_bb.json
│   ├── ema_crossover.json
│   ├── breakout_volume.json
│   └── pairs_cointegration.json
└── deployer.py             # Strategy → broker deployment bridge
```

### Competitive Positioning

| Feature | QuantConnect | Backtrader | Build Alpha | StrategyQuant X | **AlgoChains V8** |
|---|---|---|---|---|---|
| Natural language → strategy | No | No | No | No | **Yes (AI-native)** |
| MCP tool interface | [Partial](https://github.com/QuantConnect/mcp-server) | No | No | No | **Yes (8 tools)** |
| Built-in MCPT validation | No | No | Partial | Partial | **Yes (6-gate)** |
| Marketplace publishing | Limited | No | No | No | **Yes (172+ bots)** |
| Multi-broker deployment | Limited | Limited | No | No | **Yes (12+ brokers)** |
| Declarative strategy format | C#/Python code | Python code | GUI | GUI | **JSON StrategySpec** |

---

## Part 2 — Social Trading

### The Problem

Social/copy trading is a $3.2B market (2025) growing at 25% CAGR. Platforms like eToro (40M+ users), ZuluTrade, NAGA, and Collective2 have proven the model. But they all operate as closed ecosystems — you can only copy within their platform and their broker.

AlgoChains already has a marketplace of 172+ validated bots. Social trading adds the *live* dimension: real-time trade mirroring, proportional position sizing, and transparent leader performance.

### Architecture

```text
┌──────────────────────────────────────────────────┐
│  LEADER (Strategy Creator)                        │
│                                                   │
│  algochains-mcp → Broker (Alpaca, IBKR, etc.)    │
│       │                                           │
│       ├── Trade executed: BUY 100 AAPL @ $185     │
│       │                                           │
│       └── Signal published to Signal Bus ─────────┤
│           {                                       │
│             "leader_id": "tyler_mnq",             │
│             "action": "buy",                      │
│             "symbol": "AAPL",                     │
│             "qty_pct": 0.05,  // 5% of portfolio  │
│             "price": 185.00,                      │
│             "stop_loss": 180.50,                  │
│             "take_profit": 195.00,                │
│             "timestamp": "2026-03-29T16:14:00Z",  │
│             "signature": "sha256:abc..."          │
│           }                                       │
└──────────────────────────────────────────────────┘
                        │
                        │ WebSocket (encrypted)
                        ▼
┌──────────────────────────────────────────────────┐
│  SIGNAL BUS (AlgoChains Cloud)                    │
│                                                   │
│  ┌─────────────┐  ┌─────────────┐                │
│  │ Auth + ACL   │  │ Rate Limiter │                │
│  └─────────────┘  └─────────────┘                │
│  ┌─────────────┐  ┌─────────────┐                │
│  │ Signal Store │  │ Leaderboard  │                │
│  │ (PostgreSQL) │  │ (Redis)      │                │
│  └─────────────┘  └─────────────┘                │
└──────────────────────────────────────────────────┘
                        │
            ┌───────────┼───────────┐
            ▼           ▼           ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ FOLLOWER A   │ │ FOLLOWER B   │ │ FOLLOWER C   │
│              │ │              │ │              │
│ MCP Server   │ │ MCP Server   │ │ MCP Server   │
│ Alpaca       │ │ IBKR         │ │ Oanda        │
│              │ │              │ │              │
│ Scale: 50%   │ │ Scale: 100%  │ │ Scale: 25%   │
│ Max risk: 2% │ │ Max risk: 5% │ │ Max risk: 1% │
│              │ │              │ │              │
│ BUY 50 AAPL  │ │ BUY 100 AAPL │ │ (skip—forex  │
│ @ $185       │ │ @ $185       │ │  only mode)  │
└──────────────┘ └──────────────┘ └──────────────┘
```

### Position Scaling Model

Followers configure their copy parameters:

```json
{
  "leader_id": "tyler_mnq",
  "scaling_mode": "risk_parity",    // or "fixed_pct", "proportional", "kelly"
  "scale_factor": 0.5,              // 50% of leader's position size
  "max_risk_per_trade": 0.02,       // Never risk more than 2% per trade
  "max_drawdown_halt": 0.10,        // Pause copying if 10% drawdown reached
  "allowed_assets": ["equity"],     // Only copy equity trades
  "excluded_symbols": ["TSLA"],     // Skip specific symbols
  "copy_stops": true,               // Mirror stop-loss and take-profit
  "slippage_tolerance": 0.005       // Max 0.5% price difference from signal
}
```

### Leader Ranking Algorithm

Leaders are ranked by a composite score combining multiple risk-adjusted metrics:

```text
Score = 0.30 × Sharpe_12m
      + 0.20 × Sortino_12m
      + 0.15 × (1 - MaxDrawdown_12m)
      + 0.15 × Consistency (% of profitable months)
      + 0.10 × Trade_Count_Score (log scale, rewards activity)
      + 0.10 × AUM_Score (total capital following, log scale)
```

Minimum requirements to become a leader:
- 90-day track record with ≥ 50 trades
- Sharpe ≥ 1.0
- Max drawdown ≤ 30%
- Verified identity (KYC via Supabase)

### Revenue Model

| Event | Leader Share | AlgoChains Fee |
|---|---|---|
| Subscription fee (monthly) | 70% | 30% |
| Performance fee (high-water mark) | 80% | 20% |
| Signal marketplace one-time purchase | 70% | 30% |

### New MCP Tools

| Tool | Description |
|---|---|
| `follow_leader` | Subscribe to a leader's signals with custom scaling and risk parameters. |
| `unfollow_leader` | Stop copying a leader. Close existing copied positions or keep them. |
| `get_leader_stats` | Detailed leader metrics: Sharpe, Sortino, drawdown, consistency, trade history. |
| `get_copy_status` | Current copy positions, P&L attribution to each leader, and pending signals. |
| `set_copy_parameters` | Update scaling, risk limits, asset filters for an active copy relationship. |
| `become_leader` | Register as a signal leader. Requires identity verification and 90-day track record. |

---

## Part 3 — Community Signals

### The Problem

Trading communities share signals via Discord, Telegram, and Twitter — unstructured, unverified, and impossible for AI agents to consume programmatically. There's no standard format for a "trade signal" that can be:
1. Published by a human or bot
2. Consumed by an AI agent via MCP
3. Verified against the publisher's actual trade history
4. Ranked by historical accuracy

### The Solution

A **structured signal bus** integrated into the MCP server. Signals are published in a standard format, verified against the publisher's real trades, and consumable by any MCP-connected AI agent.

### Signal Schema

```json
{
  "signal_id": "sig_abc123",
  "publisher": {
    "id": "user_xyz",
    "handle": "@momentum_mike",
    "verified": true,
    "accuracy_30d": 0.62,
    "sharpe_30d": 1.85
  },
  "signal": {
    "type": "entry",               // entry | exit | scale_in | scale_out | alert
    "direction": "long",
    "symbol": "NVDA",
    "asset_class": "equity",
    "timeframe": "hour",
    "confidence": 0.78,
    "entry_zone": [130.00, 132.00],
    "stop_loss": 126.50,
    "targets": [138.00, 142.00, 148.00],
    "risk_reward": 2.3,
    "thesis": "NVDA breaking out of 3-week consolidation with volume confirmation. GEX flipping positive above $131."
  },
  "metadata": {
    "strategy_type": "breakout",
    "indicators_used": ["volume", "gex", "support_resistance"],
    "market_regime": "bull",
    "catalyst": "earnings_pre",
    "expires_at": "2026-03-30T16:00:00Z"
  },
  "verification": {
    "publisher_trade_hash": "sha256:def...",  // Proves publisher entered this trade
    "broker_confirmed": true,
    "fill_price": 131.25,
    "fill_time": "2026-03-29T14:30:15Z"
  }
}
```

### Signal Categories

| Category | Description | Example |
|---|---|---|
| **Verified Signals** | Publisher has a confirmed broker fill matching the signal | Highest trust tier |
| **Unverified Signals** | Published without trade verification — opinion/analysis only | Community discussion |
| **AI Signals** | Generated by algorithmic strategies or ML models | Labeled as AI-generated |
| **Consensus Signals** | Aggregated from multiple publishers agreeing on direction | Crowd-sourced conviction |

### Consensus Engine

When multiple independent publishers signal the same direction on the same symbol within a time window:

```text
Consensus Score = Σ (publisher_accuracy × publisher_confidence) / n_publishers

If Consensus Score > 0.70 AND n_publishers ≥ 3:
    → Publish "Consensus Signal" to all subscribers
    → Include: individual signal breakdown, agreement %, divergent opinions
```

### New MCP Tools

| Tool | Description |
|---|---|
| `publish_signal` | Broadcast a trade signal to the community with optional broker verification. |
| `subscribe_signals` | Subscribe to signals matching criteria (symbol, asset class, strategy type, min accuracy). |
| `get_signal_feed` | Retrieve recent signals with filters, sorted by relevance or recency. |
| `get_consensus` | Get consensus view for a symbol — how many publishers agree, aggregate confidence. |
| `rate_signal` | Rate a signal's quality after outcome is known. Feeds into publisher accuracy scores. |

---

## Implementation Plan

### Phase 1 — Strategy Builder SDK (4 weeks)

| Week | Deliverable |
|---|---|
| 1 | StrategySpec Pydantic model, schema validation, 5 starter templates |
| 2 | Rust engine integration — StrategySpec → engine config translation + result parsing |
| 3 | Optuna parameter optimizer + walk-forward orchestration |
| 4 | MCP tool registration, end-to-end testing, documentation |

### Phase 2 — Social Trading (6 weeks)

| Week | Deliverable |
|---|---|
| 1–2 | Signal Bus infrastructure — WebSocket pub/sub, PostgreSQL signal store, Redis leaderboard |
| 3 | Leader registration, ranking algorithm, minimum requirements enforcement |
| 4 | Follower copy engine — proportional scaling, risk limits, slippage protection |
| 5 | Multi-broker copy execution — normalize fill handling across Alpaca, IBKR, Oanda |
| 6 | MCP tools, revenue tracking, end-to-end testing |

### Phase 3 — Community Signals (3 weeks)

| Week | Deliverable |
|---|---|
| 1 | Signal schema, publish/subscribe infrastructure, verification bridge |
| 2 | Consensus engine, accuracy tracking, publisher scoring |
| 3 | MCP tools, feed ranking, spam/manipulation protection |

**Total: 13 weeks**

---

## Database Schema Additions

```sql
-- Strategy Builder
CREATE TABLE strategy_specs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    name VARCHAR(255) NOT NULL,
    spec JSONB NOT NULL,                    -- Full StrategySpec JSON
    backtest_results JSONB,                 -- Latest backtest output
    validation_status VARCHAR(50),          -- draft, backtested, validated, deployed
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Social Trading
CREATE TABLE copy_relationships (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    follower_id UUID REFERENCES users(id),
    leader_id UUID REFERENCES users(id),
    config JSONB NOT NULL,                  -- Scaling, risk limits, filters
    status VARCHAR(20) DEFAULT 'active',    -- active, paused, stopped
    total_pnl DECIMAL(12,2) DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(follower_id, leader_id)
);

CREATE TABLE leader_profiles (
    user_id UUID PRIMARY KEY REFERENCES users(id),
    handle VARCHAR(50) UNIQUE NOT NULL,
    verified BOOLEAN DEFAULT FALSE,
    ranking_score DECIMAL(6,4),
    sharpe_12m DECIMAL(6,4),
    sortino_12m DECIMAL(6,4),
    max_drawdown_12m DECIMAL(6,4),
    consistency_pct DECIMAL(5,2),
    total_followers INTEGER DEFAULT 0,
    total_aum DECIMAL(14,2) DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Community Signals
CREATE TABLE signals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    publisher_id UUID REFERENCES users(id),
    signal JSONB NOT NULL,                  -- Full signal payload
    category VARCHAR(20) NOT NULL,          -- verified, unverified, ai, consensus
    symbol VARCHAR(20) NOT NULL,
    direction VARCHAR(10) NOT NULL,
    accuracy_outcome DECIMAL(5,4),          -- Filled after resolution
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ
);

CREATE INDEX idx_signals_symbol ON signals(symbol, created_at DESC);
CREATE INDEX idx_signals_publisher ON signals(publisher_id, created_at DESC);
```

---

## Security Considerations

- **Signal integrity:** All verified signals include SHA-256 hash of the publisher's actual trade log entry.
- **Copy isolation:** Follower MCP servers execute trades locally using their own broker keys. No credential sharing.
- **Manipulation protection:** Publisher accuracy scores use a 90-day rolling window. New accounts start with 0 accuracy until 50+ signals are rated.
- **Rate limiting:** Max 100 signals/day per publisher. Consensus requires ≥ 3 independent publishers (same IP/device detection).
- **Data privacy:** Signal feeds are opt-in. Publishers choose public vs. subscribers-only visibility.

---

## Success Metrics

| Metric | Target (6 months post-launch) |
|---|---|
| Strategies created via SDK | 1,000+ |
| Active leaders on social trading | 50+ |
| Active followers | 500+ |
| Daily signals published | 200+ |
| Consensus signal accuracy | > 60% |
| MRR from social trading fees | $10K+ |

---

## Research Sources

- **Strategy SDKs:** QuantConnect LEAN, Backtrader, VectorBT, Build Alpha, StrategyQuant X, Arrow Algo
- **Social Trading:** eToro CopyTrader (40M users), ZuluTrade (192 countries), NAGA, Collective2, Hummingbot MCP
- **Signal Platforms:** TradingView ideas, StockTwits, Discord trading communities
- **MCP Integration:** QuantConnect MCP Server, Hummingbot MCP, AlgoChains V1–V7
