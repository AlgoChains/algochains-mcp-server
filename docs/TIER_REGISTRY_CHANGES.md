# AlgoChains Tier Registry Change Summary
**Audit Date:** 2026-05-10  
**Author:** Hidden Killer Audit v2  
**File:** `src/algochains_mcp/tool_danger_tiers.py`  
**Tests:** `tests/test_dynamic_dispatch_safety.py`, `tests/test_tool_danger_contract.py`

---

## Summary of Changes

### BUG-07 Fix — Kalshi Execution Tools Promoted to `TIER_ORDER_EXEC`

Three Kalshi pipeline tools were discovered to be mis-tiered as `WRITE_LOCAL` (Tier 1) because they matched the `run_` prefix rule. This allowed any autonomous agent with `WRITE_LOCAL` max-tier access to invoke live Kalshi order placement without `owner_token` authorization.

---

## Change Table

| Tool Name | Previous Tier | New Tier | Tier ID | Reason |
|-----------|--------------|----------|---------|--------|
| `run_safe_compounder` | `WRITE_LOCAL` (1) | `ORDER_EXEC` (2) | `TIER_ORDER_EXEC` | Calls `kalshi_signed_post` with `execute=true`, placing real prediction market orders |
| `run_kalshi_full_pipeline` | `WRITE_LOCAL` (1) | `ORDER_EXEC` (2) | `TIER_ORDER_EXEC` | Full pipeline: analyze → compounder → execute. Real live Kalshi orders when `execute=true + confirmed=true` |
| `run_kalshi_strategy_order` | `WRITE_LOCAL` (1) | `ORDER_EXEC` (2) | `TIER_ORDER_EXEC` | Direct Kalshi strategy order execution. Wraps `place_kalshi_order` path |

---

## Why These Were Wrong

The tool tier system resolves in priority order:
1. **Explicit `_TOOL_TIERS` entry** (highest priority)
2. **`SIDE_EFFECT_HINTS` name pattern** (e.g., `place_`, `execute_`, `cancel_`, `flatten_`)
3. **Prefix rules** (`run_` → `WRITE_LOCAL` by default)
4. **Wildcard default** (`WRITE_LOCAL`)

All three Kalshi tools matched `run_` prefix → `WRITE_LOCAL`. Because none appeared in `_TOOL_TIERS` and none triggered `SIDE_EFFECT_HINTS` (Kalshi-specific verbs not in the hint table), they landed at `WRITE_LOCAL`.

**Impact:** Any MCP consumer with `execute_dynamic_tool` authorization at `WRITE_LOCAL` tier could call these tools without `owner_token`. The `place_kalshi_order` tool immediately below them was correctly `ORDER_EXEC`, but the pipeline wrappers that call it were not.

---

## Fix Applied

Added explicit entries in `_TOOL_TIERS` dictionary at lines 151–158 of `tool_danger_tiers.py`:

```python
"place_kalshi_order": TIER_ORDER_EXEC,
# BUG-07 FIX: run_safe_compounder and run_kalshi_full_pipeline call
# kalshi_signed_post (live Kalshi orders) when execute=true + confirmed=true.
# The `run_` prefix rule mis-tiered them as WRITE_LOCAL, allowing autonomous
# agents (capped at WRITE_LOCAL) to trigger real money orders without
# owner_token authorization. Explicit ORDER_EXEC overrides the prefix rule.
"run_safe_compounder": TIER_ORDER_EXEC,
"run_kalshi_full_pipeline": TIER_ORDER_EXEC,
"run_kalshi_strategy_order": TIER_ORDER_EXEC,
```

Explicit entries always win over prefix/hint rules — the registry is consulted first in `get_danger_tier()`.

---

## Contract Test Coverage

Added to `tests/test_dynamic_dispatch_safety.py`:

```python
@pytest.mark.parametrize("tool_name", [
    "place_order",
    "cancel_order",
    "close_position",
    "flatten_all_positions",
    # BUG-07 FIX: Kalshi execution tools (were WRITE_LOCAL, now ORDER_EXEC)
    "run_safe_compounder",
    "run_kalshi_full_pipeline",
    "run_kalshi_strategy_order",
])
def test_order_exec_tools_blocked_without_owner_token(tool_name):
    """Every ORDER_EXEC tool must be blocked if owner_token is wrong/missing."""
    result = evaluate_dynamic_tool(
        tool_name,
        tool_tier=TIER_ORDER_EXEC,
        owner_token="wrong-token",
        confirm=True,
    )
    assert not result.allow, f"{tool_name} must be blocked without valid owner_token"
    assert result.missing_token is True
```

---

## Tier Tier Reference

| Tier ID | Int | Label | Description |
|---------|-----|-------|-------------|
| `TIER_READ_ONLY` | 0 | `READ_ONLY` | Pure reads — market data, status, health checks. No side effects. |
| `TIER_WRITE_LOCAL` | 1 | `WRITE_LOCAL` | Writes to local files, memory, state. No broker calls. Autonomous agents capped here. |
| `TIER_ORDER_EXEC` | 2 | `ORDER_EXEC` | Executes real orders on live broker accounts. Requires `owner_token` + interactive confirmation. |
| `TIER_DESTRUCTIVE` | 3 | `DESTRUCTIVE` | Irreversible actions (flatten all, mass cancel, account shutdown). Extra confirmation required. |

---

## Other ORDER_EXEC Tools (Representative Sample — Not Exhaustive)

The following tools are already correctly classified as `TIER_ORDER_EXEC` and were not changed:

```
place_order, place_bracket_order, place_oco_order
cancel_order, close_position, modify_order
smart_route_order, route_order
restart_trading_bot, flatten_bot_position
propagate_trade_signal
submit_institutional_order
place_kalshi_order
```

---

## Scanning for Future Mis-Tiering

Run the contract test suite to catch any newly added tool that:
1. Has a name implying side effects (`place_`, `execute_`, `cancel_`, `kill_`, `flatten_`, `stop_`, `start_`, `restart_`, `run_`) but is not `ORDER_EXEC`
2. Has `TIER_WRITE_LOCAL` but touches a broker API

```bash
cd algochains-mcp-server
python -m pytest tests/test_tool_danger_contract.py -v
python -m pytest tests/test_dynamic_dispatch_safety.py -v
```

CI gate: Run `scripts/run_bandit.sh` before every deploy to catch regressions.
