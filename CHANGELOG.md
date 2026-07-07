# Changelog

All notable changes to AlgoChains MCP Server are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Fixed — subscriber key env alias (2026-07-07)

- Accept BOTH `ALGOCHAINS_SUBSCRIBER_KEY` (canonical) and `ALGOCHAINS_SUB_KEY` (back-compat alias) in the Python server + TS CLI. Fixes real drift: the Python server read `ALGOCHAINS_SUBSCRIBER_KEY` while the CLI told users to set `ALGOCHAINS_SUB_KEY`, so a subscriber could auth to one component but not the other.

### Fixed / Documented — subscriber onboarding parity (2026-07-06)

- README: corrected "9 tools" → **16 subscriber tools** (matched `SUBSCRIBER_TOOLS`).
- Documented the server-side **Broker Hub** (`algochains.ai/account/brokers/`) — no-daemon way to connect a real broker (Tradovate/Alpaca); virtual paper still needs no broker.
- Documented the live MNQ `strategy_name = "MNQ Upgraded Scalper"` → `bot = "MNQ"` fanout mapping.
- Added the `algochains-mcp-server` (signals) vs `algochains-library-mcp` (backtesting) shared-`algochains`-alias collision note.

---

## [22.7.0] — 2026-06-29

### Changed — Documentation parity and tool count sync

- Synced all public-facing tool counts to match `server.py` registration:
  full mode **525** (was 503), smart mode **188** (was 168).
- Updated badges, prose, and table in `README.md`.
- Updated `AGENTS.md` smart/full counts and discover-tools examples.
- Updated `SERVER_INSTRUCTIONS` in `server.py`: "~525 tools across 21 domains"
  (was ~481 / 20 domains — reflected the pre-physical-world count).
- Synced `registry.json` version from 22.4.1 → 22.7.0 and updated description count.
- Updated `pyproject.toml` description to include physical-world event intelligence.

### Added — Physical-world event intelligence (from June 14 merge)

- `get_physical_event_sources` — enumerate real-world event feeds mapped to market signals.
- `map_physical_event_assets` — resolve physical event categories to affected asset universes.
- `score_physical_event_alpha` — score a physical event's alpha potential across asset classes.
- `get_sonia_air_heartbeat` — advisory connectivity check for Sonia Air node.
- All four tools are `agent_memory` authority (advisory-only), never `broker_truth`.

---

## [22.6.0] — 2026-06-13

### Added — Revenue platform (WS1–WS6), legal defense & onboarding

#### Onboarding meta-tools (zero-auth "wow" tools)
- `get_started(goal?)` — guided next-step map for brand-new users: subscriber /
  creator / developer / explore personas; no auth, no signup required.
- `get_pricing()` — transparent tier pricing ($29/$99/mo), referral %, creator
  80% revenue share — single source of truth (`onboarding_meta.py`).
- `get_system_status()` — platform health, live bot roster, tool count; no auth,
  best-effort, never raises.

#### Billing & subscription funnel (12 new MCP tools)
- `get_checkout_url` — Stripe-hosted checkout URL; no auth needed; subscriber API key
  emailed automatically after payment.
- `accept_subscriber_terms` — CFTC risk-disclosure consent gate (required before
  `join_bot`); consent persisted and audit-trailed.
- `get_my_usage` — calls this month, included quota, projected overage cost.
- `create_referral_code` / `get_my_referrals` / `get_referral_earnings` —
  full referral program (20% commission, 3 months, first-touch, self-referral blocked).
- `create_creator_onboarding_link` / `get_my_creator_earnings` —
  Stripe Connect Express KYC + earnings dashboard for strategy creators.
- `run_creator_payouts` — owner-gated, dry-run default, idempotency-keyed batch
  payout run via Stripe Connect transfers.
- `get_my_realized_pnl` — live/paper-segregated P&L; all outputs carry CFTC 4.41(b)
  hypothetical disclaimer.

### Added — Revenue platform (WS1–WS6) & legal defense

- **Legal defense memo** (`docs/LEGAL_COMPLIANCE_AUDIT.md`) — researched CFTC/NFA
  precedent (Lowe v. SEC, Taucher v. Born, CFTC v. Vartuli, Reg. 4.14(a)(9),
  4.41(b)). Anchors the "impersonal, subscriber-initiated signals" defense and
  flags auto-execution as the principal liability. Not legal advice.
- **CFTC Reg. 4.41(b)** hypothetical-performance disclaimer on all paper/simulated
  outputs; general past-performance disclaimer everywhere else.
- **Creator payouts (WS1)** — `connect_payouts.py` over the existing Stripe Connect
  engine + `creator_earnings`/`creator_payouts` ledger; tools
  `create_creator_onboarding_link`, `get_my_creator_earnings`,
  `run_creator_payouts` (owner-gated, dry-run default, idempotency-keyed).
- **Usage metering (WS2)** — `usage_metering.py` (Stripe Meters v2 model, fail-open)
  + `get_my_usage`. Write-side middleware wiring is a documented phased step.
- **Referrals (WS3)** — `referrals.py` (first-touch, self-referral block, 20%/3mo);
  `create_referral_code`, `get_my_referrals`, `get_referral_earnings`. Attribution
  is recorded from the Stripe webhook when `get_checkout_url(referral_code=…)` was
  used (best-effort, fail-open).
- **Realized P&L + HWM (WS4)** — `realized_pnl.py`: `get_my_realized_pnl`
  (live/paper segregated), owner-gated `reconcile_creator_pnl`, and
  `compute_hwm_performance_fee` (high-water-mark; **performance fees DISABLED by
  default** for CTA-registration reasons — enable only after counsel).
- **OAuth 2.1 resource server (WS5)** — `auth/oauth_resource.py` JWT validation
  (JWKS + aud/iss/exp/scope, sub→identity, app_metadata.tenant_id→tenant); RFC 9728
  metadata + `WWW-Authenticate` discovery in `http_transport.py`. AS delegated to
  an external IdP.
- **Multi-tenant (WS6)** — `tenants`, `current_tenant_id()` RLS helper, null-safe
  permissive policy templates, request-lifecycle tenant context
  (`multi_tenant/isolation.py`). Phased per-table RLS rollout documented.
- **Tests** — `tests/test_revenue_compliance.py` (15 hermetic tests: disclaimers,
  HWM math, OAuth fail-closed, tenant context, fail-open metering, tool-registration
  invariants incl. money tools never in smart mode).

### Added — Compliance, Discovery & CI

#### Compliance (CFTC/NFA posture)
- `compliance/disclosures.py` — canonical, versioned risk disclosure, ToS, and
  past-performance disclaimer (single source of truth; reuses the broker-onboarding
  futures risk disclosure text).
- `accept_subscriber_terms` MCP tool — records a subscriber's explicit futures
  risk-disclosure + ToS acknowledgment; persisted and audit-trailed.
- `join_bot` now **fails closed** with `consent_required` (returns the disclosure
  text) until the subscriber has acknowledged the current risk-disclosure version.
- Provision-time auto-MNQ assignment now starts **paused** — no copy-trade before
  explicit risk acknowledgment. ToS consent is stamped from the Stripe checkout
  click-through; the futures risk disclosure must be explicitly accepted.
- Past-performance / not-advice disclaimer attached to all performance-bearing
  subscriber outputs (`get_my_pnl`, `get_my_portfolio`, `get_subscriber_status`,
  `get_marketplace_listings`).
- Migration `20260525_subscriber_consent.sql` — consent columns on
  `subscriber_api_keys`, append-only `subscriber_consent_log`, and the
  `record_subscriber_consent()` SECURITY DEFINER RPC.

#### Build plans (future revenue levers)
- `docs/MANAGED_CLOUD_PROVISIONING_BUILD_PLAN.md` — Pulumi Automation API per-tenant
  IaC (resale + BYOC), AWS ExternalId / GCP WIF / Azure Lighthouse federation,
  6 new MCP tools, P0→P2 phased delivery.
- `docs/GPU_COMPUTE_RENTAL_BUILD_PLAN.md` — RTX 5080 GPU rental marketplace:
  gVisor+nvproxy sandboxing, Tailscale/Headscale federation, Stripe Connect operator
  payouts (70/30), 10 new MCP tools, 3-phase delivery grounded in `dispatch_tower_job`.

#### Docs / domain table
- README: new "Billing & Subscription Funnel" section; domain table now 21 domains;
  smart-mode count corrected to 168.
- AGENTS.md: billing domain row; full billing workflow patterns (discovery → checkout
  → consent → join_bot → signals); `accept_subscriber_terms` gate documented as agent
  safety rule; tool counts corrected (168 smart, 503 full).

#### Discovery
- `smithery.yaml` + `server.json` — registry manifests for Smithery and the
  official MCP Registry (registry.modelcontextprotocol.io).

#### CI/CD
- `.github/workflows/test.yml` — pytest matrix (3.11/3.12), hermetic.
- `.github/workflows/lint.yml` — ruff error-level gate + advisory full check.
- `.github/workflows/migrations.yml` — replays all Supabase migrations on a fresh DB.
- `.github/workflows/security.yml` — CodeQL + gitleaks secret scanning.

#### Payment-path safety
- `create_platform_checkout_session` validates the Stripe Price (recurring USD)
  and logs CRITICAL if `RESEND_API_KEY` is absent before taking a payment.

---

## [22.4.0] — 2026-04-06

### Added

#### UX & Team Onboarding
- `scripts/quickstart.py` — interactive setup wizard and health-check path for demo/paper/live setup.
- `SAFETY_MODEL.md` — plain-language safety guide for guardrails, confirmations, circuit breakers, and team access.
- `get_onboarding_status` and `generate_ide_config` MCP tools — expose setup progress and generate IDE MCP config for Cursor, Windsurf, Claude, or VS Code.

#### Desktop Tower and Bot Health Visibility
- `get_tower_health` and `get_tower_job_status` are part of the smart tool set so operators can inspect desktop tower reachability and dispatched job status without full-mode exposure.
- `get_bot_health` includes `e2e_sentinel`, `desktop_inference_slo`, and `decision_latency_slo` slices for MNQ signal -> order -> bracket -> fill traceability.

### Changed

- `tool_danger_tiers.py` is the documented machine-readable danger classification layer for the 478-tool full surface and bridge `/tools` metadata.
- README setup and docs navigation were reorganized around demo/paper/live setup paths and operational safety references.

---

## [22.3.0] — 2026-04-06

### Added

#### Proprietary Data Ingestion
- `ingest_csv_data` — validates real OHLCV CSV files, normalizes symbol/timeframe path components, and writes clean rows under `state/custom_data/`.
- `ingest_json_signals` — ingests pre-computed entry/exit signals, ML features, labels, or regime tags from JSON.
- `connect_onyx_docs` — indexes local research documents into Onyx for `onyx_ask()` and `onyx_search()`.
- `register_strategy` — validates and registers custom strategy specs for later backtesting.
- `list_ingested_data` — audits imported custom datasets, signal files, Onyx documents, and registered strategies.

### Security / Hardening

- Data ingestion validates file existence, expected suffixes, required columns/types, symbol/timeframe safety, and destination jail boundaries. It does not synthesize missing data.

---

## [22.2.0] — 2026-04-21

### Added

#### Kalshi Prediction Markets Pipeline
- `order_flow/kalshi_ai_ensemble.py` (404 lines) — AI ensemble for Kalshi event probability scoring; combines FinBERT, market microstructure, and macro signals into a single directional edge estimate
- `order_flow/kalshi_pipeline.py` (361 lines) — Full pipeline: event discovery → AI scoring → position sizing → order execution (paper + live) with Kelly fraction gate
- `order_flow/kalshi_slack_notifier.py` — Pushes Kalshi signals + fills to #openclaw Slack channel

#### Subscriber Auth + Tools
- `src/algochains_mcp/subscriber_auth.py` — Subscriber key resolution via Supabase SECURITY DEFINER RPC; 60-second cache; key plaintext never leaves process
- `src/algochains_mcp/subscriber_tools.py` — 9 subscriber-scoped tools: `get_my_portfolio`, `get_signal_stream`, `get_my_pnl`, `get_my_fills`, `get_my_assignments`, `get_marketplace_listings`, `place_paper_order`, `cancel_paper_order`, `get_my_paper_positions`; all scoped to the resolved `subscriber_id` — no cross-subscriber data access possible

#### Unified Path Resolution
- `src/algochains_mcp/paths.py` (120 lines) — `default_control_tower()` resolver: honours `ALGOCHAINS_CONTROL_TOWER` env first, then `ALGOCHAINS_CONTROL_TOWER_PATH`, then Mac/WSL/desktop legacy paths. Eliminates layout-specific `parents[N]` chains that broke on desktop tower WSL2.

#### Data Backends (control-tower research pipeline)
- `research/ssrn_3904097/data_backends.py` — Priority chain: **Databento** (XNAS.ITCH, confirmed accessible) → **Massive S3** (`us_stocks_sip/day_aggs_v1/`, back to 2003) → yfinance fallback. Used by SSRN smart-beta + dollar-bar replication pipeline.

#### Test Coverage
- `tests/test_bot_health_signal_slice.py` — Unit tests for `get_bot_health` signal_health slice contract (params, risk_bootstrap, bot_version, trading_mode)

### Changed

- **`get_bot_health`**: Added `ml_env_flags` slice (MASSIVE_NEWS_FEATURES, MASSIVE_HALT_GUARD, ENABLE_INTRINIO_MNQ, train/serve skew note) and `cc_health` (Command Center last-seen, WS status, Databento live feed age)
- **`verify_model_artifact.py`**: Now checks `.pkl.sha256` sidecar (blocking on mismatch), reports XGBoost JSON companion and manifest presence
- **Tradovate parity**: Full endpoint mapping → `docs/TRADOVATE_PARITY.md` (vs community `mcp-tradovate`)
- **`FUTURES_SCALPER_UPGRADED.py`**: SHA-256 startup check (raises RuntimeError on tampered pkl), `drawdown_start_ts` Triple Penance tracking, `ws_health.NO_CLIENT` explanatory note

### Fixed

- `dashboard/live_dashboard.py`: path resolution now uses `_default_control_tower()` on all render paths (was hardcoded Mac path)
- `live_bot_intelligence/heartbeat.py`: stale-signal alert threshold now configurable (default 60 min); CL false-positive suppressed when volume-filtered scanning is active
- `academic_registry.py`: updated with SSRN 3904097 replication entry (Harke, Shishlenin, Koppisetti 2021)

### Security / Hardening

- Model artifact SHA-256 sidecar (`futures_model_latest.pkl.sha256`) now generated post-retrain by CI workflow
- XGBoost companion JSON (`futures_model_latest.json`) exported via `booster.save_model()` — non-executable safe-load path
- `model_manifest.json` stores sha256 of both formats + OOS metrics for cross-reference

---

## [22.1.0] — 2026-04-20

### Docs / metadata (Plan v3 alignment)

- **README version parity**: Version badge moved from stale `26.0` to `22.1.0`, matching both this CHANGELOG entry and `SERVER_INSTRUCTIONS` in `server.py`. `pyproject.toml` bumped from `22.0.0` to `22.1.0` and the project description now names Massive.com and the control-tower validation framework so CLI metadata matches the README.
- **README tool count**: Tools badge updated from `407` to `468`, reflecting the current registered `TOOLS_ANNOTATED` size. Architecture diagram now reads `460+ tools` instead of the stale `350+`.
- **New section: Quality & Trust** — documents (a) bot-path vs agent-path latency tiers with a link to `LATENCY_GUIDE.md`, (b) the control-tower validation framework (how `retrain_mnq_v2.py` and `publish_backtest_run.py` write `validation_submissions` rows with `data_fingerprint`, `engine_version`, `seed`, and the live `feature_snapshot_hash`), and (c) deprecations (Intrinio on the MNQ path → Massive.com, default-approve FinGPT sentiment → fail-closed without news context).

### Changed

- **`get_bot_health`**: Response now includes a `signal_health` key. For each bot (or the filtered bot), this slice exposes `params`, `risk_bootstrap`, `bot_version`, and `trading_mode` from `state/signal_health.json`. If the file is absent or unparseable the field is `{"error": "..."}` and the rest of the response remains intact.

- **`get_kronos_shadow_stats`**: Fixed broken control-tower path resolution. Previously used `Path(__file__).resolve().parent.parent.parent.parent.parent / "algochains-control-tower"` which was layout-specific and returned the wrong directory on any non-default install. Now resolves via `_default_control_tower()`, which honours `ALGOCHAINS_CONTROL_TOWER` env, `ALGOCHAINS_CONTROL_TOWER_PATH`, the shared legacy path list, and the Mac hardcoded fallback — consistent with all other tools.

- **Server version**: `v22.0` → `v22.1` in `SERVER_INSTRUCTIONS`.

### Fixed (risk metric definitions)

- `state/signal_health.json` `MNQ_Upgraded_Scalper` now has a structured `risk_bootstrap` object with unambiguous field names and `_definitions` explaining what P5 total P&L vs P95 max drawdown mean, their correct use in capital planning, and the relationship to the `max_daily_loss` hard cap. Previously these were documented only in the prose trust report.

### Path resolution audit

Audited all 7 files in `src/` that reference `algochains-control-tower`. Findings:

- `order_flow/kalshi_pipeline.py`: `parents[4]` (deeper nesting) → `/Users/treycsa/CascadeProjects` ✅ correct
- `telos.py`: 4× `.parent` → `/Users/treycsa/CascadeProjects` ✅ correct
- `brokers/prop_fund_autopilot.py`: `os.getenv("ALGOCHAINS_CONTROL_TOWER", hardcoded)` ✅ env-var-aware
- `dashboard/live_dashboard.py`: passed as `extra_candidates` to `_default_control_tower()` ✅
- `order_flow/kalshi_slack_notifier.py`: comment only ✅
- `server.py` (other tools): already use `_default_control_tower()` ✅

Only `get_kronos_shadow_stats` (5× `.parent` = wrong `/Users/treycsa/algochains-control-tower`) was broken. Fixed in this release.

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
