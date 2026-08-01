# Live-Ops Tool Routing (Agents & IDEs)

> **Audience:** Any agent or IDE connected to `algochains-mcp-server` (Cursor, Claude, ChatGPT, OpenClaw).
> **Authority:** Broker REST / MCP live tools = `broker_truth` for positions, orders, quotes, and portfolio equity.
> Graphiti / Onyx / memory = `agent_memory` (advisory only). Never invent fills or P&L.

---

## Hard rule

**Never use web search, scraped CME/Yahoo quotes, or chat memory for AlgoChains live ops.**

If the user asks about bot health, session P&L, flatness, brackets, working orders, or a live quote,
call the matching MCP tool below. If the tool fails, say so and fail closed — do not guess.

| User intent (examples) | Call this tool | Do **not** call |
|------------------------|----------------|-----------------|
| “MNQ health check”, “is the bot running”, “bot status” | `get_bot_health` | web search, `get_quote` alone |
| “today’s P&L”, “how did we do”, “am I up” (owner/broker) | `portfolio_summary` | web search, memory |
| “paper P&L today” (subscriber) | `get_my_pnl` / `get_my_portfolio` | inventing numbers |
| “am I flat”, “open positions”, “exposure” | `get_positions` | web search, `get_bot_health` alone |
| “unprotected?”, “do I have stops?”, “bracket integrity” | `check_unprotected_positions` (+ `bracket_integrity_check`) | web search |
| “working / pending orders” | `get_orders` | web search |
| “MNQ price **right now**” / live quote | `get_quote` | web search, news scrapers |
| “MNQ bars last 30 days” / historical OHLCV | data tools (`massive_query_data` / backtest path) | `get_quote` alone |
| “MNQ **news**” / geopolitics / headlines | web / news tools (if available) | `get_bot_health` |
| “what regime are we in”, “VIX term structure” | `get_current_regime` / `get_vix_term_structure` | inventing regime labels |

---

## Persona split (critical)

| Persona | How they auth | Live P&L / positions tools |
|---------|---------------|----------------------------|
| **Subscriber** (hosted paper) | `ALGOCHAINS_SUBSCRIBER_KEY=sub_live_…` | `get_my_portfolio`, `get_my_pnl`, `get_my_fills`, `get_my_paper_positions` |
| **Owner / ops** (local control-tower + broker) | Broker env + optional `OWNER_API_TOKEN` | `portfolio_summary`, `get_positions`, `get_orders`, `get_bot_health`, bracket tools |
| **Developer** (`ac_live_*`) | Developer API key | Read-scoped tools only — never treat as owner |

Default new users to the **subscriber paper path** (`get_started`) unless they explicitly ask for live/broker.

---

## P&L authority (ghost P&L class)

1. **Unrealized** open P&L on a position ≠ **realized** session result.
2. Prefer broker/`portfolio_summary` (owner) or `get_my_pnl` (subscriber).
3. Never report `open_pnl_dollars` / estimated tick-math as a confirmed win.
4. On the control-tower host, `scripts/check_trade_accuracy_v2.py` remains the multi-source verifier for owner session P&L.

---

## `get_bot_health` vs market price

- **`get_bot_health`** → process up?, log age, signal health, e2e sentinel — **AlgoChains bots**, not CME quotes.
- **`get_quote`** → bid/ask/last from the connected broker for a symbol.
- Asking “MNQ health” after a quiet session is almost always **bot health**, not “search the web for MNQ”.

Requires local control-tower paths (`ALGOCHAINS_CONTROL_TOWER`) or a bridge that can read them.

---

## Related docs

- [AGENTS.md](../AGENTS.md) — safety tiers + domain map
- [docs/SUBSCRIBER_TOOLS.md](SUBSCRIBER_TOOLS.md) — subscriber-only surface
- [docs/MCP_TRACEABILITY_CONTRACT.md](MCP_TRACEABILITY_CONTRACT.md) — authority labels
- Control-tower OpenClaw routing (operators): `core/openclaw_live_ops_routing.py` (ADR-OPENCLAW-LIVE-OPS-ROUTING-MATRIX-001)
