<!--
BLUEPRINT_STATUS: active
LAST_REVIEWED: 2026-04-18
-->

# AlgoChains MCP Server — Upgrade Blueprint V22
**Date:** 2026-04-06 | **Trigger:** Gemini Architecture Review
**Status:** ACTIVE IMPLEMENTATION

---

## Executive Summary

This blueprint addresses three critical gaps identified in the Gemini review of AlgoChains MCP Server v21.3:

1. **Circuit Breakers** — AI agents can loop. One verified case study: 10,000 trades in 8 seconds → $2.4M loss from a single infinite loop. Hard-coded, AI-unoverridable guardrails must sit at the execution boundary.

2. **Latency Honesty** — MCP over stdio via LLM adds 100–2000ms per tool call. This is categorically NOT HFT infrastructure. The README and tool descriptions must be accurate about where this system lives in the execution speed spectrum.

3. **SSE Streaming Transport** — Current architecture requires the AI to poll `get_quote` repeatedly. MCP Python SDK 1.26.0 supports `StreamableHTTPServerTransport` with native SSE push. This eliminates polling and enables real-time market data subscriptions.

---

## Section 1: Hard-Coded Trading Circuit Breakers

### Why "Hard-Coded" Matters

The existing `account_protection` module allows the AI to call `update_protection_config` to change thresholds. This is exactly wrong for safety limits. An AI in a loop can:
1. Place 50 orders
2. See position limits hit
3. Call `update_protection_config(max_positions=100)` to bypass
4. Place 50 more orders
5. Repeat until broker blocks account

V22 introduces `trading_guardrails.py` — limits defined as Python constants at module import time. They are **not tools**. The AI has no tool to modify them. The only way to change them is a code deploy.

### Hard Limits (Research-Based)

```
Tradovate API limits (official, not overridable):
  - 80 requests/minute (rolling 60s window)
  - 5,000 requests/hour (rolling)
  - 429 response + 20-30s block on violation
  - Repeated violations → P-ticket (extended ban)

AlgoChains hard limits (code-level, not configurable by AI):
  - MAX_ORDERS_PER_MINUTE = 10          (1/8th of Tradovate cap = buffer)
  - MAX_ORDERS_PER_HOUR = 60            (vs Tradovate's 5000 — aggressive buffer)
  - MAX_DAILY_LOSS_USD = 500            (Tyler's hard limit from CLAUDE.md)
  - MAX_DRAWDOWN_PCT = 0.15             (15%, from validation gates)
  - MAX_CONSECUTIVE_LOSSES = 5          (halt and review)
  - MAX_POSITION_SIZE_CONTRACTS = 5     (per symbol)
  - VIX_KILL_THRESHOLD = 35.0           (from live bot configs)
  - MAX_NOTIONAL_USD = 100_000          (total open notional)
  - AI_LOOP_DETECTION_WINDOW_SEC = 60   (scan for repeated calls)
  - AI_LOOP_MAX_IDENTICAL_CALLS = 5     (trip breaker on 5 identical in 60s)
  - MAX_TOOL_CALLS_PER_MINUTE = 30      (MCP tool call rate limit)
```

### Circuit Breaker State Machine

```
CLOSED (normal)  →  threshold breach  →  OPEN (all orders blocked)
OPEN             →  cooldown expires  →  HALF_OPEN (1 test call allowed)
HALF_OPEN        →  success          →  CLOSED
HALF_OPEN        →  failure          →  OPEN (reset cooldown)
```

### AI Loop Detection Algorithm

Three complementary detection layers (from SupraWall research):

1. **String-match (O(1))**: Hash each tool call signature. 5 identical hashes in 60s → trip.
2. **Frequency analysis**: Count tool calls per minute. >30/min → warn at 25, trip at 30.
3. **Order velocity**: Orders placed per minute. >10/min → immediate trip.

---

## Section 2: Streamable HTTP SSE Transport

### Current Architecture (Polling)
```
Claude/Cursor ──stdio──→ MCP Server ──HTTP──→ Tradovate/Polygon
Agent must call get_quote every N seconds (polling).
100-300ms round trip per quote. Wastes tokens. Misses ticks.
```

### V22 Architecture (Push via SSE)
```
Claude/Cursor ──HTTP──→ MCP Bridge (FastAPI + SSE)
                              ↕ (WebSocket to Tradovate)
                         Market data arrives → SSE push to client
                         Agent receives: price, volume, L2 without polling
```

### MCP Python SDK 1.26.0 Transport APIs

The SDK provides two transport options:
1. `stdio_server` — current, synchronous, one client, no streaming
2. `StreamableHTTPServerTransport` — HTTP-based, multi-client, SSE push, session management

### SSE Stream Types Available

```python
# Stream 1: Quote updates (every tick)
stream_quotes(symbols: list[str]) → SSE stream of {symbol, bid, ask, last, volume}

# Stream 2: Bot metrics (every 30s)
stream_bot_metrics() → SSE stream of {bot_id, pnl, signal, confidence}

# Stream 3: Order fills (real-time)
stream_order_fills(broker: str) → SSE stream of {order_id, fill_price, qty, side}

# Stream 4: Alert events (threshold-triggered)
stream_alerts() → SSE stream of {alert_type, message, severity, timestamp}
```

### Security: Origin Validation

Per MCP 2025-03-26 spec, the `StreamableHTTPServerTransport` MUST validate `Origin` headers to prevent DNS rebinding attacks. V22 enforces a whitelist:
```python
ALLOWED_ORIGINS = [
    "https://algochains.ai",
    "http://localhost:3000",
    "http://localhost:5173",
    # Add Cursor/Claude Desktop origins as they expose SSE
]
```

---

## Section 3: Latency Reality Document

### Where MCP AI Trading Lives in the Speed Spectrum

```
Execution Speed Tiers (2025 market structure):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Tier 1: Co-located HFT          100ns – 10μs
         C++/FPGA, co-lo at CME/NYSE
         Latency arbitrage, market making, flash orders
         AlgoChains: NOT HERE ❌

Tier 2: Algorithmic HFT         1ms – 50ms
         Python/Rust, VPS in NY4/LD4/CH2
         Statistical arb, pairs trading, order book strats
         AlgoChains Rust engine: CAN REACH THIS ✅

Tier 3: Medium-frequency algo    50ms – 1s
         Direct API execution, minimal AI overhead
         AlgoChains bot direct (no MCP hop): HERE ✅

Tier 4: MCP AI-assisted          100ms – 2000ms
         LLM inference + MCP tool call overhead
         AlgoChains MCP server: HERE ✅
         Best for: swing trading, overnight holds,
         portfolio rebalancing, strategy research,
         mean-reversion (minutes to hours), signal routing

Tier 5: Human-speed              2s – minutes
         Research, alerts, post-trade analysis
         AlgoChains dashboards + Onyx RAG: HERE ✅
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### MCP Tool Call Latency Breakdown (measured, Mac M3 Max)

```
Component                           Latency
────────────────────────────────────────────────
LLM token generation (Claude 3.5)   80–400ms
MCP tool call overhead              2–5ms
Python script overhead              1–3ms
Tradovate REST API round trip       15–80ms
Polygon bar data fetch              20–150ms
Databento tick stream fetch         30–200ms
Onyx RAG query                      100–500ms
────────────────────────────────────────────────
Total (typical MCP tool call)       ~120–750ms
Total (complex multi-tool call)     500–2000ms
```

### What This Means in Practice

| Strategy Type | Min Signal-to-Fill Time | MCP Suitable? |
|---------------|------------------------|---------------|
| HFT / Market Making | <1ms | ❌ Never |
| Statistical Arb (seconds) | <500ms | ❌ Too slow |
| Intraday momentum (min bars) | <5s | ⚠️ Marginal |
| Swing (15min/1h bars) | <30s | ✅ Ideal |
| Daily rebalancing | <5 min | ✅ Perfect |
| Options strategies | <2 min | ✅ Perfect |
| Portfolio research | <10 min | ✅ Perfect |

**Recommendation in README:** Include this table. Be explicit that the 4 live bots run on direct Python WebSocket execution — NOT through MCP. MCP is the intelligence layer (research, monitoring, signal routing), not the execution layer.

---

## Section 4: Implementation Plan

### Files Created/Modified

```
algochains-mcp-server/
├── src/algochains_mcp/
│   ├── trading_guardrails.py      NEW — Hard-coded circuit breakers
│   ├── sse_server.py              NEW — Streamable HTTP + SSE transport
│   ├── agent_loop_detector.py     NEW — AI loop detection
│   ├── server.py                  MOD — Wire guardrails into place_order
│   └── middleware.py              MOD — Strengthen Tradovate rate limits
├── blueprints/
│   └── UPGRADE_BLUEPRINT_V22.md  NEW — This document
└── LATENCY_GUIDE.md              NEW — User-facing latency tier doc
```

### Priority Order

1. `trading_guardrails.py` — **P0**: No new orders until this is live
2. `agent_loop_detector.py` — **P0**: Prevent the $2.4M loop scenario
3. `sse_server.py` — **P1**: Streaming transport (enables algochains.ai real-time)
4. `LATENCY_GUIDE.md` — **P1**: User trust + correct expectations
5. `server.py` wiring — **P1**: Connect all modules

---

## Section 5: V22 Tool Changes

### Tools REMOVED from AI control (moved to hard-coded constants)
- ~~`update_protection_config`~~ → limits are now code-level constants
- ~~`set_rate_limits`~~ → limits are read-only via `get_protection_config`

### Tools MODIFIED
- `place_order` → now gated by `TradingGuardrails.check_all()` before execution
- `get_protection_config` → now returns hard-coded V22 constants

### Tools ADDED
- `get_circuit_breaker_status` → returns current CB state per broker
- `get_agent_loop_status` → shows detected loop risk and call frequency
- `subscribe_quote_stream` → initiates SSE market data subscription
- `subscribe_fills_stream` → SSE order fill notifications
- `get_latency_profile` → returns real-time latency measurements for this session

---

## Research Sources

1. SupraWall (2025). "AI Agent Infinite Loop Detection & Circuit Breakers" — 10,000 trades in 8s case study
2. Dev.to / QCAutomation (2025). "How I Added a Circuit Breaker to My AI Trading Bot"
3. MCP Specification 2025-03-26. Transports. modelcontextprotocol.io/specification
4. MCP Python SDK 1.26.0. `mcp.server.streamable_http.StreamableHTTPServerTransport`
5. NYCServers Blog (2025). "Algorithmic Trading Tech Stack 2025: Benchmarks"
6. Tradovate Community (2025). Rate limit: 80 req/min, 5000 req/hour
7. Bailey & Lopez de Prado (2012). "The Sharpe Ratio Efficient Frontier" (deflated Sharpe basis)
8. FPX Framework arXiv:2512.02227 — adaptive latency management for AI trading
