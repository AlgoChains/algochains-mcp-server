<!--
BLUEPRINT_STATUS: archived
ARCHIVED_DATE: 2026-04-18
SUPERSEDED_BY: docs/MCP_SERVER_ENHANCEMENT_ROADMAP.md (V2 P4-16)
NOTE: The Tier-1 tool table and Skills Bridge delivery webhook are stale vs actual
server.py TIER1_TOOL_NAMES. The marketplace delivery loop described here is tracked
as P4-16 in the V2 roadmap. Do not treat this document as a current spec.
-->

# AlgoChains Skills Bridge Blueprint
**Version:** 1.0 | **Date:** 2026-04-07 | **Author:** AI Audit + Tyler Reynolds

---

## The Mega Prompt

> "Expose all AlgoChains skills (363 OpenClaw, 80 Windsurf, 15 Cursor) as first-class MCP tools so any agentic AI — Cursor, Claude Code, Windsurf, custom agent — can discover, read, and invoke the full skill library without switching contexts. Bridge skill execution to the same Onyx intel brain that indexes all research, blueprints, bot logs, and Slack history. No synthetic data. Fail closed if sources are unavailable. Update all readmes when done."

---

## Gap Analysis vs Best-in-Class

### What AlgoChains MCP Has Today
| Category | Status |
|----------|--------|
| Onyx search/ask/health/ingest | ✅ Implemented |
| Bot diagnostics, deploy, health audit | ✅ Via skills in agent context |
| Prediction markets (Polymarket, Kalshi) | ✅ Implemented |
| MCPT pipeline ops | ✅ Via script calls |
| Skills discovery | ❌ **Missing** |
| Unified skill invocation from MCP | ❌ **Missing** |
| OpenClaw memory read/write via MCP | ❌ **Missing** |
| Agent crew orchestration via MCP | ❌ **Missing** |
| Real-time skill telemetry | ❌ **Missing** |
| Cross-platform skill catalog (Windsurf + OpenClaw + Cursor) | ❌ **Missing** |
| Onyx connector for skills directories | ⚠️ Partial (skills indexed but stale) |

### Best-in-Class Reference (2026 Agentic AI Platforms)
| Pattern | Example Platform | Gap |
|---------|----------------|-----|
| Unified skill registry API | LangChain Tool Hub, Anthropic Tools | Skills not queryable from MCP |
| Dynamic tool discovery | OpenAI Swarm, AutoGPT | Agent can't ask "what can I do?" |
| Agent memory persistence | Mem0, MemGPT | OpenClaw memory not MCP-accessible |
| Cross-agent skill handoff | CrewAI, AutoGen | crew-orchestrator not exposed |
| Self-healing orchestration | OpenDevin, SWE-agent | self-healing-orchestrator not wired |

---

## What to Build

### Module 1: `skills_registry.py` (NEW)
Loads and indexes all SKILL.md files from three roots:
- `.windsurf/skills/` — 80+ Windsurf skills
- `~/.openclaw/skills/` — 363 OpenClaw skills  
- `~/.claude/skills/` — 8 Claude skills
- `~/.cursor/skills-cursor/` — 15 Cursor skills

Provides:
- `list_skills(category, platform)` → paginated index of all skills with name, description, trigger, tools
- `get_skill_detail(name)` → full SKILL.md content
- `search_skills(query)` → fuzzy search across all skill descriptions
- `get_skills_for_task(task_description)` → semantic match to skill descriptions

### Module 2: `agent_memory.py` (NEW)
Bridges OpenClaw memory system to MCP:
- `get_openclaw_memory(key_prefix)` → query `~/.openclaw/memory.json`
- `store_trade_lesson(lesson_dict)` → write to trade lesson memory
- `get_agent_evaluations()` → read `~/.openclaw/agent_evaluations.json`
- `get_bot_heartbeat()` → read `~/.openclaw/bot_heartbeat.json`
- `get_current_regime()` → read `~/.openclaw/current_regime.json`

### Module 3: Top-10 Skill Direct MCP Tools (NEW in server.py)
High-frequency skills get first-class MCP tools (not just skill text):

| MCP Tool | Backed By | Category |
|----------|-----------|----------|
| `invoke_debate` | moltbook-debate skill + moltbook/debate_engine.py | Trading |
| `run_autonomous_research` | autonomous-researcher skill | Research |
| `run_mcpt_pipeline` | mcpt-pipeline-ops skill + mcpt_autopilot.py | Operations |
| `check_circuit_breaker` | circuit-breaker skill + trading_guardrails.py | Risk |
| `run_regime_detection` | regime-detector + vix-regime-selector skills | Alpha |
| `scan_unusual_options` | unusual-options-alert skill | Alpha |
| `scan_dark_pool` | dark-pool-activity-scan skill | Alpha |
| `check_congressional_trades` | congressional-insider-scan skill | Alpha |
| `run_skill` | Generic skill executor | Meta |
| `get_openclaw_memory` | OpenClaw memory bridge | Memory |

### Module 4: Onyx Connector Update
Ensure Onyx indexes:
- `~/.openclaw/skills/` (363 skills)
- `.windsurf/skills/` (80 skills)
- `.claude/skills/` (8 skills)  
- `~/.openclaw/memory.json`
- `~/.openclaw/agent_evaluations.json`
- `~/CascadeProjects/algochains-mcp-server/blueprints/`

---

## File Map

```
algochains-mcp-server/
├── src/algochains_mcp/
│   ├── skills_registry.py        NEW — skill catalog + search
│   ├── agent_memory.py           NEW — OpenClaw memory bridge  
│   └── server.py                 UPDATE — add 12 new tools
├── config/
│   └── onyx_connectors.json      UPDATE — add skills dirs
├── README.md                     UPDATE — skills section
└── blueprints/
    └── SKILLS_BRIDGE_BLUEPRINT.md THIS FILE
```

---

## MCP Tool Additions Summary

```
list_skills           — discover all available skills (paginated)
get_skill_detail      — read full SKILL.md for a skill
search_skills         — fuzzy/semantic search across skill library
get_skills_for_task   — match task description to best skill
invoke_debate         — trigger Moltbook bull/bear debate  
run_autonomous_research — queue a research hypothesis run
run_mcpt_pipeline     — run MCPT autopilot steps
check_circuit_breaker — query hard risk limits + current state
run_regime_detection  — run current market regime analysis
scan_unusual_options  — scan for unusual options flow
scan_dark_pool        — scan dark pool activity
check_congressional_trades — scan congressional trading data
run_skill             — generic skill executor by name
get_openclaw_memory   — read OpenClaw memory/state files
store_trade_lesson    — persist trade lesson to OpenClaw memory
get_current_regime    — read current market regime from OpenClaw
```

---

## Onyx Knowledge Brain Update

Onyx at `100.89.114.31:8085` needs to index these additional paths:

| Path | Contents | Priority |
|------|----------|----------|
| `~/.openclaw/skills/` | 363 skills SKILL.md files | HIGH |
| `~/.openclaw/memory.json` | Agent memory state | HIGH |
| `~/.openclaw/current_regime.json` | Current regime | HIGH |
| `.windsurf/skills/` | 80 Windsurf skills | HIGH |
| `~/.openclaw/agent_evaluations.json` | Agent performance | MEDIUM |
| `~/.openclaw/calibration_history.json` | Model calibrations | MEDIUM |
| `algochains-mcp-server/blueprints/` | All blueprints | HIGH |

---

## Validation Checklist

- [ ] `list_skills()` returns at least 363 skills
- [ ] `get_skill_detail("moltbook-debate")` returns full SKILL.md
- [ ] `search_skills("regime")` returns regime-related skills
- [ ] `get_openclaw_memory("bot_heartbeat")` returns real heartbeat data
- [ ] `invoke_debate` calls real moltbook engine or fails closed
- [ ] `run_mcpt_pipeline` calls real `mcpt_autopilot.py` or fails closed
- [ ] Onyx connector updated with skills dirs
- [ ] All 16 new tools appear in MCP tool list
- [ ] README updated with skills section
