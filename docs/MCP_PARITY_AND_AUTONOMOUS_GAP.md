# MCP parity with control-tower — Massive standard + autonomous ops gap analysis

**Scope:** [`algochains-mcp-server`](../) read-only and health surfaces vs [`algochains-control-tower`](https://github.com/algochains/algochains-control-tower) (bots, watchdogs, ML pipeline). **Data vendor stance:** standardize on **Massive.com** (Polygon white-label) for options/news/PCR-style metrics. **Out of scope:** Unusual Whales — do not add UW-specific tools, env keys, or documentation paths to the MCP server; legacy UW code in other repos is not part of this parity track.

**Related:** [`MCP_SERVER_ENHANCEMENT_ROADMAP.md`](MCP_SERVER_ENHANCEMENT_ROADMAP.md) (broader P0–P4 backlog), [`TRADOVATE_PARITY.md`](TRADOVATE_PARITY.md).

---

## 1. Implementation checklist (highest leverage first)

These items align agents and OpenClaw-style automation with what the Mac/tower stack actually runs after the Massive PCR + news + CC watchdog work.

| ID | Item | Details | Primary files |
|----|------|---------|----------------|
| P1 | **`get_bot_health`: ML / Massive feature flags** | Expose non-secret env mirrors: `MASSIVE_NEWS_FEATURES`, `MASSIVE_PCR_FEATURES` (and optionally `MASSIVE_HALT_GUARD` if used). Values come from **`os.environ` in the MCP process** — document that parity with live bots requires the same env as trading, or a future `state/ml_ops_flags.json` written by the bot. | `server.py` (`get_bot_health` handler) |
| P1 | **`get_bot_health`: Command Center watchdog state** | Read `state/cc_health_state.json` from `ALGOCHAINS_CONTROL_TOWER` (same resolver as today). Surfaces CC log age, API probe, restarts, circuit-breaker snapshot if present — matches Slack/OpenClaw “CC degraded” without a second hop. | `server.py`, path via `paths.default_control_tower()` |
| P2 | **Clarify `get_feature_importance` vs MNQ artifact** | Today `get_feature_importance` is the **v10 ML engine** (`ml_engine/feature_engine.py`) for feature-set IDs — **not** the MNQ `futures_model_latest.pkl` path. Update tool `description` in `server.py` and, if applicable, `middleware.py` / `discover_tools` hints. Point to control-tower CLI: `scripts/feature_importance_report.py` for LightGBM/XGBoost gain on the promoted pickle. | `server.py`, `tests/test_v10_v16_modules.py` (doc-only) |
| P2 | **`SERVER_INSTRUCTIONS` version blurb** | Bump e.g. v22.1 → **v22.2** (or next): Massive `/options/v1/pcr` train/serve parity, `core/massive_pcr_features.py`, `MASSIVE_PCR_FEATURES`, reference `docs/BACKTEST_FEATURE_TRACE.md` on the control-tower repo for placeholders/skews. | `server.py` `SERVER_INSTRUCTIONS` |
| P3 | **Optional: `verify_mnq_model_artifact` read-only tool** | Spawn or document path to `python3 scripts/verify_model_artifact.py` under `ALGOCHAINS_CONTROL_TOWER`. Avoid duplicating pickle logic inside MCP; keep thin wrapper. | `server.py`, `tool_danger_tiers.py` |

**Explicit non-goals**

- No new **Unusual Whales** tools, manifests, or keys.
- No requirement to embed Massive API secrets in tool outputs (flags are 0/1 only; PCR values stay in bot or Massive tools).

---

## 2. Gap analysis — what is still missing for “autonomous genius”

The MCP server already exposes a very large surface (533 tools in full mode,
181 tools in smart mode): brokers, marketplace, validation, Onyx, Massive query,
backtests, `get_bot_health` + `signal_health`, `dispatch_tower_job`, etc. Gaps
below are **what an autonomous agent still cannot see or do in one coherent
pass** without extra scripting or multiple repos.

### 2.1 Observability and single-pane health

| Gap | Why it matters | Direction |
|-----|----------------|-----------|
| **No merged “incident” object** | Control tower writes `logs/incidents/incident_*.json`; CC watchdog writes `cc_health_state.json`; bridge has `guardrails_state.json`. Agents must know three paths. | One read-only tool or an extension of `get_bot_health` returning `{ cc_health, bridge_guardrails?, last_incident_summary? }` with size caps. |
| **Copy-trade / paper executor** | OpenClaw alerts mention `paper_trade_executor` — not clearly exposed as MCP health. | Optional: parse bridge HTTP health or a small state file if the bridge writes it. |
| **Supabase migration / schema drift** | MCP marketplace tools assume DB; agents do not get “subscriber copy-trade / subscriber_api_keys migration not applied” style signals. | Read-only `supabase_schema_probe` or document bridge endpoint only (avoid raw service_role in MCP). |

### 2.2 ML ops and promotion discipline

| Gap | Why it matters | Direction |
|-----|----------------|-----------|
| **MNQ pkl vs v10 feature engine** | Two different “importance” stories (see checklist). | Descriptions + optional wrapper for `verify_model_artifact.py` / `feature_importance_report.py`. |
| **Retrain / WFV job status** | Heavy jobs run on tower via SSH/`nohup`; MCP does not show queue or last exit code. | Extend `dispatch_tower_job` result persistence or add `get_tower_job_status` reading a well-known JSON in control-tower `results/`. |
| **Promotion gates** | `verify_promotion_gate_parity.py` is CLI-only. | Thin read-only tool or link in `server_diagnostics` output. |

### 2.3 Massive-first data plane

| Gap | Why it matters | Direction |
|-----|----------------|-----------|
| **PCR as first-class narrative** | Massive `/options/v1/pcr` is partnership-documented in control-tower docs, not in MCP instructions. | Mention in `SERVER_INSTRUCTIONS` + optional tiny probe tool **or** reuse `massive_call_api` with documented path (no UW). |
| **News vs geopol keyword scoring** | Geopol is derived from Massive news, not a separate product. | Document in MCP help text to avoid agents searching for a “geopol endpoint.” |

### 2.4 Autonomy enablers (cross-cutting)

| Gap | Why it matters | Direction |
|-----|----------------|-----------|
| **Tool discovery at scale** | Full mode has hundreds of tools; agents lose track. | Strengthen `discover_tools` filters by domain: `health`, `massive`, `tower`, `marketplace`. |
| **Lazy-import diagnostics** | Roadmap already notes lazy modules failing silently. | Ensure `server_diagnostics` lists failed lazy imports (see enhancement roadmap §P2 #11). |
| **Correlation: bot log errors vs signal_health** | `get_bot_health` gives error counts and `signal_health` separately; no automated “this error pattern matches stale token.” | Heuristic optional — lower priority than cc_health merge. |

### 2.5 Security and autonomy tradeoffs

Autonomous agents should not gain **new** ways to read secrets. All additions above should be **read-only**, **non-secret**, and consistent with [`tool_danger_tiers.py`](../src/algochains_mcp/tool_danger_tiers.py).

---

## 3. Suggested priority order (this doc only)

1. Implement §1 checklist items **P1–P2** (flags, cc_health, descriptions, SERVER_INSTRUCTIONS).
2. Pick **one** item from §2.1 (merged health or copy-trade health) for the next sprint.
3. Defer **P3** wrapper tools until agents repeatedly miss promotion checks.

---

## 4. Revision history

| Date | Change |
|------|--------|
| 2026-04-21 | Initial doc: Massive parity checklist, UW explicitly out of scope, autonomous gap analysis. |
