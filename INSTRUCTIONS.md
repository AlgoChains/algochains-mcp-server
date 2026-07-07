# AlgoChains MCP Server — Setup Instructions

`algochains-mcp-server` **v22.7.1** — the trading/signals MCP server + CLI that subscribers
install. It connects your AI assistant (Claude, Cursor, ChatGPT) to AlgoChains: live
copy-trade signals, a hosted virtual paper account, real fills, P&L, and (optionally) your
own broker.

This is the **subscriber toolkit**. For agent-facing tool routing and safety tiers see
[AGENTS.md](AGENTS.md); for the full feature tour see [README.md](README.md).

> **⚠️ Namespace note:** Do NOT co-register this package (`algochains-mcp-server` —
> trading/signals) with **`algochains-library-mcp`** (Roo's natural-language backtesting MCP)
> under the same `algochains` alias. Give them distinct aliases
> (e.g. `algochains` + `algochains-backtest`).

---

## 1. Install

```bash
# via pipx (recommended)
pipx install algochains-mcp-server

# or via Homebrew
brew install algochains/algochains/algochains
```

---

## 2. Get a subscriber key (free — no broker needed)

1. Sign up at **[algochains.ai](https://algochains.ai)**. A **$50k virtual paper account** is
   auto-provisioned on signup.
2. Copy your subscriber key from the dashboard — it starts with `sub_live_`
   (production) or `sub_test_` (sandbox).
3. Export it:

```bash
export ALGOCHAINS_SUBSCRIBER_KEY=sub_live_…
```

**Environment variables**

| Variable | Who it's for | Notes |
|----------|--------------|-------|
| `ALGOCHAINS_SUBSCRIBER_KEY` | Subscribers | **Canonical.** `sub_live_…` / `sub_test_…`. |
| `ALGOCHAINS_SUB_KEY` | Subscribers | Back-compat **alias** — accepted by both the Python server and the TS CLI (#242 / v22.7.1). Prefer the canonical name. |
| `ALGOCHAINS_BRIDGE_KEY` | **Owners / developers** | This is the owner/dev key. It is **NOT** a subscriber key — do not set it for subscriber flows. |

Your key is resolved to a `subscriber_id` server-side via Supabase; the plaintext key
never touches this repo.

---

## 3. Endpoint & auth

- **Base URL:** `https://api.algochains.ai` (`mcp.algochains.ai` is the same endpoint).
- **Header:** subscriber requests authenticate with `X-Api-Key: <your sub_live_… key>`.

---

## 4. The 16 subscriber tools

| Tool | What it does |
|------|--------------|
| `get_signal_stream` | Live copy-trade signals (e.g. from the MNQ bot). |
| `get_my_pnl` | Your paper/live P&L. |
| `get_my_fills` | Your fill history with per-trade P&L. |
| `get_my_assignments` | Bots/strategies assigned to you. |
| `get_my_portfolio` | Paper balance + portfolio snapshot. |
| `get_marketplace_listings` | Bots available to subscribe to. |
| `place_paper_order` | Submit a self-directed paper order. |
| `cancel_paper_order` | Cancel a paper order. |
| `get_my_paper_positions` | Your open paper positions. |
| `report_fill` | Report a fill back to the platform. |
| `heartbeat` | Keep-alive / liveness signal. |
| `ack_signal` | Acknowledge a received signal. |
| `join_bot` | Join a copy-trade bot. |
| `get_subscriber_status` | Your subscription/tier status. |
| `accept_subscriber_terms` | Accept the CFTC risk disclosure (required before signals). |
| `get_my_usage` | Usage metering (calls, quota, overage). |

**Follow the MNQ bot:** the live bot posts `strategy_name = "MNQ Upgraded Scalper"`; the fanout
maps it to `bot = "MNQ"`, so filter with `get_signal_stream(bots=["MNQ"])`.

Typical first sequence: `accept_subscriber_terms` → `join_bot` → `get_my_portfolio` →
`get_signal_stream`.

---

## 5. Connect a real broker (optional)

Brokers are connected **server-side** in the Broker Hub at
**[algochains.ai/account/brokers/](https://algochains.ai/account/brokers/)** — there is no
local daemon to run.

| Broker | Notes |
|--------|-------|
| **Tradovate** | Futures. Connect via email+password OAuth, **or** your own API Key + Secret as a fallback. |
| **Alpaca** | Equities, ETFs, options, crypto. |
| **OANDA** | Forex. |
| **FTMO / MT5** | Prop-firm accounts (MetaTrader 5). |
| **Robinhood** | Futures — coming soon. |

---

## 6. Submit your own algo → auto-graduation

Submit a strategy and it **paper-trades on AlgoChains**, then:

- **Graduates to live** when `live_sharpe_30d >= 0.80 × backtest_oos_sharpe`.
- **Retires** when `live_sharpe_30d < 1.0`.
- **Anti-overfit caps** on the accepted OOS Sharpe per timeframe (min trades): daily ≤ 5
  (≥ 20 trades), hourly ≤ 7 (≥ 50), 15-min ≤ 10 (≥ 80), 5-min ≤ 12 (≥ 100).

See [MARKETPLACE_CREATOR_GUIDE.md](MARKETPLACE_CREATOR_GUIDE.md) for the full flow.

---

## 7. Optional Managed Hosting — $49/mo

Don't want to run infra? Managed Hosting runs your tenant on **GCP Cloud Run
(scale-to-zero, per-tenant)** for **$49/mo**.

---

## 8. API reference

Downloadable and always current:

- OpenAPI 3.1 JSON — `https://algochains.ai/docs/openapi.json`
- OpenAPI 3.1 YAML — `https://algochains.ai/docs/openapi.yaml`
- Postman collection — `https://algochains.ai/docs/postman-collection.json`

---

## More docs

- [README.md](README.md) — full feature tour and setup options.
- [AGENTS.md](AGENTS.md) — agent context: tool routing, safety tiers, workflows.
- [SAFETY_MODEL.md](SAFETY_MODEL.md) — "is this safe?" for every failure mode.
- [docs/SUBSCRIBER_TOOLS.md](docs/SUBSCRIBER_TOOLS.md) — subscriber tools, scopes, constraints.
- [CHANGELOG.md](CHANGELOG.md) — full version history.
