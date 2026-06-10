# AlgoChains — Confirmed Gotchas, Bugs, and Incidents

> **Maintained:** Active. New entries added after every incident.  
> **Format:** STATUS | Severity | Date Found | File | Description | Fix  
> **Statuses:** ✅ FIXED | ⚠ OPEN | 📋 KNOWN-ACCEPTABLE

---

## Critical (P0) — Would/Did Cause Real Money Loss

### ✅ FIXED | P0 | 2026-04-08 | `trading_safeguards.py`
**Bug:** `close_position_with_validation()` hardcoded `qty=1` regardless of actual position size.  
**Impact:** A 4-contract Sell position only closed 1 contract. 3 contracts remained open and unprotected on the exchange. netPos drifted to -7 over multiple incidents.  
**Fix:** Now reads `position.get("position_size") or position.get("qty")` from tracked position. Falls back to 1 only if key is missing.  
**Detection:** Compare `logs/{symbol}_position_state.json` qty to Tradovate `get_positions()` netPos.

---

### ✅ FIXED | P0 | 2026-04-08 | `FUTURES_SCALPER_UPGRADED.py`
**Bug:** Demo trading path (`if self.tradovate and not self.demo:`) bypassed the full safeguards stack.  
**Impact:** When `TRADING_MODE=DEMO`, no coordinator check, no fill tracking, no bracket placement, no position rebase. Orders placed but positions potentially unprotected.  
**Fix:** Gate changed to `if self.tradovate:`. Both demo and live now run identical protection.  
**Note:** `TRADING_MODE` controls bot logic. `TRADOVATE_ENVIRONMENT` controls API URL. These are INDEPENDENT. Do not conflate.

---

### ✅ FIXED | P0 | 2026-04-08 | `FUTURES_SCALPER_UPGRADED.py` (scale-in)
**Bug:** Scale-in bracket IDs not stored separately. Parent position exit cancelled only main brackets; scale-in brackets orphaned on exchange.  
**Impact:** If main stop fires, 1 extra contract remains open with no stop or target.  
**Fix:** `scale_stop_order_id` and `scale_target_order_id` now stored in `open_positions`. `exit_position()` cancels all 4 bracket IDs.

---

### ✅ FIXED | P0 | 2026-04-08 | `tradovate_client.py` + all order paths
**Bug:** `account_id` is `None` until `get_accounts()` is called. Direct `place_order()` without prior account init returns `"Access is denied"` (HTTP 200 but `failureReason: UnknownReason`).  
**Impact:** Orders silently fail.  
**Fix:** Always call `client.get_accounts()` before `place_order()`. The `flatten_position_tradovate()` in `bot_ops.py` enforces this.  
**Gotcha:** The error message is `UnknownReason` not `unauthorized` — very misleading.

---

### ✅ FIXED | P0 | 2026-04-08 | `multi_agent/bot_adapter.py`
**Bug:** `pipeline.analyze()` had no timeout. When Anthropic credits were exhausted (zero balance), all 7 agents failed, and the pipeline hung for ~102 seconds per scan iteration.  
**Impact:** Each 5-minute scan took 102+ extra seconds. Bot effectively paused between market opportunities.  
**Fix:** `concurrent.futures.ThreadPoolExecutor` with `PIPELINE_TIMEOUT_SECONDS=8` (env-configurable). Timeout returns signal as-is with `shadow_mode=True`.

---

### ✅ FIXED | P0 | 2026-04-08 | `multi_agent/bot_adapter.py`
**Bug:** Pipeline returning `None` on advisory rejection (even when all agents agreed to reject). Bot had advisory-bypass fallback but this was fragile — a code path that could break silently.  
**Fix:** Pipeline now always returns the signal on rejection with `shadow_mode=True`. `None` is never returned. Bot never blocks on advisory ensemble result.

---

### ✅ FIXED | P0 | 2026-04-07 | `multi_agent/debate_layer.py`, `specialized_agents.py`
**Bug:** Cerebras `llama-3.3-70b` model removed from Cerebras API. All agents using it returned HTTP 404.  
**Impact:** All 7 AI agents in the debate ensemble failed. The primary confidence gate continued working (backtested — advisory not required for trade execution), but ensemble voting was blind.  
**Fix:** Model updated to `llama3.1-8b` (verified available). Fallback chain updated.  
**Detection:** `grep "404\|model not found" logs/futures_bot_live.log`

---

## High (P1) — Correctness / Reliability Issues

### ⚠ OPEN | P1 | 2026-04-08 | `core/trading_agents_orchestrator.py` (signal logger)
**Bug:** `StrategyLogger.log_signal_generated()` called with unexpected keyword argument `thresholds_checked`. Raises `TypeError` on every signal — logged to log file but caught.  
**Impact:** Signal logging partially broken. Non-blocking (caught in try/except). No trade impact.  
**Fix needed:** Find call site passing `thresholds_checked=` and either add the parameter to the method signature or remove from call.

---

### ✅ FIXED | P1 | 2026-06-09 | `tests/test_live_audit.py`
**Bug:** Lines 12–17 contained hardcoded API keys: `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `POLYGON_API_KEY`, `MASSIVE_API_KEY`, `FINNHUB_KEY`.  
**Fix:** File is now env-only (`os.environ.get`) and ships its own regression test
(`test_no_hardcoded_secrets_in_test_file()` scans for bad patterns). The live variant at
`tests/live/test_live_audit.py` is env-only too and excluded from default pytest runs
(`norecursedirs` in `pyproject.toml`). `scripts/secret_scan.py` guards CI.
A full git-history audit was run 2026-06-09 ahead of making the repo public.

---

### 📋 KNOWN-ACCEPTABLE | P1 | Ongoing | Exit quantity fallback
**Situation:** `exit_position` fallback (when `tradovate_order` key not in position) uses `position_qty` from `get_positions()` API. If API is unavailable, falls back to `1`.  
**Acceptable because:** API outages are rare and monitored. Bot has circuit breakers.  
**Watch for:** If ever `netPos` on Tradovate doesn't match `position_state.json`, check for this fallback path being hit.

---

## Medium (P2) — Quality / Developer Experience

### ✅ FIXED | P2 | 2026-04-06 | `server.py` (version drift)
**Bug:** Version string in docstring, SERVER_INSTRUCTIONS, pyproject.toml, and README all showed different values.  
**Impact:** AI agents reading SERVER_INSTRUCTIONS believed wrong tool count / version.  
**Fix:** All synchronized to current version with single-source mechanism.

---

### 📋 KNOWN-ACCEPTABLE | P2 | 2026-04-08 | `cc.algochains.io` returns 403 from `curl`
**Situation:** Cloudflare Access (Zero Trust) requires browser authentication. `curl` and API health checks without cookies return 403.  
**This is INTENTIONAL security.** The tunnel is working; authenticate in the browser with your authorized SSO account.  
**Do NOT remove Cloudflare Access** — this prevents unauthorized access to the command center.

---

### 📋 KNOWN-ACCEPTABLE | P2 | 2026-04-08 | Desktop tower SSH on port 2222
**Situation:** Compute node (configured via `ALGOCHAINS_TOWER_HOST`) pingable but WSL SSH fails.  
**Recovery:** Start WSL2 on Windows, ensure `sshd` is running in WSL, or use Windows SSH on port 22.  
**rsync workaround:** Push to GitHub from Mac; pull on desktop.

---

## Operational Gotchas (Not Bugs, But Will Burn You)

### TRADING_MODE vs TRADOVATE_ENVIRONMENT — Two Independent Flags
```
TRADING_MODE=LIVE    → bot uses full safeguards stack (coordinator, fill tracking, brackets)
TRADING_MODE=DEMO    → SAME full stack (fixed 2026-04-08 — was bypassing before)

TRADOVATE_ENVIRONMENT=demo  → connects to demo.tradovateapi.com (fake P&L)
TRADOVATE_ENVIRONMENT=live  → connects to live.tradovateapi.com (REAL MONEY)
```
**To go live:**
1. Set `TRADOVATE_ENVIRONMENT=live` in `.env`
2. Verify `TRADING_MODE=LIVE` (already set — do NOT change)
3. Verify `MAX_DAILY_LOSS`, position sizing phase, VIX circuit breaker
4. Restart bot and monitor first 3 fills manually

**NEVER** assume `TRADING_MODE=LIVE` means live API. They are separate.

---

### Tradovate account_id Must Be Initialized
```python
# WRONG — will silently return "Access is denied"
client = TradovateClient(cid, secret, env)
client.place_order(...)

# CORRECT — must init account_id first
client = TradovateClient(cid, secret, env)
client.get_accounts()   # populates self.account_id
client.place_order(...)
```
The error returned is `{"failureReason": "UnknownReason", "failureText": "Access is denied"}` — it looks like a permissions error but is actually a missing account_id.

---

### Anthropic Credits — Silent Degradation
When Anthropic API credits hit zero:
- All AI agents silently fail with `insufficient_quota` or `529 overloaded`
- The debate ensemble logs errors but the bot continues (advisory only)
- **BUT:** Before pipeline timeout fix (2026-04-08), this caused 102s stalls per scan
- **Fix:** Pipeline now times out at 8s and returns signal as-is
- **Action:** Monitor `logs/futures_bot_live.log` for `insufficient_quota`. Top up at console.anthropic.com

---

### Cerebras Model Deprecations
- `llama3.3-70b` was removed from Cerebras API without notice (discovered 2026-04-07)
- **Current model:** `llama3.1-8b` (verified available as of 2026-04-08)
- **Check available models:** `curl https://api.cerebras.ai/v1/models -H "Authorization: Bearer $CEREBRAS_API_KEY"`
- Models can be deprecated without notice — add to monitoring

---

### Position State Drift (Bot Thinks Flat, Exchange Has Open Contracts)
This happens when:
1. Bot closes wrong qty (e.g., qty=1 bug, now fixed)
2. Bot crashes mid-close
3. Token expires during close attempt
4. WebSocket disconnects at fill confirmation

**Detection:**
```python
# Bot's internal view
cat logs/mnq_position_state.json

# Tradovate's actual view
python3 -c "
from tradovate_client import TradovateClient
import os
from dotenv import load_dotenv
load_dotenv()
c = TradovateClient(os.getenv('TRADOVATE_CID'), os.getenv('TRADOVATE_SECRET'), os.getenv('TRADOVATE_ENV','demo'))
c.get_accounts()
print(c.get_positions())
"
```
**Resolution:** Use `flatten_bot_position` MCP tool or manually flatten on Tradovate UI, then restart bot.

---

### Cloudflare Tunnel Must Be Running for the Command Center
The tunnel process must be started after every host restart:
```bash
# Tunnel name/ID lives in your local ~/.cloudflared/config.yml (never commit it)
cloudflared tunnel run "$CF_TUNNEL_NAME" >> logs/cloudflared_cc.log 2>&1 &
```
Add to launchd (macOS) or your shell profile for persistence.

**onyx.algochains.io** requires the desktop tower to be running (onyx-tower tunnel). Desktop must be online and cloudflared running in Windows.

---

### MCP Server Tool Count Drift
The tool count in README badges, `SERVER_INSTRUCTIONS`, `pyproject.toml`, and the server docstring must be manually kept in sync. After adding tools, run:
```bash
grep -c "Tool(name=" src/algochains_mcp/server.py
```
and update all four locations.

---

### Order Mutex Database Location
`core/order_mutex.py` uses `/tmp/algochains_order_mutex.db`. This is wiped on Mac restart.  
The mutex auto-creates on first use — no initialization needed.  
TTL is 15 seconds. Stale locks are ignored.  
If the bot crashes mid-order, the lock expires naturally within 15s.

---

## Version History of Major Bug Fixes

| Version | Date | Fix |
|---------|------|-----|
| V26.0 | 2026-04-08 | Bot ops module: bracket status, position state, pipeline health, restart, flatten |
| V26.0 | 2026-04-08 | Pipeline timeout (8s), shadow mode, advisory-always-returns-signal |
| V26.0 | 2026-04-08 | qty=1 close bug fixed in trading_safeguards.py |
| V26.0 | 2026-04-08 | Demo path unified with live path (same safeguards) |
| V26.0 | 2026-04-08 | Scale-in bracket IDs tracked and cancelled on exit |
| V26.0 | 2026-04-08 | Order mutex (cross-process deconfliction) |
| V26.0 | 2026-04-07 | Cerebras llama3.3-70b → llama3.1-8b |
| V25.0 | 2026-04-06 | AlgoClaw v1.0 + Roo trade propagation |
| V24.0 | 2026-03-xx | Prop fund pipeline, credential vault, Rithmic connector |
| V20.0 | 2026-04-06 | NameError fixes, version string sync, check_validation_status fix |
