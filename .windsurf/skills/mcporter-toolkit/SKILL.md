---
description: MCPorter toolkit for AlgoChains — CLI, SDK, multi-MCP composition
---

# MCPorter Toolkit

Use this skill when composing multi-MCP tool calls, generating CLI commands, or working with the AlgoChains TypeScript SDK.

## Quick Reference

### CLI (safety wrapper)
```bash
# Safe read-only
algochains discover-tools --query "portfolio positions"
algochains detect-market-regime
algochains browse-strategy-marketplace

# With safety flags
algochains execute-intent --intent "Get me 10K AI exposure" --dry-run
algochains place-order --broker alpaca --symbol AAPL --side buy --qty 10 --confirm

# Blocked by safety
algochains close-all-positions --safe-only  # BLOCKED
algochains place-order --broker alpaca --symbol AAPL --side buy --qty 10  # Requires --confirm
```

### MCPorter Direct
```bash
npx mcporter list algochains              # 242 tools
npx mcporter list algochains --schema     # Full tool docs
npx mcporter call algochains.discover_tools query="sentiment"
npx mcporter call algochains.detect_market_regime
```

### TypeScript SDK
```typescript
import type { AlgoChainsClient } from "@algochains/sdk";
// Types at: packages/sdk/src/types.d.ts (1,404 lines, 242 tools)
```

## Multi-MCP Composition Pattern

When composing calls across multiple MCP servers (AlgoChains + Massive + Slack + Pinecone):

```typescript
import { createRuntime, createServerProxy } from "mcporter";

const rt = await createRuntime();
const ac = createServerProxy(rt, "algochains");
const massive = createServerProxy(rt, "massive");
const slack = createServerProxy(rt, "slack");

// Parallel calls
const [positions, regime, spy] = await Promise.all([
  ac.getPositions({ broker: "tradovate" }),
  ac.detectMarketRegime(),
  massive.callApi({ method: "GET", path: "/v2/aggs/ticker/SPY/prev" }),
]);

// Post to Slack
await slack.postMessage({
  channel_id: "C09F415GZ6W",
  text: `Positions: ${positions.count}, Regime: ${regime.regime}, SPY: $${spy.results?.[0]?.c}`
});

await rt.close();
```

## Tool Safety Classifications

| Classification | Flag | Examples |
|---------------|------|---------|
| SEARCH | Auto-approve | `discover_tools`, `browse_strategy_marketplace` |
| READ_ONLY | Auto-approve | `get_platform_health`, `detect_market_regime` |
| READ_EXTERNAL | Auto-approve | `get_positions`, `get_account`, `massive_call_api` |
| COMPUTE | Auto-approve | `run_backtest`, `optimize_strategy` |
| WRITE_SAFE | Allowed | `connect_broker`, `register_model` |
| TRADE_EXEC | `--confirm` | `place_order`, `deploy_strategy`, `execute_intent` |
| WRITE_DESTRUCTIVE | `--confirm` + YES | `close_all_positions` |

## Files

| File | Purpose |
|------|---------|
| `config/mcporter.json` | MCPorter server config |
| `dist/algochains-cli.js` | Standalone CLI bundle (1.9MB) |
| `scripts/algochains` | Safety wrapper with --dry-run/--safe-only/--confirm |
| `packages/sdk/src/types.d.ts` | Auto-generated TypeScript SDK (52KB) |
| `scripts/test_cli_20.sh` | CLI test suite (20 commands) |
| `scripts/demo_algochains.sh` | Investor demo script (5 steps) |
| `.github/workflows/mcp-regression-gate.yml` | CI/CD regression gate (6 gates) |
| `skills/morning-health-brief.ts` | TS composition: morning brief |
| `skills/bot-health-monitor.ts` | TS composition: bot health |
| `skills/strategy-vault-tracker.ts` | TS composition: vault tracker |
| `skills/mcpt-pipeline-ops.ts` | TS composition: MCPT pipeline |
| `skills/money-blender-ops.ts` | TS composition: forex diagnostics |
| `skills/deploy-bot-changes.ts` | TS composition: safe deploy |
| `skills/system-audit-fix.ts` | TS composition: system audit |
| `skills/blueprint-executor.ts` | TS composition: blueprint queue |
| `skills/institutional-flow-analysis.ts` | TS composition: flow analysis |
| `skills/contract-rollover-handler.ts` | TS composition: rollover alerts |

## Slack Channel Routing

| Channel | ID | Use |
|---------|----|-----|
| #quant-lab | `C09F415GZ6W` | Research, marketplace, strategy |
| #openclaw | `C0AFS7BDMSM` | System health, skill execution |
| #tradovate-futures-bot-changelog | `C09TGL20N4V` | P&L, bot status |
| #incident-response | `C0AFT0GH54Z` | P0/P1 only |

## Regeneration

```bash
# Regenerate after adding new tools to server.py
npx mcporter emit-ts algochains --mode types --out packages/sdk/src/types.d.ts
npx mcporter generate-cli --server algochains --bundle dist/algochains-cli.js
```
