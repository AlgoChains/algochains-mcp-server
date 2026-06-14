# AlgoChains MCP Server — Agent Context

This file is the primary context document for IDE agents (Cursor, Claude, Windsurf,
Codex, etc.). Read it before invoking any AlgoChains tool so you don't guess at tool
names, auth requirements, or safety rules.

---

## 1. Identity and Transport

**What this server is:** An MCP server that connects your AI assistant to trading
infrastructure — market data, backtesting, ML regime detection, broker connectivity,
and live-bot operations across 503 tools in 21 domains.

**Three transport entry-points:**

| Transport | Entry-point | Best for |
|-----------|-------------|----------|
| **stdio** | `algochains-mcp` CLI (default) | Cursor, Claude Desktop, Windsurf |
| **HTTP bridge** | `moltbook/http_bridge.py` port 8090 | algochains.ai platform, Command Center |
| **SSE** | `moltbook/sse_server.py` port 8765 | Streaming / event-driven clients |

**Claude.ai web/mobile connector rule:** never tell a user to paste `localhost`,
`127.0.0.1`, `0.0.0.0`, a phone LAN address, or a Tailscale-only `100.x` URL into a
Claude.ai custom connector. The local PyPI package is `stdio` and works for desktop
clients that can spawn a local process. Claude.ai web/mobile calls remote MCP servers
from Anthropic's infrastructure, so it needs a public `https://.../mcp` endpoint.
Use `algochains-mcp-http --host 127.0.0.1 --port 8080` behind a secure HTTPS tunnel for
testing, or a hosted HTTPS bridge for production.

Start the server:
```bash
algochains-mcp --mode demo        # no credentials needed, public tools only
algochains-mcp --mode paper       # Alpaca paper account
algochains-mcp --mode live        # full live broker connectivity
algochains-mcp --generate-config cursor   # write cursor MCP config and exit
```

---

## 2. Smart Mode vs Full Mode

Tool exposure is controlled by `ALGOCHAINS_TOOL_MODE` (default: `smart`).

| Mode | Tools visible | When to use |
|------|--------------|-------------|
| **smart** (default) | 168 curated tools | Everyday use; Cursor/Windsurf tool-count limits |
| **full** | 503 tools | When smart mode can't reach a needed tool |

**Always start in smart mode.** Use meta-tools to discover the rest:

```python
discover_tools("dark pool volume")      # semantic search across all 503 tools
get_tool_details("get_dark_pool_volume_v21")  # schema, params, tier
mcp_tool_manifest()                     # full JSON manifest with tiers and domains
execute_dynamic_tool("tool_name", {...})  # call any tool by name without switching modes
```

---

## 3. Safety Tier Model

Every tool has a danger tier enforced at dispatch. Agents must respect these.

| Tier | Label | Examples | Agent rule |
|------|-------|----------|-----------|
| **0** | `READ_ONLY` | `get_quote`, `detect_market_regime`, `get_bot_health`, `get_positions` | Call freely, no confirmation |
| **1** | `WRITE_LOCAL` | `validate_strategy`, `run_backtest`, `create_alert`, `submit_to_marketplace` | No confirmation needed |
| **2** | `ORDER_EXEC` | `place_order`, `close_position`, `export_config` | Require explicit user confirmation before calling |
| **3** | `DESTRUCTIVE` | `flatten_all_positions`, `cancel_all_orders` | `owner_token` header + explicit confirmation |

**Hard-coded safety limits (cannot be overridden by any agent):**
- Daily loss limit: $500 (hard stop, all orders blocked until manual reset)
- Max drawdown: 15% circuit breaker
- Human confirmation required for orders above $10K notional
- AI loop detection: 5 identical calls in 60 s → 30-minute order block
- VIX gate: all trades blocked when VIX > 35

---

## 4. Domain Map

Use this table to route a user request to the correct tool family.

| Domain | Entry tools (Tier-1 smart mode) | Notes |
|--------|--------------------------------|-------|
| **Market data** | `get_quote`, `get_macro_signals`, `get_funding_rate`, `get_vix_term_structure`, `get_earnings_catalyst` | No auth needed in demo mode |
| **Regime / ML** | `detect_market_regime`, `detect_regime_hmm`, `get_current_regime`, `run_regime_detection`, `compute_volatility_surface`, `compute_factor_exposure` | Uses Polygon + FRED |
| **Account / broker** | `get_account`, `get_positions`, `get_orders`, `connect_broker` | Read-only Tier 0–1; `place_order` is Tier 2 |
| **Backtesting** | `run_backtest`, `validate_strategy`, `validate_strategy_metrics`, `optimize_strategy`, `run_evolution_cycle` | Uses Databento / Massive S3 / Polygon data |
| **Live bots** | `get_bot_health`, `get_bot_heartbeat_openclaw`, `get_live_bot_metrics`, `get_all_bot_metrics`, `get_bot_dashboard`, `get_bot_position_state`, `get_bot_bracket_status` | AlgoChains live-bot telemetry |
| **Bot ops (safety)** | `check_unprotected_positions`, `get_bracket_guardian_status`, `get_ai_pipeline_health`, `get_circuit_breaker_status` | Always Tier 1; safety-critical reads |
| **Marketplace** | `get_marketplace_listings`, `submit_to_marketplace`, `run_marketplace_autopilot`, `run_mcpt_pipeline` | Strategy publishing and decay tracking |
| **Subscriber / paper** | `get_my_portfolio`, `get_signal_stream`, `get_my_pnl`, `get_my_fills`, `get_my_assignments`, `get_marketplace_listings`, `place_paper_order`, `cancel_paper_order`, `get_my_paper_positions` | Requires `sub_live_…` key from algochains.ai — free hosted virtual paper account; no broker credentials needed. All Tier 0 (read) except `place_paper_order` / `cancel_paper_order` (Tier 1 write-local). `check_propagation_health` and `test_signal_propagation` are owner-side pipeline tools, not subscriber tools. |
| **Billing & subscription** | `get_started`, `get_pricing`, `get_system_status`, `get_checkout_url`, `accept_subscriber_terms`, `get_my_usage`, `create_referral_code`, `get_my_referrals`, `get_referral_earnings`, `get_my_realized_pnl`, `create_creator_onboarding_link`, `get_my_creator_earnings`, `run_creator_payouts` | `get_started`/`get_pricing`/`get_system_status`/`get_checkout_url` are public (no auth). Subscriber tools require `sub_live_*` key. Creator/payout tools require `OWNER_API_TOKEN`. All Tier 0–1 except `run_creator_payouts` (Tier 2, owner-gated). |
| **Prediction markets** | `get_prediction_markets`, `search_prediction_markets`, `get_polymarket_high_volume`, `get_kalshi_settlements`, `get_prediction_market_bot_metrics` | Kalshi + Polymarket |
| **Graphiti (temporal KG)** | `graphiti_search`, `graphiti_health`, `graphiti_add_episode` | Advisory `agent_memory` only; never `broker_truth` |
| **Sentiment / Onyx** | `analyze_sentiment`, `onyx_ask`, `onyx_search`, `run_onyx_ingest` | FinBERT + RAG over codebase/docs |
| **Portfolio / risk** | `portfolio_summary`, `check_order_safety`, `get_protection_config`, `get_tradovate_risk_snapshot` | Snapshot and risk guardrail reads |
| **Intent engine** | `execute_intent`, `approve_intent`, `create_shadow_portfolio` | Natural-language trade intent pipeline |
| **Debate / Moltbook** | `invoke_moltbook_debate`, `get_quant_regime_state` | Multi-agent debate engine (shadow mode) |
| **Dark pool / flow** | `get_dark_pool_volume_v21`, `get_footprint_chart` | Institutional flow signals |
| **Config / BYOK** | `export_config` (Tier 2, masked), `list_api_keys` | Credentials always masked in response |
| **Meta** | `discover_tools`, `get_tool_details`, `execute_dynamic_tool`, `mcp_tool_manifest`, `get_system_heartbeat`, `get_api_usage` | Use first when unsure which domain to target |
| **Onboarding** | `start_onboarding`, `get_broker_setup_guide`, `validate_broker_connection`, `run_onboarding_smoke_test` | First-run setup flow |
| **Skills / Openclaw** | `list_skills`, `get_skill_detail`, `search_skills`, `get_skills_for_task`, `get_openclaw_memory`, `get_openclaw_state_summary` | AlgoChains skill registry |

---

## 5. Auth and Credentials

**All credentials come from `.env` (gitignored) or environment variables. No keys are
hard-coded in this repository.**

### Credential tiers

| Credential | Env var | Unlocks |
|------------|---------|---------|
| None | — | Demo / public mode: market data, regime, `discover_tools` |
| Alpaca paper | `ALPACA_API_KEY` + `ALPACA_SECRET_KEY` + `ALPACA_PAPER=true` | Paper trading, account info |
| Tradovate live | Tradovate OAuth via `TRADOVATE_*` env vars | Futures order execution |
| OANDA | `OANDA_*` env vars | Forex |
| Interactive Brokers | `IBKR_*` env vars | Multi-asset live |
| Subscriber key (production) | `X-Api-Key: sub_live_…` header — set `ALGOCHAINS_SUBSCRIBER_KEY` in env | 9 subscriber tools: signal stream, fills, P&L, paper orders, portfolio |
| Subscriber key (sandbox) | `X-Api-Key: sub_test_…` header — set `ALGOCHAINS_SUBSCRIBER_KEY` in env | Same scopes as `sub_live_`; hits dry-run portfolio only |
| Owner token | `OWNER_API_TOKEN` | Tier 2–3 tools (order exec, bot restart, emergency stop) |
| Bridge key | `ALGOCHAINS_BRIDGE_API_KEY` | Read-only team access to a bridge you operate |

### Stripe APP — Zero-Browser Developer Tier (free 14-day trial)

Developers can get a free `ac_live_*` key without touching a browser:

```bash
pip install stripe
stripe projects link algochains
```

Stripe calls `/app/provision` → generates `ac_live_*` key automatically → returned as credentials.
No web signup. No Stripe dashboard visit. 14-day trial, then $29/mo for paper tier.

The `ac_live_*` key unlocks 25 read-only tools: market data, regime detection, backtests,
Onyx search, marketplace listings. Set as `ALGOCHAINS_BRIDGE_KEY` env var.

### `owner_token` pattern

Tier 2 and Tier 3 tools require the owner token. **Always ask the user for explicit
confirmation before calling a Tier 2+ tool.** Pass the token in the tool's
`owner_token` parameter (not in headers — the server extracts it from the tool args
when called via stdio):

```python
# WRONG — never call without user confirmation
place_order(symbol="MNQ", side="buy", qty=1)

# CORRECT pattern
# 1. Show user the proposed order and ask for confirmation.
# 2. Only after explicit "yes", call:
place_order(symbol="MNQ", side="buy", qty=1, owner_token=<user-supplied>)
```

### Autonomous agent ceiling

Agents running autonomously (no human in the loop for the current action) must not
exceed **WRITE_LOCAL (Tier 1)**. Any Tier 2+ action requires the human to type
confirmation. This ceiling is enforced by the server; calling a Tier 2 tool without
a valid `owner_token` returns `policy_denied`.

---

## 6. Common Workflows

Use these patterns to route user requests without reading all of `server.py`.

### "What's my position / account balance?"
```python
get_account()          # balance, buying power, margin
get_positions()        # open positions with unrealized P&L
get_orders()           # working orders
```

### "Is the market trending right now?"
```python
detect_market_regime()          # current regime: trend / mean-revert / choppy / crisis
get_current_regime()            # cached regime state from signal_health
get_macro_signals()             # VIX, DXY, TNX, yield curve
```

### "Run a backtest on my MNQ strategy"
```python
validate_strategy(strategy_config={...})          # fast schema + logic check
run_backtest(strategy_config={...}, symbol="MNQ") # full backtest (uses Databento/Massive)
validate_strategy_metrics(backtest_id="...")      # Sharpe > 2.0, Win > 55%, MaxDD < 15%
```

### "How are my live bots doing?"
```python
get_bot_health()                   # all bots: alive/dead, last heartbeat
get_all_bot_metrics()              # Supabase live metrics for all bots
get_bot_position_state()           # current position and bracket status per bot
check_unprotected_positions()      # safety: any fills without brackets?
get_ai_pipeline_health()           # multi-agent validator timeout rate
```

### "Find a tool that does X"
```python
discover_tools("institutional flow data")       # semantic search
get_tool_details("get_dark_pool_volume_v21")    # full schema + tier
execute_dynamic_tool("get_dark_pool_volume_v21", {"symbol": "SPY"})  # call it
```

### "What's my paper P&L / signal stream?" (subscriber persona)

```python
# Subscriber key resolved from ALGOCHAINS_SUBSCRIBER_KEY env var (sub_live_… or sub_test_…).
# All tools below are Tier 0–1; no owner_token required.

get_my_portfolio()          # one-call snapshot: balance, assignments, open signals, 7d P&L
get_signal_stream()         # latest unread copy_trade_signals for your subscribed bots
get_my_pnl()                # today + 7-day P&L from subscriber_fills
get_my_fills(limit=50)      # paginated fill history; optional bot= filter
get_my_assignments()        # which bots you follow and their risk caps
get_marketplace_listings()  # browse approved bots available to subscribe to
```

Subscriber onramp workflow:
1. Call `get_checkout_url(email=..., tier="paper")` — user pays, `sub_live_…` key is emailed
2. Set `ALGOCHAINS_SUBSCRIBER_KEY=sub_live_…` in `.env` or shell
3. Call `accept_subscriber_terms(subscriber_id=..., acknowledgment="...")` — CFTC disclosure gate (required)
4. Call `join_bot(subscriber_id=..., bot="MNQ")` — subscribe to MNQ copy-trade signals
5. Call `get_my_assignments()` to confirm bot assignment
6. Call `get_signal_stream()` to see signals published by the live MNQ bot
7. Call `get_my_pnl()` for today's paper P&L
8. Call `get_my_portfolio()` for the full one-call snapshot

Self-directed paper trading (subscriber — Tier 1, confirm with user first):
```python
place_paper_order(symbol="MNQ", side="BUY", qty=1, order_type="market")
cancel_paper_order(order_id="<uuid>")
get_my_paper_positions()    # pending + recently filled self-directed orders
```

### "I'm a new user — what do I do?" (zero-auth discovery)

```python
get_started()                    # menu: subscriber / creator / developer / explore
get_started(goal="subscriber")   # step-by-step onboarding path for copy-trade
get_pricing()                    # tier pricing, referral %, creator revenue share
get_system_status()              # platform health, live bot list, tool count
```

These three tools always work — no API key, no signup. Good first call for any new user
or autonomous agent landing on the server for the first time.

### "How do I subscribe and join the MNQ bot?" (full programmatic funnel)

```python
# Step 1: Get a checkout URL (no auth needed — Stripe handles payment)
get_checkout_url(email="you@example.com", tier="paper")
# → checkout_url: visit once; sub_live_... key emailed automatically on success

# Step 2: Accept the CFTC risk disclosure (required before any signals)
# (use the sub_live_... key from the email as subscriber_id resolution)
accept_subscriber_terms(
    subscriber_id="<from_key>",
    acknowledgment="I have read and understand the risk disclosure above. I accept full responsibility for my trading decisions."
)

# Step 3: Subscribe to MNQ signals (signals published for you to review and act on)
join_bot(subscriber_id="<id>", bot="MNQ", size_multiplier=1.0)

# Step 4: Check status and start reading signals
get_subscriber_status(subscriber_id="<id>")
get_signal_stream()
get_my_portfolio()
```

**IMPORTANT:** Always call `accept_subscriber_terms` before `join_bot`. The server
enforces this gate — `join_bot` returns `error: consent_required` if the disclosure
hasn't been acknowledged.

### "How's my usage / billing?" (subscriber)

```python
get_my_usage(subscriber_id="<id>")
# → calls_this_month, included_quota (1000/mo paper), overage_calls, projected_overage_usd

get_my_realized_pnl(subscriber_id="<id>")
# → realized P&L for live-tier subscribers (paper returns paper-only data with 4.41(b) disclaimer)
```

### "I want to refer someone / check referral earnings"

```python
create_referral_code(subscriber_id="<id>")      # idempotent — returns existing code if already created
# → {"code": "AC-X7K2NP", "share_url": "https://algochains.ai?ref=AC-X7K2NP", ...}

get_my_referrals(subscriber_id="<id>")          # attribution count, active referrals, total commission
get_referral_earnings(subscriber_id="<id>")     # total earned, pending payout amount
```

### "Run creator payouts" (owner-only — requires OWNER_API_TOKEN)

```python
# Always dry_run=True first to preview
run_creator_payouts(dry_run=True, owner_token="<tok>")
# → list of creators, amounts, whether payout would execute

# Execute actual Stripe Connect transfers
run_creator_payouts(dry_run=False, min_payout_usd=25.0, owner_token="<tok>")
```

### "Get access without a browser" (developer Stripe APP flow)

```bash
# Terminal — zero browser interaction
stripe projects link algochains
# → Stripe provisions ac_live_* key and prints it
export ALGOCHAINS_BRIDGE_KEY=ac_live_...
algochains detect-market-regime
```

### "Verify the copy-trade pipeline is healthy" (owner/operator — NOT subscriber)

```python
# Requires OWNER_API_TOKEN. These are pipeline-health tools, not subscriber tools.
check_propagation_health()   # verify copy_trade fanout is live
test_signal_propagation()    # dry-run end-to-end signal fan-out
```

---

## Important Safety Rules for Agents

1. **Never call Tier 2+ tools without explicit user confirmation** — even if the user's
   phrasing sounds like an instruction (e.g., "buy 2 MNQ"). Confirm first.

2. **Never report `open_pnl_dollars` as realized P&L.** That field is unrealized. Use
   `check_trade_accuracy_v2.py` or broker `realizedPnL` for confirmed results.

3. **Graphiti (`graphiti_*`) is `agent_memory` authority only.** Never use Graphiti
   facts to make execution decisions or size positions. P&L and fills come from the
   broker REST API.

4. **CodeGraph (`codegraph_*`) is navigation-only.** Never import or call it from
   trading paths. It is an editor tool.

5. **`tradovate_token_guardian.py` is the only authorized token renewal path.** Never
   use `tradovate_token_auto_refresh.py`.

6. **Real data only.** Do not fabricate fill prices, paper P&L, or regime signals.
   If a real source is unavailable, fail closed and surface the missing dependency.

7. **Subscriber tools are scoped to the resolved `subscriber_id` only.** The server
   resolves identity from the API key server-side — never pass or trust a
   caller-supplied `subscriber_id`. If `supabase_unavailable` is returned, fail closed
   and surface the error rather than estimating P&L or returning stale data.

8. **`accept_subscriber_terms` is a hard gate before `join_bot`.** If `join_bot` returns
   `error: consent_required`, do NOT retry `join_bot` without first calling
   `accept_subscriber_terms` with the full acknowledgment phrase. Never fabricate the
   acknowledgment — surface the disclosure to the user and require them to confirm it.

9. **Billing tools (`get_checkout_url`, `run_creator_payouts`) are fail-closed.** If
   Stripe is unavailable, they return an error — never estimate a price or assume a
   payment succeeded. `run_creator_payouts` always requires `dry_run=True` preview before
   `dry_run=False` execution.

---

*Source files for this document: `src/algochains_mcp/server.py` (TIER1_TOOL_NAMES),
`src/algochains_mcp/tool_danger_tiers.py` (tier constants), `README.md` (install and
broker table), `MEGA_PROMPT_V22.md` (operator context).*

---

## Cursor Cloud specific instructions

Durable, non-obvious notes for running/developing this repo in the Cloud Agent VM.
Standard commands live in the `Makefile` and §1 above — prefer those; this section
only captures gotchas.

### Python environment
- Dependencies are installed into a project virtualenv at **`.venv`** (the VM lacks a
  usable global pip; `python3-venv` is a system package the snapshot already provides).
  The startup update script recreates/refreshes `.venv`.
- **Always invoke tools via `.venv/bin/...`** (e.g. `.venv/bin/algochains-mcp`,
  `.venv/bin/python`, `.venv/bin/pytest`, `.venv/bin/ruff`) or `source .venv/bin/activate`.
  The `Makefile` uses bare `python3`/`ruff`/`uvicorn`, so either activate the venv first
  or override, e.g. `make test PYTHON=.venv/bin/python`.
- The update script installs extras `dev,http,supabase,auth,quant,optimize,datasets`.
  `quant` (scipy/numpy/hmmlearn) is required just to *collect* `tests/numerai/`.

### Running the core product (stdio MCP server)
- The core product is the **stdio MCP server**, not a web app. Run with
  `ALGOCHAINS_DEMO_MODE=1 .venv/bin/algochains-mcp --mode demo` (demo mode needs no
  credentials). It speaks MCP over stdin/stdout and **exits immediately on stdin EOF** —
  so `... < /dev/null` looks like a clean exit; that is expected, not a crash.
- To exercise it end-to-end, drive it with an MCP client over stdio (see the `mcp`
  Python package: `stdio_client` + `ClientSession`) and call a Tier-0 tool such as
  `detect_market_regime` or the meta tool `discover_tools`.
- Tool exposure defaults to `ALGOCHAINS_TOOL_MODE=smart` (~150 tools). Set it to `full`
  for ~480. Brokers/market-data/Stripe/Supabase/Redis/Onyx are all optional and
  credential-gated; demo mode stubs execution-class tools.
- Optional HTTP transport: `.venv/bin/algochains-mcp-http --host 127.0.0.1 --port 8080`
  (`GET /health` → 200). This is distinct from the `http_bridge` module below.

### Known pre-existing failures (NOT environment problems)
These fail on a clean checkout regardless of setup — do not treat them as setup regressions:
- **Lint:** `make lint` (ruff) reports ~400 pre-existing findings, mostly `F401`
  unused-import in `tests/`. The ruff toolchain itself works.
- **Tests:** `make test` / `pytest` yields ~370 passed and ~165 failed. Causes:
  (a) response-shape drift — many tests assert dict keys like `"success"`/`"timeframe"`
  that the code no longer returns (`KeyError`); (b) the HTTP-bridge tests raise
  `IndexError: 4` because `src/algochains_mcp/http_bridge.py` resolves a path via
  `Path(__file__).resolve().parents[4]`, which assumes the package sits ≥4 dirs below
  root — at `/workspace` it is too shallow. Set `ALGOCHAINS_CONTROL_TOWER=<path>` to
  skip that branch when working on the bridge.
- **Full-suite vs per-file:** many async tests call the deprecated
  `asyncio.get_event_loop().run_until_complete(...)`. On Python 3.12, once a
  `@pytest.mark.asyncio` test unsets the loop, those raise
  `RuntimeError: There is no current event loop`. So the full-suite failure count is
  inflated vs running a single file; prefer per-file runs when validating a change.
