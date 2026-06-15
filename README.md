# AlgoChains MCP Server

[![MCP](https://img.shields.io/badge/MCP-2025--11--25-blue?style=flat-square)](https://modelcontextprotocol.io)
[![Tools](https://img.shields.io/badge/tools-503%20full%20%7C%20168%20smart-green?style=flat-square)](#tool-domains)
[![Version](https://img.shields.io/badge/version-22.6.0-blueviolet?style=flat-square)](#whats-new)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue?style=flat-square)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-purple?style=flat-square)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-GOTCHAS__AND__BUGS.md-red?style=flat-square)](docs/GOTCHAS_AND_BUGS.md)
[![Data](https://img.shields.io/badge/data-Databento%20%7C%20Massive%20S3%20%7C%20Polygon-yellow?style=flat-square)](#data-backends)

---

> **The only MCP server with live futures bots, real fill data, real-time ML inference, and 503 tools across 20 domains — all backed by real APIs, zero synthetic data.**

Connect your AI assistant (Claude, Cursor, ChatGPT) to your trading infrastructure in 3 commands. Ask Claude "What's my MNQ P&L today?" — it calls Tradovate, gets the real answer, and tells you.

```
You ask Claude:                    Claude calls:                    Server calls:
"What's my NQ position?"   →  get_positions()           →  Tradovate API → real data
"Run a backtest on MNQ"    →  run_backtest()             →  Databento tick archive
"Is the market trending?"  →  detect_market_regime()    →  Polygon + FRED → analysis
"Check my MNQ bot health"  →  get_bot_health(bot="MNQ") →  launchd + logs → live state
```

---

## Quick Install

```bash
# 1. Install
pip install algochains-mcp-server

# 2. Connect to your IDE (no credentials needed to start)
python scripts/quickstart.py --generate-config cursor

# 3. Verify
python scripts/quickstart.py --mode demo
```

That's it. Your AI now has 168 tools (smart mode) available immediately. Add broker credentials for live trading access. See [Option C](#option-c-full-live-setup) for live credentials.

---

## Smart Mode vs Full Mode

AlgoChains exposes tools in two tiers, controlled by `ALGOCHAINS_TOOL_MODE`:

| Mode | Tools Exposed | Token Cost | When to Use |
|------|:---:|:---:|-----|
| **Smart** (default) | 168 curated | ~4K tokens | Cursor, Windsurf (80-tool limit), everyday use |
| **Full** (`ALGOCHAINS_TOOL_MODE=full`) | 503 tools | ~40K tokens | Claude Code, full agentic sessions |

**Smart mode includes:** all live bot tools, market data, signals, research/backtest, Onyx RAG, prop fund pipeline, position sizing, broker management, and order execution. Everything you need 95% of the time.

**Full mode** unlocks the remaining 330 tools: advanced DeFi, Kalshi order placement, multi-tenant SaaS, QuantConnect integration, alt-data pipelines, and more.

### `discover_tools` — Find Any Tool Without Full Mode

Even in smart mode, you can find and use any of the 503 tools:

```python
# Ask the server to find the right tool for your task
discover_tools("walk-forward validation with leakage check")
# → Returns: walk_forward_test, run_mcpt_validation, analyze_overfitting

# Then call it
execute_dynamic_tool("walk_forward_test", {"symbol": "MNQ", "lookback_days": 252})
```

This provides 99.6% token reduction vs exposing all 503 tools (arXiv:2603.20313).

---

## Tool Domains

All 503 tools organized across 21 domains:

| # | Domain | Smart | Full | Key Tools |
|---|--------|:-----:|:----:|-----------|
| 1 | **Market Data** | 14 | 22 | `get_quote`, `get_ohlcv`, `get_tick_data`, `get_options_chain`, `get_footprint_chart`, `get_dark_pool_volume` |
| 2 | **Signals & Analysis** | 12 | 18 | `generate_signal`, `detect_market_regime`, `get_ensemble_vote`, `compute_gex`, `read_tape`, `pair_trade_signal` |
| 3 | **Research & Backtesting** | 10 | 16 | `run_backtest`, `walk_forward_test`, `run_mcpt_validation`, `validate_strategy_metrics`, `analyze_overfitting` |
| 4 | **Position Sizing** | 6 | 8 | `compute_r_multiple_size`, `compute_volatility_targeted_size`, `compute_idm`, `dual_size_conservative` |
| 5 | **Options Analytics** | 4 | 6 | `compute_option_greeks`, `find_optimal_strike`, `get_options_chain`, `unusual_options_activity` |
| 6 | **Prop Fund Pipeline** | 8 | 10 | `evaluate_strategy_for_prop_fund`, `simulate_prop_fund_evaluation`, `list_prop_funds`, `check_rithmic_status` |
| 7 | **Broker Management** | 6 | 15 | `check_all_broker_credentials`, `connect_broker`, `get_broker_onboarding_guide`, `store_api_key` |
| 8 | **Subscriber / Copy-Trade** | 3 stdio | 16 bridge | `get_subscriber_status`, `accept_subscriber_terms`, `join_bot`, `get_my_portfolio`, `get_signal_stream`, `place_paper_order` |
| 9 | **Account Protection** | 6 | 8 | `check_protection_status`, `record_stop_event`, `lock_instrument`, `check_rate_limit_status` |
| 10 | **Order Execution** | 8 | 12 | `place_order`, `place_bracket_order`, `cancel_order`, `smart_route_order`, `execute_twap` |
| 11 | **Emergency / Destructive** | 3 | 5 | `flatten_all_positions`, `cancel_all_orders`, `emergency_stop`, `trip_circuit_breaker` |
| 12 | **Intelligence (Onyx + Macro)** | 10 | 14 | `onyx_ask`, `onyx_search`, `get_macro_signals`, `get_us_economic_indicators`, `get_fed_policy_signals` |
| 13 | **Prediction Markets** | 8 | 12 | `get_prediction_markets`, `search_prediction_markets`, `get_kalshi_settlements`, `place_kalshi_order` |
| 14 | **Skills Bridge** | 5 | 5 | `list_skills`, `get_skill_detail`, `search_skills`, `get_skills_for_task`, `invoke_moltbook_debate` |
| 15 | **Agent Memory** | 6 | 8 | `get_openclaw_memory`, `store_trade_lesson`, `get_current_regime`, `get_openclaw_state_summary` |
| 16 | **Live Bot Intelligence** | 12 | 18 | `get_bot_health`, `get_live_bot_metrics`, `get_bot_position_state`, `get_ai_pipeline_health`, `restart_trading_bot` |
| 17 | **Desktop Tower / Dispatch** | 4 | 8 | `dispatch_tower_job`, `get_tower_job_status`, `run_tower_backtest`, `sync_to_tower` |
| 18 | **Performance Reporting** | 4 | 6 | `generate_bot_tearsheet`, `get_bot_metrics_full`, `run_mcpt_pipeline`, `capture_learning_signal` |
| 19 | **Billing & Subscription** | 12 | 12 | `get_started`, `get_pricing`, `get_checkout_url`, `accept_subscriber_terms`, `get_my_usage`, `create_referral_code`, `get_referral_earnings`, `create_creator_onboarding_link`, `get_my_creator_earnings`, `run_creator_payouts`, `get_my_realized_pnl`, `get_system_status` |
| 20 | **Platform / SaaS** | 8 | 20 | `join_waitlist`, `create_support_ticket`, `track_platform_event`, `get_analytics_summary` |
| 21 | **AlphaLoop / Evolution** | 12 | 22 | `run_alphaloop_cycle`, `get_alphaloop_results`, `get_algochains_telos`, `send_ntfy_notification` |

Subscriber tools are split by transport: local stdio exposes the consent/status
funnel, while the HTTP bridge exposes the full subscriber data and paper-order
surface.

---

## Live Bot Showcase

AlgoChains runs 4 live futures bots on Tradovate. Their state, fills, ML pipeline health, and brackets stream through this MCP server in real time.

| Bot | Symbol | Strategy | Live Since | Key MCP Tool |
|-----|--------|----------|:----------:|---|
| `MNQ_Upgraded_Scalper` | MNQ | 7-AI ensemble, 5-min bars | Dec 2024 | `get_bot_health(bot="MNQ")` |
| `CL_Swing_Scalper` | CL | FinBERT sentiment + momentum | Jan 2025 | `get_bot_health(bot="CL")` |
| `MES_EMA_Swing` | MES | EMA pullback + regime detection | Feb 2025 | `get_bot_health(bot="MES")` |
| `NQ_EMA_Swing` | NQ | Trend following + foundation model | Feb 2025 | `get_bot_health(bot="NQ")` |

### `get_bot_health` — Full e2e Signal→Order→Fill Trace

```python
# Returns: process state, position, bracket status, AI pipeline health,
#          ml_env_flags (MASSIVE_NEWS_FEATURES, MASSIVE_PCR_FEATURES, MASSIVE_HALT_GUARD),
#          cc_health (Command Center last-seen, WS status, Databento live feed age),
#          signal_health (params, risk_bootstrap, bot_version, trading_mode),
#          e2e_sentinel (signal→order→bracket→fill lifecycle state)
health = get_bot_health(bot="MNQ")
```

```python
# All 4 bots in one call
status = get_all_bot_ops_status()
# Returns: process + position + bracket + pipeline snapshot for MNQ/CL/MES/NQ
```

No credentials needed if you have `ALGOCHAINS_BRIDGE_API_KEY`. Read-only.

---

## Subscriber Onramp — Try It Free (No Broker Required)

The fastest way to get value from this server is as a **subscriber**: sign up at
[algochains.ai](https://algochains.ai), get a free hosted virtual paper account, and
start copy-trading the live MNQ bot's signals in seconds. No Tradovate credentials.
No Alpaca account. No real money.

### How it works

1. Sign up at **algochains.ai** — free paper account provisioned automatically
2. Dashboard shows your `sub_live_…` subscriber key — copy it
3. Set `ALGOCHAINS_SUBSCRIBER_KEY=sub_live_…` for local stdio onboarding tools,
   or send it as `X-Api-Key` to the HTTP bridge
4. Use the local stdio tools for consent/status/join flows, and use the HTTP
   bridge for the full subscriber portfolio, signal, fill, and paper-order surface:

| Tool | What it does |
|------|-------------|
| `accept_subscriber_terms` | Show or record the required futures risk-disclosure acknowledgment |
| `join_bot` | Subscribe to a bot's published signals after consent |
| `get_subscriber_status` | Consent state, bot assignments, paper account, and suggested next steps |
| `get_my_portfolio` | Paper balance + active bot assignments + open signals + 7-day P&L in one call |
| `get_signal_stream` | Unread copy-trade signals for the bots you follow (MNQ by default) |
| `get_my_pnl` | Today's P&L and 7-day P&L from your paper fills |
| `get_my_fills` | Paginated fill history — symbol, side, qty, fill price, P&L per trade |
| `get_my_assignments` | Which bots you're subscribed to and their risk caps |
| `get_marketplace_listings` | Browse all approved bots available to subscribe to |
| `place_paper_order` | Place a self-directed paper order (filled at real quotes) |
| `cancel_paper_order` | Cancel a pending paper order |
| `get_my_paper_positions` | Open and recently filled self-directed paper orders |
| `report_fill`, `ack_signal`, `heartbeat` | Subscriber daemon callbacks for fill reporting, signal acknowledgments, and liveness |

All subscriber tools require only the `sub_live_…` key — no `OWNER_API_TOKEN`, no
broker credentials. See [docs/SUBSCRIBER_TOOLS.md](docs/SUBSCRIBER_TOOLS.md) for
the stdio-vs-bridge split, scopes, examples, and daemon tools.

### Subscriber key format

Keys always start with `sub_live_` (production) or `sub_test_` (sandbox). Set the key
as `ALGOCHAINS_SUBSCRIBER_KEY` in your `.env` for local stdio tools, or pass it as
`X-Api-Key` to the HTTP bridge. The server resolves your `subscriber_id`
server-side via Supabase; callers should not pass `subscriber_id` in tool
arguments.

### Subscriber quick-start prompts

```
Portfolio snapshot over the HTTP bridge:
"Run get_my_portfolio with my subscriber bridge key. What's my paper balance and how did the MNQ bot do today?"

Signal stream over the HTTP bridge:
"Call get_signal_stream with my subscriber bridge key. What signals has the MNQ bot fired in the last hour?"

Fill history:
"Run get_my_fills with limit=20. List the last 20 fills with P&L per trade."

Marketplace browse:
"Run get_marketplace_listings. Which bots are available to subscribe to?"

Paper trade:
"I want to paper-trade 1 MES long at market. Use place_paper_order."
```

> **Bot owners:** See [MARKETPLACE_CREATOR_GUIDE.md](MARKETPLACE_CREATOR_GUIDE.md) and
> `check_propagation_health` / `test_signal_propagation` for the copy-trade pipeline
> health tools (requires `OWNER_API_TOKEN`).

---

## Billing & Subscription Funnel (Fully Programmatic)

Every billing action is available as an MCP tool — no browser required after the initial
Stripe checkout. An agent or a user can go from zero to copy-trading MNQ signals in one
conversation thread.

### New-user discovery (no auth, always available)

```python
get_started(goal="subscriber")   # guided next-step map for new users
get_pricing()                    # transparent tiers, referral %, creator share
get_system_status()              # platform health, bot roster, live tool count
```

### Subscribe programmatically

```python
# 1. Get a Stripe-hosted checkout URL (one call — no browser needed after this)
get_checkout_url(email="you@example.com", tier="paper")
# → returns a checkout_url the user visits once to enter payment details
# → sub_live_... key is emailed automatically after payment

# 2. Set ALGOCHAINS_SUBSCRIBER_KEY, then accept the CFTC risk disclosure
#    (required before signals; subscriber_id is resolved from the key)
accept_subscriber_terms(
    acknowledgment="I have read and understand the risk disclosure above. I accept full responsibility for my trading decisions."
)

# 3. Subscribe to MNQ copy-trade signals (published for you to review and act on)
join_bot(bot="MNQ", size_multiplier=1.0)

# 4. Check stdio status; use the HTTP bridge for portfolio/signal tools
get_subscriber_status()
```

### Usage metering

```python
get_my_usage()
# → calls_this_month, included_quota, overage_calls, projected_overage_usd
```

### Referral program

```python
create_referral_code()   # → code: "AC-X7K2NP"
get_my_referrals()       # attributed sign-ups + commission
get_referral_earnings()  # total earned, pending payout
```

### Creator revenue (strategy publishers)

```python
create_creator_onboarding_link(creator_id="cr_...", creator_email="you@example.com")
# → Stripe Connect Express onboarding URL (KYC, bank account)

get_my_creator_earnings(creator_id="cr_...")
# → accrued_usd, paid_usd, pending_payout_usd, next_payout_date

# Owner-gated payout run (requires OWNER_API_TOKEN)
run_creator_payouts(dry_run=True)   # preview
run_creator_payouts(dry_run=False, owner_token="tok_...")  # execute transfers
```

### Realized P&L (live-tier subscribers)

```python
get_my_realized_pnl()
# → realized_pnl_usd, trade_count, period, disclaimer (CFTC 4.41(b))
```

| Tool | Auth | Tier gate |
|------|------|-----------|
| `get_started` | None | Public |
| `get_pricing` | None | Public |
| `get_system_status` | None | Public |
| `get_checkout_url` | None | Public (Stripe handles billing) |
| `accept_subscriber_terms` | `sub_live_*` key | Paper / Live |
| `get_my_usage` | `sub_live_*` key | Paper / Live |
| `create_referral_code` | `sub_live_*` key | Paper / Live |
| `get_my_referrals` | `sub_live_*` key | Paper / Live |
| `get_referral_earnings` | `sub_live_*` key | Paper / Live |
| `get_my_realized_pnl` | `sub_live_*` key | Live |
| `create_creator_onboarding_link` | `OWNER_API_TOKEN` | Owner |
| `get_my_creator_earnings` | `OWNER_API_TOKEN` | Owner |
| `run_creator_payouts` | `OWNER_API_TOKEN` | Owner |

> Signals are published for the subscriber to review and act on — no automated execution.
> Past performance is not indicative of future results. See `accept_subscriber_terms` for
> the full CFTC risk disclosure.

---

## Desktop Tower Dispatch

Heavy ML workloads (hyperparameter sweeps, walk-forward validation, feature importance) run on the desktop tower (configured via `ALGOCHAINS_TOWER_HOST`) via `dispatch_tower_job`. The Mac stays clean.

```python
# Dispatch a backtest or ML job to the GPU tower
dispatch_tower_job(
    job_type="backtest",
    params={"strategy": "mnq_scalper", "lookback_days": 252, "wfv_windows": 12}
)

# Check job status
get_tower_job_status(job_id="job_abc123")
```

From the CLI (`ac` command):
```bash
# Not yet in ac — see CLI_GAP_ANALYSIS.md for ac tower subcommand roadmap
python3 -c "
from algochains_mcp.algoclaw.desktop_tower import dispatch_tower_job
dispatch_tower_job('backtest', {'strategy': 'mnq_scalper', 'lookback_days': 90})
"
```

**What runs where:**

| Component | MacBook (execution) | Desktop Tower (ML/GPU) |
|-----------|:-------------------:|:---------------------:|
| Live bots (MNQ/CL/MES/NQ) | ✅ launchd | — |
| Token Guardian, Kalshi daemon | ✅ launchd | — |
| Command Center (`:3333`) | ✅ cloudflared tunnel | — |
| Onyx RAG (`$ALGOCHAINS_TOWER_HOST:8085`) | — | ✅ |
| GPU/ML: FinBERT, Kronos, vLLM | — | ✅ |
| Heavy backtests via `dispatch_tower_job` | sends job → | ✅ executes |

---

## Security

### Authentication Tiers

| Scope | How to Authenticate | What's Allowed |
|-------|--------------------|----|
| **Public / demo** | No credentials | Market data, Onyx search, regime detection |
| **Team** | `ALGOCHAINS_BRIDGE_API_KEY` | Bot metrics, positions (read-only) |
| **Owner** | `OWNER_API_TOKEN` | Order execution, bot restart, emergency stop |

### Localhost-Only Services

The following services bind to `127.0.0.1` only and are **never exposed publicly**:

- MCP server HTTP bridge (port 8765 / stdio)
- Command Center dev server (port 3333) — external access via Cloudflare Access tunnel only
- Onyx RAG stack (tower port 8085) — accessible via Tailscale VPN only

### Hard-Coded Safety Limits

These cannot be overridden by any AI agent:

```
Daily loss limit:      $500   (hard stop, all orders blocked until midnight)
Max drawdown:          15%    (circuit breaker trips at 15% peak-to-trough)
Human confirmation:    required for all orders above $10K notional
AI loop detection:     5 identical calls in 60s → 30-minute order block
VIX gate:             all trades blocked when VIX > 35
```

Full safety documentation: [SAFETY_MODEL.md](SAFETY_MODEL.md)

### `OWNER_API_TOKEN` — Mutation Gating

Tools in danger tier 2 (order execution) and tier 3 (destructive) require `OWNER_API_TOKEN` in the request header. The HTTP bridge verifies this before dispatching. AI agents that do not supply it get a `policy_denied` error — not a soft warning.

```bash
# Set in .env (never commit)
OWNER_API_TOKEN=your-owner-token-here
```

---

## What's New in v22.x

### v22.4 (2026-04-06) — UX & Team Onboarding
- Complete README rewrite (plain English, team access)
- `scripts/quickstart.py` — interactive setup wizard with health checks
- `SAFETY_MODEL.md` — answers "is this safe?" for every failure mode
- `tool_danger_tiers.py` — machine-readable danger classification (0–3) for the documented 503-tool surface
- HTTP bridge `/tools` endpoint now returns `danger_tier`, `safe_in_demo_mode`, etc.
- `get_bot_health` includes `e2e_sentinel`, desktop inference SLO, and decision latency SLO slices for signal-to-fill traceability

### v22.3 (2026-04-06) — Proprietary Data Ingestion
- `ingest_csv_data` — validate and ingest real OHLCV CSV files into `state/custom_data/`
- `ingest_json_signals` — import pre-computed signals, ML features, labels, and regime tags
- `connect_onyx_docs` — index local research docs into Onyx for `onyx_ask()` / `onyx_search()`
- `register_strategy` and `list_ingested_data` — register custom strategy specs and audit imported data

### v22.2 (2026-04-21) — Kalshi Pipeline + Model Integrity
- **Kalshi prediction markets** — AI ensemble → Kelly sizing → order execution
- **Subscriber tools** — JWT tier auth, `get_subscriber_portfolio`, `get_marketplace_listings`
- **Unified path resolver** (`paths.py`) — `default_control_tower()` works on Mac + WSL tower
- **Data backend chain** — Databento → Massive S3 (back to 2003) → Polygon → yfinance
- **SHA-256 model integrity** — startup check raises on tampered `.pkl`, XGBoost JSON companion, `model_manifest.json`
- **Drawdown Triple Penance** — `drawdown_start_ts` auto-logged on first daily loss hit (Bailey & LdP 2015)

### v22.0 (2026-04-05) — MCP 2025-11-25 Full Compliance
- Elicitation (human confirmation for high-value trades)
- Durable Tasks (background backtest/optimization jobs)
- SSE streaming transport
- OIDC discovery endpoint
- Trading guardrails with circuit breakers
- AlphaLoop evolution daemon

> See the full [CHANGELOG.md](CHANGELOG.md) for v21.x, v22.x, and legacy v26 audit entries.

---

## Quick Setup Options (Pick Your Path)

| | Path | Credential needed | Best for |
|---|------|:-----------------:|----------|
| **A** | Demo mode | None | Market data, regime detection, tools exploration |
| **B** | AlgoChains hosted paper | `sub_live_…` key (free signup) | Copy-trade MNQ bot, zero broker setup |
| **B-2** | Alpaca paper | Alpaca paper API key | Your own paper equity account |
| **C** | Full live | Tradovate + others | Real futures/equities trading |

### Option A — Demo Mode (No Credentials, 1 Minute)

```bash
pip install algochains-mcp-server
python scripts/quickstart.py --mode demo
```

Available immediately (no credentials):
- `get_quote("AAPL")` — live price for any symbol
- `detect_market_regime()` — trending / ranging / choppy
- `get_macro_signals()` — macro environment analysis
- `discover_tools()` — find any of the 503 tools
- `onyx_ask("any question")` — knowledge base search

### Option B — AlgoChains Hosted Paper (Free, No Broker Needed)

No Tradovate account. No Alpaca account. No broker credentials at all.

1. Sign up at **[algochains.ai](https://algochains.ai)** — free hosted virtual paper account
2. Copy your subscriber key from the dashboard (`sub_live_…`)
3. Set it and run:

```bash
export ALGOCHAINS_SUBSCRIBER_KEY=sub_live_your_key_here
python scripts/quickstart.py --mode paper
```

What unlocks immediately: local subscriber onboarding/status tools plus the hosted
bridge subscriber surface for copy-trade signals from the live MNQ bot, paper P&L
tracking, fill history, and self-directed paper orders filled at real quotes.
See the [Subscriber Onramp](#subscriber-onramp--try-it-free-no-broker-required) section above.

### Option B-2 — Alpaca Paper (Your Own Broker)

```bash
export ALPACA_API_KEY=your-paper-key
export ALPACA_SECRET_KEY=your-paper-secret
export ALPACA_PAPER=true
python scripts/quickstart.py --mode paper
```

### Option C — Full Live Setup

```bash
cp .env.example .env
# Edit .env with Tradovate, Polygon, Databento, Slack credentials
python scripts/quickstart.py --health-check --mode live
```

### Generate IDE Config

```bash
python scripts/quickstart.py --generate-config cursor         # Cursor
python scripts/quickstart.py --generate-config claude-desktop # Claude Desktop
python scripts/quickstart.py --generate-config windsurf       # Windsurf
```

---

## Data Backends

AlgoChains uses a priority chain — best available source wins automatically:

| Priority | Backend | Coverage | Use Case |
|----------|---------|----------|----------|
| 1 | **Databento** | XNAS.ITCH + XNYS.PILLAR; OHLCV-1d + OHLCV-1m | Futures tick data, live streaming |
| 2 | **Massive S3** | `us_stocks_sip/day_aggs_v1/` back to **2003** | Historical equity backtests, survival-bias-free universe |
| 3 | **Polygon** | REST bars + news snapshots | News features, intraday bars |
| 4 | **yfinance** | Free, ~5yr history | Dev fallback, swing bots |

Force a specific backend: `DATA_BACKEND=databento|massive|polygon|yfinance` in `.env`.

---

## Command Center

| URL | Status | Notes |
|-----|--------|-------|
| **https://cc.algochains.io** | Live | Cloudflare Access — authenticate with your `@algochains.io` account |
| http://localhost:3333 | Local dev | Always accessible without auth |

**Run locally:**
```bash
cd algochains-command-center
npm run dev   # starts on :3333
```

**Start Cloudflare tunnel:**
```bash
cloudflared tunnel run <your-tunnel-id> >> logs/cloudflared_cc.log 2>&1 &
```

**Dashboard panels (V22):**
- Bot Status Cards — process state, uptime, last signal, AI confidence
- P&L Chart + Positions Table + Risk Dashboard
- Bracket Status Panel + AI Ensemble Health + Live Trade Validation Feed (SSE)
- Subscriber Protection Panel + System Health

---

## Agentic Quick-Start Prompts

Copy these directly into Claude or Cursor:

### Subscriber prompts (free, no broker needed)

```
Portfolio snapshot over the HTTP bridge:
"Run get_my_portfolio with my subscriber bridge key. What's my paper balance and P&L today?"

Signal stream check over the HTTP bridge:
"Call get_signal_stream for the MNQ bot with my subscriber bridge key. What signals fired in the last 2 hours?"

Weekly fill review:
"Run get_my_fills with limit=50. Break down P&L by day."

Marketplace discovery:
"Run get_marketplace_listings. What bots are available and what's each bot's asset class?"
```

### Live bot / operator prompts

```
Morning brief:
"Run get_macro_signals and get_live_bot_metrics. Summarize market conditions and P&L."

Bot health check:
"Run get_bot_health for all 4 bots. Flag anything that needs attention."

Pre-trade regime check:
"Before I place any orders, run detect_market_regime and check VIX. Should I trade today?"

Validate a backtest:
"Run validate_strategy_metrics: Sharpe 2.4, MaxDD 9%, WinRate 58%, 180 trades.
 Does it pass the MCPT gate? What's the DSR?"

Prop fund compatibility:
"Use evaluate_strategy_for_prop_fund: MNQ scalper, $600 max daily loss, $2500 max DD,
 $120 avg daily profit, holds overnight. Which fund should I target?"

Emergency system check:
"Run check_all_broker_credentials and check_rithmic_status. What's ready, what's missing?"

Tower dispatch:
"Dispatch an overnight Optuna sweep for MNQ to the desktop tower. 200 trials, Sharpe objective."
```

---

## Supported Brokers

| Broker | Asset Classes | Status |
|--------|--------------|--------|
| **Tradovate** | Futures (MNQ, CL, MES, NQ, ES, GC) | ✅ Live |
| **Alpaca** | Equities, ETFs, Options, Crypto | ✅ Live + Paper |
| **OANDA** | Forex (50+ pairs) | ✅ Live |
| **Interactive Brokers** | Stocks, Futures, Options, Forex | ✅ Live (`ib_async`) |
| **Kalshi** | Prediction markets (US events) | ✅ Live |
| **E*TRADE** | Equities, Options, ETFs | ✅ OAuth 1.0a |
| **Rithmic** | Futures via prop fund platforms | ⏳ DRY_RUN (vendor NDA pending) |
| **Charles Schwab** | Equities, Options, Futures | ⚠️ Stubs (OAuth 2.0 PKCE) |

```bash
# Check all broker credential status at once
check_all_broker_credentials()   # masked — never exposes values
```

---

## Architecture

```
Your AI (Claude / Cursor / ChatGPT)
         │
         │ MCP 2025-11-25 (stdio or HTTP + SSE)
         ▼
AlgoChains MCP Server
  ├── 503 tools / 168 smart-mode (20 domains)
  ├── Trading Guardrails (hard-coded limits, AI loop detection)
  ├── Account Protection (12 pre-trade guards)
  ├── Onyx RAG (semantic search — 400+ docs + 472 skills)
  └── Circuit Breakers (per-tool rate limits, daily loss stops)
         │
         ├── Tradovate     (MNQ, CL, MES, NQ futures — live fills)
         ├── Alpaca        (equities, crypto, options)
         ├── OANDA         (forex)
         ├── Databento     (tick-level data — XNAS.ITCH)
         ├── Massive S3    (day bars back to 2003)
         ├── Polygon       (real-time bars, news)
         └── FRED, CBOE, Kalshi, Polymarket  (macro / alt data)
```

**Data policy:** No synthetic data. No mock fills. No placeholder values. Every tool connects to a real API or fails closed with an explicit error.

---

## Docs

| File | Purpose |
|------|---------|
| [SAFETY_MODEL.md](SAFETY_MODEL.md) | Is this safe? Failure modes, guardrails, team access |
| [CHANGELOG.md](CHANGELOG.md) | Full version history |
| [docs/GOTCHAS_AND_BUGS.md](docs/GOTCHAS_AND_BUGS.md) | Confirmed bugs, gotchas, operational surprises |
| [docs/DEVELOPER_TIER_ONBOARDING.md](docs/DEVELOPER_TIER_ONBOARDING.md) | Developer key setup, scopes, and bridge auth constraints |
| [docs/SUBSCRIBER_TOOLS.md](docs/SUBSCRIBER_TOOLS.md) | Subscriber onboarding, stdio-vs-bridge tools, scopes, and copy-trade constraints |
| [docs/NUMERAI_TOURNAMENT.md](docs/NUMERAI_TOURNAMENT.md) | Numerai tournament tool sequence, upload gates, and troubleshooting |
| [docs/TRADOVATE_PARITY.md](docs/TRADOVATE_PARITY.md) | Tradovate endpoint mapping vs community server |
| [docs/CLI_GAP_ANALYSIS.md](docs/CLI_GAP_ANALYSIS.md) | `ac` CLI current commands + 10 missing subcommands roadmap |
| [LATENCY_GUIDE.md](LATENCY_GUIDE.md) | Measured tool call latencies (Mac M3 Max, real calls) |
| [MARKETPLACE_CREATOR_GUIDE.md](MARKETPLACE_CREATOR_GUIDE.md) | Publish a validated bot; subscriber copy-trade pipeline setup |
| [algoclaw/README.md](algoclaw/README.md) | AlgoClaw agent skill system |

---

<div align="center">

**Built by Tyler Reynolds — experimental AI trading infrastructure.**

[Safety](SAFETY_MODEL.md) · [Changelog](CHANGELOG.md) · [Command Center](https://cc.algochains.io) · [Marketplace](https://algochains.ai)

*Experimental software connected to live trading accounts. Use at your own risk.*

</div>
