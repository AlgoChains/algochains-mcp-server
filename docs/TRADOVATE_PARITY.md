# Tradovate API Parity Reference

This document maps the [official Tradovate REST API](https://api.tradovate.com/) and the community
[mcp-tradovate Go server](https://github.com/0xjmp/mcp-tradovate) surface to the AlgoChains
implementation. Use this when evaluating whether to pull in external tooling or extend the stack.

**Bottom line:** AlgoChains already covers and exceeds the community MCP surface. Do NOT run
`mcp-tradovate` alongside AlgoChains for live trading — it creates a split-brain order/auth path
that can break Token Guardian and the live bots' WebSocket connections. See the governance rule
at the end of this doc.

---

## Tool / API surface comparison

| Tradovate REST resource | mcp-tradovate tool | AlgoChains tool or module | Notes |
|-------------------------|--------------------|---------------------------|-------|
| `POST /auth/accesstokenrequest` | `authenticate` | Token Guardian (`tradovate_token_guardian.py`) | AlgoChains uses Token Guardian + shared `tradovate_token_live.txt`. Never call a second ad-hoc auth path alongside live bots. |
| `GET /account/list` | `get_accounts` | `get_positions` (server.py) / `get_account_info()` in `moltbook/tradovate_mcp_server.py` | Full account struct returned. |
| `GET /position/list` | `get_positions` | `get_positions` MCP tool; `TradovateConnector.get_positions()` in `brokers/tradovate.py` | Batch contract resolution (avoids N+1 HTTP). |
| `GET /order/list` | — | `get_working_orders` (server.py); `TradovateConnector.get_orders()` | Working + bracket orders. |
| `POST /order/placeOrder` | `place_order` | `place_order` MCP tool (via broker registry, guardrailed) | Safety model enforced: $500/day hard limit, VIX gate, loop detection, owner token required for live. Bots own primary execution. |
| `DELETE /order/cancelOrder` | `cancel_order` | `TradovateConnector.cancel_order()` | Exposed via broker registry cancel flow. |
| `GET /fill/list` (by order) | `get_fills` | `TradovateConnector.get_fills()`; `pull_tradovate_fills_for_strategy` (server.py) | Real fills only — FIFO-matched for strategy eval. |
| `GET /contract/find` | `get_contracts` | `TradovateConnector._find_contract()` (internal); `get_quote` resolves contract first | No dedicated `get_contracts` MCP tool but lookup happens automatically. |
| `GET /contract/suggest` | — | Not exposed as standalone tool; add if symbol-search UX is needed | Low priority. |
| `GET /md/getQuote` | `get_market_data` | `get_quote` MCP tool; `TradovateConnector.get_quote()` | Live bid/ask/last/volume. |
| `GET /md/getChart` | `get_historical_data` | `TradovateConnector.get_historical()` (1m/5m/15m/30m/1h/4h/1d); `pull_tradovate_fills_for_strategy` for P&L series | Called internally from backtest and strategy tools. |
| Risk limits (GET/POST) | `get_risk_limits` / `set_risk_limits` | Not a named MCP tool; prop-fund limits managed via `brokers/prop_fund_manager.py` | Tradovate /riskLimit endpoint is read-only diagnostic — add `get_tradovate_risk_limits` tool if needed (see Gap Opportunities below). |
| Bracket orders | — | `bracket_integrity_check` MCP tool; `flatten_position_tradovate` (bot_ops.py) | Bracket monitoring is a primary AlgoChains differentiator not in mcp-tradovate at all. |
| Bot process health | — | `get_bot_metrics`, `check_bot_process`, `check_bracket_integrity` | Unique to AlgoChains — no equivalent in mcp-tradovate. |
| WebSocket streaming | — | Bots own live WS (`tradovate_websocket_client.py`); MCP is REST-only by design | Intentional: never open a second WebSocket from MCP — bots own the connection. |

---

## Gap opportunities (low priority, implement inside AlgoChains only)

These are legitimate parity gaps that are worth closing if you need the functionality.
All implementations must go through `TradovateConnector` (or `tradovate_client.py`) — never
create a second OAuth session.

### 1. `search_tradovate_contracts` (read-only, Tier 0)

Expose `/contract/suggest` as a searchable tool so agents can discover available futures symbols
without needing to know exact Tradovate contract names.

```python
# In server.py, add to tool list:
Tool(
    name="search_tradovate_contracts",
    description="Search Tradovate contracts by keyword. Returns contract name, id, "
                "and description. Use before place_order to confirm the exact symbol.",
    inputSchema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Symbol or keyword, e.g. 'MNQ' or 'Nasdaq'}
        },
        "required": ["query"],
    },
    annotations=ANNOT_READ_EXTERNAL,
)

# Handler: calls TradovateConnector._get("/contract/suggest", {"t": query, "l": 10})
```

### 2. `get_tradovate_risk_snapshot` (read-only, Tier 0)

Read-only view of the Tradovate risk limit settings per account — useful for confirming
prop-fund guardrails are in place without modifying anything.

```python
# Calls GET /riskLimit/list filtered by account_id
# Returns: dayMaxLoss, maxDrawdown, maxOrderQty, trailingMaxDrawdown
```

**Important:** Never expose `set_risk_limits` as an MCP tool — risk parameter changes require
human review and must not be AI-callable. This aligns with the existing safety model.

### 3. `get_tradovate_fills` (read-only, Tier 0)

Thin wrapper exposing `TradovateConnector.get_fills()` directly as an MCP tool for ad-hoc fill
inspection without requiring the full `pull_tradovate_fills_for_strategy` pipeline.

---

## Governance: do NOT run mcp-tradovate alongside AlgoChains

The `mcp-tradovate` Go server (`github.com/0xjmp/mcp-tradovate`) is useful as a reference
implementation but must **not** be configured as a concurrent MCP server in production Cursor /
Windsurf sessions alongside AlgoChains for the following reasons:

1. **Split auth:** `mcp-tradovate authenticate` creates an independent OAuth session. The live
   bots' WebSocket connections rely on a single token maintained by Token Guardian. A second auth
   cycle can invalidate or race with the bots' token, causing 401 errors mid-trade.

2. **Split order path:** Both MCPs expose `place_order`. An AI agent in the same session can
   silently route orders through either path, bypassing AlgoChains safety guardrails (daily loss
   limit, VIX gate, owner token verification, replay guard).

3. **No bracket awareness:** `mcp-tradovate` knows nothing about AlgoChains bracket tracking.
   If it cancels an order, the `bracket_integrity_check` state diverges silently.

**Safe use of mcp-tradovate:** Demo / sandbox only, with `TRADOVATE_ENV=demo`, no overlapping
tool names in the same agent session, and Token Guardian stopped on that machine.

---

## Auth architecture (how AlgoChains does it)

```
Token Guardian (launchd, every 5 min)
  └── writes tradovate_token_live.txt + .env TRADOVATE_ACCESS_TOKEN
         ↓
TradovateClient (tradovate_client.py)           ← used by live bots
TradovateConnector (brokers/tradovate.py)       ← used by MCP server
moltbook/tradovate_mcp_server.py                ← read-only MCP (local)
         ↓
All share the same credential — no second OAuth session
```

Official reference: [Tradovate REST API Getting Started](https://api.tradovate.com/#section/Getting-Started-With-the-Tradovate-API/How-Do-I-Use-the-Tradovate-REST-API)
