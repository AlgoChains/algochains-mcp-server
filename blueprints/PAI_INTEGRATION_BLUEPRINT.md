# PAI Integration Blueprint — AlgoChains
**Source:** [danielmiessler/Personal_AI_Infrastructure](https://github.com/danielmiessler/Personal_AI_Infrastructure) v4.0.3 (⭐11.2k)
**Date:** 2026-04-07 | **Author:** AlgoChains AI Audit

---

## Executive Summary

PAI (Personal AI Infrastructure) is a 11.2k-star open-source agentic platform built natively on Claude Code. Its core innovations — TELOS (goal identity system), Algorithm v3.7.0 (ISC-driven problem-solving loop), USMetrics (68 US economic indicators), and Learning Signals (continuous improvement via outcome capture) — offer **four genuinely novel additions** to the AlgoChains stack that we have NOT already built.

This blueprint identifies what to integrate, what to skip (because we already do it better), and how to wire it all into the AlgoChains MCP server.

---

## Gap Analysis: PAI vs. AlgoChains Current Stack

### ✅ Already Have (Skip — Don't Rebuild)

| PAI Feature | AlgoChains Equivalent | Why Skip |
|-------------|----------------------|----------|
| Skill System (63 skills) | 472 skills across OpenClaw/Windsurf/Cursor | We have 7.5× more |
| Memory System (flat JSON) | OpenClaw memory.json + agent_memory.py | Our bridge is already built |
| Multi-agent debates | Moltbook debate engine (PostgreSQL + NATS + LangGraph) | Far more sophisticated |
| MCP Server | AlgoChains MCP Server (363 tools) | We have 10× more |
| Bot monitoring | autonomous_watchdog.py + adaptive_brain.py | Already live |
| Hook system | Autonomous agents (5-min cycles) | Running in production |
| BYOK / API key mgmt | byok/provider_registry.py | More comprehensive |
| Dashboard | algochains-command-center (Next.js) | Already deployed |
| RAG / knowledge brain | Onyx at 100.89.114.31:8085 | Already indexing all docs |

### 🆕 Novel (Build — Genuinely Missing)

| PAI Feature | Why Novel for AlgoChains | Priority |
|-------------|--------------------------|----------|
| **TELOS Business Identity** | No MISSION.md / GOALS.md / STRATEGIES.md for AlgoChains as a company | 🔴 P0 |
| **FRED/EIA Economic Indicators** | No systematic macro data layer (68 indicators from 7 federal APIs) | 🔴 P0 |
| **Learning Signals** | No feedback loop capturing agent outcome ratings | 🟡 P1 |
| **ntfy Mobile Push** | Slack only — no mobile push notifications | 🟡 P1 |
| **Algorithm Loop (ISC-driven)** | No PAI-style Observe→Think→Plan→Execute→Verify→Learn wrapper | 🟢 P2 |

---

## Integration Plan

### Module 1: AlgoChains TELOS (Business Identity OS)

PAI's TELOS is a structured set of files your AI reads to understand who you are and what you're trying to accomplish. AlgoChains has no equivalent. Every agent session starts with zero company context.

**What to build:**
- `algochains-control-tower/TELOS/` — 8 files capturing AlgoChains business identity
- `algochains-mcp-server/src/algochains_mcp/telos.py` — Reader + writer
- 2 MCP tools: `get_algochains_telos`, `update_algochains_telos`

**TELOS Files:**
```
TELOS/
├── MISSION.md          ← Why AlgoChains exists
├── GOALS.md            ← Q2 2026 targets (AUM, subscribers, marketplace)
├── STRATEGIES.md       ← How goals are achieved
├── MODELS.md           ← Trading mental models (regime, Kelly, Sharpe gates)
├── LEARNED.md          ← Key lessons from live trading
├── IDEAS.md            ← Future expansion ideas
├── CHALLENGES.md       ← Current blockers and risks
└── METRICS.md          ← KPIs: bot performance, marketplace, platform
```

**Agent Usage:**
```python
# Any agent can now get full business context
get_algochains_telos(section="all")
get_algochains_telos(section="goals")
update_algochains_telos(section="learned", entry="MNQ overnight holds have negative EV — learned 2026-04-07")
```

---

### Module 2: US Economic Indicators (FRED/EIA Macro Layer)

PAI's USMetrics pack fetches 68 indicators from FRED, EIA, Treasury, BLS, and Census. AlgoChains currently uses Polygon for market data and Databento for tick data, but has **no systematic macro economic layer**. This is a critical trading signal gap — especially for:
- **CL bot:** EIA weekly crude oil inventory reports are the single biggest crude oil mover
- **MNQ/NQ bot:** CPI, Fed Funds Rate, PCE drive tech-heavy index movements
- **All bots:** VIX, 10Y-2Y yield spread signal regime changes

**What to build:**
- `algochains-mcp-server/src/algochains_mcp/us_economics.py`
- 3 MCP tools: `get_us_economic_indicators`, `get_crude_oil_inventories`, `get_fed_policy_signals`

**Key FRED Series for AlgoChains:**
```python
FRED_SERIES = {
    # Rates & Monetary Policy
    "FEDFUNDS": "Federal Funds Effective Rate",
    "T10Y2Y": "10Y-2Y Treasury Spread (recession signal)",
    "GS10": "10-Year Treasury Yield",
    "GS2": "2-Year Treasury Yield",
    "M2SL": "M2 Money Supply",
    # Inflation (moves MNQ/NQ)
    "CPIAUCSL": "CPI All Urban Consumers",
    "PCEPI": "PCE Price Index",
    "CPILFESL": "Core CPI (ex-food/energy)",
    # Labor (monthly economic regime)
    "UNRATE": "Unemployment Rate",
    "IC4WSA": "Initial Jobless Claims (weekly)",
    # Growth
    "GDPC1": "Real GDP (quarterly)",
    "INDPRO": "Industrial Production Index",
    # Volatility & Sentiment
    "VIXCLS": "VIX Close (our gate is 35)",
    "UMCSENT": "University of Michigan Consumer Sentiment",
}

EIA_SERIES = {
    "PET.WCRSTUS1.W": "US Crude Oil Stocks (weekly — critical for CL)",
    "PET.WCSSTUS1.W": "Cushing Oklahoma Crude Stocks",
    "PET.WCRFPUS2.W": "US Crude Oil Production",
    "PET.WDIIMUS2.W": "US Crude Imports",
}
```

**Environment Variables Required:**
```bash
FRED_API_KEY=<from https://fred.stlouisfed.org/docs/api/api_key.html>
EIA_API_KEY=<from https://www.eia.gov/opendata/register.php>
```
Both APIs are **free** — just register.

---

### Module 3: Learning Signals (Continuous Improvement)

PAI captures ratings, sentiment, and success/failure for every interaction to improve the system over time. AlgoChains has no equivalent — we run agents and throw away outcome feedback.

**What to build:**
- `algochains-mcp-server/src/algochains_mcp/learning_signals.py` — JSONL append log
- 2 MCP tools: `capture_learning_signal`, `get_learning_signals`

**Signal Schema:**
```json
{
  "timestamp": "2026-04-07T10:30:00Z",
  "agent": "cursor",
  "action_type": "bot_diagnosis",
  "action_description": "Diagnosed MNQ no-signal issue",
  "outcome": "success",
  "rating": 9,
  "notes": "Volume threshold fix resolved 4-hour signal drought",
  "skill_used": "bot-diagnostics",
  "bot": "MNQ",
  "session_id": "abc123"
}
```

**Usage:**
```python
capture_learning_signal(
    action_type="strategy_change",
    action_description="Lowered MNQ volume threshold from 3.02x to 1.5x",
    outcome="success",
    rating=9,
    notes="Resolved signal drought, 8 trades next session"
)
```

---

### Module 4: ntfy Push Notifications (Mobile Alerts)

PAI uses ntfy (https://ntfy.sh) for mobile push notifications. AlgoChains only has Slack (#incident-response, #tradovate-futures-bot-changelog). ntfy adds:
- Instant mobile push (sub-second, no app install needed)
- Configurable priority levels (urgent for bot crashes, low for daily P&L)
- Tag-based routing to different devices/topics

**What to build:**
- `algochains-mcp-server/src/algochains_mcp/notifications/ntfy_push.py`
- 1 MCP tool: `send_ntfy_notification`

**AlgoChains ntfy Topics:**
```
algochains/bots          ← Bot up/down, trade events
algochains/risk          ← Circuit breaker, daily loss limit  
algochains/marketplace   ← New subscriber, bot promoted/demoted
algochains/ops           ← System health, deploy complete
```

**Environment Variables:**
```bash
NTFY_BASE_URL=https://ntfy.sh              # or self-hosted
NTFY_TOPIC_PREFIX=algochains               # your namespace
NTFY_AUTH_TOKEN=<optional for private topics>
```

---

## What to Skip (From PAI Packs)

| Pack | Why Skip |
|------|----------|
| **Thinking Pack** | We have Moltbook — full multi-agent debate with PostgreSQL, Redis, LangGraph. Far superior to PAI's council debates |
| **Agents Pack** | Our 472-skill library + Moltbook is more sophisticated |
| **ContentAnalysis** | Not relevant to futures trading |
| **Research Pack** | OpenClaw `deep-researcher` + Onyx RAG covers this |
| **Scraping Pack** | Databento + Polygon APIs are our data layer |
| **Security Pack** | Our `trading_guardrails.py` + circuit breakers are purpose-built for trading |
| **Investigation Pack** | OSINT not relevant to our business |
| **Media Pack** | Not relevant |
| **ContextSearch** | Onyx knowledge brain covers this with semantic search |
| **Voice System** | ElevenLabs TTS is nice but Slack is sufficient for trading ops |
| **Algorithm v3.7.0 as rigid protocol** | PAI's algorithm is designed for creative tasks. Our trading decision-making is better served by Moltbook's specialized debate engine |

---

## Architecture: Where Things Live

```
algochains-control-tower/
└── TELOS/                          ← Business identity (new)
    ├── MISSION.md
    ├── GOALS.md
    ├── STRATEGIES.md
    ├── MODELS.md
    ├── LEARNED.md
    ├── IDEAS.md
    ├── CHALLENGES.md
    └── METRICS.md

algochains-mcp-server/
└── src/algochains_mcp/
    ├── telos.py                    ← TELOS reader/writer (new)
    ├── us_economics.py             ← FRED/EIA macro data (new)
    ├── learning_signals.py         ← Outcome capture (new)
    └── notifications/
        └── ntfy_push.py            ← Mobile push (new)

state/
├── learning_signals.jsonl          ← Append-only learning log
└── us_metrics_cache.json           ← FRED/EIA cache (24h TTL)
```

---

## MCP Tools Summary (8 New)

| Tool | Module | Auth | Tier |
|------|--------|------|------|
| `get_algochains_telos` | telos.py | None | READ_ONLY |
| `update_algochains_telos` | telos.py | None | WRITE_SAFE |
| `get_us_economic_indicators` | us_economics.py | FRED_API_KEY | READ_EXTERNAL |
| `get_crude_oil_inventories` | us_economics.py | EIA_API_KEY | READ_EXTERNAL |
| `get_fed_policy_signals` | us_economics.py | FRED_API_KEY | READ_EXTERNAL |
| `capture_learning_signal` | learning_signals.py | None | WRITE_SAFE |
| `get_learning_signals` | learning_signals.py | None | READ_ONLY |
| `send_ntfy_notification` | ntfy_push.py | NTFY_AUTH_TOKEN | WRITE_SAFE |

---

## Implementation Sequence

1. **Create TELOS files** — No dependencies; immediate business value
2. **telos.py + MCP tools** — Depends on TELOS files
3. **us_economics.py + MCP tools** — Depends on FRED_API_KEY / EIA_API_KEY env vars
4. **learning_signals.py + MCP tools** — No dependencies
5. **ntfy_push.py + MCP tool** — Depends on NTFY_BASE_URL env var
6. **server.py integration** — Wire all 8 new tools
7. **Update BYOK registry** — Add FRED, EIA, ntfy providers
8. **Update README** — Document PAI Integration section

---

## Business Value Projection

| Integration | Immediate Value |
|-------------|-----------------|
| TELOS | Every AI agent (Cursor, Claude, Windsurf, OpenClaw) finally understands AlgoChains' mission, goals, and current challenges without re-explaining every session |
| FRED/EIA Indicators | CL bot can check EIA crude inventory report before trading; all bots get real-time macro regime signals; VIX < 35 gate gets context |
| Learning Signals | After 30 days of capture, can identify which skills/tools have highest success rates; drive skill improvement prioritization |
| ntfy Push | Tyler gets instant mobile push when any bot crashes, daily loss limit hit, or new marketplace subscriber — without opening Slack |

---

## Reference Links

- [PAI Repository](https://github.com/danielmiessler/Personal_AI_Infrastructure) — Main source
- [PAI Algorithm v3.7.0](https://github.com/danielmiessler/Personal_AI_Infrastructure/blob/main/Releases/v4.0.3/.claude/PAI/Algorithm/v3.7.0.md)
- [USMetrics Pack](https://github.com/danielmiessler/Personal_AI_Infrastructure/tree/main/Packs/USMetrics)
- [TELOS Pack](https://github.com/danielmiessler/Personal_AI_Infrastructure/tree/main/Packs/Telos)
- [FRED API](https://fred.stlouisfed.org/docs/api/fred/) — Free economic data
- [EIA API](https://www.eia.gov/opendata/) — Free energy data
- [ntfy](https://ntfy.sh) — Free push notification service
