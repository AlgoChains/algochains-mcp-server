# MCP Traceability Contract

**Version:** v22 (2026-05-01)  
**Purpose:** Documents how MCP broker-order paths produce correlation IDs for joining to control-tower audit tables.

---

## Context

The control tower (algochains-control-tower) maintains audit tables:
- `trade_log` — canonical per-trade record keyed on `signal_id` (UUID)
- `bracket_audit` — bracket order lifecycle keyed on `signal_id`  
- `bot_events` — bot event stream keyed on `bot_name` + timestamp

The MCP server can also place orders (via `place_order`, `cancel_order`) — primarily used by ops tools and research, not the live bots. To allow post-hoc reconstruction of "which MCP call led to which broker order", a `client_trace_id` field is supported.

---

## Fields

### `client_trace_id` (optional, echoed)

| Field | Details |
|---|---|
| Where to send it | As `client_trace_id` in `place_order` or `cancel_order` `arguments` |
| What to put there | A `signal_id` UUID from a control-tower row, or any caller-generated UUID |
| What comes back | The same `client_trace_id` is echoed in the response JSON |
| Logged where | HTTP bridge: `X-Request-Id` header in every response; dispatch logs |
| Joinable to | `trade_log.signal_id` if you pass the same UUID used when creating that row |

### `X-Request-Id` (HTTP bridge header)

Every request through the HTTP bridge (`/api/mcp` endpoint) gets an `X-Request-Id` header:
- **Inbound:** if the caller provides `X-Request-Id`, it is preserved and reflected back
- **Generated:** if absent, the bridge generates a random 8-char hex ID
- **Logged:** the middleware logs `req_id`, `path`, `method`, `status`, `elapsed_ms`

---

## Recommended call pattern (ops/research use)

```python
import uuid

signal_id = str(uuid.uuid4())  # generate or reuse from trade_log row

response = requests.post(
    "https://your-mcp-bridge/api/mcp",
    headers={"X-Api-Key": OWNER_KEY, "X-Request-Id": signal_id},
    json={
        "tool": "place_order",
        "arguments": {
            "broker": "tradovate",
            "symbol": "MNQZ5",
            "side": "buy",
            "qty": 1,
            "confirm": True,            # required for ORDER_EXEC tier
            "client_trace_id": signal_id,  # echoed back in response
        }
    }
)
result = response.json()
# result["client_trace_id"] == signal_id  ← join key to trade_log
# response.headers["X-Request-Id"] == signal_id
```

---

## What is NOT in scope

- **Live bots do NOT go through the MCP server.** `FUTURES_SCALPER_UPGRADED.py`, `CL_FUTURES_SCALPER.py`, `mes_swing_live.py`, and `nq_swing_live.py` all write to `trade_log` / `bracket_audit` directly via `autonomous/trade_log_writer.py` and `autonomous/supabase_audit.py`. The MCP `place_order` path is for ops tooling.
- **Full OpenTelemetry propagation** across Python bots + MCP + Next.js is out of scope until multiple services share a single trace backend (see `docs/TRACEABILITY_SYSTEM_AUDIT_2026-05-01.md` Section 2 — 80% path).
- **Tick-level reconstruction** from MCP calls — use the existing tick archive and `scripts/tick_replay.py`.

---

## Audit matrix (MCP broker tools)

| Tool | Tier | client_trace_id | X-Request-Id | broker_order_id returned | joinable to trade_log |
|---|---|---|---|---|---|
| `place_order` | ORDER_EXEC | ✅ echoed in response | ✅ via bridge | ✅ order.id in response | ✅ if caller uses signal_id |
| `cancel_order` | ORDER_EXEC | ✅ echoed in response | ✅ via bridge | ✅ order_id echoed | ✅ if caller uses signal_id |
| `close_position` | ORDER_EXEC | ✅ echoed in response | ✅ via bridge | ✅ order returned | ✅ if caller uses signal_id |
| `get_positions` | READ_ONLY | n/a | ✅ via bridge | n/a | n/a |
| `get_orders` | READ_ONLY | n/a | ✅ via bridge | n/a | n/a |

---

## Gaps / future work

- Broker `order.id` returned by `place_order` should be stored in `trade_log.entry_order_id` by the caller if using MCP for ops order placement
- If MCP order volume grows, consider writing a `bot_events` row with `event="MCP_ORDER_PLACED"` and `detail={"client_trace_id":..., "order_id":...}` automatically

---

## `trade_log.exit_reason` code catalogue (updated 2026-06-10)

When querying `trade_log` via MCP or Supabase, use this catalogue to interpret `exit_reason` values. Codes written by `_async_upsert` are preserved verbatim (uppercase); codes written by `on_exit()` are lowercased.

### Normal trade exits (bracket lifecycle)
| Code | Meaning |
|---|---|
| `bracket_stop` | Stop-loss bracket order filled (normal stop-out) |
| `bracket_target` | Take-profit bracket order filled (normal win) |
| `bracket_stop_ws` | Stop filled via WebSocket fast-path |
| `bracket_target_ws` | Target filled via WebSocket fast-path |
| `daily_loss` | Daily max-loss enforcer triggered |
| `manual` | Operator or MCP tool manually closed position |
| `timeout` | Time-based exit (overnight hold, session end) |

### Abort exits — pre-fill (no broker position created)
| Code | Meaning |
|---|---|
| `T4_FAIL_CLOSED_ABORTED` | T4 price-source gate fired (no live market price) — order never placed |
| `PRE_SUBMIT_RR_ABORT` | Pre-submit R:R gate fired — signal geometry insufficient before OSO |
| `PRE_SUBMIT_FLOOR_RR_PROJECTED` | Pre-submit projected-floor R:R gate fired — current market slip would trigger stop floor, collapsing R:R below minimum |
| `slip_abort_ref_px` | Slip abort — market moved > ceiling ticks from signal entry before submit |

### Abort exits — post-fill (broker position was created and immediately closed)
| Code | Meaning |
|---|---|
| `SIGNAL_ABORTED_BAD_REBASE_RR` | Post-fill R:R gate fired — fill deviation triggered stop floor, R:R < minimum. `fill_price` IS set; broker position was closed by OSO brackets. `confirmed_pnl=NULL` (unreconciled) until `oso_bracket_stop_fill` is written by STALE_PENDING_PURGE. |

### Reconciliation / cleanup exits
| Code | Meaning |
|---|---|
| `oso_bracket_stop_fill` | OSO bracket stop fill detected by STALE_PENDING_PURGE reconciliation — actual exit price written back. `confirmed_pnl=NULL` (needs V2 broker reconciliation). |
| `sentinel_reconciled_stale` | E2E sentinel marked row stale after 600s with broker flat and no fill — DB-only cleanup, no order sent. |
| `EXIT_UNCONFIRMED` | `result` field value for unreconciled rows — do NOT report as confirmed P&L. |

### Key rules for consumers
1. **Never report `confirmed_pnl=0.0` without `confirmed_basis="broker_confirmed"`** — `0.0` may be an unreconciled abort, not a genuine breakeven.
2. **`confirmed_pnl=NULL`** always means unreconciled. Use `check_trade_accuracy_v2.py` Source A for broker ground truth.
3. **Abort rows** (`T4_FAIL_CLOSED_ABORTED`, `PRE_SUBMIT_*`, `SIGNAL_ABORTED_*`) may have `fill_price` set even though `exit_price` is absent — the fill happened but `on_exit()` was never called cleanly.
4. **Do NOT treat abort rows as losses or wins** in win-rate calculations until `confirmed_pnl` is populated by broker reconciliation.
