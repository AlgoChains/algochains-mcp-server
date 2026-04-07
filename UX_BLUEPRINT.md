# AlgoChains MCP Server — UX Gap Analysis & Blueprint
**Date:** 2026-04-06 | **Author:** AI System Audit | **Version:** V22.4

---

## Executive Summary

This document identifies 14 concrete UX gaps in the AlgoChains MCP Server and prescribes actionable fixes. The primary context driving this audit is the following real feedback:

> *"I honestly do not understand the MCP server in the context of the big picture. I'm happy to learn your thought process and vision you have for it."*
> — Roo Fernando, Co-Founder

> *"Giving LLM direct access to brokerage accounts that are live is clearly a no, if issues happen there is no fixing it, the actions are reflected in the live account which are irreversible."*
> — Roo Fernando

These are exactly the right concerns from someone seeing this for the first time. Every gap below traces back to the same root cause: **the server was built by and for Tyler (expert user) but the README and onboarding were never designed for a first-time audience.**

---

## Gap 1 — The README Does Not Explain What MCP Is

**Current state:** The README opens with "The institutional-grade Model Context Protocol server for autonomous trading systems." This means nothing to someone who doesn't know what MCP is.

**Impact:** Roo read the README and still doesn't understand the server. If a co-founder can't extract the concept in 2 minutes, no external user will.

**Fix:** Add a 5-line plain-English explanation at the very top, before any technical content, that answers:
1. What is MCP?
2. What does the server actually do?
3. What does a user actually experience?
4. What are the safety guarantees?

**Implemented in:** `README.md` rewrite (this sprint)

---

## Gap 2 — The Quickstart Opens With Live Broker Credentials

**Current state:** The 60-second quickstart immediately asks for `TRADOVATE_USERNAME` and live broker credentials. A new user following this guide will configure live account access before understanding the safety model.

**Impact:** Roo's concern is exactly this — irreversible actions on live accounts. The quickstart should default to paper trading mode.

**Fix:** Rewrite quickstart with three tiers:
1. **Demo mode** — no credentials, reads public market data only (get_quote, get_macro_signals, detect_market_regime)
2. **Paper mode** — Alpaca paper account only (free, risk-free, Alpaca paper is free to sign up)
3. **Live mode** — requires reading SAFETY_MODEL.md, explicitly stating "I understand live broker access is configured"

**Implemented in:** `scripts/quickstart.py` (this sprint)

---

## Gap 3 — 338 Tools With No Progressive Disclosure

**Current state:** The README lists all 338 tools across 18 categories. This is overwhelming for any new user. There's no concept of "start here."

**Impact:** Analysis paralysis. Users can't figure out what to do first.

**Fix:** Introduce a Tier-0 "starter pack" — 12 tools that cover 80% of first-time use cases:

| Tool | What It Does | Risk Level |
|------|-------------|------------|
| `get_quote` | Get live price for any symbol | SAFE |
| `detect_market_regime` | Is the market trending or ranging? | SAFE |
| `get_macro_signals` | Macro risk-on/off state | SAFE |
| `get_positions` | What do I currently hold? | SAFE |
| `get_account` | Account balance and buying power | SAFE |
| `run_backtest` | Test a strategy on historical data | SAFE |
| `validate_strategy` | Check if strategy passes quality gates | SAFE |
| `onyx_ask` | Ask your knowledge base in plain English | SAFE |
| `discover_tools` | Find the right tool for a task | SAFE |
| `get_live_bot_metrics` | Real-time bot performance | SAFE (read-only) |
| `place_order` | Execute a trade | ⚠️ LIVE MONEY |
| `flatten_all_positions` | Close everything | ⚠️ LIVE MONEY |

**Implemented in:** README rewrite + `http_bridge.py` danger tier API

---

## Gap 4 — No Danger Tier Classification on Tools

**Current state:** The HTTP bridge `/tools` endpoint returns a flat list of tool names. There's no machine-readable signal for which tools are read-only vs. order-executing vs. destructive.

**Impact:** An AI agent (or a developer integrating the bridge) has no way to know that `get_quote` is safe but `flatten_all_positions` is irreversible without reading the full documentation.

**Fix:** Add `danger_tier` to every tool in the API response:
- `0` — Read-only, no side effects (get_quote, detect_market_regime)
- `1` — Writes to internal state only (validate_strategy, create_alert)
- `2` — Executes real orders (place_order, modify_order)
- `3` — Irreversible bulk actions (flatten_all_positions, cancel_all_orders)

**Implemented in:** `http_bridge.py` + `tool_danger_tiers.py` (this sprint)

---

## Gap 5 — No "What Can Go Wrong" Section

**Current state:** The README is entirely promotional ("institutional-grade", "Holy Fuck Factor"). There is no honest description of failure modes, risks, or things that can go wrong.

**Impact:** Roo correctly identified that live broker actions are irreversible. A user needs to know: "What happens if the AI makes a wrong call?" before they connect a live account.

**Fix:** Add an explicit "Failure Modes & Mitigations" section to README and SAFETY_MODEL.md covering:
- AI sends wrong order → hard-coded circuit breakers, elicitation confirmation required
- Bot crashes mid-trade → open position is NOT auto-closed, monitor via Tradovate app
- Token expires during session → Token Guardian renews it, 30min window
- Daily loss limit hit → all orders blocked until midnight reset
- Max drawdown hit → all orders blocked, requires manual reset

**Implemented in:** `SAFETY_MODEL.md` (this sprint)

---

## Gap 6 — No Team Sharing Story

**Current state:** The README is written as a single-user setup. There is no documentation for how a 5-person team (Tyler, Roo, Eric, RJ, +1) can share the same MCP server instance without stepping on each other or sharing live trading credentials.

**Impact:** The team is currently siloed. Roo and Eric can't access Tyler's knowledge base or bot metrics without separate setup.

**Fix:** Add `TEAM_GUIDE.md` section in README + a separate doc covering:
1. **Read-only team access** — Roo/Eric/RJ can call safe tools (market data, bot metrics) without broker credentials
2. **Shared Onyx knowledge base** — one Onyx instance, all team members can search
3. **Team API key setup** — owner API key stays with Tyler, team gets a read-only key
4. **Shared MCP config** — one `mcporter.json` per team role (read-only, trading, admin)

**Implemented in:** `SAFETY_MODEL.md` team section + template configs (this sprint)

---

## Gap 7 — Marketplace Creator Workflow Is Undocumented

**Current state:** Roo explicitly said he will "get you a good document md for making your bots available in the marketplace." This means the flow for a bot creator to submit, gate, and publish is not clear from any existing documentation.

**Impact:** No one on the team knows how to actually get a bot from backtest → marketplace listing → subscriber.

**Fix:** Write `MARKETPLACE_CREATOR_GUIDE.md` covering the exact 7-step flow:
1. Build strategy spec
2. Run validation gates (Sharpe > 2.0, Win Rate > 55%, MaxDD < 15%)
3. Run MCPT (deflated Sharpe test)
4. Submit to marketplace (staging)
5. Set subscription price
6. Monitor subscriber metrics
7. Handle decay → auto-delist

**Implemented in:** `MARKETPLACE_CREATOR_GUIDE.md` (this sprint)

---

## Gap 8 — Onboarding Module Exists But Isn't Linked From README

**Current state:** `onboarding.py` exists with a full compliance-gated wizard. The README does not mention it exists. Users are told to set `export TRADOVATE_USERNAME=...` manually.

**Impact:** New users skip the guided flow and bypass the compliance disclosures.

**Fix:** Make onboarding the primary entry point. The 60-second quickstart should call `start_onboarding()` first, not raw environment variable export.

**Implemented in:** README rewrite (this sprint)

---

## Gap 9 — No "Paper Mode" Indicator in API Responses

**Current state:** When you call `get_account`, the response includes `paper: true/false` but this is buried in the JSON. There's no top-level "you are in PAPER mode" banner in any tool response.

**Impact:** A user connecting for the first time doesn't know if they're in paper or live mode without reading the JSON carefully.

**Fix:** Add a session-level `mode` field to all order-related tool responses:
```json
{
  "_mode": "PAPER",
  "_warning": "This is a paper account. No real money at risk.",
  "result": { ... }
}
```

**Implemented in:** `server.py` response wrapper (this sprint)

---

## Gap 10 — Error Messages Are Not Actionable for New Users

**Current state:** When a tool fails because a required env var is missing, the error is:
`BrokerNotConfiguredError: Broker 'tradovate' is not configured. Set environment variables.`

**Impact:** "Set environment variables" is not helpful. Which ones? Where? How?

**Fix:** Every `BrokerNotConfiguredError` should include a direct link to the relevant configuration section:
```
Broker 'tradovate' not configured.
Required env vars:
  TRADOVATE_USERNAME  — your Tradovate login email
  TRADOVATE_PASSWORD  — your Tradovate login password
  TRADOVATE_APP_ID    — found at: https://trader.tradovate.com/account
  TRADOVATE_APP_SECRET — same location as APP_ID

Run: python scripts/quickstart.py --broker tradovate
  to configure and test your connection interactively.
```

**Implemented in:** `config.py` error enrichment (this sprint)

---

## Gap 11 — No Shared Knowledge Base Vision Articulated

**Current state:** Tyler mentioned "all of our LLMs should be interconnected somehow — same knowledge base." This is already technically possible with the current Onyx setup, but there's no documentation for how to achieve it.

**Impact:** The team is siloed. Tyler's Claude, Roo's Claude, Eric's Cursor all have different context.

**Fix:** Document the "AlgoChains Shared Brain" architecture:
- One Onyx instance (on desktop, accessible via Tailscale)
- All team members' AI tools point at the same `ONYX_API_URL`
- All research, blueprints, decisions are ingested automatically via `connect_onyx_docs`
- Any team member can ask "What did Tyler decide about the CL bot?" and get a real answer

**Implemented in:** `SAFETY_MODEL.md` team section (this sprint)

---

## Gap 12 — No Clear Answer to "What Happens to My Money If This Breaks"

**Current state:** Nowhere in the docs is there an explicit answer to: what happens if the AI goes crazy or the server crashes mid-trade?

**Fix:** Add explicit answers:
- AI goes in a loop → AI Loop Detector trips after 5 identical calls, blocks all orders for 30min
- Server crashes mid-trade → The OPEN POSITION REMAINS. It is NOT auto-closed. Check your broker app.
- Token expires → Orders cannot be placed. Existing positions are unaffected.
- Daily loss limit hit → All new orders blocked. Existing positions are unaffected.

**Implemented in:** `SAFETY_MODEL.md` (this sprint)

---

## Gap 13 — The Quickstart Has No Verification Step

**Current state:** The quickstart says "ask Claude: What's my current MNQ position P&L?" — but there's no way to verify the server is actually running correctly before you do this.

**Fix:** Add `scripts/quickstart.py --health-check` that validates:
- Can import all modules
- Required env vars are set
- Broker connectivity (if credentials present)
- HTTP bridge is reachable
- Token is valid (for Tradovate)

**Implemented in:** `scripts/quickstart.py` (this sprint)

---

## Gap 14 — No IDE Config Generator

**Current state:** Users manually write JSON for Claude Desktop config, Cursor MCP settings, etc.

**Fix:** `quickstart.py` generates the correct config for the user's IDE:
```bash
python scripts/quickstart.py --generate-config cursor
python scripts/quickstart.py --generate-config claude-desktop
python scripts/quickstart.py --generate-config windsurf
```

**Implemented in:** `scripts/quickstart.py` (this sprint)

---

## Build Plan Summary

| Deliverable | Priority | Status |
|-------------|----------|--------|
| `README.md` — complete rewrite | CRITICAL | Build this sprint |
| `scripts/quickstart.py` — setup wizard | HIGH | Build this sprint |
| `SAFETY_MODEL.md` — safety + team guide | HIGH | Build this sprint |
| `MARKETPLACE_CREATOR_GUIDE.md` — Roo's doc | HIGH | Build this sprint |
| `tool_danger_tiers.py` + bridge endpoint | MEDIUM | Build this sprint |
| Error message enrichment in `config.py` | MEDIUM | Build this sprint |
| Paper/live mode banner in responses | MEDIUM | Build this sprint |

---

## Metrics for Success

When these gaps are fixed, a new team member should be able to:
1. Read the README and explain in 2 sentences what the MCP server does ✓
2. Run `quickstart.py` and have a working paper connection in < 5 minutes ✓
3. Call `get_quote("AAPL")` without any broker credentials ✓
4. Understand which tools are safe vs. which touch real money ✓
5. Know exactly what happens if something goes wrong ✓
6. Submit a bot to the marketplace without asking Tyler ✓
