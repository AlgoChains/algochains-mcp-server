# AlgoChains MCP Server — Hidden Killers Audit
> Date: 2026-04-06 | Scope: v20.0 | Auditor: Cursor Agent
>
> This document catalogs every confirmed and suspected defect found during a fine-tooth-comb
> audit of the full repository. Issues are ranked P0 (runtime crash / data integrity) through
> P2 (quality/clarity). FIXED items have been patched in this commit. OPEN items require
> additional work.

---

## P0 — FIXED: Unbound Names in `_dispatch_tool` (NameErrors on First Call)

**File:** `src/algochains_mcp/server.py`  
**Root cause:** Lazy-loaded type symbols were declared only in `_LAZY_SPECS` (a string registry),
but were referenced as bare identifiers in handler branches — guaranteeing a `NameError` every
time any of these tools was invoked.

### Symbols fixed

| Symbol(s) | Handler(s) | Module |
|-----------|-----------|--------|
| `StreamTopic` | `stream_subscribe`, `stream_snapshot` | `.streaming.manager` |
| `BotMetrics`, `AllocationMethod` | `optimize_portfolio`, `compare_allocations` | `.portfolio.optimizer` |
| `Notification`, `NotificationEvent`, `NotificationChannel`, `NotificationPriority` | `send_notification`, `get_notification_history` | `.notifications.push` |
| `Interval` | `get_market_data` | `.data_providers.base` |
| `DatasetRequest` | `build_dataset` | `.datasets.builder` |
| `StrategySpec` | `create_strategy`, `validate_strategy`, `backtest_strategy`, `optimize_strategy`, `walk_forward_test`, `deploy_strategy` | `.strategy_builder.spec` |

**Fix applied:** Each handler now resolves its required type via `_lazy_import()` before use,
matching the existing pattern used throughout the rest of `_dispatch_tool`.

---

## P0 — FIXED: Version String Drift Across Four Sources

**Files:** `server.py` (docstring), `server.py` (SERVER_INSTRUCTIONS), `pyproject.toml`, `README.md`

| Location | Before | After |
|----------|--------|-------|
| `server.py` docstring | v18.0, 242 tools, 12 domains | v20.0, 227 tools, 14 domains |
| `SERVER_INSTRUCTIONS` | v19, 262 tools, ~25 Tier-1 | v20, 227 tools, 38 Tier-1 |
| `pyproject.toml` description | 275+ tools | 227 tools |
| README | 275+ tools, ~29 Tier-1 | 227 tools, 38 Tier-1 (see README rewrite) |

**Actual registered tools:** `grep -c "Tool(name=" server.py` → **227**  
**Actual Tier-1 tools:** `len(TIER1_TOOL_NAMES)` → **38**

Stale counts misled AI agents reading SERVER_INSTRUCTIONS, causing them to believe more tools
existed than were discoverable, and to expect the wrong smart-mode behavior.

---

## P0 — FIXED: `check_validation_status` Always Returns `pending_review`

**File:** `src/algochains_mcp/server.py` (~line 3123)  
**Before:**
```python
return _text({
    "submission_id": arguments["submission_id"],
    "status": "pending_review",
    "note": "Validation results are returned immediately from submit_strategy.",
})
```

This was a dangerous placeholder: any AI agent that called `submit_strategy` then polled
`check_validation_status` would receive an eternally-`pending_review` response and loop forever
or conclude the strategy was stuck.

**Fix applied:** Returns `status: "not_applicable"` with a clear explanation that validation
is synchronous and results are in the `submit_strategy` response.

---

## P0 — ✅ RESOLVED (2026-06-14): `test_live_audit.py` Hardcoded API Keys

**File:** `tests/test_live_audit.py` (now in `tests/live/`)
**Original finding:** Hardcoded live credentials in test file.

**Resolution verified (2026-06-14):**
- `tests/live/` directory now reads all credentials exclusively from environment
  variables via `os.environ.get(...)`.
- Live tests are gated by `PYTEST_LIVE=1` env var; they never run in standard CI.
- No live keys found in any tracked file as of audit sweep on this date.
- `scripts/secret_scan.py` now runs in Gate 9 of the MCP regression gate CI to
  catch future regressions.

*Original stale finding kept for audit history.*

---

## P1 — OPEN: `deploy_strategy` Is Not Real Broker Deployment

**File:** `src/algochains_mcp/strategy_builder/deployer.py`  
**Behavior:** Creates an in-memory Python dict with `status: "deployed"` — no broker API call,
no paper trading account connection, no live order routing.

**Impact:** An AI agent calling `deploy_strategy(mode="paper")` receives `{"status": "deployed"}`
but nothing actually connects to Alpaca/Tradovate paper accounts.

**Required actions:**
1. Implement real paper deployment: connect the StrategySpec's signal loop to the broker's
   paper/sandbox endpoint via BrokerRegistry.
2. OR rename to `register_strategy_locally` and update tool description to accurately say
   "registers for local tracking only."
3. Update `tool_manifest.py` — currently `deploy_strategy` is not in the stub list but its
   implementation is effectively a stub.

---

## P1 — OPEN: `optimize_strategy` Missing `optuna` in Core Dependencies

**File:** `pyproject.toml`  
**Before:** `optuna` was absent from all dependency groups.  
**Fix applied:** Added `optimize = ["optuna>=3.0.0"]` optional extra.

**Remaining gap:** `pip install algochains-mcp-server` (core install) will succeed but
`optimize_strategy` will fail at runtime with `ModuleNotFoundError: No module named 'optuna'`.

**Required actions:**
1. Add `optuna` install hint to `optimize_strategy` tool description (e.g. "Requires
   `pip install 'algochains-mcp-server[optimize]'`").
2. Add graceful import guard in `strategy_builder/optimizer.py`:
   ```python
   try:
       import optuna
   except ImportError:
       raise RuntimeError("optimize_strategy requires: pip install 'algochains-mcp-server[optimize]'")
   ```

---

## P1 — OPEN: `http` and `backtrader` Extras Not Installed by `pip install algochains-mcp-server`

**File:** `pyproject.toml`  
`fastapi`, `uvicorn`, and `backtrader` are under optional extras. Default installs silently
succeed but the `algochains-mcp-http` entrypoint and Backtrader-based strategy templates will
crash.

**Required actions:**
1. Add import guards + clear `RuntimeError` messages in `http_transport.py` and
   `builder_sdk/strategy_runner.py`.
2. README already documents `pip install "algochains-mcp-server[all,data-all,datasets,backtrader]"`.
   Add `optimize` to that command.

---

## P1 — OPEN: Duplicate Guard in README Account Protection Table

**File:** `README.md` lines 170 and 173  
"Max Positions" guard appears twice in the table (both rows identical). There are actually
**12 unique guards** (VIX killswitch + daily loss + drawdown + buying power + margin +
time restriction + consecutive loss + fat finger + position size + max positions +
concentration + correlation).

**Fix applied:** Corrected in README rewrite (see below).

---

## P2 — OPEN: `deploy_strategy` Listed in TIER1_TOOL_NAMES Despite Being Unimplemented

**File:** `server.py` line ~2787  
`TIER1_TOOL_NAMES` includes `"deploy_strategy"` — the most prominent tool in smart mode.
Every AI agent's first session will try to use it, receive a convincing-looking success
response, but no deployment will occur.

**Required actions:** Either implement it (P1 above) or move it to Tier-2 until real
broker deployment is wired.

---

## P2 — OPEN: Duplicate `RegimeDetector` Class in Two Packages

**Files:**
- `src/algochains_mcp/intent_engine/regime_detector.py`
- `src/algochains_mcp/realtime_analytics/regime_detector.py`

Two separate `RegimeDetector` implementations exist under different packages. The `_LAZY_SPECS`
entry for `"regime_detector"` points to `realtime_analytics`, while `"intent_regime"` points to
`intent_engine`. The descriptions and method signatures may diverge silently over time.

**Required actions:**
1. Audit both implementations — consolidate common logic into a shared base class in
   `src/algochains_mcp/shared/regime_detector.py`.
2. Have both package-specific versions subclass the shared base.

---

## P2 — OPEN: Two Tools Named `validate_strategy` with Different Semantics

**File:** `server.py`  
Tool definitions:
- `"validate_strategy"` (line ~1420): Runs the 7-gate marketplace validation via `StrategyValidator`
  (marketplace/validator.py).
- `"validate_strategy"` (line ~1831): Validates a `StrategySpec` for schema/parameter correctness
  via `StrategySpecValidator` (strategy_builder/spec.py).

These are two completely different operations with the same tool name. Depending on which handler
branch executes first in `_dispatch_tool`, one silently shadows the other.

**Required actions:**
1. Rename marketplace validation to `"validate_strategy_metrics"` or `"gate_check_strategy"`.
2. Keep `"validate_strategy"` for the schema/spec validator since that is the more intuitive meaning.
3. Update all Tool definitions, TIER1_TOOL_NAMES, tool_manifest.py, and README.

---

## P2 — OPEN: `tool_manifest.py` Uses "stub" Label Incorrectly

**File:** `src/algochains_mcp/tool_manifest.py` lines 43–44  
`optimize_strategy` and `deploy_strategy` are labeled `stub`. However:
- `optimize_strategy` calls a real `StrategyOptimizer` backed by `optuna` — it is not a stub,
  it is `partial` (depends on optional dep).
- `deploy_strategy` writes to memory only — it is effectively a stub.

**Required actions:** Update manifest labels:
- `optimize_strategy` → `partial` (with dependency note)
- `deploy_strategy` → `stub` (until real broker deployment exists)

---

## P2 — OPEN: `tests/test_live_audit.py` Is in Default `pytest tests/` Discovery Path

**File:** `pytest.ini` / `pyproject.toml` (no `testpaths` configuration)  
Running `pytest tests/` discovers and attempts to run live API tests that require real credentials
and make real network calls. Default CI will either fail (no credentials) or incur API costs.

**Required actions:**
1. Add `testpaths = ["tests/unit"]` to `[tool.pytest.ini_options]` in `pyproject.toml`.
2. Move live tests to `tests/live/` with a clear `conftest.py` that skips all unless
   `ALGOCHAINS_RUN_LIVE_TESTS=1` is set.

---

## P2 — OPEN: README Tool Count in Architecture Diagram Still Says 275+

**File:** `README.md` architecture ASCII diagram (~line 488)  
The server box says "275+ tools". Updated in the rewrite to say "227 tools".

---

## Naming Schema Improvements (Non-Bug)

These are style/readability enhancements aligned with best-in-class MCP repos
(modelcontextprotocol/servers, Zapier MCP, Stripe MCP):

| Current | Recommended | Reason |
|---------|-------------|--------|
| `_dispatch_tool` (~4500 lines) | Split into domain handler modules | Single function is unmaintainable |
| `_LAZY_SPECS` | `_LAZY_MODULE_SPECS` | More descriptive |
| `_get_stream_manager()` | `_stream_manager()` | Remove redundant `get_` prefix |
| `_get_spec_validator()` | `_spec_validator()` | Consistency |
| `execute_dynamic_tool` | `call_tool` or `invoke_tool` | "dynamic" is vague |
| `massive_*` tool prefix | `data_*` | "massive" is a vendor name, not a semantic descriptor |
| Tool descriptions mixing "NEW" tags | Remove "NEW" — it's relative | Breaks semantic search |

---

## Follow-up Hidden Killers Audit — 2026-06-13 MCP Hardening Pass

### P0/P1 — FIXED: Sensitive Tool Outputs and Direct Handler Bypasses

**Files:** `src/algochains_mcp/server.py`, `src/algochains_mcp/tool_danger_tiers.py`,
`src/algochains_mcp/brokers/oauth_manager.py`, `src/algochains_mcp/onboarding.py`,
`src/algochains_mcp/support_tickets.py`

**Issues closed:**
- `get_broker_oauth_status` no longer returns plaintext `access_token`; owner-token is required
  and responses use masked token metadata.
- `generate_ide_config` returns a redacted config unless the caller provides the owner token.
- `test_signal_propagation` requires owner token plus `confirm=true` before sending live paper
  signal propagation requests.
- Support ticket admin reads/updates now require owner token through the direct stdio handler,
  not only through `execute_dynamic_tool`.
- Dynamic dispatch tier coverage was extended so sensitive tools are ORDER_EXEC-gated even when
  called indirectly.

**Verification:** `tests/test_sec_2026_c5_c8_handlers.py` and
`tests/test_dynamic_dispatch_safety.py` cover both direct handler and dynamic-dispatch gates.

### P1 — FIXED: Subscriber Strategy Delivery SSRF and Config Leakage

**File:** `src/algochains_mcp/marketplace/supabase_tools.py`

**Issues closed:**
- `deliver_strategy_to_subscriber` now verifies an active subscription before loading or
  delivering a strategy config.
- Caller-supplied and subscription-record webhook URLs are screened for loopback/private/link-local
  targets before POST delivery.
- The signed strategy token is no longer returned in the MCP tool response; callers receive a
  delivery receipt only.

### P1 — FIXED: Subscriber Assignment Shape Drift

**Files:** `src/algochains_mcp/marketplace/supabase_tools.py`,
`src/algochains_mcp/subscriber_tools.py`, `src/algochains_mcp/marketplace/bridge.py`

**Issues closed:**
- Subscriber bot reads now use `subscriber_bot_assignments` fields (`bot`, `mode`, `paused`,
  sizing caps) instead of stale Django subscription columns.
- Paper marketplace subscription requests no longer require a broker field when the mode is
  `paper`.
- Subscriber P&L responses include explicit paper aliases and UTC boundary metadata so downstream
  agents do not mislabel cumulative paper values as broker-realized P&L.

### Still Needs Operator Attention

- **Remote integration:** Local `main` is behind `origin/main` by 96 commits. Do not force-push.
  Merge via the pushed feature branch / PR after rebasing or recreating the branch from current
  remote `main`.
- **Local test environment:** `pytest-asyncio` is declared in the repo's `dev` extra, but the active
  Homebrew Python environment does not have it installed and is PEP-668 externally managed.
  Non-async hardening tests pass; full `tests/test_bridge.py` async coverage needs a project venv
  or CI environment with `pip install -e '.[dev]'`.
- **Value preserved:** Changes are fail-closed and permission-tightening only; no live trading
  thresholds, sizing, stop/target logic, or broker execution paths were changed.

---

## Architecture Improvements (Future Sprint)

### 1. Split `_dispatch_tool` into Domain Handler Modules

The current `_dispatch_tool` is ~4,500 lines. Best-in-class MCP servers (Stripe, Linear,
GitHub) register handlers as a dict of `name → async callable`. Recommended refactor:

```
src/algochains_mcp/handlers/
├── __init__.py         # register_handlers() → dict
├── trading.py          # place_order, cancel_order, close_position, ...
├── portfolio.py        # optimize_portfolio, compare_allocations, ...
├── strategy.py         # create_strategy, validate_strategy, backtest, ...
├── marketplace.py      # submit_to_marketplace, validate_strategy_metrics, ...
├── streaming.py        # stream_subscribe, stream_snapshot, ...
├── notifications.py    # send_notification, configure_notifications, ...
├── data_providers.py   # get_market_data, get_realtime_quote, ...
└── ...
```

Benefit: Each domain file can have its own lazy imports at module top, eliminating the
inline `_lazy_import()` workaround applied in this fix.

### 2. Add `conftest.py` with Shared Fixtures

Current tests repeat broker mock setup. A shared `conftest.py` with `@pytest.fixture`
for mock broker, mock data provider, and mock notifier would cut test boilerplate by ~60%.

### 3. Add a Real Tool Count CI Test

```python
# tests/test_tool_registration.py
def test_tool_count_matches_documentation():
    """Fail if tool count drifts from documented value."""
    from algochains_mcp.server import TOOLS_ANNOTATED
    assert len(TOOLS_ANNOTATED) == 227, (
        f"Tool count mismatch: {len(TOOLS_ANNOTATED)} registered, 227 documented. "
        "Update server.py docstring, SERVER_INSTRUCTIONS, pyproject.toml, and README.md."
    )
```

### 4. MCP Registry Compliance (`registry.json`)

The [modelcontextprotocol/registry](https://github.com/modelcontextprotocol/registry) requires
a machine-readable `registry.json` at the repo root. Add:

```json
{
  "name": "algochains-mcp-server",
  "description": "Universal AI-to-broker trading protocol",
  "version": "20.0.0",
  "license": "MIT",
  "transport": ["stdio", "http"],
  "categories": ["finance", "trading", "data"],
  "homepage": "https://algochains.ai",
  "repository": "https://github.com/AlgoChains/algochains-mcp-server"
}
```

---

## Fix Summary

| Priority | Count | Status |
|----------|-------|--------|
| P0 | 3 | All FIXED |
| P1 | 4 | `optuna` extra added; 3 OPEN |
| P2 | 7 | All OPEN (tracked above) |

All P0 fixes are in `src/algochains_mcp/server.py` and `pyproject.toml`.
All P1/P2 items are tracked in this document for the next sprint.
