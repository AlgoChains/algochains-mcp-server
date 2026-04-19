---
BLUEPRINT_STATUS: active
CREATED: 2026-04-18
LAST_REVIEWED: 2026-04-18
VERSION: 1.0
PRIORITY: P0
---

# Kalshi Integration Blueprint V1
## AlgoChains MCP Server × ryanfrigo/kalshi-ai-trading-bot × yllvar/Kalshi-Quant-TeleBot

---

## Executive Summary

Two open-source Kalshi repos were analyzed. Key finding: **FED, CPI, and ECON_MACRO
markets have proven negative ROI (-40% to -65%) across hundreds of live trades.** Our
existing strategy engine was focused exclusively on those — a critical error now corrected.

The highest validated edge on Kalshi is **NCAAB NO-side trading (74% win rate, +10% ROI)**
and the **Safe Compounder** strategy (pure math, near-certain NO outcomes).

This blueprint defines a phased integration that grafts the best patterns from both repos
into the AlgoChains MCP server while discarding their weaknesses (standalone processes,
no Supabase, no Moltbook integration, no OpenClaw/Slack wiring).

---

## What We're Taking From Each Repo

### ryanfrigo/kalshi-ai-trading-bot (⭐361)

| Component | What We Take | Why |
|---|---|---|
| **Category Scoring** | 0-100 scoring, hard-block < 30 | Validated against live trades; blocks -65% ROI categories |
| **Safe Compounder** | NO-side, YES ≤ 20¢, edge > 5¢ | Historically validated positive edge |
| **Events API scanner** | `/events?with_nested_markets=true` | Full tradeable universe — /markets returns only KXMVE parlays |
| **AI Ensemble config** | 5-model weights + roles | Port to OpenRouter → integrate with existing Moltbook patterns |
| **Category blocklist** | FED, CPI, ECON_MACRO = BLOCKED | Hard evidence of negative edge |
| **Risk params** | 15% max DD, 25% fractional Kelly, 30% sector cap | Battle-tested parameters |
| **Trailing stops** | 20% profit trailing, 10-day max hold | Exit discipline |
| **Daily AI cost budget** | Cap per-day AI spend | Cost control |

### yllvar/Kalshi-Quant-TeleBot (⭐43)

| Component | What We Take | Where We Route It |
|---|---|---|
| **News Sentiment Strategy** | NLP signal → Kalshi event probability | Onyx intelligence layer |
| **Statistical Arbitrage** | Cointegration between related event pairs | New kalshi_stat_arb.py |
| **Volatility Strategy** | GARCH vol patterns → mean reversion | kalshi_volatility.py |
| **Telegram Commands** | /status /positions /balance /start_trading | → Slack OpenClaw (already wired) |
| **REST API pattern** | Health/status/positions/performance endpoints | → AlgoChains MCP tools |
| **Backtesting framework** | Strategy attribution by market type | → backtest-governance skill |

### What We Discard (and Why)

- **Standalone SQLite** → We use Supabase (cloud, multi-process safe)
- **Streamlit dashboard** → We have Command Center (Next.js, port 3333)
- **Telegram bot interface** → We have OpenClaw + Slack (#quant-lab)
- **Railway/Docker infra** → We run on Mac M3 Max + launchd
- **Separate venv** → Integrated into algochains-mcp-server package

---

## Architecture: The Integrated Stack

```
                    ┌─────────────────────────────────────┐
                    │   AlgoChains MCP Server (22.x)      │
                    │   server.py (tool dispatch)          │
                    └──────────────┬──────────────────────┘
                                   │
              ┌────────────────────┼──────────────────────┐
              │                    │                       │
   ┌──────────▼──────────┐  ┌──────▼──────────┐  ┌───────▼────────────┐
   │ kalshi_safe_         │  │ kalshi_category_ │  │ kalshi_ai_          │
   │ compounder.py        │  │ scorer.py        │  │ ensemble.py         │
   │ (NO-side ≤20¢)       │  │ (0-100, block<30)│  │ (5 OpenRouter LLMs) │
   └──────────┬──────────┘  └──────┬──────────┘  └───────┬────────────┘
              │                    │                       │
              └────────────────────┼──────────────────────┘
                                   │
                    ┌──────────────▼──────────────────┐
                    │   kalshi_events_scanner.py        │
                    │   /events API → full universe     │
                    │   (NOT /markets → only KXMVE)     │
                    └──────────────┬──────────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                     │
   ┌──────────▼──────────┐  ┌──────▼──────────┐  ┌─────▼──────────────┐
   │ kalshi_stat_arb.py   │  │ kalshi_news_     │  │ kalshi_strategy_   │
   │ (cointegration arb)  │  │ sentiment.py     │  │ engine.py (Kelly,  │
   │                      │  │ (Onyx-powered)   │  │ FedWatch, fixed)   │
   └──────────────────────┘  └──────────────────┘  └────────────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                     │
   ┌──────────▼──────────┐  ┌──────▼──────────┐  ┌─────▼──────────────┐
   │ Supabase             │  │ kalshi_slack_    │  │ Command Center     │
   │ kalshi_trades table  │  │ notifier.py      │  │ (Next.js :3333)    │
   │ kalshi_pnl_snapshots │  │ (#quant-lab)     │  │ Kalshi tab         │
   └──────────────────────┘  └──────────────────┘  └────────────────────┘
```

---

## Phase 1: Critical Fixes (EXECUTE NOW — blocking money)

### P1-1: Block FED/CPI/ECON_MACRO in strategy engine
**File**: `kalshi_strategy_engine.py`
**Change**: Add `BLOCKED_CATEGORIES` constant; skip all FED, CPI, NFP series from edge scanning
**Evidence**: ryanfrigo live data → FED: 32% WR, -40% ROI; CPI: 25% WR, -65% ROI

### P1-2: Add Events API scanner
**File**: `kalshi_events_scanner.py` (new)
**Change**: Use `/trade-api/v2/events?status=open&with_nested_markets=true` to get
ALL tradeable markets — not just the KXMVE parlay junk the /markets endpoint returns

### P1-3: Build Safe Compounder
**File**: `kalshi_safe_compounder.py` (new)
**Rules**:
- NO side only (never YES)
- YES last price ≤ 20¢ (near-certain outcome)
- NO ask > 80¢
- Edge (EV - price) > 5¢
- Maker limit orders at `lowest_ask - 1¢` (near-zero fees)
- Max 10% bankroll per position (half-Kelly)
- Skip: sports entertainment, "mention" markets
**Edge**: Near-zero-risk arbitrage of mispriced near-certain NO outcomes

---

## Phase 2: AI Ensemble + Category Scoring

### P2-1: Category Scoring System
**File**: `kalshi_category_scorer.py` (new)
**Logic**: Score 0-100 per category based on live trade history from Supabase
- ROI 40% + Recent Trend 25% + Sample Size 20% + Win Rate 15%
- < 30 = hard block; 30-59 = reduced allocation; 60+ = full allocation
**Backed by**: `kalshi_trades` Supabase table

### P2-2: AI Ensemble via OpenRouter
**File**: `kalshi_ai_ensemble.py` (new)
**Models via OpenRouter** (single API key):
- Claude Sonnet 4.5 → Lead Analyst (30%)
- Gemini 3.1 Pro → Forecaster (30%)
- GPT-5.4 → Risk Manager (20%)
- DeepSeek V3.2 → Bull Researcher (10%)
- Grok 4.1 Fast → Bear Researcher (10%)
**Integration**: Debates route through Moltbook debate engine (debate_metrics → confidence_adjustment)
**Cost control**: Daily budget cap (default $5/day for Kalshi)

---

## Phase 3: Quantitative Strategies

### P3-1: News Sentiment Strategy
**File**: `kalshi_news_sentiment.py` (new)
**NLP source**: Onyx intelligence layer (`onyx_client.py`) + RSS feeds
**Logic**: Sentiment score → YES/NO probability delta → signal if > threshold

### P3-2: Statistical Arbitrage
**File**: `kalshi_stat_arb.py` (new)
**Logic**: Find related event pairs (primary → general election, primary → runoff)
where combined probabilities violate logical constraints. E.g., P(general win) cannot
exceed P(primary win). Flag and trade the mispricing.

### P3-3: Volatility Strategy
**File**: `kalshi_volatility.py` (new)
**Logic**: Price standard deviation over rolling window. High volatility = mean
reversion opportunity. Low volatility followed by jump = breakout.

---

## Phase 4: Command Layer + Observability

### P4-1: Slack Notifier (replaces Telegram)
**File**: `kalshi_slack_notifier.py` (new)
**Channel**: `#quant-lab` (already wired)
**Events**: Trade executed, edge found, circuit breaker triggered, daily P&L

### P4-2: Command Center Kalshi Tab
**File**: Command Center Next.js page `/kalshi`
**Data**: Supabase `kalshi_trades`, `kalshi_bankroll_snapshots`
**Features**: Live P&L, positions, category scores, recent signals

---

## MCP Tools Added (End State)

| Tool | Source | Description |
|---|---|---|
| `run_safe_compounder` | P1-3 | Find near-certain NO-side opportunities |
| `scan_all_kalshi_events` | P1-2 | Full universe scan via Events API |
| `get_kalshi_category_scores` | P2-1 | Show category scoring with block status |
| `run_kalshi_ai_debate` | P2-2 | 5-model ensemble on a specific market |
| `analyze_kalshi_sentiment` | P3-1 | News sentiment for a Kalshi event |
| `find_kalshi_stat_arb` | P3-2 | Related event arbitrage opportunities |
| `get_kalshi_volatility_signals` | P3-3 | Volatility-based trade ideas |
| `run_kalshi_full_pipeline` | All | One call: scan → score → rank → size → alert |

---

## Key Risk Parameters (Battle-Tested from ryanfrigo)

```python
# From 100s of live trades — DO NOT DEVIATE without evidence
MAX_DAILY_LOSS_PCT       = 0.10   # 10% daily loss → halt
MAX_DRAWDOWN_PCT         = 0.15   # 15% portfolio DD → halt
MAX_SECTOR_CONCENTRATION = 0.30   # 30% max in any category
KELLY_FRACTION           = 0.25   # Quarter-Kelly (NOT 0.5 or 0.75)
MAX_POSITION_PCT         = 0.10   # 10% max per trade
MIN_CONFIDENCE           = 0.45   # 45% ensemble confidence threshold
MIN_CATEGORY_SCORE       = 30     # Hard block below this score
BLOCKED_SERIES           = ['KXFED', 'KXCPI', 'KXNFP', 'KXGDP']  # Proven negative edge
TRAILING_TAKE_PROFIT     = 0.20   # 20% gain → trail stop
STOP_LOSS_PCT            = 0.15   # 15% per position
MAX_HOLD_DAYS            = 10     # Time-based exit
DAILY_AI_COST_LIMIT_USD  = 5.00   # Cap AI spend
```

---

## Sports Markets: The Actual Edge

ryanfrigo's data shows the consistently profitable edge:

| Category | Win Rate | ROI | Status |
|---|---|---|---|
| NCAAB | 74% | +10.0% | STRONG ✅ |
| NBA | 52% | +1.5% | WEAK (but positive) 🟡 |
| FED | 32% | -40.0% | BLOCKED 🚫 |
| CPI | 25% | -65.0% | BLOCKED 🚫 |
| ECON_MACRO | 30% | -55.0% | BLOCKED 🚫 |

**Strategy for $250**: Focus Safe Compounder on NCAAB NO-side during March–April
(college basketball season), then NBA NO-side during NBA playoffs (April–June).

Safe Compounder rules on sports:
- Find YES prices ≤ 20¢ (team has < 20% implied win probability)
- Buy NO at market (they're heavy underdogs, NO almost certain)
- Edge: you're getting paid 80¢+ for a 95%+ probability event
- Volume: hundreds of games per week during season

---

## Implementation Timeline

| Phase | Duration | Expected Outcome |
|---|---|---|
| P1 (Critical Fixes) | Day 1 | Safe Compounder live, FED/CPI blocked, Events API working |
| P2 (AI Ensemble + Scoring) | Days 2-3 | Category guard rails + AI consensus gating |
| P3 (Quant Strategies) | Days 4-7 | News sentiment, stat arb, volatility signals |
| P4 (Command Layer) | Days 8-10 | Slack alerts, CC dashboard tab |

---

## Files Created by This Blueprint

```
algochains-mcp-server/src/algochains_mcp/order_flow/
├── kalshi_safe_compounder.py       # P1-3: Safe Compounder strategy
├── kalshi_events_scanner.py        # P1-2: Full universe via Events API
├── kalshi_category_scorer.py       # P2-1: Category scoring + blocklist
├── kalshi_ai_ensemble.py           # P2-2: OpenRouter 5-model ensemble
├── kalshi_news_sentiment.py        # P3-1: News/Onyx sentiment
├── kalshi_stat_arb.py              # P3-2: Statistical arbitrage
├── kalshi_volatility.py            # P3-3: Volatility-based signals
├── kalshi_slack_notifier.py        # P4-1: #quant-lab Slack alerts
├── kalshi_strategy_engine.py       # MODIFIED: block FED/CPI, use Events API
└── kalshi_pipeline.py              # Unified one-call pipeline
```

---
END BLUEPRINT
