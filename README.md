# AlgoChains MCP Server

[![MCP](https://img.shields.io/badge/MCP-2025--11--25-blue?style=flat-square)](https://modelcontextprotocol.io)
[![Tools](https://img.shields.io/badge/tools-~480%20full%20%7C%20curated%20smart-green?style=flat-square)](#tool-domains)
[![Version](https://img.shields.io/badge/version-22.5.0-blueviolet?style=flat-square)](#whats-new)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue?style=flat-square)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-purple?style=flat-square)](LICENSE)
[![Data](https://img.shields.io/badge/data-Databento%20%7C%20Massive%20S3%20%7C%20Polygon-yellow?style=flat-square)](#data-backends)

---

> **An MCP server for AI-assisted trading research and operations — market data, backtesting, ML inference, and broker connectivity across ~480 tools in 20 domains. Real APIs only, no synthetic data.**

Connect your AI assistant (Claude, Cursor, ChatGPT) to trading infrastructure. You bring your
own broker and data credentials; the server exposes them to your assistant as typed, guarded tools.

```
You ask Claude:                    Claude calls:                    Server calls:
"What's my NQ position?"   →  get_positions()           →  your broker API → real data
"Run a backtest on MNQ"    →  run_backtest()             →  your tick-data archive
"Is the market trending?"  →  detect_market_regime()    →  Polygon + FRED → analysis
```

> **Two ways to use AlgoChains — know which one you're in:**
>
> | | **Self-hosted OSS server** (this repo) | **Hosted platform** ([algochains.ai](https://algochains.ai)) |
> |---|---|---|
> | You provide | Your own broker + data API keys | A platform account |
> | Runs on | Your machine | Managed by AlgoChains |
> | Brokers | Anything you have credentials for | Alpaca + Tradovate live today; more rolling out |
> | Paper trading | Alpaca paper (your keys) | AlgoChains-managed paper accounts (subscriber feature) |
> | Trust model | You own the keys and the risk | Scoped, per-account permissions (see [Access Tiers](#access-tiers--scoped-permissions)) |
>
> This README documents the **self-hosted OSS server**. Platform-only features (managed paper
> accounts, marketplace subscriptions) are called out explicitly and gated behind platform auth.

---

## Quick Install

**Option 1 — pipx (recommended, works on macOS Homebrew Python)**
```bash
pipx install algochains-mcp-server
algochains-mcp --generate-config cursor
algochains-mcp --mode demo
```

**Option 2 — pip in a virtual environment**
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install algochains-mcp-server
```

**Option 3 — editable install from source (for development / contributors)**
```bash
git clone https://github.com/AlgoChains/algochains-mcp-server.git
cd algochains-mcp-server
pip install -e ".[http,supabase,auth]"
algochains-mcp --mode demo
```

**Option 4 — Homebrew (macOS)**
```bash
brew tap algochains/algochains
brew install algochains
```

**Option 5 — Windows (PowerShell)**
```powershell
# Step 1: Check Python is installed and on PATH
python --version       # should print 3.11 or higher
# If not found: winget install Python.Python.3.12  (then restart PowerShell)

# Step 2: Install pipx (one-time)
python -m pip install pipx
python -m pipx ensurepath
# ← RESTART PowerShell after this — PATH changes require a new shell

# Step 3: Install
pipx install algochains-mcp-server

# Step 4: Verify
algochains-mcp --version
```

> **macOS Homebrew Python:** If you hit a PEP 668 "externally-managed-environment" error, use
> `pipx install algochains-mcp-server`. Do **not** use `--break-system-packages`.
>
> **Windows — `pip` not found:** Use `python -m pip`. If `python` itself is missing, install via
> `winget install Python.Python.3.12` or [python.org](https://www.python.org/downloads/) (check
> "Add Python to PATH").
>
> **Windows winget package:** `winget install AlgoChains.algochains` is pending submission to the
> community repo.

That's it. In **demo mode you need no credentials** — the server starts with public market-data,
regime, and search tools. Add your own broker/data keys to unlock live access (see
[Setup Options](#setup-options)).

---

## Access Tiers & Scoped Permissions

**Nothing in this server is "open by default."** Capability is gated by which credential you
present. Higher tiers are strictly additive and every mutation is checked at the boundary, not
just hinted at.

| Tier | Credential | What it can do | Touches algochains.ai? |
|------|-----------|----------------|:----------------------:|
| **Public / demo** | none | Market data, `detect_market_regime`, `onyx_search`, `discover_tools` | no |
| **Self-hosted live** | *your own* broker/data keys in `.env` | Everything your keys allow on **your** accounts | no |
| **Team (read)** | `ALGOCHAINS_BRIDGE_API_KEY` | Read bot metrics / positions from a bridge you operate | only your bridge |
| **Subscriber** | `sub_live_…` key (`X-Api-Key` on hosted bridge) | Read **your** paper portfolio, fills, assignments; optional self-directed paper orders | yes (scoped to your account) |
| **Owner** | `OWNER_API_TOKEN` | Order execution, bot restart, emergency stop on **owner** infra | owner infra only |

Key properties:

- **Order execution and destructive tools (danger tiers 2–3)** require `OWNER_API_TOKEN` in the
  request header. The HTTP bridge verifies it *before* dispatch — agents without it receive a
  hard `policy_denied`, never a soft warning.
- **Subscriber keys are scoped to one account.** A `sub_live_` key can read that subscriber's own
  paper portfolio and the public bot metrics they're subscribed to — it cannot place orders, read
  other users' data, or reach owner infra.
- **Bridge keys are read-only** and only see the bridge you point them at.
- **Public/demo never reaches private infrastructure.** Tools that depend on owner-only services
  fail closed with an explicit error when their credential or backend is absent.

> **Self-hosting:** You are the owner of your own deployment. Set your own `OWNER_API_TOKEN`,
> broker keys, and data keys in `.env` (never commit it). The permission model above is what keeps
> a connected AI agent from escalating beyond the credential you gave it.

---

## Smart Mode vs Full Mode

Tool exposure is controlled by `ALGOCHAINS_TOOL_MODE`:

| Mode | Tools Exposed | When to Use |
|------|---------------|-------------|
| **Smart** (default) | Curated subset | Cursor, Windsurf (tool-count limits), everyday use |
| **Full** (`ALGOCHAINS_TOOL_MODE=full`) | ~480 tools | Claude Code, full agentic sessions |

> Exact counts evolve per release — `discover_tools()` and `registry.json` are the source of truth.

**Smart mode includes** all live-bot tools, market data, signals, research/backtest, semantic
search, prop-fund pipeline, position sizing, broker management, and order execution. **Full mode**
adds advanced DeFi, prediction-market order placement, multi-tenant SaaS, alt-data pipelines, and
more.

### `discover_tools` — Find Any Tool Without Full Mode

```python
discover_tools("walk-forward validation with leakage check")
# → walk_forward_test, run_mcpt_validation, analyze_overfitting

execute_dynamic_tool("walk_forward_test", {"symbol": "MNQ", "lookback_days": 252})
```

This keeps the everyday token cost low while still letting an agent reach every tool on demand.

---

## Tool Domains

~480 tools across 20 domains (smart-mode exposes a curated slice of each):

| # | Domain | Key Tools |
|---|--------|-----------|
| 1 | **Market Data** | `get_quote`, `get_ohlcv`, `get_tick_data`, `get_options_chain`, `get_footprint_chart` |
| 2 | **Signals & Analysis** | `generate_signal`, `detect_market_regime`, `get_ensemble_vote`, `compute_gex`, `read_tape` |
| 3 | **Research & Backtesting** | `run_backtest`, `walk_forward_test`, `run_mcpt_validation`, `validate_strategy_metrics` |
| 4 | **Position Sizing** | `compute_r_multiple_size`, `compute_volatility_targeted_size`, `compute_idm` |
| 5 | **Options Analytics** | `compute_option_greeks`, `find_optimal_strike`, `unusual_options_activity` |
| 6 | **Prop Fund Pipeline** | `evaluate_strategy_for_prop_fund`, `simulate_prop_fund_evaluation`, `list_prop_funds` |
| 7 | **Broker Management** | `check_all_broker_credentials`, `connect_broker`, `get_broker_onboarding_guide` |
| 8 | **Account Protection** | `check_protection_status`, `record_stop_event`, `lock_instrument`, `check_rate_limit_status` |
| 9 | **Order Execution** | `place_order`, `place_bracket_order`, `cancel_order`, `smart_route_order` *(owner-gated)* |
| 10 | **Emergency / Destructive** | `flatten_all_positions`, `cancel_all_orders`, `emergency_stop` *(owner-gated)* |
| 11 | **Intelligence (Search + Macro)** | `onyx_ask`, `onyx_search`, `get_macro_signals`, `get_fed_policy_signals` |
| 12 | **Prediction Markets** | `get_prediction_markets`, `search_prediction_markets`, `place_kalshi_order` *(owner-gated)* |
| 13 | **Skills Bridge** | `list_skills`, `get_skill_detail`, `search_skills`, `get_skills_for_task` |
| 14 | **Agent Memory** | `get_openclaw_memory`, `store_trade_lesson`, `get_current_regime` |
| 15 | **Live Bot Intelligence** | `get_bot_health`, `get_live_bot_metrics`, `get_bot_position_state` |
| 16 | **Compute Dispatch** | `dispatch_tower_job`, `get_tower_job_status`, `run_tower_backtest` |
| 17 | **Performance Reporting** | `generate_bot_tearsheet`, `get_bot_metrics_full`, `run_mcpt_pipeline` |
| 18 | **Platform / SaaS** | `join_waitlist`, `create_support_ticket`, `track_platform_event` |
| 19 | **AlphaLoop / Evolution** | `run_alphaloop_cycle`, `get_alphaloop_results`, `get_algochains_telos` |
| 20 | **Temporal Knowledge Graph** | `graphiti_search`, `graphiti_temporal_query`, `graphiti_health` |

> **Domain 20 (Graphiti):** an **advisory** temporal context graph (getzep/graphiti), isolated in
> its own Python 3.13 venv. Reads are Tier-1; writes are local-discovery only. It is `agent_memory`
> authority — **never** broker truth and never a trading dependency. Fails closed
> (`graphiti_unavailable`) when its backend is absent.

---

## Live Bot Showcase

AlgoChains operates live futures bots on Tradovate. Their **public, read-only** state — process
health, ML pipeline status, brackets — streams through this server. Order placement and bot control
remain owner-gated.

| Bot | Symbol | Strategy | Read Tool |
|-----|--------|----------|-----------|
| `MNQ_Upgraded_Scalper` | MNQ | Multi-AI ensemble, 5-min bars | `get_bot_health(bot="MNQ")` |
| `CL_Swing_Scalper` | CL | Sentiment + momentum | `get_bot_health(bot="CL")` |
| `MES_EMA_Swing` | MES | EMA pullback + regime detection | `get_bot_health(bot="MES")` |
| `NQ_EMA_Swing` | NQ | Trend following + foundation model | `get_bot_health(bot="NQ")` |

```python
# Read-only health snapshot (signal→order→bracket→fill lifecycle, ML flags, pipeline state)
health = get_bot_health(bot="MNQ")

# All bots in one call
status = get_all_bot_ops_status()
```

Read access to live-bot metrics is granted by a **bridge key** you operate; it exposes no
credentials and cannot place orders.

---

## Compute Dispatch

Heavy ML workloads (hyperparameter sweeps, walk-forward validation) can be dispatched to a separate
GPU compute node, keeping your interactive machine responsive. The target host is configured via
the `ALGOCHAINS_TOWER_HOST` environment variable — no host is hard-coded.

```python
dispatch_tower_job(
    job_type="backtest",
    params={"strategy": "mnq_scalper", "lookback_days": 252, "wfv_windows": 12}
)
get_tower_job_status(job_id="job_abc123")
```

| Component | Interactive node | Compute node |
|-----------|:----------------:|:------------:|
| Live bots | ✅ | — |
| GPU/ML (FinBERT, foundation models, vLLM) | — | ✅ |
| Heavy backtests via `dispatch_tower_job` | sends job → | ✅ executes |

> Compute and search backends bind to private addresses on the operator's own network (e.g. a
> Tailscale tailnet). They are not internet-reachable and are configured entirely via environment
> variables.

---

## Security

### Hard-Coded Safety Limits

These cannot be overridden by any AI agent:

```
Daily loss limit:    $500   (hard stop, all orders blocked until reset)
Max drawdown:        15%    (circuit breaker trips at 15% peak-to-trough)
Human confirmation:  required for all orders above $10K notional
AI loop detection:   5 identical calls in 60s → 30-minute order block
VIX gate:            all trades blocked when VIX > 35
```

Full safety documentation: [SAFETY_MODEL.md](SAFETY_MODEL.md)

### Secrets & Configuration

- **All credentials come from `.env` (gitignored) or environment variables.** No keys, tokens,
  hostnames, or personal infrastructure are hard-coded in this repository.
- **Owner / order-execution tools require `OWNER_API_TOKEN`**, verified at the HTTP bridge before
  any mutation dispatches.
- **Private services bind to localhost or a private VPN address** and are never exposed publicly.
- **Reporting a vulnerability:** email `security@algochains.ai`.

```bash
# Set in .env (never commit) — example placeholders only
OWNER_API_TOKEN=replace-with-your-own-owner-token
ALGOCHAINS_BRIDGE_API_KEY=replace-with-your-own-bridge-key
```

---

## Setup Options

### Option A — Demo Mode (no credentials, 1 minute)
```bash
algochains-mcp --mode demo
```
Available immediately: `get_quote("AAPL")`, `detect_market_regime()`, `get_macro_signals()`,
`discover_tools()`, `onyx_ask("...")`.

### Option B — Paper Mode (Alpaca paper, free)
```bash
export ALPACA_API_KEY=your-paper-key
export ALPACA_SECRET_KEY=your-paper-secret
export ALPACA_PAPER=true
algochains-mcp --mode paper
```

### Option C — Full Live Setup
```bash
cp .env.example .env
# Edit .env with your Tradovate / Polygon / Databento credentials
algochains-mcp --health-check --mode live
```

### Generate IDE Config
```bash
algochains-mcp --generate-config cursor          # Cursor
algochains-mcp --generate-config claude-desktop  # Claude Desktop
algochains-mcp --generate-config windsurf        # Windsurf
```

### Which URL Do I Use?

Local installs and remote connectors use different transports:

| Client | Use this | Why |
|--------|----------|-----|
| Cursor, Claude Desktop, Windsurf | Generated `stdio` config from `algochains-mcp --generate-config ...` | These apps can spawn the local PyPI package on your machine. |
| Claude.ai web/mobile custom connector | Public HTTPS URL such as `https://<your-domain>/mcp` | Claude.ai calls the server from Anthropic's infrastructure, so it cannot reach your `localhost`, phone, LAN, or Tailscale-only URL. |
| Local remote-connector test | `algochains-mcp-http --host 127.0.0.1 --port 8080` plus a secure HTTPS tunnel | The tunnel provides the public `https://.../mcp` URL that Claude.ai requires. |

For a mobile Claude test, the PyPI package alone is not enough because it runs locally.
Start the HTTP transport, expose it through a secure tunnel, then paste the tunnel's
`https://.../mcp` URL into Claude.ai:

```bash
pipx install "algochains-mcp-server[http]"
export ALGOCHAINS_HTTP_TRANSPORT_SECRET="<random-token>"
algochains-mcp-http --host 127.0.0.1 --port 8080
cloudflared tunnel --url http://127.0.0.1:8080
```

Use the tunnel URL as the custom connector URL:

```text
https://<cloudflared-subdomain>.trycloudflare.com/mcp
```

If the connector UI asks for authentication, use the value of
`ALGOCHAINS_HTTP_TRANSPORT_SECRET` as the bearer token. For production, replace the
temporary tunnel with a stable hosted endpoint such as `https://mcp.algochains.ai/mcp`
or your own domain behind Cloudflare.

Never expose owner/live trading tools publicly without bearer auth, WAF/IP restrictions,
and strict tool policy checks. See [Remote Connectors](docs/REMOTE_CONNECTORS.md) for the
full transport matrix and security checklist.

---

## Data Backends

A priority chain — best available source wins automatically:

| Priority | Backend | Coverage | Use Case |
|----------|---------|----------|----------|
| 1 | **Databento** | Tick + OHLCV | Futures tick data, live streaming |
| 2 | **Massive S3** | Day aggregates back to 2003 | Survival-bias-free historical backtests |
| 3 | **Polygon** | REST bars + news | News features, intraday bars |
| 4 | **yfinance** | Free, ~5yr history | Dev fallback |

Force a backend with `DATA_BACKEND=databento|massive|polygon|yfinance` in `.env`.

---

## Supported Brokers (self-hosted, bring your own credentials)

When you self-host and supply your own API keys, the server can connect to:

| Broker | Asset Classes | Status |
|--------|--------------|--------|
| **Tradovate** | Futures | ✅ Supported |
| **Alpaca** | Equities, ETFs, Options, Crypto | ✅ Supported (live + paper) |
| **OANDA** | Forex | ✅ Supported |
| **Interactive Brokers** | Stocks, Futures, Options, Forex | ✅ Supported (`ib_async`) |
| **Kalshi** | Prediction markets | ✅ Supported |
| **E*TRADE** | Equities, Options, ETFs | 🧪 Experimental (OAuth 1.0a) |
| **Rithmic** | Futures (prop platforms) | ⏳ DRY_RUN (vendor NDA pending) |
| **Charles Schwab** | Equities, Options | ⚠️ Stub (OAuth 2.0 PKCE) |

> **On the hosted [algochains.ai](https://algochains.ai) platform**, managed broker connections are
> currently **Alpaca and Tradovate**, with more rolling out. The list above is the broader set the
> OSS server can talk to when *you* hold the credentials.

```bash
check_all_broker_credentials()   # masked — never exposes values
```

---

## Architecture

```
Your AI (Claude / Cursor / ChatGPT)
         │  MCP 2025-11-25 (stdio or HTTP + SSE)
         ▼
AlgoChains MCP Server
  ├── ~480 tools (smart-mode curated subset by default)
  ├── Trading Guardrails (hard-coded limits, AI loop detection)
  ├── Account Protection (pre-trade guards)
  ├── Semantic Search (RAG over docs + skills)
  └── Circuit Breakers (per-tool rate limits, daily loss stops)
         │
         ├── Your broker(s)     (Tradovate / Alpaca / OANDA / IB …)
         ├── Databento          (tick-level data)
         ├── Massive S3         (day bars back to 2003)
         ├── Polygon            (real-time bars, news)
         └── FRED, CBOE, Kalshi (macro / alt data)
```

**Data policy:** No synthetic data. No mock fills. No placeholder values. Every tool connects to a
real API or fails closed with an explicit error.

---

## What's New

### v22.4 — UX & onboarding
- Plain-English README + setup wizard with health checks
- `SAFETY_MODEL.md` — failure modes for every guardrail
- Machine-readable danger tiers (0–3) for every tool; `/tools` endpoint returns `danger_tier`, `safe_in_demo_mode`

### v22.2 — Prediction markets + model integrity
- Kalshi pipeline (ensemble → Kelly sizing → execution, owner-gated)
- Subscriber tools: `sub_live_*` key auth, `get_my_portfolio`, `get_marketplace_listings`, `place_paper_order`
- Multi-backend data chain (Databento → Massive S3 → Polygon → yfinance)
- SHA-256 model-integrity check on startup (raises on tampered artifacts)

### v22.0 — MCP 2025-11-25 compliance
- Elicitation (human confirmation for high-value trades), durable background tasks,
  SSE streaming, OIDC discovery, trading guardrails with circuit breakers

> Full history: [CHANGELOG.md](CHANGELOG.md)

---

## Agentic Quick-Start Prompts

```
Morning brief:
"Run get_macro_signals and get_live_bot_metrics. Summarize market conditions."

Bot health check:
"Run get_bot_health for all bots. Flag anything that needs attention."

Validate a backtest:
"Run validate_strategy_metrics: Sharpe 2.4, MaxDD 9%, WinRate 58%, 180 trades.
 Does it pass the MCPT gate? What's the DSR?"

Prop fund compatibility:
"Use evaluate_strategy_for_prop_fund: MNQ scalper, $600 max daily loss, $2500 max DD."
```

---

## Docs

| File | Purpose |
|------|---------|
| [SAFETY_MODEL.md](SAFETY_MODEL.md) | Failure modes, guardrails, access model |
| [CHANGELOG.md](CHANGELOG.md) | Full version history |
| [docs/GOTCHAS_AND_BUGS.md](docs/GOTCHAS_AND_BUGS.md) | Confirmed gotchas and operational notes |
| [docs/TRADOVATE_PARITY.md](docs/TRADOVATE_PARITY.md) | Tradovate endpoint mapping |
| [docs/CLI_GAP_ANALYSIS.md](docs/CLI_GAP_ANALYSIS.md) | `ac` CLI roadmap |
| [LATENCY_GUIDE.md](LATENCY_GUIDE.md) | Measured tool-call latencies |
| [MARKETPLACE_CREATOR_GUIDE.md](MARKETPLACE_CREATOR_GUIDE.md) | Submit a validated bot to the marketplace |

---

<div align="center">

**AlgoChains — experimental AI trading infrastructure.**

[Safety](SAFETY_MODEL.md) · [Changelog](CHANGELOG.md) · [Platform](https://algochains.ai)

*Experimental software that can connect to live trading accounts. Use at your own risk. Nothing
here is financial advice.*

</div>
