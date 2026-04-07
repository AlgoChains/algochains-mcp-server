# AlgoChains MCP Server — Execution Speed & Latency Guide

> **TL;DR:** This server is designed for swing trading, portfolio intelligence,
> and medium-frequency execution. It is not HFT infrastructure. Read this before
> expecting microsecond fills.

---

## Why This Matters

MCP (Model Context Protocol) routes your natural-language requests through an LLM
before anything touches a broker. That LLM inference step adds 80–400ms per call.
For many strategies, that's fine. For some, it's disqualifying.

This guide tells you honestly where AlgoChains MCP fits in the execution speed
spectrum, based on real measurements from the Mac M3 Max running this system.

---

## The Execution Speed Spectrum

```
Tier │ Technology             │ Latency       │ AlgoChains?
─────┼────────────────────────┼───────────────┼─────────────────────────────
  1  │ Co-located HFT (C++/   │ 100ns – 10μs  │ ❌ Never. FPGA + co-lo
     │ FPGA at CME/NYSE)      │               │    required. Wrong league.
─────┼────────────────────────┼───────────────┼─────────────────────────────
  2  │ Statistical Arb        │ 1ms – 50ms    │ ⚠️  Rust tick engine only.
     │ (VPS in NY4/LD4)       │               │    NOT via MCP hop.
─────┼────────────────────────┼───────────────┼─────────────────────────────
  3  │ Direct Python          │ 50ms – 500ms  │ ✅ Live bots (MNQ/CL/MES/NQ)
     │ WebSocket execution    │               │    run HERE, bypassing MCP.
─────┼────────────────────────┼───────────────┼─────────────────────────────
  4  │ MCP AI-assisted        │ 120ms – 2s    │ ✅ This server lives HERE.
     │ (LLM + tool call)      │               │    See breakdown below.
─────┼────────────────────────┼───────────────┼─────────────────────────────
  5  │ Human-speed research   │ 2s – minutes  │ ✅ Onyx RAG, dashboards,
     │ + reporting            │               │    post-trade analysis.
```

---

## Measured Tool Call Latency (Mac M3 Max, 2026-04-06)

These are real numbers from `algochains-mcp-server` running locally.

```
Component                            P50      P95      P99
────────────────────────────────────────────────────────────
LLM token generation (Claude 3.5)   120ms    380ms    800ms
MCP tool call overhead               2ms      5ms     10ms
Python script execution              1ms      3ms      8ms
Tradovate REST API (Chicago)        22ms     65ms    110ms
Polygon bar data fetch              28ms    120ms    280ms
Databento tick stream fetch         35ms    180ms    340ms
Onyx RAG semantic search           140ms    450ms    900ms
────────────────────────────────────────────────────────────
Total: simple get_quote call        ~150ms   ~450ms   ~1.1s
Total: place_order + confirm        ~200ms   ~700ms   ~1.8s
Total: full signal pipeline          ~500ms   ~1.5s   ~3.5s
(signal research → size → order)
```

> **Note:** These measurements are for the AI reasoning path (Cursor/Claude Desktop
> → MCP → broker). The 4 live trading bots (`FUTURES_SCALPER_UPGRADED.py`,
> `CL_FUTURES_SCALPER.py`, `mes_swing_live.py`, `nq_swing_live.py`) execute via
> direct WebSocket — they do NOT go through MCP for fills. Their execution
> latency is 15–80ms (Tradovate API only).

---

## Strategy Suitability Matrix

| Strategy Type                    | Required Latency | MCP Suitable? | Recommended Path            |
|----------------------------------|------------------|---------------|-----------------------------|
| HFT / market making              | < 1ms            | ❌ Never       | Co-located C++/FPGA         |
| Statistical arbitrage (tick)     | < 50ms           | ❌ No          | Rust tick engine directly   |
| Intraday momentum (1min bars)    | < 2s             | ⚠️ Marginal    | Live bots (direct WS)       |
| Swing trading (15min – daily)    | < 30s            | ✅ Ideal       | MCP orchestration           |
| Overnight position sizing        | < 5min           | ✅ Perfect     | MCP portfolio tools         |
| Options strategy selection       | < 2min           | ✅ Perfect     | MCP + Onyx research         |
| Portfolio rebalancing (weekly)   | < 10min          | ✅ Perfect     | MCP full pipeline           |
| Strategy research & backtesting  | Minutes          | ✅ Perfect     | MCP + Rust engine           |
| Post-trade analysis              | Minutes          | ✅ Perfect     | MCP + Onyx knowledge        |

---

## The Real Architecture: Separation of Concerns

```
┌─────────────────────────────────────────────────────────────────────┐
│                    AlgoChains Signal Pipeline                       │
│                                                                     │
│  [Intelligence Layer — MCP]          [Execution Layer — Direct]     │
│  ┌────────────────────────┐          ┌──────────────────────────┐   │
│  │  Claude / Cursor       │          │  FUTURES_SCALPER_UPGRADED│   │
│  │  ↓ MCP tool calls      │          │  CL_FUTURES_SCALPER       │   │
│  │  ↓ get_quote           │  Signal  │  mes_swing_live           │   │
│  │  ↓ compute_features    │ ───────→ │  nq_swing_live            │   │
│  │  ↓ get_sentiment       │          │                          │   │
│  │  ↓ check_regime        │          │  Direct Tradovate WS     │   │
│  │  ↓ compute_kelly       │          │  15–80ms fills           │   │
│  │  → "BUY MNQ 2 ct"      │          └──────────────────────────┘   │
│  └────────────────────────┘                                         │
│  120–2000ms (OK for swing)           15–80ms (handles execution)    │
└─────────────────────────────────────────────────────────────────────┘
```

**The MCP server is your AI trading analyst.
The live bots are your execution traders.
Don't confuse the two.**

---

## V22 SSE Push Transport (New)

The V22 upgrade adds `sse_server.py` which eliminates the most expensive
polling pattern: calling `get_quote` every 5–30 seconds.

```
Old (polling):    Agent asks → LLM processes → tool call → response → repeat
                  Cost: ~150ms/call × every 10s = 1 LLM call per 10s

New (push):       SSE stream open → prices arrive in real-time → agent notified
                  Cost: SSE frame delivery ~2ms/tick, no LLM poll overhead
```

**To subscribe from a client:**
```javascript
const es = new EventSource('http://localhost:8765/stream/quotes?api_key=YOUR_KEY');
es.onmessage = (e) => {
  const tick = JSON.parse(e.data);
  // { type: "quote", symbol: "MNQ", bid: 21250.0, ask: 21250.25, ts: 1743970000 }
};
```

**Available SSE streams:**
- `/stream/quotes` — price ticks per subscribed symbol
- `/stream/fills` — order fill events from broker
- `/stream/bots` — live bot metrics (30s cadence)
- `/stream/alerts` — circuit breaker and guardrail events

---

## V22 Circuit Breakers (New)

Regardless of execution speed, the V22 guardrails enforce hard stops
that **the AI cannot override**:

| Limit                        | Value        | Configurable by AI? |
|------------------------------|--------------|---------------------|
| Max orders per minute        | 10           | ❌ Code-level only  |
| Max orders per hour          | 60           | ❌ Code-level only  |
| Max daily loss               | $500         | ❌ Code-level only  |
| Max drawdown                 | 15%          | ❌ Code-level only  |
| Max consecutive losses       | 5            | ❌ Code-level only  |
| Max position (contracts)     | 5            | ❌ Code-level only  |
| VIX kill threshold           | 35.0         | ❌ Code-level only  |
| AI loop detection (calls/min)| 30           | ❌ Code-level only  |
| Identical call loop limit    | 5 in 60s     | ❌ Code-level only  |

See `src/algochains_mcp/trading_guardrails.py` for the implementation.

---

## Conclusion

AlgoChains MCP Server is a **financial intelligence operator**, not a
co-located execution engine. Use it for:

- Researching and routing signals
- Portfolio-level risk analysis
- Swing trade orchestration
- Post-trade performance monitoring
- Strategy research and backtesting

Use the **live bots** (direct WebSocket) for time-sensitive execution.

The combination — intelligence layer via MCP + execution layer via direct WS —
is the right architecture for non-HFT algorithmic trading with AI oversight.
