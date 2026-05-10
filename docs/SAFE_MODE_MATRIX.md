# AlgoChains MCP Safe-Mode Environment Matrix
**Date:** 2026-05-10  
**Author:** Hidden Killer Audit v2 (Arch Risk: stdio full-mode parity fix AUDIT-2)

This document describes every combination of `ALGOCHAINS_TOOL_MODE`, `ALGOCHAINS_REQUIRE_CONFIRMATION`, and `OWNER_API_TOKEN` and their resulting security behavior. Use it to configure hosts correctly and avoid accidental trading gate bypass.

---

## Environment Variables

| Variable | Values | Default | Description |
|----------|--------|---------|-------------|
| `ALGOCHAINS_TOOL_MODE` | `smart` \| `full` | `smart` | `smart` exposes ~54 Tier-1 tools. `full` exposes all 338 tools. Dev/debug only. |
| `ALGOCHAINS_REQUIRE_CONFIRMATION` | `0` \| `1` | `1` | `1` blocks ORDER_EXEC+ tools when no elicitation client is available. `0` allows pass-through for automated/headless callers. |
| `OWNER_API_TOKEN` | any string | unset | Secret required for ORDER_EXEC+ tools. If unset, all ORDER_EXEC tools fail-closed regardless of other settings. |

---

## Combination Matrix

### Transport: `stdio` (Cursor / local agent)

| `TOOL_MODE` | `REQUIRE_CONFIRMATION` | `OWNER_API_TOKEN` | ORDER_EXEC Reachable? | Notes |
|-------------|----------------------|-------------------|----------------------|-------|
| `smart` | `1` | set | ❌ Blocked | Smart mode hides ORDER_EXEC from direct dispatch. Must use `execute_dynamic_tool` + `owner_token + confirm=true`. |
| `smart` | `0` | set | ❌ Blocked | Still blocked — ORDER_EXEC tools not in `TOOLS_TIER1`, can't be directly dispatched. |
| `smart` | `1` | unset | ❌ Blocked | No owner token → all ORDER_EXEC fail-closed in `execute_dynamic_tool`. |
| `smart` | `0` | unset | ❌ Blocked | Same as above. |
| `full` | `1` | set + correct | ✅ Allowed | Full mode + valid `owner_token` in arguments + `REQUIRE_CONFIRMATION=0`. Intended for automated CI/backfill. **Set on dev machines only.** |
| `full` | `1` | set | ❌ Blocked | Even in full mode: `REQUIRE_CONFIRMATION=1` blocks ORDER_EXEC via `evaluate_stdio_direct_tool`. |
| `full` | `0` | set + correct | ✅ Allowed | Requires caller to pass correct `owner_token` argument with each call. ORDER_EXEC enforced at dispatch. |
| `full` | `0` | unset | ❌ Blocked | `OWNER_API_TOKEN` unset → fail-closed in `evaluate_stdio_direct_tool`. |
| `full` | `0` | set + wrong | ❌ Blocked | Wrong `owner_token` → fail-closed. |

> **Key insight (AUDIT-2 fix):** Before the fix, `full + REQUIRE_CONFIRMATION=0` was an architectural backdoor — ORDER_EXEC tools were reachable without `owner_token`. The fix added ORDER_EXEC gate to `evaluate_stdio_direct_tool`, so both transports enforce the same policy.

---

### Transport: `http_bridge` (external API callers)

| `BRIDGE_API_KEY` | `OWNER_API_TOKEN` | ORDER_EXEC Reachable? | Notes |
|-----------------|-------------------|-----------------------|-------|
| set + correct | set + correct | ✅ Allowed | Full auth path. `confirm=true` also required in envelope. |
| set + correct | set + wrong | ❌ Blocked | Bridge auth passes but ORDER_EXEC gate fails. |
| set + correct | unset | ❌ Blocked | OWNER_API_TOKEN unset → fail-closed for ORDER_EXEC. |
| unset | any | ❌ Blocked | Bridge itself rejects request (401). Never reaches dispatch. |
| `ALGOCHAINS_BRIDGE_DEV_MODE=true` | set + correct | ✅ Allowed | Dev mode bypasses API key check but ORDER_EXEC gate still enforces owner_token. **Never set in production.** |
| `ALGOCHAINS_BRIDGE_DEV_MODE=true` | unset | ❌ Blocked | Dev mode + no token → ORDER_EXEC still fail-closed. |

---

### Transport: `dynamic` (execute_dynamic_tool — all transports)

| `OWNER_API_TOKEN` | `confirm` arg | ORDER_EXEC Reachable? | Notes |
|-------------------|--------------|-----------------------|-------|
| set + correct | `true` | ✅ Allowed | Standard automated path. |
| set + correct | `false` | ❌ Blocked | `confirm=false` fails if `REQUIRE_CONFIRMATION=1`. |
| set + correct | `false` | ✅ Allowed (only if `REQUIRE_CONFIRMATION=0`) | Headless path. |
| set + wrong | `true` | ❌ Blocked | Token mismatch → `missing_token=True`. |
| unset | `true` | ❌ Blocked | No token → hard fail-closed. |

---

## Production vs Development Configuration

### Production Bots (Mac M5 treycsa — live trading)

```bash
ALGOCHAINS_TOOL_MODE=smart          # Never expose all 338 tools on prod
ALGOCHAINS_REQUIRE_CONFIRMATION=1   # Block ORDER_EXEC without elicitation
OWNER_API_TOKEN=<secret>            # Required. Set in .env, not env.local.
ALGOCHAINS_BRIDGE_DEV_MODE=         # Unset (or false)
```

### Development / Backtest Machine

```bash
ALGOCHAINS_TOOL_MODE=smart          # Still prefer smart unless debugging tier issues
ALGOCHAINS_REQUIRE_CONFIRMATION=0   # Allow headless automated tool calls
OWNER_API_TOKEN=<secret>            # Still required for ORDER_EXEC tools
ALGOCHAINS_BRIDGE_DEV_MODE=false    # Keep false even in dev
```

### CI (MCP contract tests only — no live broker)

```bash
ALGOCHAINS_TOOL_MODE=smart
ALGOCHAINS_REQUIRE_CONFIRMATION=0
OWNER_API_TOKEN=test-owner-token-ci-only   # Not a real Tradovate token
# No ALGOCHAINS_BRIDGE_API_KEY — bridge tests mock the bridge layer
```

### Debug Session (tier troubleshooting ONLY)

```bash
ALGOCHAINS_TOOL_MODE=full           # ⚠️ Dev-only. One-time startup WARNING emitted.
ALGOCHAINS_REQUIRE_CONFIRMATION=0   # ⚠️ Required to use ORDER_EXEC in full mode without elicitation
OWNER_API_TOKEN=<secret>            # ⚠️ Must still be passed per-call for ORDER_EXEC tools
# Revert to smart after debugging session.
```

---

## What the Startup Warning Looks Like

When `ALGOCHAINS_TOOL_MODE=full` is set, the MCP server logs this once per process:

```
WARNING  algochains_mcp.server: ALGOCHAINS_TOOL_MODE=full — DEVELOPMENT MODE ACTIVE.
  All 338 tools are exposed for direct stdio call.
  ORDER_EXEC+ tools still require owner_token + ALGOCHAINS_REQUIRE_CONFIRMATION=0.
  Do NOT run live production bots with ALGOCHAINS_TOOL_MODE=full.
  Set ALGOCHAINS_TOOL_MODE=smart for production (default).
```

If you see this in production logs, immediately check your environment configuration.

---

## What `evaluate_stdio_direct_tool` Now Enforces

After the AUDIT-2 fix, `evaluate_stdio_direct_tool` in `tool_policy.py` enforces:

1. In `smart` mode: tool must be in `tier1_names` or it is blocked with a message to use `execute_dynamic_tool`.
2. In `full` mode: any `ORDER_EXEC+` tool (tier ≥ 2) requires:
   - `OWNER_API_TOKEN` set in environment
   - Caller's `owner_token` argument must match `OWNER_API_TOKEN`
   - If `REQUIRE_CONFIRMATION=True`, the call is blocked regardless of token

This means `full + REQUIRE_CONFIRMATION=0 + no owner_token` still fails closed — `OWNER_API_TOKEN` must be set.

---

## Guard Rails That Apply Regardless of Transport/Mode

These safety gates are applied **after** tool dispatch reaches the handler, regardless of what transport or mode is configured:

| Gate | Where | Effect |
|------|-------|--------|
| VIX extreme veto (`VIX ≥ 35`) | `guardrail.py` | Blocks all orders |
| Daily loss limit | `guardrail.py` | Blocks orders beyond `MAX_DAILY_LOSS` |
| Drawdown circuit breaker | `trading_guardrails.py` | Trips all brokers when drawdown exceeds threshold |
| Consecutive loss streak | `server.py` | Blocks when `consecutive_losses ≥ MAX_CONSECUTIVE_LOSSES` |
| Phase 3 fail-closed gates | `FUTURES_SCALPER_UPGRADED.py:9353` | Phase 3 VIX/daily-loss/CB gates, any exception returns None |
| Bracket integrity validator | `FUTURES_SCALPER_UPGRADED.py:3882` | Rejects malformed bracket geometry |

These are the last line of defense if tool policy is misconfigured.
