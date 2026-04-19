# AlgoChains MCP Server

[![MCP](https://img.shields.io/badge/MCP-2025--11--25-blue?style=flat-square)](https://modelcontextprotocol.io)
[![Tools](https://img.shields.io/badge/tools-407-green?style=flat-square)](#tool-categories)
[![Skills](https://img.shields.io/badge/skills-472-orange?style=flat-square)](#skills-bridge)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue?style=flat-square)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-purple?style=flat-square)](LICENSE)
[![Version](https://img.shields.io/badge/version-26.0-blueviolet?style=flat-square)](#changelog)
[![Docs](https://img.shields.io/badge/gotchas-docs%2FGOTCHAS__AND__BUGS.md-red?style=flat-square)](docs/GOTCHAS_AND_BUGS.md)

---

## What Is This? (Plain English)

**Problem:** You want your AI assistant (Claude, Cursor, ChatGPT) to actually interact with your trading accounts — look up prices, check positions, run backtests, monitor bots. But AI tools are isolated; they can't call Tradovate or Alpaca directly.

**Solution:** The AlgoChains MCP Server is a **bridge**. It sits between your AI and your trading infrastructure. When you ask Claude "What's my MNQ P&L today?", Claude calls this server, this server calls Tradovate, and the answer comes back.

```
You ask Claude:               Claude calls:                Server calls:
"What's my NQ position?" → get_positions() → Tradovate API → returns real data
"Run a backtest on GBPUSD" → run_backtest() → Rust engine → returns real metrics
"Is the market trending?" → detect_market_regime() → Polygon + FRED → returns analysis
```

**What it is NOT:**
- It is NOT a trading bot by itself (the bots are separate processes)
- It is NOT investment advice
- It is NOT a managed account service
- It is NOT a product sold to the public (this is Tyler's personal experimental infrastructure)

**What it IS:**
- Tyler's personal trading command center accessible from any AI tool
- An experimental platform for AI-assisted strategy research and execution
- A team knowledge base (via Onyx) that all team members can search

---

## Safety First — Read This Before Connecting Live Accounts

**New to this? Start in demo mode — no credentials, no risk:**
```bash
python scripts/quickstart.py --mode demo
```

**Connecting a live broker account is only for Tyler currently.** Everyone else on the team should use demo or read-only mode (see [SAFETY_MODEL.md](SAFETY_MODEL.md)).

**Roo, Eric, RJ — your setup is 2 minutes and zero financial risk. [Jump to Team Setup →](#team-access)**

**The guardrails that prevent the AI from losing money:**
- Hard-coded daily loss limit: $500 (cannot be overridden by AI)
- Hard-coded max drawdown: 15% (cannot be overridden by AI)
- Human confirmation required for all orders above $10K notional
- AI loop detection: blocks all orders after 5 identical calls in 60s
- VIX gate: all trades blocked when VIX > 35

Full safety documentation: [SAFETY_MODEL.md](SAFETY_MODEL.md)

---

## Quick Start

### Option A: Demo Mode (No Credentials — 1 Minute)

```bash
pip install algochains-mcp-server
python scripts/quickstart.py --mode demo
# → Validates setup, generates IDE config, no broker needed
```

What you can do in demo mode:
- `get_quote("AAPL")` — live price for any symbol
- `detect_market_regime()` — is the market trending or ranging?
- `get_macro_signals()` — macro environment analysis
- `discover_tools()` — find the right tool for any task
- `onyx_ask("any question")` — search the knowledge base (if ONYX_API_URL set)

### Option B: Paper Mode (Alpaca Paper — Free, No Real Money)

```bash
# 1. Get a free Alpaca paper account: https://app.alpaca.markets/paper
# 2. Set credentials
export ALPACA_API_KEY=your-paper-key
export ALPACA_SECRET_KEY=your-paper-secret
export ALPACA_PAPER=true

# 3. Run
python scripts/quickstart.py --mode paper
```

### Option C: Full Live Setup (Tyler's Config)

Read [SAFETY_MODEL.md](SAFETY_MODEL.md) first, then:
```bash
# Copy .env.example and fill in your credentials
cp .env.example .env
# Edit .env with your real keys

# Run health check
python scripts/quickstart.py --health-check --mode live
```

### Generate Your IDE Config

```bash
python scripts/quickstart.py --generate-config cursor         # Cursor
python scripts/quickstart.py --generate-config claude-desktop # Claude Desktop
python scripts/quickstart.py --generate-config windsurf       # Windsurf
```

---

## For Oleg (Planex) — Developer Handoff

The MCP server has implemented the backend for all critical + high-value tasks from Eric's list. Here's exactly what the frontend (algochains.ai Next.js) needs to wire up:

| Task | Backend API | Frontend Action Needed |
|------|-------------|------------------------|
| **Support Page → Notion** | `POST /mcp/create_support_ticket` | Connect Support form → this endpoint. Pass subject, description, user_email, category, priority |
| **Fix multi-bot metrics** | `GET /mcp/get_all_user_bots` | Dashboard must call this per user, render fallback states (BROKER_NOT_CONNECTED / METRICS_PENDING) |
| **Schwab OAuth** | `GET /mcp/generate_broker_auth_url?broker=schwab` → callback → `exchange_broker_oauth_code` | Brokers page: "Connect Schwab" button triggers this 2-step flow |
| **Join Waitlist** | `POST /mcp/join_waitlist` | Wire waitlist form → this. Email confirmation is automatic |
| **Brokers Page UI** | `GET /mcp/get_connected_brokers` + `POST /mcp/revoke_broker_connection` | Show connected brokers, connect/disconnect buttons per broker |
| **Purchase verification** | `POST /mcp/send_email_verification_code` + `POST /mcp/verify_code` | On checkout, send code to email, verify before processing payment |
| **Analytics** | `POST /mcp/track_platform_event` | Add to every page (page_view), signup flow, broker connect |
| **Password reset** | `POST /mcp/initiate_password_reset` → `POST /mcp/complete_password_reset` | Auth flow: Forgot Password page + reset page |
| **Analytics dashboard** | `GET /mcp/get_analytics_summary?days=7` | Admin dashboard panel |

**Run the Supabase migration first:**
```bash
# Apply to your Supabase project
supabase db push --file supabase/migrations/20260406_platform_tables.sql
# Or paste the SQL in: Supabase Dashboard → SQL Editor → Run
```

**Required new env vars** (see `.env.example` for all):
```
SUPABASE_SERVICE_KEY=     # Service role key from Supabase Settings → API
RESEND_API_KEY=           # From resend.com — for emails
SCHWAB_CLIENT_ID=         # From developer.schwab.com
SCHWAB_CLIENT_SECRET=
NOTION_API_KEY=           # Optional — for ticket sync to Notion
NOTION_SUPPORT_DB_ID=     # Optional — Notion database ID
```

---

## Team Access

**5-person team (Tyler, Roo, Eric, RJ, +1) — different access levels:**

### Read-Only Team Members (Roo, Eric, RJ)

Get access to bot metrics, knowledge base, and market data. **No broker credentials needed.**

```bash
# Get from Tyler: ALGOCHAINS_BRIDGE_API_KEY and ONYX_API_URL
export ALGOCHAINS_BRIDGE_API_KEY=<ask-tyler>
export ONYX_API_URL=http://100.89.114.31:8085    # Desktop Onyx via Tailscale

python scripts/quickstart.py --mode demo
python scripts/quickstart.py --generate-config cursor
```

Once connected, you can ask your AI:
- "What's the current status of Tyler's MNQ bot?"
- "What are the validation gates for marketplace submission?"
- "How does the Token Guardian work?"
- "Show me recent AlphaLoop evolution results"

### Shared Knowledge Base (Onyx)

All team members' AI tools point at the same Onyx knowledge base. When Tyler runs a research cycle or makes a decision, it's automatically indexed. When you ask your AI about it, it finds it.

Full team guide: [SAFETY_MODEL.md — Team Access Setup](SAFETY_MODEL.md#team-access-setup)

---

## Tradovate API Parity

AlgoChains already covers and exceeds the surface area of the community
[mcp-tradovate](https://github.com/0xjmp/mcp-tradovate) Go server. The detailed mapping of every
Tradovate REST endpoint to its AlgoChains equivalent (plus gap opportunities and the governance
rule against running both MCPs concurrently) lives in:

**[docs/TRADOVATE_PARITY.md](docs/TRADOVATE_PARITY.md)**

Key points:
- `get_positions`, `place_order`, `cancel_order`, `get_fills`, `get_historical_data`, `get_quote` — all covered via `TradovateConnector` in `brokers/tradovate.py`.
- `authenticate` — handled exclusively by Token Guardian. Never run a second auth session alongside live bots.
- Risk limits, bracket tracking, bot process health — unique AlgoChains capabilities with no equivalent in the community server.
- Low-priority gaps (`search_tradovate_contracts`, `get_tradovate_risk_snapshot`) are documented in the parity file with implementation sketches.

---

## Desktop Tower Transfer Guide

> **Use this when switching development from the MacBook to the desktop tower (teespc-1 / `100.89.114.31`) or picking up where you left off.**

### What Runs Where

| Component | MacBook (primary) | Desktop Tower (teespc-1) |
|-----------|:-----------------:|:------------------------:|
| Live Tradovate bots (MNQ, CL, MES, NQ) | ✅ launchd | — |
| Kalshi daemon (autonomous execution) | ✅ launchd | — |
| Token Guardian | ✅ launchd | — |
| Command Center (`:3333`) | ✅ cloudflared tunnel | — |
| Onyx RAG stack | — | ✅ `:8085` |
| GPU/ML workloads (FinBERT, Kronos, vLLM) | — | ✅ |
| Heavy backtests via `dispatch_tower_job` | sends job | ✅ executes |
| Code (synced repo) | source | receives rsync |

**Rule:** Execution (live orders, daemons) stays on Mac. Development and ML work can happen on either machine. The desktop tower is never a primary execution node.

---

### Step 1 — Verify Desktop Connectivity

```bash
# Check Tailscale sees the tower
tailscale status | grep teespc

# Test SSH (Windows OpenSSH on port 22)
ssh -p 22 tyler@100.89.114.31 "echo OK"

# If using WSL2 on the tower, it may listen on port 2222 instead:
ssh -p 2222 trrey@100.89.114.31 "echo OK"
```

> **Canonical IP:** `100.89.114.31` (teespc-1). Any docs or scripts referencing `100.99.127.119` are stale — ignore them.

---

### Step 2 — Sync Code to Desktop

Run from the MacBook (dry-run by default):

```bash
# Review what would be synced (safe, no changes)
bash scripts/desktop_sync.sh

# Apply the sync (copies both control-tower + mcp-server)
bash scripts/desktop_sync.sh --live

# Just check connectivity without syncing
bash scripts/desktop_sync.sh --check
```

**What is NOT synced** (intentionally excluded):
- `.env` — secrets must be set manually on the tower (see Step 3)
- `logs/`, `state/`, `data/` — runtime state stays local to each machine
- `.git/`, `node_modules/`, `.next/` — rebuild on the tower
- `*.db-shm`, `*.db-wal`, `vendor/`, `.kronos_cache/`

---

### Step 3 — Set Up Desktop Environment

**On the desktop tower (WSL2 Ubuntu or Windows with Python):**

```bash
# 1. Navigate to synced repos
cd /home/trrey/algochains-mcp-server

# 2. Create Python 3.11+ virtual environment
python3.11 -m venv .venv && source .venv/bin/activate

# 3. Install all dependencies (full stack)
pip install -e ".[full_v21]"

# 4. Copy and populate .env (secrets not synced by rsync)
cp .env.example .env
# Edit .env and fill in: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY,
# KALSHI_ACCESS_KEY, KALSHI_PRIVATE_KEY_PATH, TRADOVATE_*, SLACK_BOT_TOKEN,
# OPENROUTER_API_KEY, ONYX_API_URL=http://localhost:8085

# 5. Repeat for control-tower
cd /home/trrey/algochains-control-tower
cp .env.example .env   # fill same keys
```

**Key env vars the desktop needs** (at minimum for Kalshi + MCP):

| Variable | Source |
|----------|--------|
| `SUPABASE_URL` | Supabase dashboard |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase dashboard → API settings |
| `KALSHI_ACCESS_KEY` | Kalshi API portal |
| `KALSHI_PRIVATE_KEY_PATH` | Path to RSA private key on desktop |
| `OPENROUTER_API_KEY` | OpenRouter dashboard |
| `SLACK_BOT_TOKEN` | Slack app settings |
| `ONYX_API_URL` | `http://localhost:8085` (Onyx runs locally on tower) |

---

### Step 4 — Smoke Test on Desktop

```bash
# Verify MCP server starts
cd /home/trrey/algochains-mcp-server
source .venv/bin/activate
python scripts/quickstart.py --mode demo

# Run Kalshi pipeline dry-run (no real orders)
cd /home/trrey/algochains-control-tower
python3 autonomous/kalshi_daemon.py --once

# Run with execution (manual one-shot, not daemon)
python3 autonomous/kalshi_daemon.py --once --execute --confirmed
```

> **Important:** Do NOT run `--daemon` on the desktop tower for bots that also run on Mac. The Mac launchd is the single authoritative execution node. Run `--once` for ad-hoc testing only.

---

### Step 5 — GPU / ML Workloads

The desktop runs Onyx (RAG), Kronos (foundation model), and heavy backtests. From the Mac:

```bash
# Dispatch a backtest job to the desktop GPU
python3 -c "
from algochains_mcp.algoclaw.desktop_tower import dispatch_tower_job
dispatch_tower_job('backtest', {'strategy': 'mnq_scalper', 'lookback_days': 90})
"

# Check desktop Onyx is reachable
curl http://100.89.114.31:8085/health
```

---

### Audit: Mac vs Desktop (as of Apr 2026)

| Service | Mac PID / launchd | Desktop |
|---------|:-----------------:|:-------:|
| MNQ bot (`FUTURES_SCALPER_UPGRADED.py`) | `com.algochains.futures-scalper-upgraded` | — |
| CL bot (`CL_FUTURES_SCALPER.py`) | `com.algochains.cl-futures-scalper` | — |
| MES bot (`mes_swing_live.py`) | `com.algochains.mes-bot` | — |
| NQ bot (`nq_swing_live.py`) | `com.algochains.nq-bot` | — |
| Kalshi daemon | `com.algochains.kalshi-bot` | — |
| Token Guardian | `com.algochains.token-guardian` | — |
| Autonomous Watchdog | `com.algochains.autonomous-watchdog` | — |
| Onyx RAG | — | port 8085 |
| MCP server (HTTP bridge) | `com.algochains.mcp-bridge` | dev only |
| Command Center UI | `com.algochains.command-center` (`:3333`) | — |

To list all active Mac launchd services:
```bash
launchctl list | grep com.algochains
```

---

## Tool Starter Pack — Start Here, Not With 400 Tools

Most first-time use cases are covered by these tools. All are safe in demo mode unless marked.

| Tool | What It Does | Safe In Demo? |
|------|-------------|:---:|
| `get_quote` | Live price for any symbol | ✅ |
| `detect_market_regime` | Trending / ranging / choppy | ✅ |
| `get_macro_signals` | Risk-on / risk-off macro state | ✅ |
| `discover_tools` | Find the right tool with natural language | ✅ |
| `onyx_ask` | Ask the knowledge base anything | ✅ |
| `run_backtest` | Test a strategy on real historical data | ✅ |
| `validate_strategy` | Check if strategy passes quality gates | ✅ |
| `get_positions` | What am I currently holding? | ✅ |
| `get_live_bot_metrics` | Tyler's live bot P&L (read-only) | ✅ |
| `compute_gex` | Dealer gamma exposure for options | ✅ |
| `check_all_broker_credentials` | Masked status for all 15 brokers | ✅ |
| `evaluate_strategy_for_prop_fund` | Which prop funds fit your strategy? | ✅ |
| `compute_r_multiple_size` | Van Tharp R-Multiple position sizing | ✅ |
| `compute_option_greeks` | Black-Scholes delta/gamma/theta/vega | ✅ |
| `check_rithmic_status` | Rithmic connector + vendor agreement status | ✅ |
| `place_order` | Execute a real trade | ⚠️ Live money |
| `flatten_all_positions` | Close everything | 🔴 Irreversible |

Every tool has a `danger_tier` (0-3) accessible via the `/tools` endpoint. See [Tool Danger Tiers →](#tool-danger-tiers)

### Agentic AI Quick-Start Patterns

These are the exact prompts that work well in Claude / Cursor / GPT-4:

```
# Morning brief
"Run get_macro_signals and get_live_bot_metrics. Summarize market conditions and P&L."

# Prop fund compatibility check
"Use evaluate_strategy_for_prop_fund for MNQ scalper with $600 max daily loss, $2500 max DD,
 $120 avg daily profit, holds overnight. Which fund should I target?"

# Prop fund risk sizing
"Compute R-Multiple position size for MNQ entry at 18050, stop at 17990, capital $50K, 1% risk."

# Pre-trade regime check
"Before I place any orders, run detect_market_regime and check VIX. Should I trade today?"

# Emergency system check
"Run check_all_broker_credentials and check_rithmic_status. What's ready, what's missing?"

# Research a new strategy
"Use onyx_ask to find if we've ever backtested an MES mean-reversion strategy. Then run
 search_ssrn_strategies for similar academic papers."

# Validate a backtest
"Run validate_strategy with these results: Sharpe 2.4, MaxDD 9%, WinRate 58%, 180 trades.
 Does it pass the MCPT gate? What's the DSR?"

# Options analysis
"Compute option greeks for AAPL $185 call expiring 30 days out with spot at $182, IV 28%."

# Full system health
"Check bot health, token status, and all active positions. Any issues?"
```

---

## Tool Categories

### Core Tools (Tier 0 — Safe For Everyone)

**Market Data**
```
get_quote           get_ohlcv           get_tick_data
get_options_chain   get_futures_curve   get_level2
get_footprint_chart compute_cumulative_delta  get_dark_pool_volume
get_vix_term_structure  get_yield_curve  get_credit_spreads
get_macro_signals   detect_market_regime
```

**Signals & Analysis**
```
generate_signal     get_regime_state    get_ensemble_vote
compute_gex         unusual_options_activity  read_tape
pair_trade_signal   compute_kelly       compute_vwap
```

**Research & Backtesting**
```
run_backtest        validate_strategy   optimize_strategy
walk_forward_test   run_mcpt_validation compute_sharpe
analyze_overfitting search_ssrn_strategies
```

**Intelligence**
```
onyx_ask            onyx_search         discover_tools
get_earnings_catalyst  get_congressional_trades
get_prediction_markets  get_news_sentiment
```

**Skills Bridge (V22.7 — 472 Skills Indexed)**
```
list_skills          get_skill_detail     search_skills
get_skills_for_task  reload_skills_registry
```

**Agent Memory & Regime (V22.7)**
```
get_openclaw_memory  store_trade_lesson   get_current_regime
get_bot_heartbeat_openclaw               get_agent_evaluations
get_openclaw_state_summary
```

**Skill Execution Shortcuts (V22.7)**
```
invoke_moltbook_debate   run_mcpt_pipeline   run_regime_detection
```

**Position Sizing (v24.0)**
```
compute_r_multiple_size          compute_volatility_targeted_size
compute_idm                      compute_forecast_scalar
dual_size_conservative
```

**Options Analytics (v24.0)**
```
compute_option_greeks            find_optimal_strike
```

**Prop Fund Pipeline (v24.0)**
```
evaluate_strategy_for_prop_fund  simulate_prop_fund_evaluation
list_prop_funds                  get_prop_fund_rules
register_prop_fund_account       get_prop_fund_monitor_status
run_prop_fund_check              check_rithmic_status
```

**Broker Credential Management (v24.0)**
```
check_broker_credentials         check_all_broker_credentials
get_broker_onboarding_guide      get_prop_fund_broker_options
```

**Account Protection (v23.0)**
```
check_protection_status          record_stop_event
lock_instrument                  unlock_instrument
check_rate_limit_status
```

**Performance Reporting (v23.0)**
```
generate_bot_tearsheet           get_bot_metrics_full
```

### Infrastructure Tools (Tier 1 — Internal State)

```
create_price_alert  connect_broker      build_strategy
deploy_strategy     ingest_csv_data     register_strategy
submit_to_marketplace  run_onyx_ingest  store_api_key
```

### Order Execution (Tier 2 — Live Money ⚠️)

```
place_order         place_bracket_order  place_oco_order
modify_order        cancel_order         close_position
smart_route_order   execute_twap         execute_vwap
```

### Destructive Actions (Tier 3 — Irreversible 🔴)

```
flatten_all_positions   cancel_all_orders   emergency_stop
reset_daily_loss_limit  trip_circuit_breaker
```

---

## Tool Danger Tiers

Every tool is classified by danger tier. The HTTP bridge `/tools` endpoint returns this for every tool:

```json
{
  "tool": "place_order",
  "danger_tier": 2,
  "danger_label": "ORDER_EXEC",
  "danger_description": "Executes real orders on a live broker account. Requires human confirmation.",
  "safe_in_demo_mode": false,
  "safe_in_paper_mode": false,
  "requires_live_account": true,
  "requires_human_confirmation": true,
  "irreversible": false
}
```

Use this in your agent prompts to prevent accidental order execution:
```python
# Before calling any tool, check its danger tier
info = get_tool_danger_info("flatten_all_positions")
# → danger_tier: 3, irreversible: true
```

---

## Live Bot Showcase

AlgoChains runs 4 live futures bots on Tradovate. Their real-time metrics stream through this MCP server:

| Bot | Symbol | Strategy | Live Since |
|-----|--------|----------|-----------|
| `MNQ_Upgraded_Scalper` | MNQ | 7-AI ensemble, 5-min | Dec 2024 |
| `CL_Swing_Scalper` | CL | FinBERT sentiment + momentum | Jan 2025 |
| `MES_EMA_Swing` | MES | EMA pullback + regime detection | Feb 2025 |
| `NQ_EMA_Swing` | NQ | Trend following + foundation model | Feb 2025 |

Read live metrics (no credentials needed if you have the bridge API key):
```python
metrics = get_live_bot_metrics(bot_name="MNQ_Upgraded_Scalper")
```

---

## Architecture

```
Your AI (Claude / Cursor / ChatGPT)
         │
         │ MCP protocol (stdio or HTTP)
         ▼
AlgoChains MCP Server
  ├── 350+ tools (market data, execution, research, intelligence)
  ├── Trading Guardrails (hard-coded limits, AI loop detection)
  ├── Account Protection (12 pre-trade guards)
  ├── Onyx RAG Knowledge Base (semantic search over 400+ docs)
  └── Circuit Breakers (per-tool rate limits, daily loss stops)
         │
         ├── Tradovate (MNQ, CL, MES, NQ futures)
         ├── Alpaca (equities, crypto, options)
         ├── OANDA (forex)
         ├── Polygon (real-time bars, options)
         ├── Databento (tick-level data)
         └── FRED, CBOE, Polymarket, SEC EDGAR (macro/alt data)
```

### Data Policy

**No synthetic data. No mock fills. No placeholder values. Everywhere.**

- Every tool connects to a real API or fails closed with an explicit error
- If a data source is unavailable: the tool returns an error, not fake data
- Backtest metrics are computed on real historical data (Databento tick archives)
- P&L figures are from real broker fills (Tradovate/Alpaca)

---

## Failure Modes — What Happens If Things Go Wrong

| Failure | What Happens | What To Do |
|---------|-------------|-----------|
| AI sends wrong order | Guardrails reject if outside limits; elicitation confirmation required above $10K | Review guardrail config; start with paper mode |
| Server crashes mid-trade | Open positions REMAIN — not auto-closed | Check broker app directly; close manually |
| Token expires | Orders blocked with 401 error; positions unaffected | Run `python3 tradovate_token_guardian.py` |
| AI loops | 5 identical calls → all orders blocked 30min | Restart bot process to reset |
| Max daily loss hit | All orders blocked until midnight | Wait or reset manually (owner only) |

Full failure mode documentation: [SAFETY_MODEL.md](SAFETY_MODEL.md)

---

## Configuration Reference

### Required (for live trading)
```bash
# Tradovate (futures)
TRADOVATE_USERNAME=...         # login email
TRADOVATE_PASSWORD=...         # login password
TRADOVATE_APP_ID=...           # from https://trader.tradovate.com/account
TRADOVATE_APP_SECRET=...

# Alpaca (equities/crypto)
ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...
ALPACA_PAPER=true              # set false only for live trading
```

### Optional (enhances capabilities)
```bash
# Market data
POLYGON_API_KEY=...            # https://polygon.io — real-time bars
DATABENTO_API_KEY=...          # https://databento.com — tick data
FRED_API_KEY=...               # https://fred.stlouisfed.org — free

# Knowledge base
ONYX_API_URL=http://...        # self-hosted Onyx instance URL
ONYX_API_KEY=...               # Onyx API key

# AI ensemble
OPENAI_API_KEY=...             # for GPT-4o in debate engine
ANTHROPIC_API_KEY=...          # for Claude in debate engine

# Notifications
SLACK_WEBHOOK_URL=...          # alert channel
ALGOCHAINS_BRIDGE_API_KEY=...  # HTTP bridge authentication

# Security
KEY_VAULT_PASSPHRASE=...       # AES-256-GCM vault master key

# Marketplace
STRIPE_SECRET_KEY=...          # Stripe Connect billing
LISTING_API_KEY=...            # algochains.ai marketplace API
```

---

## Supported Brokers

| Broker | Asset Classes | Status | Connector |
|--------|--------------|--------|-----------|
| **Tradovate** | Futures (MNQ, CL, MES, NQ, ES, GC) | ✅ Live | `brokers/tradovate.py` |
| **Alpaca** | Equities, ETFs, Options, Crypto | ✅ Live + Paper | `brokers/alpaca_connector.py` |
| **OANDA** | Forex (50+ pairs) | ✅ Live | `brokers/oanda_connector.py` |
| **Interactive Brokers** | Stocks, Futures, Options, Forex | ✅ Live (`ib_async`) | `brokers/ibkr_connector.py` |
| **Kalshi** | Prediction markets (US events) | ✅ Live | `brokers/kalshi_connector.py` |
| **Polymarket** | Prediction markets (crypto/global) | ✅ Live | `brokers/polymarket.py` |
| **E*TRADE** | Equities, Options, ETFs | ✅ OAuth 1.0a | `brokers/etrade_connector.py` |
| **Charles Schwab** | Equities, Options, Futures | ⚠️ Stubs (OAuth 2.0 PKCE) | `brokers/schwab_connector.py` |
| **Rithmic** | Futures via prop fund platforms | ⏳ DRY_RUN (vendor NDA pending) | `brokers/rithmic_connector.py` |
| **QuantConnect** | Cloud algorithm management | ✅ Research/deploy only | `brokers/quantconnect_connector.py` |

**Credential status for all brokers:**
```bash
# In Claude / Cursor — ask the MCP server:
check_all_broker_credentials()         # masked status for all 15 brokers
get_broker_onboarding_guide("rithmic") # step-by-step setup for any broker
get_prop_fund_broker_options()         # which brokers support prop funds
```

---

## Prop Fund Pipeline (v24.0)

AlgoChains automates the full path from validated strategy to funded prop account — no manual steps after setup.

**Revenue math (Apex $50K, MNQ scalper):**

```
Evaluation fee:  $147 (subscriber pays once)
Expected P&L:    ~$2,400/month → 90% split → $2,160/month to subscriber
AlgoChains sub:  $149/month = $1,788/year ARR
Pass rate:       ~78% of evaluations (based on drawdown simulation)
Subscriber LTV:  $1,788 × 18 months = $3,218
```

**Supported prop funds (all use Rithmic infrastructure):**

| Fund | Account Sizes | Daily Loss | Trailing DD | Overnight | Consistency Rule |
|------|--------------|-----------|-------------|-----------|-----------------|
| **Apex** | $25K–$300K | $1.5K–$5K | 6% of size | ✅ | None |
| **TradeDay** | $10K–$150K | $500–$2K | 5% of size | ✅ | None |
| **Bulenox** | $10K–$100K | $500–$2K | 5% of size | ✅ | None |
| **Topstep** | $50K–$150K | $1K–$2K | 5% of size | ❌ | 30% daily limit |
| **MyFundedFutures** | $10K–$200K | $500–$2K | 5% of size | ❌ | 40% daily limit |
| **Earn2Trade** | $10K–$200K | $500–$2K | 6% of size | ❌ | 50% daily limit |
| **FTMO** | $10K–$400K | $500–$4K | 5% of size | ✅ | None (forex only) |

**MCP tools for prop fund workflow:**

```python
# Check which funds are compatible with your strategy
evaluate_strategy_for_prop_fund(
    strategy_name="MNQ Scalper",
    symbol="MNQ",
    max_daily_loss_usd=600,
    max_drawdown_usd=2500,
    avg_profit_per_day_usd=120,
    holds_overnight=True,
)

# Simulate evaluation P&L against fund rules
simulate_prop_fund_evaluation(
    fund_name="apex",
    daily_pnl_series=[-80, 120, 200, -150, 90, ...],
    account_size_usd=50000,
)

# Register an account for real-time monitoring
register_prop_fund_account(
    account_id="ABC123",
    fund_name="apex",
    broker="rithmic",        # or "tradovate" for Apex demo
    account_size_usd=50000,
)

# Check live evaluation status
get_prop_fund_monitor_status()  # all registered accounts

# Check Rithmic connection status
check_rithmic_status()          # dry_run_mode, credentials_configured, vendor_agreement link
```

**Drawdown monitoring daemon** (`autonomous/prop_fund_monitor.py`):
```bash
# Runs every 30 min during market hours (9:30–16:00 ET, Mon–Fri)
python autonomous/prop_fund_monitor.py --check-now   # immediate check
python autonomous/prop_fund_monitor.py --status       # print current state
python autonomous/prop_fund_monitor.py --daemon       # continuous mode
```

Alert tiers (ntfy + Slack):
- `70%` daily limit → ⚠️ scale down size
- `85%` daily limit → 🚨 stop new entries
- `95%` daily limit → 🔴 **EMERGENCY FLATTEN** (automatic)

**Status:** Rithmic connector built. Awaiting vendor NDA: https://www.rithmic.com/contacts

---

## Marketplace

The AlgoChains marketplace allows strategy creators to publish validated bots and charge subscribers.

**For creators:** [MARKETPLACE_CREATOR_GUIDE.md](MARKETPLACE_CREATOR_GUIDE.md)

**Submission requirements:**
- Sharpe Ratio > 2.0 (out-of-sample)
- Win Rate > 55%
- Max Drawdown < 15%
- Minimum 50 trades in test period
- Pass Deflated Sharpe (MCPT) test

**Revenue split:** Creator 70% / Platform 30% via Stripe Connect.

---

## AlgoClaw — Agent Skill System (v25.0)

AlgoClaw is the OpenClaw-like autonomous skill layer embedded in this MCP server.
Every subscriber gets 21 trading-specific skills out of the box.

**Run any skill:**
```bash
python algoclaw/cli.py bot-health          # check all 4 live bots
python algoclaw/cli.py prop-fund-check     # check prop fund evaluation accounts
python algoclaw/cli.py position-size --param symbol=MNQ entry=18050 stop=17990 capital=50000
python algoclaw/cli.py security-posture    # CoSAI + SAFE-MCP coverage audit
python algoclaw/cli.py --list              # all skills with tier + status
```

**From AI (Claude / Cursor):**
```
"Run bot-health AlgoClaw skill"       → run_algoclaw_skill("bot-health")
"What AlgoClaw skills exist?"         → list_algoclaw_skills()
"AlgoClaw status"                     → get_algoclaw_status()
```

| Tier | Skills | Purpose |
|------|--------|---------|
| 0 | bot-health, credential-audit, regime-scan, position-size, security-posture | Daily essentials |
| 1 | mcpt-validate, tearsheet-gen, options-scan, gex-monitor | Research |
| 2 | prop-fund-check, prop-fund-match, rithmic-status | Prop fund pipeline |
| 3 | kill-switch | Emergency (owner-only) |
| 4 | marketplace-audit, portfolio-optimize | Marketplace |

See: [`algoclaw/README.md`](algoclaw/README.md) | Full blueprint: `blueprints/ALGOCLAW_BLUEPRINT.md`

---

## Command Center (cc.algochains.io)

The AlgoChains Command Center is a real-time Next.js dashboard.

| URL | Status | Notes |
|-----|--------|-------|
| **https://cc.algochains.io** | Live (Cloudflare Access) | Authenticate with tyler@algochains.io |
| **https://cc.algochains.ai** | Live (same tunnel) | Alternative domain |
| http://localhost:3333 | Local dev | Always accessible without auth |

**Marketplace subscribe links** point to **https://algochains.ai** (separate marketing site).

**Run locally:**
```bash
cd algochains-command-center
npm run dev              # starts on http://localhost:3333
```

**Start Cloudflare tunnel (required for external access):**
```bash
# Start tunnel (run after every Mac restart)
cloudflared tunnel run def269f2-6c52-471a-9648-c2fe631bc9bf >> logs/cloudflared_cc.log 2>&1 &

# Verify
curl -sI https://cc.algochains.io | head -3
# Expected: HTTP/2 200 (if authenticated in browser) or HTTP/2 403 (Cloudflare Access gate)
```

> ⚠ **403 from curl is EXPECTED.** Cloudflare Access blocks unauthenticated requests. Open in browser and authenticate with Google SSO (`tyler@algochains.io`).

**V22 Dashboard rows:**
- Row 1: Bot Status Cards (process, uptime, last signal, AI confidence, errors)
- Row 2: P&L Chart + Positions Table + Risk Dashboard
- Row 3: **Bracket Status Panel** + **AI Ensemble Health** + **Live Trade Validation Feed (SSE)**
- Row 4: **Subscriber Protection Panel** + System Health
- Row 5: Skills Panel + MCP Tools

**V22 New capabilities:**
- `GET /api/bots/stream` — Server-Sent Events feed: entry→fill→bracket→exit in real time
- `POST /api/bots/restart` — Kill + restart any bot (confirm: "RESTART")
- `GET /api/bots` — Includes `bracketStatus`, `positionState`, `pipelineHealth` per bot
- Marketplace cards link to `algochains.ai/marketplace/{symbol}?ref=cc`

**Pages:**
- `/` — V22 dashboard (brackets, AI ensemble, live trade feed)
- `/algoclaw` — AlgoClaw skill runner + ecosystem map
- `/prop-funds` — Prop fund pipeline + evaluation accounts
- `/marketplace` — Strategy cards with subscribe → algochains.ai
- `/subscribers` — Subscriber protection metrics + risk flags
- `/setup` — MCP server setup wizard

---

## Developer Guide

### Adding a New Tool

```python
# 1. Register in server.py TOOLS_ANNOTATED list
Tool(
    name="my_new_tool",
    description="What it does. What real data source it uses. Never synthetic.",
    inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]},
    annotations=ANNOT_READ_EXTERNAL,  # Use appropriate annotation
)

# 2. Add dispatch in _dispatch_tool()
elif name == "my_new_tool":
    # Always real API, never synthetic
    result = await real_api.fetch(arguments["symbol"])
    if result is None:
        return _text({"error": "Real data unavailable — check API key"})
    return _text(result)

# 3. Add danger tier in tool_danger_tiers.py
"my_new_tool": TIER_READ_ONLY,  # or appropriate tier

# 4. Update tool_manifest.py if needed for CI visibility
```

### Builder SDK — Strategy Templates

```python
from algochains_mcp.builder_sdk.templates.sma_crossover import SMACrossover
import backtrader as bt

cerebro = bt.Cerebro()
cerebro.addstrategy(SMACrossover, fast=20, slow=50)
cerebro.adddata(your_data_feed)
cerebro.run()
```

### Health Check

```bash
# Full health check
python scripts/quickstart.py --health-check

# Verify specific mode
python scripts/quickstart.py --health-check --mode live

# Quick module check
python3 -c "from algochains_mcp import server; print('OK')"
```

---

## Skills Bridge — 472 Skills Accessible via MCP (V22.7)

All OpenClaw, Windsurf, Cursor, and Claude skills are now discoverable and readable through the AlgoChains MCP server. AI agents no longer need to switch contexts to find the right skill.

### Skill Counts by Platform

| Platform | Skills | Location |
|----------|--------|----------|
| OpenClaw | 334 | `~/.openclaw/skills/` |
| Windsurf | 126 | `.windsurf/skills/` (control-tower) |
| Cursor | 7 | `~/.cursor/skills-cursor/` |
| Claude | 5 | `.claude/skills/` (control-tower) |
| **Total** | **472** | |

### Skill Categories Available

`trading` · `research` · `operations` · `intelligence` · `agent` · `comms` · `risk` · `data` · `ml` · `marketplace`

### How to Use (for AI agents)

```python
# 1. Discover skills for your task
get_skills_for_task(task_description="detect regime change and adjust position sizing")

# 2. Browse all skills in a category
list_skills(category="trading", limit=20)

# 3. Read full SKILL.md instructions
get_skill_detail(name="moltbook-debate")

# 4. Search by keyword
search_skills(query="dark pool unusual options regime")

# 5. Invoke the debate engine directly
invoke_moltbook_debate(symbol="MNQ", direction="LONG", confidence=72.5)

# 6. Run MCPT pipeline
run_mcpt_pipeline(step="decay", dry_run=True)

# 7. Read agent memory
get_openclaw_memory(key_prefix="trade")

# 8. Read current regime
get_current_regime()
```

### Onyx Now Indexes All Skills

The Onyx knowledge brain at `100.89.114.31:8085` now indexes all 472 skills plus OpenClaw memory state files. Ask Onyx questions like:

- "Which skills handle regime detection?"
- "What is the moltbook-debate skill and when should I use it?"
- "What are the backtest governance rules?"
- "What has the agent memory stored about MNQ trades?"

Configuration: [config/onyx_connectors.json](../algochains-control-tower/config/onyx_connectors.json)

Blueprint: [blueprints/SKILLS_BRIDGE_BLUEPRINT.md](blueprints/SKILLS_BRIDGE_BLUEPRINT.md)

---

## Prediction markets + Django signal propagation (Roo / Michael)

**v22.8 update** — Gap analysis vs [berlinbra/polymarket-mcp](https://github.com/berlinbra/polymarket-mcp) (⭐130) and [9crusher/mcp-server-kalshi](https://github.com/9crusher/mcp-server-kalshi) (⭐18) identified and filled 4 genuine gaps. We now go beyond both external MCPs.

### Full Prediction Market Tool Suite

| Tool | Platform | Auth | Description |
|------|----------|------|-------------|
| `get_prediction_markets` | Both | None | Live macro signals (Fed, elections) mapped to equity signals |
| `search_prediction_markets` | Both | None | Keyword search with YES/NO prices, volume, URLs |
| `get_polymarket_high_volume` | Polymarket | None | Top 24h-volume markets |
| `get_polymarket_market` | Polymarket | None | **NEW** — Specific market by slug or ID (title, prices, vol, liquidity) |
| `get_polymarket_market_history` | Polymarket | None | **NEW** — Price history 1d/7d/30d/all; auto-resolves CLOB token ID |
| `list_polymarket_markets` | Polymarket | None | **NEW** — Filter open/closed/resolved + pagination |
| `get_kalshi_settlements` | Kalshi | RSA-PSS | **NEW** — Recently settled contracts, results, profit-per-contract |
| `place_kalshi_order` | Kalshi | RSA-PSS | **NEW** — Limit order placement via signed POST |
| `place_polymarket_order` | Polymarket | CLOB creds | Limit order via py-clob-client |
| `record_prediction_market_bot_metric` | Both | None | Append JSONL audit snapshot for marketplace promotion |
| `get_prediction_market_bot_metrics` | Both | None | Read bot performance history |
| `propagate_trade_signal` | Django | HMAC | Fan-out signals to subscriber paper accounts |

### Environment Variables

```bash
# Kalshi (RSA-PSS signing — no API token auth for trade APIs)
KALSHI_ACCESS_KEY=your-api-key-id
KALSHI_PRIVATE_KEY_PATH=/path/to/kalshi_private.pem   # or KALSHI_PRIVATE_KEY_PEM
KALSHI_API_HOST=https://api.elections.kalshi.com       # demo: https://demo-api.kalshi.co

# Polymarket CLOB (for order placement only — data is public)
POLYMARKET_API_KEY=...
POLYMARKET_API_SECRET=...
POLYMARKET_API_PASSPHRASE=...
```

### Comparison vs External MCPs

The external MCPs we evaluated are simpler standalone servers. Our implementation is a superset:

- **History endpoint** (`get_polymarket_market_history`): auto-resolves slug → CLOB YES token → prices-history. Handles Gamma's double-encoded JSON `clobTokenIds` field. Validated: `1d` (131 candles), `7d` (132 candles), `all` (704 candles).
- **Kalshi**: we use `kalshi_signed_get` + new `kalshi_signed_post` for order placement. The `get_kalshi_settlements` tool uses the `/trade-api/v2/settlements` endpoint (9crusher's latest feature).
- **Sort bug fix**: Gamma API's correct sort param is `sort=volume24hr` (not `order=volume_24hr`). Fixed in `get_top_markets` and `list_polymarket_markets`.

- **Subscriber fan-out:** `propagate_trade_signal` — Roo's live Django endpoint (`http://172.232.170.168/signals/signal/`) used as default. Override via `SIGNAL_URL` + `SIGNAL_SECRET` env vars.
- **Health check:** `check_propagation_health` — verify service is reachable before running bots.
- **Setup test:** `test_signal_propagation` — runs BUY→SELL→BUY sequence; watch your algochains.ai dashboard to confirm 3 paper trades appear.
- **GUARDRAIL:** `run_guardrail` — 6-gate pre-flight chain (VIX, daily-loss, stoploss-guard, cooldown, confidence, R/R). Wire before every `place_order` call.
- **Agent docs:** [MEGA_PROMPT_PREDICTION_MARKETS_V1.md](MEGA_PROMPT_PREDICTION_MARKETS_V1.md), [blueprints/PREDICTION_MARKET_BOTS_BLUEPRINT.md](blueprints/PREDICTION_MARKET_BOTS_BLUEPRINT.md).
- **BYOK:** `polymarket` and `kalshi` entries in `byok/provider_registry.py`.

---

## PAI Integration — Business Identity OS + US Economics + Learning Signals

**v22.9** — Gap analysis vs [danielmiessler/Personal_AI_Infrastructure](https://github.com/danielmiessler/Personal_AI_Infrastructure) (⭐11.2k). We already surpass PAI on skills (472 vs 63), memory, MCP tools (371 vs handful), multi-agent debate (Moltbook >> PAI's council), and BYOK. Four genuinely novel additions integrated:

### AlgoChains TELOS (Business Identity OS)

Every AI agent (Cursor, Claude, Windsurf, OpenClaw) now has instant access to AlgoChains' mission, goals, strategies, mental models, lessons learned, challenges, and KPIs — without re-explaining every session.

```python
get_algochains_telos(section="all")           # Full business context
get_algochains_telos(section="goals")         # Q2 2026 targets
get_algochains_telos(section="learned")       # Lessons from live trading
update_algochains_telos(section="learned", entry="New lesson from live trading")
```

**TELOS files** live in `algochains-control-tower/TELOS/`:

| File | Contents |
|------|----------|
| `MISSION.md` | Why AlgoChains exists |
| `GOALS.md` | Q2 2026 targets (AUM, marketplace, subscribers) |
| `STRATEGIES.md` | How goals are achieved (bot quality, Roo's signal prop, MCP moat) |
| `MODELS.md` | Trading & business mental models (Kelly, Sharpe, Regime-First, MCPT) |
| `LEARNED.md` | Key lessons from live trading (volume threshold, WebSocket, MCPT) |
| `CHALLENGES.md` | Current blockers and risks |
| `IDEAS.md` | Future expansion ideas (GEX, congressional trading, eToro) |
| `METRICS.md` | KPIs: bots, marketplace, platform, macro indicators |

### US Economic Indicators (FRED + EIA Macro Layer)

68 US economic indicators adapted from PAI's USMetrics pack. Critical for trading:
- **CL bot:** EIA weekly crude oil inventories (biggest crude mover — released Wednesdays)
- **MNQ/NQ:** CPI, PCE, Fed Funds Rate, 10Y-2Y spread for rate regime detection
- **All bots:** VIX with pre-computed regime signal (crisis/elevated/normal)

```python
get_us_economic_indicators(categories=["volatility", "rates"])  # FRED data
get_crude_oil_inventories()    # EIA weekly crude — cl_signal: BULLISH/BEARISH/NEUTRAL
get_fed_policy_signals()       # 7 Fed indicators + AI regime interpretation
```

**Required env vars** (both free):
```bash
FRED_API_KEY=<from https://fred.stlouisfed.org/docs/api/api_key.html>
EIA_API_KEY=<from https://www.eia.gov/opendata/register.php>
```

### Learning Signals (Continuous Improvement)

Capture agent outcome ratings for every significant action. After 30+ signals, patterns emerge: which skills produce the best outcomes, where failure is common, what to improve.

```python
capture_learning_signal(
    action_type="bot_diagnosis",
    action_description="Fixed MNQ volume threshold 3.02x → 1.5x",
    outcome="success",
    rating=9,
    skill_used="bot-diagnostics",
    bot="MNQ",
    agent="cursor"
)
get_learning_signals(summarize=True)  # Success rates, top skills, bot activity
```

Storage: `state/learning_signals.jsonl` (append-only, never deleted).

### ntfy Mobile Push Notifications

Instant mobile push for trading events — no app install required. Users subscribe at `ntfy.sh/algochains/bots` etc.

```python
send_ntfy_notification(title="MNQ Bot Crashed", message="Process not found", topic="bots", priority="urgent")
send_ntfy_notification(title="Daily P&L", message="MNQ: +$340, CL: +$180", topic="bots", priority="low")
```

| Topic | Events |
|-------|--------|
| `algochains/bots` | Bot up/down, trade events, reconnects |
| `algochains/risk` | Circuit breaker, daily loss limit hit |
| `algochains/marketplace` | New subscriber, bot promoted/demoted |
| `algochains/ops` | Deploy complete, system health |
| `algochains/alpha` | High-confidence signal detected |

**Required env vars:** `NTFY_BASE_URL` (default: `https://ntfy.sh`), optional `NTFY_AUTH_TOKEN` for private topics.

### What We Skipped From PAI (Already Have Better)

| PAI Feature | Why Skipped |
|-------------|-------------|
| Skill System (63 skills) | We have 472 — 7.5× more |
| Memory (flat JSON) | OpenClaw memory.json already running |
| Multi-agent debates | Moltbook (PostgreSQL + NATS + LangGraph) is far more sophisticated |
| Hook system | autonomous_watchdog.py + adaptive_brain.py already live |
| Dashboard | algochains-command-center (Next.js) already deployed |
| Voice (ElevenLabs) | Slack + ntfy sufficient for trading ops |
| Context Search | Onyx knowledge brain provides semantic search |
| Algorithm v3.7.0 rigid protocol | Moltbook debate engine is our specialized equivalent |

---

## Changelog

**v26.0** (2026-04-08) — Bot Ops Module, Live Incident Fixes, Command Center V22, Pipeline Hardening

*6 new tools. All critical production fixes from 2026-04-07 incident. Command Center V22.*

- **Bot Ops Module** (`live_bot_intelligence/bot_ops.py`): Operational management tools for all 4 bots
  - `get_bot_position_state` — read persisted position state (flat/qty/entry_price)
  - `get_bot_bracket_status` — detect bracket mode (live/oso_only/none/unknown) from logs
  - `get_ai_pipeline_health` — Anthropic quota status, Cerebras model health, shadow mode
  - `get_all_bot_ops_status` — full snapshot (process + position + bracket + pipeline) for all 4 bots
  - `restart_trading_bot` — kill + restart a bot (owner-token gated, owner: OWNER_API_TOKEN)
  - `flatten_bot_position` — close all contracts via Tradovate MKT (owner-token gated)
- **P0 Fix: qty=1 close bug** (`trading_safeguards.py`): `close_position_with_validation()` was hardcoded `qty=1`. Now reads from `position_size`/`qty`. Orphaned 3 contracts on 2026-04-07.
- **P0 Fix: Demo path bypass** (`FUTURES_SCALPER_UPGRADED.py`): `TRADING_MODE=DEMO` bypassed all safeguards. Unified path: always runs coordinator, fill tracking, brackets, mutex.
- **P0 Fix: Scale-in bracket tracking**: Scale-in bracket IDs (`scale_stop_order_id`, `scale_target_order_id`) now stored and cancelled on position exit.
- **P0 Fix: Pipeline 102s stall**: `concurrent.futures` 8s timeout on `pipeline.analyze()`. Shadow mode returns signal as-is, never blocks trade.
- **P0 Fix: Cerebras model** (`debate_layer.py`, `specialized_agents.py`): `llama3.3-70b` removed from API → `llama3.1-8b`
- **P0 Fix: Order mutex** (`core/order_mutex.py`): SQLite-backed cross-process lock prevents bot+MCP duplicate orders. 15s TTL, auto-expire.
- **Command Center V22**: Bracket Status Panel, AI Ensemble Panel, SSE Trade Validation Feed, Subscriber Protection Panel. Restart Bot endpoint. Subscribe → algochains.ai.
- **Cloudflare tunnel**: cc.algochains.io + cc.algochains.ai now both served via tunnel `def269f2`. Tunnel must be running (`cloudflared tunnel run def269f2-...`).
- **GOTCHAS_AND_BUGS.md**: New doc in `docs/` cataloging all confirmed bugs, gotchas, and operational surprises.
- **Tools: 401 → 407**

**v25.0** (2026-04-07) — AlgoClaw v1.0: Full Agent Skill System + Roo Trade Propagation

**v24.0** (2026-04-08) — Full Prop Fund Pipeline: Rithmic Connector, Drawdown Monitor Daemon, E*TRADE + Options, Credential Vault, Command Center /prop-funds

*11 new tools. Tools: 387 → 398. Complete prop fund automation pipeline — from validated strategy to funded account.*

- **Rithmic Connector** (`brokers/rithmic_connector.py`): Full R|Protocol connector for all 6 major US prop funds. DRY_RUN mode active until vendor agreement signed. Covers MNQ, NQ, MES, CL, ES, GC. Dry-run simulator validates all order logic.
- **PropFundDrawdownMonitor** (`brokers/prop_fund_drawdown_monitor.py`): Real-time evaluation account monitoring daemon. Alerts at 70%/85%/95% of daily limits via ntfy + Slack. Emergency auto-flatten at 95%. Trailing drawdown tiers at 80%/90%/98%. Profit target detection with pass notification. State persisted across restarts.
- **Autonomous Daemon** (`autonomous/prop_fund_monitor.py`, control tower): Control tower-level daemon with direct Tradovate REST API integration. CLI with `--check-now`, `--status`, `--interval` flags. Runs every 30 min during market hours (9:30-16:00 ET, Mon-Fri).
- **E*TRADE Connector** (`brokers/etrade_connector.py`): Full OAuth 1.0a connector for equities + options. R-Multiple sizing (Van Tharp methodology). Black-Scholes Greeks computation (pure Python, no scipy). `find_optimal_strike()` by target delta. Options chain fetching with full Greeks.
- **Credential Vault** (`brokers/credential_vault.py`): Centralized credential management for 15 brokers/data sources. `check_all_broker_credentials()` — masked status (never exposes values). Step-by-step onboarding guides per broker. Prop fund broker option selector.
- **Command Center /prop-funds** (command center): Full React dashboard with fund cards, bot × fund compatibility matrix, pipeline visualization (6-phase), drawdown tier visualization, revenue model projections per account size. Rithmic vendor agreement status banner.
- **New MCP Tools**: `verify_hmac_signature`, `compute_r_multiple_size`, `compute_option_greeks`, `find_optimal_strike`, `check_broker_credentials`, `get_broker_onboarding_guide`, `check_rithmic_status`, `register_prop_fund_account`, `get_prop_fund_monitor_status`, `get_prop_fund_broker_options`

**v23.0** (2026-04-07) — GitHub Acceleration Research: Protection Patterns, Vol Targeting, Prop Funds, Security Hardening

*16 new tools. Tools: 371 → 387. All real code — no placeholders.*

- **Protection Patterns** (`account_protection/protection_patterns.py`): Freqtrade-style guards for live bots
  - `StoplossGuard`: Lock instrument after N stops in X hours (default: 3 stops in 4h → 2h lock)
  - `CooldownPeriod`: 30-min re-entry delay after any stop (prevents revenge trading)
  - `LowProfitPairs`: Pause instruments failing profit threshold over rolling window
  - MCP tools: `check_protection_status`, `record_stop_event`, `lock_instrument`, `unlock_instrument`

- **Volatility Targeting** (`volatility_targeting.py`): Robert Carver's pysystemtrade methodology
  - `compute_volatility_targeted_size`: Size positions for consistent % vol of capital (not wealth-based Kelly)
  - `compute_idm`: Instrument Diversification Multiplier — auto-reduces MNQ+NQ aggregate when correlated
  - `compute_forecast_scalar`: Normalize signals to [-20, +20] Carver scale
  - `dual_size_conservative`: Run both Kelly and vol targeting, take the minimum (recommended)

- **Performance Reports** (`performance_reports.py`): quantstats-inspired tearsheets
  - `generate_bot_tearsheet`: HTML tearsheet (quantstats if installed) + JSON metrics with marketplace grade
  - `get_bot_metrics_full`: All 20+ metrics: Sharpe, Sortino, Calmar, Omega, CVaR, profit factor, time-in-DD
  - Auto-grades: A (Sharpe ≥ 2.0, MaxDD ≤ 15%, WR ≥ 55%), B/C/D tiers

- **Prop Fund Manager** (`brokers/prop_fund_manager.py`): Pipeline validated strategies to funded accounts
  - 7 funds fully modeled: Apex, Topstep, MyFundedFutures, TradeDay, Bulenox, Earn2Trade, FTMO
  - `list_prop_funds`: All funds with rules (daily limits, profit targets, consistency rules)
  - `evaluate_strategy_for_prop_fund`: Rank all 7 funds by compatibility with a given strategy
  - `simulate_prop_fund_evaluation`: Replay historical P&L against fund rules → pass probability
  - `get_prop_fund_rules`: Full ruleset for any fund
  - **Revenue model**: MNQ scalper (Sharpe 4.61) → Apex $50K → ~$2,160/mo (90% split) → $149/mo subscription

- **Security Hardening** (`security/`): SAFE-MCP techniques T051 + T067 fixed
  - `security/replay_guard.py`: Nonce + timestamp validation, HMAC request signing, replay protection
  - `security/per_tool_rate_limiter.py`: Per-tool token bucket (place_order: 5/min, cancel_all: 2/5min, flatten: 1/hr)
  - MCP tools: `check_rate_limit_status`, `generate_hmac_signature`

- **GitHub Acceleration Blueprint** expanded to 5,000+ lines:
  - [blueprints/GITHUB_ACCELERATION_RESEARCH_2026.md](blueprints/GITHUB_ACCELERATION_RESEARCH_2026.md) — Part I (471 lines, 12 repos)
  - [blueprints/GITHUB_ACCELERATION_RESEARCH_2026_PART2.md](blueprints/GITHUB_ACCELERATION_RESEARCH_2026_PART2.md) — Part II (5,000+ lines, NautilusTrader, hftbacktest, MlFinLab, E*TRADE, Rithmic, Schwab, prop fund architecture)

- Tools badge: **387** registered (was 371)

**v22.9** (2026-04-07) — PAI Integration: TELOS + US Economics + Learning Signals + ntfy
- Evaluated [danielmiessler/Personal_AI_Infrastructure](https://github.com/danielmiessler/Personal_AI_Infrastructure) (⭐11.2k) — identified 4 genuine gaps vs AlgoChains stack
- **TELOS System**: 8-file business identity OS in `algochains-control-tower/TELOS/` — mission, goals, strategies, models, learned, challenges, ideas, metrics
- **`telos.py`**: `get_algochains_telos(section)` + `update_algochains_telos(section, entry)` — instant business context for all AI agents
- **`us_economics.py`**: `get_us_economic_indicators` (16 FRED series, 6h cache), `get_crude_oil_inventories` (EIA weekly, critical for CL), `get_fed_policy_signals` (7 Fed indicators + AI regime interpretation)
- **`learning_signals.py`**: `capture_learning_signal` + `get_learning_signals` — outcome capture, success rate analysis, skill effectiveness tracking
- **`notifications/ntfy_push.py`**: `send_ntfy_notification` — mobile push via ntfy.sh, 5 topic channels, priority routing
- All 8 new tools added to smart-mode list; danger tiers updated (READ_ONLY, READ_EXTERNAL, WRITE_LOCAL)
- Blueprint: [blueprints/PAI_INTEGRATION_BLUEPRINT.md](blueprints/PAI_INTEGRATION_BLUEPRINT.md)
- Tools badge: **371** registered (was 363)

**v22.8** (2026-04-07) — Prediction Market Gap Fill (vs mcp-server-kalshi + polymarket-mcp)
- Evaluated [berlinbra/polymarket-mcp](https://github.com/berlinbra/polymarket-mcp) (⭐130) and [9crusher/mcp-server-kalshi](https://github.com/9crusher/mcp-server-kalshi) (⭐18)
- `get_polymarket_market(slug_or_id)` — fetch specific Polymarket market by slug/numeric ID via Gamma query params (path-based lookups return 422)
- `get_polymarket_market_history(slug, timeframe)` — price history 1d/7d/30d/all via `clob.polymarket.com/prices-history`; auto-resolves slug → CLOB YES token ID; handles double-encoded `clobTokenIds` JSON string
- `list_polymarket_markets(status, limit, offset)` — filter open/closed/resolved with pagination; sort by `sort=volume24hr` (fixed from broken `order=volume_24hr`)
- `get_kalshi_settlements(limit)` — signed GET to `/trade-api/v2/settlements`; returns results, timestamps, profit-per-contract
- `place_kalshi_order(ticker, side, action, count, limit_price_cents)` — signed POST to `/trade-api/v2/orders` via new `kalshi_signed_post()` in `kalshi_signed.py`
- **Bug fix**: `get_top_markets` was using `order=volume_24hr` which returns HTTP 422; corrected to `sort=volume24hr`
- All 5 new tools added to smart-mode list; danger tiers updated (4× READ_ONLY, 1× ORDER_EXEC)
- Tools badge: **363** registered (was 358)

**v22.7** (2026-04-07) — Skills Bridge + Agent Memory + Onyx Expansion
- `skills_registry.py` — indexes 472 skills across OpenClaw (334), Windsurf (126), Cursor (7), Claude (5); keyword search + category filter + task-matching
- `agent_memory.py` — reads real OpenClaw memory.json, current_regime.json, bot_heartbeat.json, agent_evaluations.json, ai_cost_state.json
- 17 new MCP tools: `list_skills`, `get_skill_detail`, `search_skills`, `get_skills_for_task`, `reload_skills_registry`, `get_openclaw_memory`, `store_trade_lesson`, `get_current_regime`, `get_bot_heartbeat_openclaw`, `get_agent_evaluations`, `get_openclaw_state_summary`, `invoke_moltbook_debate`, `run_mcpt_pipeline`, `run_regime_detection` + skill execution shortcuts
- All 17 tools added to smart-mode list (visible by default)
- `onyx_connectors.json` — expanded to index `~/.openclaw/skills/` (363 skills), `~/.openclaw/memory.json` + state files, `~/.cursor/skills-cursor/`, `algochains-mcp-server/blueprints/`
- Onyx persona updated: knows about 472 skills, can answer skill-related questions with citations
- Blueprint: [blueprints/SKILLS_BRIDGE_BLUEPRINT.md](blueprints/SKILLS_BRIDGE_BLUEPRINT.md)
- Tools badge: **358** registered (was 344)

**v22.6** (2026-04-08) — Prediction Markets + Trade Propagation
- Fixed `get_prediction_markets`: was calling non-existent async `get_signals` on wrong class name — now uses `PredictionMarketsEngine.get_signals()` (sync) with thematic category queries
- `search_prediction_markets`, `get_polymarket_high_volume`, `propagate_trade_signal`, `record_prediction_market_bot_metric`, `get_prediction_market_bot_metrics` MCP tools
- `trade_propagation.py` — HMAC POST to Django; no default secrets
- `prediction_market_metrics.py` — JSONL audit log for subscribable PM bot promotion path
- `PredictionMarketEngine` alias for backwards-compatible lazy imports
- `examples/trade_propagation/` — env-only `send_signal.py`, `dummy_signal_test.py`, TRADE_PROPAGATION stub
- BYOK registry extended for Polymarket + Kalshi
- **v22.6.1** — Audit hardening: no synthetic 0.5 Polymarket prices; Gamma sort params `order=volume_24hr` + `ascending=false`; Kalshi **RSA-PSS** via `order_flow/kalshi_signed.py` (replaces broken Token auth); `place_polymarket_order` requires explicit `limit_price`; [blueprints/PREDICTION_MARKETS_GAP_ANALYSIS.md](blueprints/PREDICTION_MARKETS_GAP_ANALYSIS.md)

**v22.5** (2026-04-07) — Soft-Launch Platform Release (Oleg / Planex Tasks)
- `support_tickets.py` — IT support ticket system: Supabase-backed, Notion sync, Resend confirmation emails. MCP tools: `create_support_ticket`, `get_support_ticket`, `list_support_tickets`, `update_ticket_status`, `get_ticket_stats`
- `brokers/oauth_manager.py` — Generic OAuth 2.0 PKCE manager for broker connections. Supports Schwab, Alpaca, Tradovate, OANDA. Tokens persisted in Supabase + local fallback
- `brokers/schwab_connector.py` — Full Charles Schwab (TD Ameritrade successor) connector: accounts, positions, orders, quotes, options chain. Uses `api.schwabapi.com`
- `waitlist.py` — Join Waitlist with Supabase persistence + Resend welcome email. `join_waitlist`, `get_waitlist_stats`, `send_waitlist_invite` (with unique invite codes)
- `verification.py` — Email/SMS code verification (6-digit, SHA-256 hashed, 10min TTL): `send_email_verification_code`, `send_sms_verification_code`, `verify_code`. Rate-limited 3/hour. Twilio for SMS
- `platform_analytics.py` — Soft-launch analytics: `track_platform_event`, `get_analytics_summary`. Tracks full signup→verify→connect→purchase funnel with by-day breakdown
- `auth/password_reset.py` — Supabase Auth password reset + account recovery flow: `initiate_password_reset`, `complete_password_reset`, `initiate_account_recovery`. Password policy: 12 chars + upper/lower/number/special
- `live_bot_intelligence/multi_account_metrics.py` — Multi-bot account metrics: `get_user_bot_metrics`, `get_all_user_bots`, `upsert_bot_performance`. Handles 4 fallback states: LIVE, METRICS_PENDING, BROKER_NOT_CONNECTED, DATA_STALE
- `supabase/migrations/20260406_platform_tables.sql` — 7 new tables with RLS policies: `algochains_support_tickets`, `algochains_oauth_tokens`, `algochains_waitlist`, `algochains_verification_codes`, `algochains_analytics_events`, `algochains_bot_performance`, `algochains_subscriptions`
- `config.py` — Added `SupabaseConfig`, `EmailConfig`, `SchwabConfig`, `TwilioConfig`, `NotionConfig` dataclasses
- `.env.example` — Added `SUPABASE_SERVICE_KEY`, `RESEND_API_KEY`, `TWILIO_*`, `NOTION_*`, `SCHWAB_*` env vars
- Total: 25 new MCP tools, 386 tools total

**v22.4** (2026-04-06) — UX & Team Onboarding Release
- Complete README rewrite — plain English explanation, team access docs
- `scripts/quickstart.py` — interactive setup wizard with health checks
- `SAFETY_MODEL.md` — answers "is this safe?" for every failure mode
- `MARKETPLACE_CREATOR_GUIDE.md` — step-by-step bot submission guide for Roo's request
- `tool_danger_tiers.py` — machine-readable danger classification (0-3) for all 350+ tools
- HTTP bridge `/tools` endpoint now returns `danger_tier`, `safe_in_demo_mode`, etc. for every tool
- Fixed 3 undefined `ANNOT_*` constants in `server.py` causing import failure
- Deployment persistence: `StrategyDeployer` now persists to `state/deployments.json`
- SaaS persistence: `StrategyMarketplace`, `TenantManager`, `WhiteLabelEngine` all persist to state dir
- Tool manifest: `optimize_strategy`, `deploy_strategy`, `create_shadow_portfolio` corrected from `stub` → `partial`

**v22.3** (2026-04-06) — Proprietary Data Ingestion
- `data_ingestion.py` — 5 ingestion tools: ingest_csv_data, ingest_json_signals, connect_onyx_docs, register_strategy, list_ingested_data
- Path traversal protection on all user-supplied file paths

**v22.0** (2026-04-05) — MCP 2025-11-25 Full Compliance
- Elicitation (human confirmation for high-value trades)
- Durable Tasks (background backtest/optimization jobs)
- SSE streaming transport
- OIDC discovery endpoint
- Trading guardrails with circuit breakers
- AlphaLoop evolution daemon

---

## License

MIT — use freely, build great things.

---

<div align="center">

**Built by Tyler Reynolds as an experimental AI trading infrastructure platform.**

[SAFETY_MODEL.md](SAFETY_MODEL.md) · [MARKETPLACE_CREATOR_GUIDE.md](MARKETPLACE_CREATOR_GUIDE.md) · [UX_BLUEPRINT.md](UX_BLUEPRINT.md)

*This is experimental software connected to live trading accounts. Use at your own risk.*

</div>
