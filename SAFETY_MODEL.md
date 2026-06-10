# AlgoChains MCP Server — Safety Model & Team Guide
**Version:** V22.4 | **For:** Team members, first-time users, and anyone asking "is this safe?"

---

## For Roo and the Tech Team — Plain English First

> *"Giving LLM direct access to brokerage accounts that are live is clearly a no, if issues happen there is no fixing it, the actions are reflected in the live account which are irreversible."*

This is the exact right concern. Here is the honest answer.

### What the MCP server actually is

An MCP (Model Context Protocol) server is a **translation layer**. When you ask Claude or Cursor "what's my MNQ position?", the AI cannot directly access Tradovate. Instead:

1. Claude calls `get_positions()` on the MCP server
2. The MCP server calls Tradovate's API
3. The result comes back as readable text to Claude

Think of it like a **secretary**: Claude tells the secretary what to look up, the secretary does it, and reports back. The AI doesn't have your password — it talks to a server that has the password.

### Can the AI lose my money?

**Short answer: Yes, if you give it permission. That's the point.**

Tyler's setup has the AI connected to live Tradovate accounts **intentionally and experimentally**. The guardrails below explain what prevents it from going haywire.

**This is not a product we are selling to retail customers. It is Tyler's personal experimental trading infrastructure.**

For anyone else on the team, there are two safe options:
- **Demo mode** — public market data only, no broker credentials, zero financial risk
- **Read-only mode** — can see Tyler's bot metrics and ask Onyx questions, cannot place trades

---

## How the Safety System Works

### Layer 1 — Hard-coded Circuit Breakers (cannot be overridden by AI)

The following limits are **hard-coded in `trading_guardrails.py`** and CANNOT be overridden by the AI, by prompt engineering, or by any tool call:

| Guardrail | Limit | What happens when tripped |
|-----------|-------|--------------------------|
| Max daily loss | $500 | ALL orders blocked until midnight reset |
| Max drawdown | 15% of account | ALL orders blocked, manual reset required |
| Max position size | 5 contracts (MNQ/MES), 2 (CL) | Order rejected before broker API is called |
| Max orders per minute | 10 | Rate limit enforced, excess orders dropped |
| AI loop detection | 5 identical tool calls in 60s | All orders blocked for 30 minutes |
| VIX gate | VIX > 35 | All trades blocked regardless of signal |
| Max loss per trade | 1% of account equity | Order rejected at validation |
| Max concurrent positions | 8 | New orders blocked |

These are enforced BEFORE any broker API call is made. The AI never sees a "success" if the guardrail rejects the order.

### Layer 2 — Human Confirmation for High-Value Trades

For orders above a configurable notional threshold (default: $10,000 notional value), the MCP spec's **Elicitation** feature requires explicit human confirmation in the AI client before the order is submitted:

```
Claude: I'm about to execute BUY 4 MNQ contracts @ ~$19,400/contract ($77,600 total).
        Confirm? [Yes / No / Cancel]
```

The trade does not execute until you click confirm. There is no way to skip this step programmatically.

### Layer 3 — Order Validation Before Broker

Every order goes through `account_protection/guards.py` before reaching the broker:

- `PositionSizeGuard` — max 10% of equity per position
- `NotionalValueGuard` — max 25% of equity per single order
- `DailyLossGuard` — running loss tracker, blocks when daily limit approached
- `ConcentrationGuard` — prevents putting all capital in one symbol
- `SessionTimeGuard` — blocks orders outside configured trading hours

### Layer 4 — Broker-Side Controls (Tradovate native)

Tradovate itself has its own server-side risk limits. Even if all the above failed, Tradovate would enforce its own per-contract position limits and margin requirements.

---

## What Happens When Things Go Wrong

### Scenario 1: AI sends a wrong order

What actually happens:
1. Order is validated against guardrails → rejected if outside limits
2. If within limits, Elicitation confirmation required for large orders
3. If confirmed and executed → it's a real trade. The AI's mistake is your loss.

**Mitigation:** Set conservative guardrail limits. Start with paper mode. Never give the AI permission to trade without reviewing the guardrail config.

### Scenario 2: The server crashes mid-trade

What actually happens:
- The MCP server process dies
- Any **open positions remain open** — they are NOT automatically closed
- The broker (Tradovate/Alpaca) holds the position as-is
- No trailing stops or take-profits are cancelled by the crash

**What to do:** Check your broker app directly. The MCP server crash has no effect on existing positions. Close manually if needed.

### Scenario 3: Token expires mid-session

What actually happens:
- The Tradovate access token has a ~80-minute lifetime
- Token Guardian renews it every 30 minutes via launchd
- If Token Guardian fails, the next order attempt gets a 401 error
- Existing positions are unaffected — they exist at the broker level, not in the server

**What to do:** Run `python3 tradovate_token_guardian.py` to renew manually.

### Scenario 4: AI goes into a loop

What actually happens:
1. AI Loop Detector in `trading_guardrails.py` counts identical consecutive tool calls
2. After 5 identical calls in 60 seconds → `GuardrailTripped` exception
3. All orders blocked for 30 minutes
4. Slack notification sent to `#incident-response`

**What to do:** The system auto-recovers after 30 minutes. If needed, restart the bot process to reset the counter.

### Scenario 5: Someone else gets the API key

What actually happens:
- The HTTP bridge API key is required for all tool calls
- Owner-level tools (trading) require owner email verification
- Logs record every tool call with timestamp and source

**Mitigation:** Rotate `ALGOCHAINS_BRIDGE_API_KEY` immediately. The key is in `.env` — do not commit it to git.

---

## Team Access Setup

### Role 1: Read-Only (Roo, Eric, RJ)

Get public market data and Tyler's bot metrics without any broker credentials.

**What you can do:**
- `get_quote("AAPL")` — live price for any symbol
- `detect_market_regime()` — is the market trending or ranging?
- `get_macro_signals()` — macro risk-on/off state
- `get_live_bot_metrics()` — Tyler's live bot P&L (read-only)
- `onyx_ask("any question")` — search the AlgoChains knowledge base
- `discover_tools()` — find the right tool for any task

**Setup (2 minutes):**
```bash
# 1. Clone and install
git clone https://github.com/AlgoChains/algochains-mcp-server
cd algochains-mcp-server
pip install -e .

# 2. Set only the bridge API key (get from Tyler)
export ALGOCHAINS_BRIDGE_API_KEY=<key-from-tyler>
export ONYX_API_URL=http://localhost:8085    # Desktop Onyx via Tailscale

# 3. Run quickstart in demo mode
python scripts/quickstart.py --mode demo

# 4. Generate Cursor config
python scripts/quickstart.py --generate-config cursor
```

**What you CANNOT do in read-only mode:**
- Place orders
- See account balances (requires owner auth)
- Modify any configuration

### Role 2: Trading (Tyler only currently)

Full broker access. Requires reading this document and explicitly acknowledging risks.

**Requirements:**
- `TRADOVATE_USERNAME`, `TRADOVATE_PASSWORD`, `TRADOVATE_APP_ID`, `TRADOVATE_APP_SECRET`
- `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`
- Understanding of all guardrails above

---

## Shared Knowledge Base (Onyx)

Tyler, Roo, Eric, RJ — all AI tools should talk to the **same Onyx instance**. This means:

- When Tyler's Claude has a conversation about the CL bot, it can be ingested into Onyx
- When Roo's Cursor asks "what is the AlgoChains MCP server?", it searches the same base
- All blueprints, bot research, decisions, and runbooks are in one place

**To connect your AI to the shared Onyx:**
```bash
export ONYX_API_URL=http://localhost:8085   # via Tailscale
export ONYX_API_KEY=<get-from-tyler>
```

Once connected, any of your AI tools can ask:
- "What did Tyler decide about the CL strategy in April 2026?"
- "How does the Token Guardian work?"
- "What are the validation gates for marketplace submission?"

This is the "interconnected LLMs with the same knowledge base" that Tyler described.

---

## Compliance Notes

### This is NOT investment advice

AlgoChains MCP Server is a **software infrastructure tool**. It:
- Does NOT manage your money
- Does NOT give investment recommendations
- Does NOT have a fiduciary duty to you
- Is NOT registered as an investment advisor or CTA

The backtest results shown (Sharpe ratios, win rates) are computed on **historical data** using Deflated Sharpe Ratio methodology to account for overfitting. They are statistical measurements of past performance, NOT predictions of future returns.

### Regulatory context

- Futures trading in the US is regulated by the **CFTC**
- Equity trading is subject to **SEC** regulations
- If you're trading other people's money, you may need a **CTA registration**
- **Consult a licensed attorney and financial advisor** before using this for anything beyond personal use

### Risk disclosure acknowledgment

By running any tool in live mode, you acknowledge:
1. You understand that AI-assisted trading can result in losses up to and exceeding your account value
2. You have read this document and the guardrail configuration
3. You are solely responsible for any financial outcomes
4. AlgoChains and its developers are not liable for trading losses

---

## Quick Reference — "Is This Safe?"

| Action | Safe? | Notes |
|--------|-------|-------|
| `get_quote("AAPL")` | ✅ Always | Public market data |
| `detect_market_regime()` | ✅ Always | Reads data only |
| `get_live_bot_metrics()` | ✅ Read-only | No money at risk |
| `onyx_ask("...")` | ✅ Always | Knowledge base search |
| `run_backtest(...)` | ✅ Always | Historical simulation |
| `validate_strategy(...)` | ✅ Always | Gate check only |
| `create_price_alert(...)` | ✅ Safe | Internal state only |
| `place_order(...)` | ⚠️ Live money | Requires confirmation; guardrails enforced |
| `cancel_order(...)` | ⚠️ Live money | Cannot undo a cancel |
| `flatten_all_positions()` | 🔴 Irreversible | Closes everything immediately |
| `emergency_stop()` | 🔴 Irreversible | Kills all positions and blocks trading |
