# Changelog

All notable changes to AlgoChains MCP Server are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [26.0.0] — 2026-04-08

### Added

#### Bot Ops Module (`live_bot_intelligence/bot_ops.py`)
- `get_bot_position_state(bot_id)` — Read persisted position state file (flat/qty/entry_price)
- `get_bot_bracket_status(bot_id)` — Detect bracket mode (live/oso_only/none/unknown) from log parse
- `get_ai_pipeline_health(bot_id)` — Detect Anthropic quota errors, Cerebras model errors, shadow mode
- `get_all_bot_ops_status()` — Full snapshot of process + position + bracket + pipeline health for all 4 bots
- `restart_trading_bot(bot_id, owner_token)` — Kill + restart (owner-token gated via OWNER_API_TOKEN)
- `flatten_bot_position(symbol, owner_token)` — Close all contracts via Tradovate MKT (owner-token gated)

#### Documentation
- `docs/GOTCHAS_AND_BUGS.md` — Comprehensive bug registry: P0/P1/P2 issues, operational gotchas, version history of fixes

#### Server Tool Registration
- All 6 new tools registered in `server.py` with proper ANNOT_READ_ONLY / ANNOT_DESTRUCTIVE annotations
- 4 read-only tools added to TIER1_TOOL_NAMES (always exposed in smart mode)

### Fixed (P0 — Critical Production Bugs)
- **qty=1 close bug**: `trading_safeguards.close_position_with_validation()` hardcoded `qty=1`. Now reads `position_size`/`qty` from tracked position dict.
- **Demo path bypass**: `TRADING_MODE=DEMO` bypassed safeguards. `if self.tradovate and not self.demo:` → `if self.tradovate:`. Both modes run identical protection stack.
- **Scale-in bracket orphan**: Scale-in bracket IDs (`scale_stop_order_id`, `scale_target_order_id`) now stored in `open_positions` and cancelled on `exit_position()`.
- **Pipeline 102s stall**: `concurrent.futures.ThreadPoolExecutor` 8s timeout on `pipeline.analyze()`. `PIPELINE_TIMEOUT_SECONDS` env-configurable. Timeout returns signal as-is (shadow mode).
- **Pipeline None return**: Advisory rejection now returns signal with `shadow_mode=True` instead of `None`. Ensemble never blocks a trade.
- **Cerebras llama3.3-70b**: Model removed from Cerebras API without notice. Updated to `llama3.1-8b` in `debate_layer.py` and `specialized_agents.py`.
- **Order mutex**: `core/order_mutex.py` SQLite-backed cross-process lock with 15s TTL prevents bot+MCP simultaneous orders on same contract/side.

### Changed
- Command Center section in README: cc.algochains.ai → cc.algochains.io, docs on Cloudflare Access, V22 feature list
- Marketplace subscribe buttons wired to `algochains.ai/marketplace/{symbol}?ref=cc` (were dead divs)
- Tools: 401 → 407

---

## [21.0.0] — 2026-04-06

### Added

#### MCP 2025-11-25 Spec Compliance (Phase 1)
- **Elicitation support** — server requests structured user confirmation before high-notional orders (>$10K), close-all-positions, and live strategy deployments. Fields rendered as typed JSON Schema forms.
- **Tasks (durable requests)** — long-running operations return a `task_id` immediately. Agents poll `get_task_status` for progress. State persists in `~/.algochains/tasks.db`. Applies to: `submit_to_marketplace`, `optimize_strategy` (n_trials > 500), `walk_forward_test`, `run_paper_trading_period`.
- **Resource subscriptions** — clients subscribe to resource URIs (`algochains://positions/{broker}`, `algochains://circuit_breaker/status`, `algochains://market/regime/{symbol}`, etc.) and receive push notifications on state changes.
- **Sampling with tools** — `sampling.tools` capability enabled for `execute_intent` and `detect_market_regime`.

#### Self-Improving Trading Loop — AlphaLoop Feature (Phase 2)
- **`evolution/trade_memory.py`** — episodic vector store of all completed trades. SQLite + optional chromadb. Records: symbol, side, entry/exit, P&L, regime, signals, lessons, tags.
- **`evolution/reward_model.py`** — RL reward function: `α·risk_adj_return + β·regime_alignment + γ·consistency - δ·drawdown_penalty`. Per-strategy reward scores over 30-trade rolling window. Auto-promotion above 0.65 threshold.
- **`evolution/evolution_daemon.py`** — background 4-stage cycle (SCAN→MUTATE→VALIDATE→PROMOTE). Opt-in via `ALGOCHAINS_EVOLUTION_MODE=enabled`. Uses real trade history for objective — no synthetic fitness.
- **`evolution/lessons_injector.py`** — injects top-5 regime-specific lessons into agent session context at session start.
- **New MCP tools**: `record_trade_outcome`, `query_trade_memory`, `get_trading_lessons`, `summarize_performance_by_regime`, `compute_strategy_reward`, `get_strategy_rankings`, `start_evolution_loop`, `get_evolution_status`, `list_evolved_strategies`, `rollback_evolution`, `inject_session_lessons`.

#### Order Flow & Institutional Data (Phase 3)
- **`order_flow/footprint.py`** — footprint chart engine (bid/ask volume per price level per bar). Detects absorption and imbalance clusters.
- **`order_flow/cumulative_delta.py`** — cumulative delta series with divergence detection (bullish, bearish, exhaustion).
- **`order_flow/volume_profile.py`** — Volume at Price (VAP) with POC, Value Area High/Low, HVN/LVN nodes.
- **`order_flow/dark_pool_volume.py`** — real dark pool volume from Polygon.io TRF conditions + FINRA ATS public reports. Zero synthetic estimates — fails closed if data unavailable.
- **`order_flow/earnings_catalyst.py`** — SEC EDGAR 8-K → FinBERT/VADER sentiment → directional signal pipeline. EPS data from Polygon.io financials.
- **`order_flow/prediction_markets.py`** — real Polymarket + Kalshi API connectors. Order placement via `py-clob-client`.
- **`order_flow/macro_signals.py`** — FRED API (yield curve, credit spreads), CBOE VIX public CSV, Polygon.io DXY — all real sources.
- **New MCP tools**: `get_footprint_chart`, `compute_cumulative_delta`, `get_volume_profile`, `get_dark_pool_volume`, `analyze_earnings_catalyst`, `get_prediction_market_signals`, `trade_prediction_market`, `get_macro_signals`.

#### Encrypted Key Vault & Agent Sub-Accounts (Phase 4)
- **`auth/key_vault.py`** — AES-256-GCM + scrypt (N=2^17) encrypted vault at `~/.algochains/vault.enc`. Raw keys NEVER returned to LLM. Runtime buffer caches decrypted keys for 5 min only.
- **`auth/agent_provisioner.py`** — per-agent broker sub-account provisioning. Alpaca: real Broker API sub-account creation. Tradovate: named entity. Paper: isolated tracking. Agent registry at `~/.algochains/agents.json`.
- **New MCP tools**: `vault_store_credential`, `vault_list_credentials`, `vault_rotate_credential`, `vault_delete_credential`, `register_agent`, `list_agents`, `deactivate_agent`, `check_agent_risk`.

#### Streaming & Real-Time Events (Phase 5)
- **`streaming/alert_engine.py`** — persistent price alert engine (SQLite). Conditions: `price_above`, `price_below`, `pct_change_15min`, `vwap_cross`, `volume_spike`. Driven by real Polygon.io REST polling.
- **`streaming/earnings_calendar.py`** — earnings subscription system. Pre-market alerts fired when event is ≤2h away. Real data from Polygon.io events + EDGAR.
- **New MCP tools**: `create_price_alert`, `list_price_alerts`, `cancel_price_alert`, `subscribe_earnings_alerts`, `get_earnings_calendar`, `get_pending_notifications`, `subscribe_resource`, `unsubscribe_resource`.

#### Developer Experience & Ecosystem (Phase 6)
- **`registry.json`** — MCP registry manifest at repo root. Enables organic discovery via `modelcontextprotocol/registry`.
- **`CHANGELOG.md`** — this file. Keep a Changelog format. Required for all future PRs.
- **`tests/test_tool_registration.py`** — CI-enforced tool count gate. Fails if registered tool count drifts from documented count.
- **`tests/conftest.py`** — shared pytest fixtures eliminating 60+ lines of boilerplate per test file.

#### Crypto Feature Parity — Binance Gap (Phase 7)
- **`social_trading/copy_engine.py`** — real copy trading engine. Polls leader positions from Alpaca/Tradovate and mirrors to follower accounts.
- **`defi_engine/staking.py`** — staking engine with real Lido, Cosmos validator, and Binance Simple Earn connectors.
- **`execution_engine/dca_engine.py`** — DCA (auto-invest) scheduling. Persistent SQLite schedules. Real broker execution via Alpaca.
- **`brokers/crypto_perps.py`** — perpetual futures via Binance, Bybit, Hyperliquid APIs. Funding rate optimization, open interest trends, liquidation cluster analysis.
- **New MCP tools**: `subscribe_copy_trade`, `get_copy_trade_status`, `unsubscribe_copy_trade`, `get_staking_opportunities`, `stake_assets`, `unstake_assets`, `get_staking_rewards`, `create_dca_schedule`, `list_dca_schedules`, `pause_dca_schedule`, `delete_dca_schedule`, `get_crypto_funding_rates`, `compute_funding_carry_trade`, `get_open_interest_trend`, `list_liquidation_clusters`.

#### SaaS Production Hardening (Phase 8)
- **`cloud_saas/billing_engine.py`** — real Stripe Connect integration replacing stub. Creator onboarding, subscription billing, 70/30 payout splits.
- **`multi_tenant/tenant_middleware.py`** — per-tenant rate limiting (Redis-backed or in-memory fallback). Immutable audit log with JSONL export for compliance.
- **Sandbox environments** — `create_tenant_sandbox`, `switch_tenant_context`, `destroy_tenant_sandbox`.
- **New MCP tools**: `create_stripe_connect_account`, `process_subscriber_payment`, `trigger_creator_payout`, `get_tenant_usage`, `export_audit_log`, `set_tenant_limits`, `create_tenant_sandbox`, `switch_tenant_context`, `destroy_tenant_sandbox`.

### Fixed
- All P0 unbound-name bugs from v20.0 audit (9 `NameError` instances in `_dispatch_tool`)
- `check_validation_status` returning stale `"pending_review"` — now returns `"not_applicable"` with explanation
- Version/tool count drift across `server.py`, `pyproject.toml`, `README.md`

### Changed
- Tool count: 227 (v20) → 350+ (v21)
- MCP spec compliance: 2025-06-18 → 2025-11-25

---

## [20.0.0] — 2026-03-15

### Added
- Account Protection Engine with 12 guards (daily loss, VIX circuit breaker, position size, drawdown, overnight, consecutive loss, correlation exposure, flash crash, fat finger, bracket integrity, cancel-on-disconnect, margin buffer)
- Builder SDK: DataWarehouseClient, StrategyRunner, SubmissionPipeline
- Memory safety: OOM prevention with 8GB limit monitor
- V20 BYOK (Bring Your Own Key) module

### Fixed
- Memory spike at startup: lazy-loading all V8-V19 modules reduced RSS from ~800MB to ~45MB

---

## [19.0.0] — 2026-02-20

### Added
- Alpha Engines: VWAP deviation, GEX (dealer gamma exposure), Kelly criterion sizing, unusual options flow, tape reading, dark pool detection (v1), cross-asset correlation
- V19 advanced execution: bracket orders, TWAP, VWAP execution

---

## [18.0.0] — 2026-01-15

### Added
- Intent Engine: natural language order parsing and routing
- Shadow portfolios: paper-mode monitoring of any live strategy
- Strategy evolution: parameter mutation via Optuna (integrated into Evolution Daemon in v21)

---

## [17.0.0] — 2025-12-01

### Added
- Compliance engine: surveillance, audit trail, MiFID II / US retail profiles
- Risk dashboard with real-time VaR and stress testing
- Multi-tenant white-label framework (cloud_saas module)

---

*All versions before 17.0 are pre-release and not documented here.*
