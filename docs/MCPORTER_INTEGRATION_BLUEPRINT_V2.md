# MCPorter Integration Blueprint V2 — Deep Stack Integration

> **Source:** [github.com/steipete/mcporter](https://github.com/steipete/mcporter) (3.5k stars, v0.8.1, MIT)
> **What it is:** TypeScript runtime, CLI, and code-generation toolkit for MCP servers
> **Priority:** P1 — Unlocks CLI distribution, CI testing, and multi-MCP orchestration
> **Est. effort:** 8-12 hours across 5 phases
> **Date:** March 30, 2026
> **Supersedes:** `MCPORTER_INTEGRATION_BLUEPRINT.md` (V1)

---

## Executive Summary

V1 described MCPorter's generic value. V2 maps every integration point against **real AlgoChains components**: the **242-tool** MCP server (V10-V18), 4 live Tradovate bots, 80+ OpenClaw skills, 120+ Windsurf skills, the Rust backtest engine, 172 marketplace bots, Massive white-label partnership, and the 47-service launchd stack.

**Three killer outcomes:**

1. **`algochains` CLI binary** — investors, quants, and DevOps run `algochains browse_marketplace --min-sharpe 2.0` without an IDE ✅ DONE
2. **CI/CD regression gate** — every PR validates 242 tool schemas + Smart Discovery BM25 index in GitHub Actions ✅ DONE
3. **Typed multi-MCP orchestration** — replace the 10+ bash/Python OpenClaw skills that shell out to multiple MCP servers with composable TypeScript ✅ DONE

---

## 1. Stack Map — Where MCPorter Connects

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                        ALGOCHAINS PRODUCTION STACK                           │
│                                                                              │
│  ┌─────────────────────┐  ┌──────────────────┐  ┌────────────────────────┐  │
│  │ algochains-mcp v18  │  │ Control Tower    │  │ Rust Engine v2         │  │
│  │ 242 tools (Python)  │  │ 4 live bots      │  │ 4 strategy binaries    │  │
│  │ Smart/Full mode     │  │ 7 paper traders  │  │ Walk-forward + MCPT    │  │
│  │ V10-V18 engines     │  │ 47 launchd svcs  │  │ 172 marketplace bots   │  │
│  └─────────┬───────────┘  │ 80 OpenClaw jobs │  └────────────────────────┘  │
│            │               │ 120+ WS skills   │                              │
│            │               └──────────────────┘                              │
│            │                                                                 │
│  ┌─────────▼─────────────────────────────────────────────────────────────┐  │
│  │                         MCPorter Layer (NEW)                          │  │
│  │                                                                       │  │
│  │  1. CLI Binary  ─── algochains browse_marketplace --min-sharpe 2.0   │  │
│  │  2. TS SDK      ─── @algochains/sdk (auto-generated .d.ts)          │  │
│  │  3. CI Gate     ─── GitHub Actions: tool count + BM25 regression     │  │
│  │  4. Compose     ─── AlgoChains + Massive + Slack + Notion + Pinecone │  │
│  │  5. Ops Scripts ─── Replace bash plumbing in OpenClaw/Windsurf skills│  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  Connected MCP Servers (auto-discovered from ~/.codeium/windsurf/):         │
│  algochains | massive | slack | notion | pinecone | brave-search | exa      │
│  perplexity | openclaw-bridge | tradovate | memory | sequential-thinking    │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Real Tool Inventory — What MCPorter Exposes

MCPorter reads the MCP server's `list_tools` response and generates CLI/SDK for every tool. Here's the **actual tool map** from `server.py`:

### Tier 1 — Always Visible (21 tools, ~4K tokens)

| Tool | Annotation | Category |
|------|-----------|----------|
| `discover_tools` | SEARCH | Smart Discovery |
| `get_tool_details` | SEARCH | Smart Discovery |
| `execute_dynamic_tool` | TRADE_EXEC | Smart Discovery |
| `massive_search_endpoints` | SEARCH | Market Data |
| `massive_call_api` | READ_EXTERNAL | Market Data |
| `massive_query_data` | COMPUTE | Market Data |
| `massive_run_pipeline` | COMPUTE | Market Data |
| `place_order` | TRADE_EXEC | Trading |
| `cancel_order` | TRADE_EXEC | Trading |
| `close_position` | TRADE_EXEC | Trading |
| `close_all_positions` | TRADE_EXEC | Trading |
| `get_positions` | READ_EXTERNAL | Portfolio |
| `get_account` | READ_EXTERNAL | Portfolio |
| `get_orders` | READ_EXTERNAL | Portfolio |
| `portfolio_summary` | READ_EXTERNAL | Portfolio |
| `connect_broker` | WRITE_SAFE | Connectivity |
| `run_backtest` | COMPUTE | Strategy |
| `validate_strategy` | COMPUTE | Strategy |
| `optimize_strategy` | COMPUTE | Strategy |
| `deploy_strategy` | TRADE_EXEC | Strategy |
| `browse_strategy_marketplace` | SEARCH | Marketplace |

### Tier 2 — Discoverable (137+ tools)

| Version | Domain | Key Tools | Count |
|---------|--------|-----------|-------|
| V10 | ML Engine | `create_feature_set`, `train_model`, `register_model`, `predict_model`, `create_rl_agent`, `dispatch_gpu_job`, `generate_strategy_llm` | 12 |
| V11 | Execution | `submit_inst_order`, `cancel_inst_order`, `start_algo_executor`, `stop_algo_executor`, `route_smart_order`, `start_fix_session` | 10 |
| V12 | Analytics | `stream_pnl`, `analyze_order_flow`, `analyze_microstructure`, `detect_regime`, `configure_alert` | 8 |
| V13 | Alt Data | `analyze_sentiment`, `get_satellite_data`, `start_scrape_job`, `get_sec_filing`, `get_social_sentiment` | 10 |
| V14 | Agent Swarm | `create_agent_swarm`, `assign_swarm_task`, `get_swarm_status`, `get_consensus`, `get_agent_memory` | 8 |
| V15 | DeFi | `execute_swap`, `create_yield_position`, `execute_flash_loan`, `detect_mev`, `vote_governance` | 10 |
| V16 | Cloud SaaS | `configure_white_label`, `generate_api_key`, `get_platform_health`, `get_api_usage` | 8 |
| V17 | Dynamic | `register_external_toolset`, `massive_get_endpoint_docs` | 4 |
| V18 | Intent | `execute_intent`, `create_shadow_portfolio`, `evolve_strategies`, `detect_arbitrage`, `detect_market_regime`, `prefetch_state` | 8 |
| V1-V9 | Core | Brokers, BYOK, Notifications, Datasets, Strategy Builder, Social, Signals, Risk, Compliance, Multi-Tenant | 59 |

**MCPorter generates CLI + SDK for ALL 242 tools automatically.**

### Tool Annotation Safety Map

MCPorter respects MCP 2025-06-18 `ToolAnnotations`. The server classifies every tool:

```
ANNOT_READ_ONLY      → Safe for CI, demos, auto-approval
ANNOT_READ_EXTERNAL  → Safe but hits external APIs (get_positions, get_quote, massive_call_api)
ANNOT_WRITE_SAFE     → Creates resources but non-destructive (connect_broker, register_model)
ANNOT_WRITE_DESTRUCTIVE → Dangerous: close_all_positions, cancel_all_orders
ANNOT_TRADE_EXEC     → MOST DANGEROUS: place_order, deploy_strategy, execute_swap
ANNOT_SEARCH         → Safe: discover_tools, browse_strategy_marketplace
ANNOT_COMPUTE        → Safe but expensive: run_backtest, optimize_strategy, massive_query_data
```

**MCPorter CLI should respect these annotations:**
- `--dry-run` flag: skip TRADE_EXEC and WRITE_DESTRUCTIVE tools
- `--safe-only` flag: only execute READ_ONLY + SEARCH + COMPUTE
- Default: require `--confirm` for TRADE_EXEC

---

## 3. Seven Integration Plays (Stack-Specific)

### Play 1: AlgoChains CLI Binary — Investor & Quant Distribution

**What:** Ship a standalone `algochains` CLI via `mcporter generate-cli`. No npm, no pip, just download and run.

**Real commands with real tools:**

```bash
# Marketplace browsing (investor demos, Sharpe filtering)
algochains browse_strategy_marketplace asset_class:futures min_sharpe:2.0
algochains browse_strategy_marketplace asset_class:forex strategy:ema tier:MICRO

# Portfolio intelligence
algochains get_positions broker:tradovate
algochains get_account broker:tradovate
algochains portfolio_summary broker:alpaca

# Market data via Massive white-label
algochains massive_search_endpoints query:"stock aggregates"
algochains massive_call_api method:GET path:/v2/aggs/ticker/SPY/range/1/day/2024-01-01/2024-12-31
algochains massive_query_data sql:"SELECT * FROM prices ORDER BY timestamp DESC LIMIT 10"

# Smart Discovery — find tools by description
algochains discover_tools query:"sentiment analysis SEC filings"
algochains discover_tools query:"options flow dark pool"
algochains discover_tools query:"walk-forward optimization"

# Strategy pipeline
algochains run_backtest strategy_id:rsi_bb_mnq data_source:tick_data period:2020-2024
algochains validate_strategy strategy_id:rsi_bb_mnq
algochains optimize_strategy strategy_id:rsi_bb_mnq trials:500

# V18 Intent-based trading (killer feature for demos)
algochains execute_intent intent:"Get me $10K AI exposure, max 2% per stock"
algochains create_shadow_portfolio name:"test_portfolio" capital:100000
algochains detect_market_regime
algochains detect_arbitrage
```

**Build pipeline:**

```bash
# Generate from live server
npx mcporter generate-cli algochains --compile --name algochains

# Cross-compile (requires Bun)
GOOS=darwin GOARCH=arm64 npx mcporter generate-cli algochains --compile --name algochains-macos-arm64
GOOS=linux GOARCH=amd64 npx mcporter generate-cli algochains --compile --name algochains-linux-amd64
```

**Distribution:**
- GitHub Releases: attach binaries per platform
- Homebrew: `brew tap algochains/tap && brew install algochains`
- npm: `npx @algochains/cli browse_strategy_marketplace`

---

### Play 2: TypeScript SDK — `@algochains/sdk`

**What:** Auto-generate typed TypeScript client from all 242 MCP tool schemas. Publish to npm.

**Generated types (real tools, real params):**

```typescript
// @algochains/sdk — auto-generated by mcporter emit-ts
export interface AlgoChainsClient {
  // ── Smart Discovery ──────────────────────────────────
  discoverTools(params: { query: string }): Promise<DiscoverToolsResult>;
  getToolDetails(params: { tool_name: string }): Promise<ToolDetailsResult>;
  executeDynamicTool(params: { tool_name: string; arguments: Record<string, any> }): Promise<any>;

  // ── Trading ──────────────────────────────────────────
  placeOrder(params: {
    broker: 'alpaca' | 'ibkr' | 'oanda' | 'tradovate' | 'traderspost';
    symbol: string;
    side: 'buy' | 'sell';
    qty: number;
    order_type?: 'market' | 'limit' | 'stop' | 'stop_limit' | 'trailing_stop';
    limit_price?: number;
    stop_price?: number;
    time_in_force?: 'day' | 'gtc' | 'ioc';
  }): Promise<PlaceOrderResult>;

  cancelOrder(params: { broker: string; order_id: string }): Promise<CancelResult>;
  closePosition(params: { broker: string; symbol: string }): Promise<CloseResult>;
  closeAllPositions(params: { broker: string }): Promise<CloseAllResult>;

  // ── Portfolio Intelligence ───────────────────────────
  getPositions(params: { broker: string }): Promise<PositionsResult>;
  getAccount(params: { broker: string }): Promise<AccountResult>;
  getOrders(params: { broker: string; status?: string }): Promise<OrdersResult>;
  portfolioSummary(params: { broker: string }): Promise<PortfolioSummaryResult>;

  // ── Massive Market Data (White-Label) ────────────────
  massiveSearchEndpoints(params: { query: string; scope?: 'endpoints' | 'functions' | 'all' }): Promise<SearchResult>;
  massiveCallApi(params: { method: 'GET'; path: string; params?: Record<string, any>; store_as?: string }): Promise<ApiResult>;
  massiveQueryData(params: { sql: string }): Promise<QueryResult>;
  massiveRunPipeline(params: { steps: PipelineStep[] }): Promise<PipelineResult>;

  // ── Strategy Builder ─────────────────────────────────
  runBacktest(params: { strategy_id: string; data_source?: string; period?: string }): Promise<BacktestResult>;
  validateStrategy(params: { strategy_id: string }): Promise<ValidationResult>;
  optimizeStrategy(params: { strategy_id: string; trials?: number }): Promise<OptimizationResult>;
  deployStrategy(params: { strategy_id: string; broker: string; capital: number }): Promise<DeployResult>;

  // ── Marketplace ──────────────────────────────────────
  browseStrategyMarketplace(params: { asset_class?: string; min_sharpe?: number; tier?: string; strategy?: string }): Promise<MarketplaceResult>;

  // ── V10: ML Engine ───────────────────────────────────
  createFeatureSet(params: { name: string; features: FeatureSpec[] }): Promise<FeatureSetResult>;
  trainModel(params: { name: string; feature_set_id: string; algorithm: string }): Promise<TrainResult>;
  registerModel(params: { name: string; artifact_path: string }): Promise<RegisterResult>;
  predictModel(params: { model_id: string; features: Record<string, number> }): Promise<PredictResult>;
  createRlAgent(params: { name: string; environment: string }): Promise<RLAgentResult>;
  dispatchGpuJob(params: { script: string; gpu_target?: 'mac' | 'desktop' }): Promise<GPUJobResult>;

  // ── V11: Institutional Execution ─────────────────────
  submitInstOrder(params: { symbol: string; side: string; qty: number; algo?: 'TWAP' | 'VWAP' }): Promise<InstOrderResult>;
  routeSmartOrder(params: { symbol: string; side: string; qty: number }): Promise<SmartRouteResult>;
  startAlgoExecutor(params: { algo_id: string }): Promise<AlgoResult>;

  // ── V12: Real-Time Analytics ─────────────────────────
  analyzeOrderFlow(params: { symbol: string }): Promise<OrderFlowResult>;
  detectRegime(params: { symbol?: string }): Promise<RegimeResult>;
  configureAlert(params: { condition: string; channel: string }): Promise<AlertResult>;

  // ── V13: Alternative Data ────────────────────────────
  analyzeSentiment(params: { symbol: string; sources?: string[] }): Promise<SentimentResult>;
  getSecFiling(params: { ticker: string; filing_type?: string }): Promise<SECResult>;

  // ── V14: Agent Swarm ─────────────────────────────────
  createAgentSwarm(params: { name: string; agents: AgentSpec[] }): Promise<SwarmResult>;
  assignSwarmTask(params: { swarm_id: string; task: string }): Promise<TaskResult>;
  getConsensus(params: { swarm_id: string; question: string }): Promise<ConsensusResult>;

  // ── V18: Intent Engine (Genius Layer) ────────────────
  executeIntent(params: { intent: string; constraints?: Record<string, any> }): Promise<IntentResult>;
  createShadowPortfolio(params: { name: string; capital: number }): Promise<ShadowResult>;
  evolveStrategies(params: { population_size?: number; generations?: number }): Promise<EvolutionResult>;
  detectArbitrage(params: { brokers?: string[] }): Promise<ArbitrageResult>;
  detectMarketRegime(params: { lookback_days?: number }): Promise<RegimeResult>;
}
```

**Build pipeline:**

```bash
# Generate types
npx mcporter emit-ts algochains --out packages/sdk/src/types.d.ts

# Publish
cd packages/sdk && npm publish --access public
```

**Who uses this:**
- External devs building on AlgoChains platform
- V14 Agent Swarm (typed tool calls)
- V18 Intent Engine (typed constraint resolution)
- algochains.io frontend (direct MCP calls from Next.js API routes)

---

### Play 3: CI/CD Regression Gate

**What:** Block PRs that break tool schemas, reduce tool count, or corrupt the BM25 index.

```yaml
# .github/workflows/mcp-regression-gate.yml
name: MCP Tool Regression Gate
on: [push, pull_request]

jobs:
  tool-schema-validation:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: 22 }
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }

      - name: Install AlgoChains MCP + MCPorter
        run: |
          pip install -e ".[dev]"
          npm install -g mcporter

      - name: Configure MCPorter
        run: |
          mkdir -p config
          cat > config/mcporter.json << 'EOF'
          {
            "mcpServers": {
              "algochains": {
                "command": "algochains-mcp",
                "env": {
                  "ALGOCHAINS_TOOL_MODE": "full"
                }
              }
            }
          }
          EOF

      # Gate 1: Tool count must not decrease
      - name: "Gate 1: Tool Count (≥242)"
        run: |
          TOOLS=$(mcporter list algochains --json | jq '.tools | length')
          echo "Tool count: $TOOLS"
          [ "$TOOLS" -ge 242 ] || (echo "❌ FAIL: Expected ≥242 tools, got $TOOLS" && exit 1)
          echo "✅ Tool count: $TOOLS"

      # Gate 2: All Tier 1 tools must be present in smart mode
      - name: "Gate 2: Tier 1 Tools Present"
        env:
          ALGOCHAINS_TOOL_MODE: smart
        run: |
          TIER1_EXPECTED="discover_tools get_tool_details execute_dynamic_tool massive_search_endpoints massive_call_api massive_query_data massive_run_pipeline place_order cancel_order close_position get_positions get_account portfolio_summary connect_broker run_backtest validate_strategy optimize_strategy deploy_strategy browse_strategy_marketplace"
          TOOLS_JSON=$(mcporter list algochains --json)
          for tool in $TIER1_EXPECTED; do
            echo "$TOOLS_JSON" | jq -e ".tools[] | select(.name == \"$tool\")" > /dev/null 2>&1 \
              || (echo "❌ Missing Tier 1 tool: $tool" && exit 1)
          done
          echo "✅ All Tier 1 tools present"

      # Gate 3: BM25 Smart Discovery returns relevant results
      - name: "Gate 3: Smart Discovery BM25"
        run: |
          # Sentiment query should find V13 tools
          RESULT=$(mcporter call algochains.discover_tools query:"sentiment analysis" --json)
          echo "$RESULT" | jq -e '.tools[] | select(.name | contains("sentiment"))' > /dev/null \
            || (echo "❌ BM25 failed: 'sentiment analysis' didn't find sentiment tools" && exit 1)

          # Portfolio query should find portfolio tools
          RESULT=$(mcporter call algochains.discover_tools query:"portfolio positions" --json)
          echo "$RESULT" | jq -e '.tools[] | select(.name | contains("position"))' > /dev/null \
            || (echo "❌ BM25 failed: 'portfolio positions' didn't find position tools" && exit 1)

          echo "✅ BM25 index returning relevant results"

      # Gate 4: Tool Annotations present on all tools
      - name: "Gate 4: Tool Annotations (MCP 2025-06-18)"
        run: |
          MISSING=$(mcporter list algochains --json | jq '[.tools[] | select(.annotations == null)] | length')
          echo "Tools missing annotations: $MISSING"
          [ "$MISSING" -eq 0 ] || (echo "⚠️ $MISSING tools missing annotations" && exit 1)
          echo "✅ All tools have behavior annotations"

      # Gate 5: Resource templates registered
      - name: "Gate 5: Resource Templates"
        run: |
          RESOURCES=$(mcporter list algochains --json | jq '.resources | length // 0')
          echo "Resource templates: $RESOURCES"
          [ "$RESOURCES" -ge 3 ] || echo "⚠️ Expected ≥3 resource templates"
```

---

### Play 4: Multi-MCP Orchestration — Replace OpenClaw Bash Plumbing

**Target skills for conversion:** These 10 OpenClaw/Windsurf skills currently shell out to Python or chain multiple MCP calls via bash. MCPorter gives them typed, composable TypeScript.

| Skill | Current Pattern | MCPorter Replacement |
|-------|----------------|---------------------|
| `bot-health-monitor-auto` | `python3 scripts/bot_health_check.py` | TS: call `algochains.get_positions` + `algochains.get_account` + post to Slack |
| `mcpt-pipeline-ops` | `python3 scripts/mcpt_autopilot.py` → rsync + SSH + Rust | TS: compose `algochains.run_backtest` + `algochains.validate_strategy` + Slack |
| `money-blender-ops` | `grep` + `tail` + `python3` chains | TS: compose `algochains.get_positions(broker:'oanda')` + `massive_call_api` + Slack |
| `deploy-bot-changes` | Multi-step bash (backup → kill → deploy → verify) | TS: `algochains.get_positions` → confirm flat → run deploy → `algochains.get_account` verify |
| `system-audit-fix` | 7-layer bash audit | TS: `algochains.get_platform_health` + process checks + Slack report |
| `blueprint-executor` | `python3 scripts/blueprint_manager.py` | TS: read queue → compose tools → report to Slack |
| `institutional-flow-analysis` | Python Intrinio + UW calls | TS: `algochains.analyze_order_flow` + `algochains.analyze_sentiment` |
| `contract-rollover-handler` | Python rollover logic | TS: `algochains.get_positions` → detect expiring → `algochains.close_position` → `algochains.place_order` |
| `data-capture-system` | Python data pipeline | TS: `massive_call_api` → `massive_query_data` → store |
| `strategy-vault-tracker` | Python file scanning | TS: `algochains.browse_strategy_marketplace` + diff against local |

**Example: Morning Health Brief (currently 40+ lines of bash)**

```typescript
// skills/morning-health-brief.ts
import { createRuntime, createServerProxy } from "mcporter";

const rt = await createRuntime();
const ac = createServerProxy(rt, "algochains");
const massive = createServerProxy(rt, "massive");
const slack = createServerProxy(rt, "slack");

// 1. Portfolio snapshot (Tradovate live + Alpaca paper)
const [tvPositions, tvAccount, alpPositions] = await Promise.all([
  ac.getPositions({ broker: "tradovate" }),
  ac.getAccount({ broker: "tradovate" }),
  ac.getPositions({ broker: "alpaca" }),
]);

// 2. Market regime
const regime = await ac.detectMarketRegime({ lookback_days: 5 });

// 3. Pre-market data via Massive
const [spyPrev, vix] = await Promise.all([
  massive.callApi({ method: "GET", path: "/v2/aggs/ticker/SPY/prev" }),
  massive.callApi({ method: "GET", path: "/v2/aggs/ticker/VIX/prev" }),
]);

// 4. Marketplace pipeline status
const marketplace = await ac.browseStrategyMarketplace({ min_sharpe: 2.0 });

// 5. Compose & post
const brief = [
  `📊 *Morning Health Brief* — ${new Date().toLocaleDateString()}`,
  ``,
  `*Tradovate:* $${tvAccount.json().equity} | ${tvPositions.json().count} positions`,
  `*Alpaca Paper:* ${alpPositions.json().count} positions`,
  `*Regime:* ${regime.json().current_regime} (confidence: ${regime.json().confidence}%)`,
  `*SPY Prev:* $${spyPrev.json().results?.[0]?.c} | *VIX:* ${vix.json().results?.[0]?.c}`,
  `*Marketplace:* ${marketplace.json().total} bots with Sharpe ≥2.0`,
].join("\n");

await slack.postMessage({ channel_id: "C09F415GZ6W", text: brief }); // #quant-lab
await rt.close();
```

**Run via OpenClaw scheduler:**

```json
{
  "name": "morning-health-brief",
  "schedule": "0 7 * * 1-5",
  "command": "npx tsx /Users/treycsa/CascadeProjects/algochains-control-tower/skills/morning-health-brief.ts",
  "timeout": 180
}
```

---

### Play 5: Marketplace Bot Submission CLI

**What:** Bot creators can submit, validate, and list strategies from terminal.

```bash
# Discover marketplace tools
algochains discover_tools query:"marketplace submit publish"

# Validate a strategy before submission
algochains validate_strategy strategy_id:rsi_mnq_5min

# Run MCPT gate (500 permutations)
algochains run_backtest strategy_id:rsi_mnq_5min permutations:500

# Browse existing marketplace
algochains browse_strategy_marketplace asset_class:futures min_sharpe:2.0 tier:STARTER

# Deploy to paper trading
algochains deploy_strategy strategy_id:rsi_mnq_5min broker:alpaca capital:25000 mode:paper
```

**Integration with Rust engine pipeline:**

```bash
# After Rust engine generates results, use CLI to submit to marketplace
algochains publish_strategy_to_marketplace \
  strategy_id:rsi_mnq_5min \
  sharpe:4.61 \
  win_rate:0.58 \
  max_drawdown:0.12 \
  mcpt_p_value:0.003
```

---

### Play 6: Desktop GPU Dispatch via CLI

**What:** Trigger GPU jobs on the RTX 5080 desktop tower from terminal.

```bash
# Dispatch optimization to desktop GPU
algochains dispatch_gpu_job script:optimize_rsi_bb.py gpu_target:desktop

# Monitor GPU job
algochains get_gpu_job_status job_id:gpu_12345

# Generate features on GPU
algochains generate_features feature_set_id:mnq_tick_features gpu_target:desktop

# Train model on GPU
algochains train_model name:mnq_regime_classifier feature_set_id:mnq_tick_features algorithm:xgboost
```

**Maps to real infrastructure:**
- Desktop: `localhost` (WSL2, RTX 5080)
- GPU dispatcher: `ml_engine/gpu_dispatcher.py`
- Data path: `/home/trrey/tick_data/` (rsync'd)

---

### Play 7: Investor Demo Script

**What:** A 60-second demo script that shows AlgoChains capabilities without any IDE.

```bash
#!/bin/bash
# demo_algochains.sh — Run this in any terminal for investor demos

echo "🏗️ AlgoChains — AI-Native Algorithmic Trading Platform"
echo "======================================================="
echo ""

echo "📊 Step 1: Browse our validated bot marketplace (172 bots, MCPT-validated)"
algochains browse_strategy_marketplace min_sharpe:2.0 --limit 5

echo ""
echo "🔍 Step 2: Smart Tool Discovery — find any capability by description"
algochains discover_tools query:"options flow dark pool institutional"

echo ""
echo "📈 Step 3: Real-time market data via Massive partnership"
algochains massive_call_api method:GET path:/v2/aggs/ticker/SPY/prev

echo ""
echo "🧠 Step 4: V18 Intent-Based Trading — natural language to execution plan"
algochains execute_intent intent:"Show me the top 5 AI stocks by momentum, max 2% risk each" --dry-run

echo ""
echo "🔬 Step 5: Market regime detection — are we in bull, bear, or sideways?"
algochains detect_market_regime

echo ""
echo "✅ All from CLI — no IDE required. 242 tools, 5 brokers, 10+ data providers."
```

---

## 4. Slack Integration — Automated Reporting via MCPorter

MCPorter + Slack MCP = automated channel routing that matches the existing channel architecture.

| Channel | ID | MCPorter Use |
|---------|----|-------------|
| #quant-lab | `C09F415GZ6W` | Morning brief, marketplace updates, strategy research |
| #openclaw | `C0AFS7BDMSM` | System health, skill execution reports |
| #tradovate-futures-bot-changelog | `$SLACK_CHANNEL_BOT_CHANGELOG` | P&L updates, bot status changes |
| #incident-response | `C0AFT0GH54Z` | P0/P1 alerts only |
| #moltbook-crew | `C0AGF6P5ZPA` | Crew agent outputs |

---

## 5. MCPorter Config — Project-Level

```json
// config/mcporter.json (algochains-mcp-server repo)
{
  "mcpServers": {
    "algochains": {
      "command": "algochains-mcp",
      "env": {
        "ALGOCHAINS_TOOL_MODE": "full",
        "POLYGON_API_KEY": "${POLYGON_API_KEY}",
        "MASSIVE_API_KEY": "${MASSIVE_API_KEY}"
      }
    }
  },
  "defaults": {
    "outputFormat": "json",
    "timeout": 120
  }
}
```

```json
// config/mcporter.json (algochains-control-tower repo)
{
  "importFrom": ["~/.codeium/windsurf/mcp_config.json"],
  "mcpServers": {
    "algochains": {
      "command": "algochains-mcp",
      "env": { "ALGOCHAINS_TOOL_MODE": "full" }
    }
  },
  "defaults": {
    "outputFormat": "json",
    "timeout": 180,
    "safetyMode": "confirm-destructive"
  }
}
```

---

## 6. Implementation Roadmap

### Phase 1: Smoke Test + Config (1 hour) — ✅ COMPLETE

| Step | Action | Verify | Status |
|------|--------|--------|--------|
| 1.1 | `npm install -g mcporter` | `mcporter --version` | ✅ |
| 1.2 | Create `config/mcporter.json` in both repos | File exists | ✅ |
| 1.3 | `npx mcporter list algochains` | Shows 242 tools | ✅ |
| 1.4 | `npx mcporter call algochains.discover_tools query:"portfolio"` | Returns results | ✅ |
| 1.5 | `npx mcporter call algochains.browse_strategy_marketplace` | Returns marketplace | ✅ |

### Phase 2: CLI Binary + SDK Generation (2 hours) — ✅ COMPLETE

| Step | Action | Output | Status |
|------|--------|--------|--------|
| 2.1 | `npx mcporter emit-ts algochains --out packages/sdk/src/types.d.ts` | 1,404-line TypeScript types (52KB) | ✅ |
| 2.2 | `npx mcporter generate-cli algochains --bundle dist/algochains-cli.js` | 1.9MB CLI bundle | ✅ |
| 2.3 | Test 20 most useful CLI commands | 20/20 pass (`scripts/test_cli_20.sh`) | ✅ |
| 2.4 | Add `--dry-run`, `--safe-only`, `--confirm` via wrapper script | `scripts/algochains` (5/5 tests pass) | ✅ |

### Phase 3: CI/CD Regression Gate (2 hours) — ✅ COMPLETE

| Step | Action | Output | Status |
|------|--------|--------|--------|
| 3.1 | Create `.github/workflows/mcp-regression-gate.yml` | 6-gate PR workflow | ✅ |
| 3.2 | Gate 1: Tool count ≥242 | Blocks breaking PRs | ✅ |
| 3.3 | Gate 2: Tier 1 tools present (incl V18) | Blocks smart mode regression | ✅ |
| 3.4 | Gate 3: BM25 index validation | Blocks discovery corruption | ✅ |
| 3.5 | Gate 4: V18 Intent Engine tools present | 10 V18 tools validated | ✅ |
| 3.6 | Gate 5: V18 smoke test (detect_market_regime) | Returns valid JSON | ✅ |
| 3.7 | Gate 6: Python AST validation | No syntax errors in server.py | ✅ |

### Phase 4: Windsurf + OpenClaw Skills (3 hours) — ✅ COMPLETE

| Step | Action | Output | Status |
|------|--------|--------|--------|
| 4.1 | Create `.windsurf/skills/mcporter-toolkit/SKILL.md` | Windsurf skill with safety map, Slack routing, multi-MCP patterns | ✅ |
| 4.2 | Convert `morning-health-brief` to TS | `skills/morning-health-brief.ts` (84 lines, 5 parallel MCP calls) | ✅ |
| 4.3 | Convert `bot-health-monitor-auto` to TS | `skills/bot-health-monitor.ts` (104 lines, risk alerts + regime) | ✅ |
| 4.4 | Convert `strategy-vault-tracker` to TS | `skills/strategy-vault-tracker.ts` (74 lines, 3 asset classes) | ✅ |
| 4.5 | Add to OpenClaw scheduler as TS jobs | Ready for `npx tsx skills/*.ts` scheduling | ✅ |

### Phase 5: Distribution + Docs (2 hours) — ✅ COMPLETE

| Step | Action | Output | Status |
|------|--------|--------|--------|
| 5.1 | Add CLI section to README.md | CLI section with 242 tools, V18 examples | ✅ |
| 5.2 | GitHub Actions release workflow | `.github/workflows/release.yml` (builds CLI + publishes SDK) | ✅ |
| 5.3 | Create `demo_algochains.sh` investor script | `scripts/demo_algochains.sh` (60-second demo) | ✅ |
| 5.4 | `@algochains/sdk` package | `packages/sdk/` (package.json + types.d.ts + client.d.ts + index.js) | ✅ |
| 5.5 | Homebrew tap formula | `homebrew/algochains.rb` (Node.js dependency) | ✅ |

---

## 7. Safety Rules

### ✅ SAFE (Auto-run, no approval)

- `discover_tools`, `get_tool_details`, `browse_strategy_marketplace`
- `massive_search_endpoints`, `massive_get_endpoint_docs`
- `get_positions`, `get_account`, `get_orders`, `portfolio_summary`
- `detect_market_regime`, `detect_arbitrage` (read-only analysis)
- All SEARCH and READ_ONLY annotated tools

### ⚠️ CONFIRM (Require `--confirm` flag)

- `place_order`, `cancel_order`, `close_position`, `close_all_positions`
- `deploy_strategy`, `submit_inst_order`
- `execute_swap`, `execute_flash_loan`
- All TRADE_EXEC annotated tools

### ❌ NEVER via CLI without IDE context

- `close_all_positions` without checking open P&L first
- `execute_intent` with real capital (use `--dry-run`)
- Any tool that modifies `.env` or credential files

---

## 8. What NOT to Do

- **Don't rewrite the Python MCP server** — MCPorter wraps it as-is
- **Don't add MCPorter as a Python dependency** — it's Node.js tooling
- **Don't replace Windsurf/Cursor MCP integration** — MCPorter supplements IDE consumption
- **Don't expose TRADOVATE_ACCESS_TOKEN via CLI** — use env var passthrough
- **Don't run TRADE_EXEC tools in CI** — use `--safe-only` mode
- **Don't deploy this before launch** — Phase 1-3 this week, Phase 4-5 post-launch

---

## 9. Competitive Edge

No other algorithmic trading platform offers a CLI that auto-generates from MCP tool definitions. The closest competitors:

| Platform | CLI | SDK | CI Testing | Multi-Server Compose |
|----------|-----|-----|-----------|---------------------|
| QuantConnect | ❌ Web only | C# SDK (manual) | ❌ | ❌ |
| Alpaca | REST API | Python/JS (manual) | Custom | ❌ |
| Interactive Brokers | TWS API | Java/Python (manual) | Custom | ❌ |
| **AlgoChains + MCPorter** | **Auto-generated from 242 MCP tools** | **Auto-generated TypeScript** | **Built-in regression gates** | **12 MCP servers composable** |

---

## 10. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| MCPorter can't handle 242 tools | Low | Med | Tested with 23-tool Linear server; our tools follow MCP spec |
| Performance overhead on large tool list | Low | Low | MCPorter caches tool schemas; stdio transport same as IDEs |
| Safety: CLI user places bad trade | Med | High | `--confirm` required for TRADE_EXEC; `--dry-run` default for intents |
| MCPorter project abandoned | Low | Med | 3.5k stars, MIT license; we can fork if needed |
| npm supply chain attack | Low | High | Pin version, use `npm audit`, review before upgrade |
| Credential leak via CLI history | Med | High | Use env vars, never pass tokens as CLI args |

---

## 11. Quick Start (Test Right Now)

```bash
# 1. Install MCPorter
npm install -g mcporter

# 2. Verify algochains-mcp is configured
# MCPorter auto-discovers from ~/.codeium/windsurf/mcp_config.json

# 3. List all tools
npx mcporter list algochains

# 4. Call a safe read-only tool
npx mcporter call algochains.discover_tools query:"market data"

# 5. Browse marketplace
npx mcporter call algochains.browse_strategy_marketplace min_sharpe:2.0

# 6. Generate TypeScript types
npx mcporter emit-ts algochains --out /tmp/algochains.d.ts
wc -l /tmp/algochains.d.ts  # Should be 500+ lines of typed interfaces

# 7. Generate standalone CLI
npx mcporter generate-cli algochains --bundle /tmp/algochains-cli.js
node /tmp/algochains-cli.js discover_tools query:"portfolio"
```

---

*Blueprint V2 authored by Cascade — March 30, 2026*
*Updated: March 31, 2026 — ALL 5 PHASES COMPLETE*
*Stack context: algochains-mcp v18 (**242 tools**), Control Tower (4 bots, 47 services, 80 skills), Rust Engine v2 (172 marketplace bots), Massive white-label partnership*
*Relates to: V17 Smart Tool Discovery, V18 Intent Engine, OpenClaw Gateway, Windsurf Skills, Marketplace Pipeline, Investor Demo*

### Files Created During Implementation

| File | Repo | Purpose |
|------|------|---------|
| `config/mcporter.json` | mcp-server | MCPorter server config |
| `config/mcporter.json` | control-tower | Control tower MCPorter config (multi-server) |
| `dist/algochains-cli.js` | mcp-server | Standalone CLI bundle (1.9MB) |
| `packages/sdk/src/types.d.ts` | mcp-server | Auto-generated TypeScript types (1,404 lines) |
| `packages/sdk/src/client.d.ts` | mcp-server | Typed client interface (140 lines) |
| `packages/sdk/src/index.js` | mcp-server | Runtime client factory |
| `packages/sdk/package.json` | mcp-server | @algochains/sdk v18 npm package |
| `scripts/algochains` | mcp-server | Safety wrapper (--dry-run, --safe-only, --confirm) |
| `scripts/test_cli_20.sh` | mcp-server | CLI test suite (20 commands) |
| `scripts/demo_algochains.sh` | mcp-server | Investor demo script (5 steps) |
| `skills/morning-health-brief.ts` | mcp-server | TS composition: morning brief |
| `skills/bot-health-monitor.ts` | mcp-server | TS composition: bot health |
| `skills/strategy-vault-tracker.ts` | mcp-server | TS composition: vault tracker |
| `skills/mcpt-pipeline-ops.ts` | mcp-server | TS composition: MCPT pipeline |
| `skills/money-blender-ops.ts` | mcp-server | TS composition: forex diagnostics |
| `skills/deploy-bot-changes.ts` | mcp-server | TS composition: safe deploy |
| `skills/system-audit-fix.ts` | mcp-server | TS composition: system audit |
| `skills/blueprint-executor.ts` | mcp-server | TS composition: blueprint queue |
| `skills/institutional-flow-analysis.ts` | mcp-server | TS composition: flow analysis |
| `skills/contract-rollover-handler.ts` | mcp-server | TS composition: rollover alerts |
| `.windsurf/skills/mcporter-toolkit/SKILL.md` | mcp-server | Windsurf skill reference |
| `.github/workflows/mcp-regression-gate.yml` | mcp-server | CI/CD gate (6 validations) |
| `.github/workflows/release.yml` | mcp-server | Release workflow (CLI + SDK) |
| `homebrew/algochains.rb` | mcp-server | Homebrew formula |
