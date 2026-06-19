# Subscriber Copy-Trade Tools

This guide describes the subscriber-facing copy-trade and paper-account tools.
The source of truth is `src/algochains_mcp/subscriber_tools.py`; auth resolution
lives in `src/algochains_mcp/subscriber_auth.py`, and HTTP dispatch lives in
`src/algochains_mcp/http_bridge.py`.

## Intent

Subscriber tools let a paying or sandbox subscriber:

- discover and join published strategy signals,
- view a hosted simulated paper account,
- review signals and fills scoped to their own assignments,
- place self-directed paper orders, and
- report daemon health/fills back into the copy-trade pipeline.

They do not grant owner access, live broker order execution, or access to another
subscriber's data. The bridge resolves `subscriber_id` from the production or
sandbox subscriber API key server-side; callers must not pass or trust a
client-supplied `subscriber_id`.

## Transport Surfaces

There are two subscriber paths. They intentionally expose different surfaces.

| Path | Auth | Tool surface | Use it for |
|---|---|---|---|
| Local stdio MCP | `ALGOCHAINS_SUBSCRIBER_KEY=<SUBSCRIBER_API_KEY>` | Subscriber funnel/status tools registered in `server.py` | New-user onboarding, consent, joining bots, usage, referrals, realized P&L |
| HTTP bridge | `X-Api-Key: <SUBSCRIBER_API_KEY>` or `Authorization: Bearer <SUBSCRIBER_API_KEY>` | all `SUBSCRIBER_TOOLS` from `subscriber_tools.py` | Portfolio, signal stream, fills, paper orders, daemon callbacks, plus the onboarding/status tools |

The HTTP bridge should be the default for dashboards, daemons, and any workflow
that needs portfolio, signal, fill, or paper-order tools. Local stdio remains
useful for agent-driven onboarding because the server can resolve
`ALGOCHAINS_SUBSCRIBER_KEY` from the local environment.

## Onboarding Flow

The compliant subscriber funnel is:

1. Call `get_started(goal="subscriber")`, `get_pricing()`, or
   `get_system_status()` without auth to inspect the public offer.
2. Call `get_checkout_url(email="you@example.com", tier="paper")` to generate a
   Stripe-hosted checkout link. Payment provisioning emails a subscriber API key.
3. Store the key in a secrets store, `.env`, or MCP client header. For local
   stdio, set `ALGOCHAINS_SUBSCRIBER_KEY=<SUBSCRIBER_API_KEY>`.
4. Call `accept_subscriber_terms()` once without an acknowledgment to retrieve
   the current futures risk disclosure and exact acknowledgment phrase.
5. Call `accept_subscriber_terms(acknowledgment="<exact phrase>")`.
6. Call `join_bot(bot="MNQ", size_multiplier=1.0)` to activate a copy-trade
   signal assignment.
7. Call `get_subscriber_status()` and then use bridge tools such as
   `get_signal_stream`, `get_my_portfolio`, and `get_my_pnl`.

The exact acknowledgment phrase is defined by `RISK_ACK_PHRASE` in
`src/algochains_mcp/compliance/disclosures.py`:

```text
I have read and understand the risk disclosure above. I accept full responsibility for my trading decisions.
```

`join_bot` fails closed with `{"error": "consent_required", ...}` until the
current `RISK_DISCLOSURE_VERSION` has been acknowledged. Agents must surface the
disclosure and wait for the subscriber's explicit acknowledgment; never invent
or auto-fill consent on the subscriber's behalf.

## HTTP Bridge Examples

List the subscriber surface and required scopes:

```bash
curl -sS https://api.algochains.ai/tools \
  -H "X-Api-Key: <SUBSCRIBER_API_KEY>"
```

Call a subscriber tool:

```bash
curl -sS https://api.algochains.ai/api/mcp \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: <SUBSCRIBER_API_KEY>" \
  -d '{
    "tool": "get_my_portfolio",
    "arguments": {}
  }'
```

Join a bot after consent:

```bash
curl -sS https://api.algochains.ai/api/mcp \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: <SUBSCRIBER_API_KEY>" \
  -d '{
    "tool": "join_bot",
    "arguments": {
      "bot": "MNQ",
      "size_multiplier": 1.0,
      "max_contracts": 10,
      "daily_loss_cap_usd": 5000.0
    }
  }'
```

Stream copy-trade signals over Server-Sent Events:

```bash
curl -N https://api.algochains.ai/api/signals/stream?bots=MNQ \
  -H "X-Api-Key: <SUBSCRIBER_API_KEY>"
```

Daemons should reconnect on disconnect and can resync missed entries with
`get_signal_stream`.

## Subscriber Tool Catalog

These 16 tools are the current `SUBSCRIBER_TOOLS` bridge surface.

| Tool | Required scope | Purpose |
|---|---|---|
| `accept_subscriber_terms` | `my_assignments` | Show or record the risk-disclosure and ToS consent gate. |
| `join_bot` | `my_assignments` | Assign or re-activate a subscriber for published bot signals. |
| `get_subscriber_status` | `my_assignments` | Show consent state, assignments, paper account, and suggested next steps. |
| `get_my_assignments` | `my_assignments` | List followed bots and subscriber-defined risk caps. |
| `get_signal_stream` | `signal_stream` | Return unread copy-trade signals filtered to the subscriber's assignments. |
| `get_my_pnl` | `my_pnl` | Return today and 7-day simulated paper P&L. |
| `get_my_portfolio` | `my_pnl` | Return paper account, assignments, open signals, and P&L in one payload. |
| `get_my_fills` | `my_fills` | Return paginated subscriber fill history. |
| `get_my_usage` | `my_pnl` | Return current-month metered calls, quota, and projected overage. |
| `get_marketplace_listings` | `my_assignments` | Browse approved bots available to follow. |
| `place_paper_order` | `paper_trade` | Place a self-directed simulated paper order. |
| `cancel_paper_order` | `paper_trade` | Cancel a pending simulated paper order. |
| `get_my_paper_positions` | `paper_trade` | List pending and recently filled self-directed paper orders. |
| `report_fill` | `report_fill` | Daemon callback that records an executed subscriber-side fill. |
| `ack_signal` | `report_fill` | Daemon callback that acknowledges a signal for auditability. |
| `heartbeat` | `heartbeat` | Daemon liveness callback. |

`DEFAULT_SUBSCRIBER_SCOPES` grants all scopes above. Custom keys may be narrower;
the bridge returns a missing-scope error rather than falling back to public or
owner behavior.

## Safety and Compliance Constraints

- Subscriber keys are scoped before owner/developer dispatch. A subscriber key
  can only call `SUBSCRIBER_TOOLS`.
- `call_subscriber_tool` removes any caller-supplied `subscriber_id` before
  invoking handlers.
- Signal delivery is informational and subscriber-initiated. The platform does
  not auto-execute live broker orders for subscribers.
- Paper-account, paper-P&L, portfolio, and marketplace performance payloads use
  `with_hypothetical_disclaimer()` and carry the CFTC Reg. 4.41(b)
  hypothetical-performance disclaimer.
- If Supabase is unavailable, subscriber identity and data reads fail closed.
  Do not estimate P&L, fills, or assignments from stale or fabricated data.

## Source and Tests

- Source: `src/algochains_mcp/subscriber_tools.py`
- Key resolution: `src/algochains_mcp/subscriber_auth.py`
- HTTP bridge dispatch: `src/algochains_mcp/http_bridge.py`
- Disclosure text/versioning: `src/algochains_mcp/compliance/disclosures.py`
- Legal context: `docs/LEGAL_COMPLIANCE_AUDIT.md`
- Tests: `tests/test_subscriber_tools.py`, `tests/test_revenue_compliance.py`,
  `tests/test_http_bridge_auth.py`
