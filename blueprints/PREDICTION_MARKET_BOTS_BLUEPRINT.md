<!--
BLUEPRINT_STATUS: active
LAST_REVIEWED: 2026-04-18
NOTE: Kalshi CLOB implementation tracked as P4-17 in docs/MCP_SERVER_ENHANCEMENT_ROADMAP.md.
-->

# Blueprint — Subscribable Polymarket & Kalshi Bots on AlgoChains

**Version:** 2026-04-08  
**Context:** Roo/Michael slack — prediction-market latency edge, marketplace subscription model, MCP + Django propagation.

## 1. Goals

1. **Data plane:** Live odds and liquidity from Polymarket (Gamma + optional CLOB) and Kalshi, with no synthetic quotes.
2. **Signal plane:** Bots emit either (a) prediction-market orders via BYOK keys, or (b) **equity/crypto/paper equity** signals via Django `propagate_trade_signal` (Roo architecture).
3. **Metrics plane:** Normalized schema for marketplace promotion — latency vs reference feed, YES probability path, volume, and optional cross-venue arb.
4. **Promotion:** Same gating ethos as futures bots — real logs/JSONL + MCPT-style robustness (prediction markets need bespoke gates: calibration, holding period, resolution risk).

## 2. MCP stack (implemented / wired)

| Capability | Tool / module |
|------------|----------------|
| Thematic market pull | `get_prediction_markets` → `PredictionMarketsEngine.get_signals()` |
| Keyword search | `search_prediction_markets` |
| High volume discovery | `get_polymarket_high_volume` (Roo “new listed YES/NO + quick movers”) scan helper |
| Bot audit log | `record_prediction_market_bot_metric` / `get_prediction_market_bot_metrics` → `state/prediction_market_bot_metrics.jsonl` |
| Django propagation | `propagate_trade_signal` → `trade_propagation.py` (HMAC, fail-closed without URL/secret) |
| BYOK registry | `byok/provider_registry.py` — `polymarket`, `kalshi` entries |

## 3. Metrics schema (normalized)

Each `record_prediction_market_bot_metric` row:

- `recorded_at` (ISO UTC)  
- `bot_id`, `platform` (`polymarket`|`kalshi`), `market_id`  
- `yes_probability` (0–1, from venue)  
- `edge_vs_entry` (model-defined; document units in `notes`)  
- `latency_ms_observed` (e.g. venue chainlink vs Coinbase for BTC bins — **only real measured**)  
- `action` (`BUY_YES`, `BUY_NO`, `HOLD`, `ARB`, …)  
- `metadata` (JSON: signal_id, subscriber fan-out id, etc.)

**Marketplace promotion:** require minimum history length + staleness checks + jurisdiction/compliance review (separate legal checklist).

## 4. Roo hypothesis — “new binary + first wave”

**Research cadence (no fake backtests here):**

1. `get_polymarket_high_volume` + `search_prediction_markets` for new `active=true` contracts.  
2. Bucket by time since listed (from API fields when available).  
3. Record early YES/NO flow via repeated `record_prediction_market_bot_metric` snapshots **from live polls** — compute statistics offline from JSONL.

## 5. Kalshi auth note

Kalshi’s production API may use **RSA-signed requests**, not a single `Token` header. Operators should confirm against [Kalshi API docs](https://docs.kalshi.com) and align env vars (`KALSHI_API_KEY`, `KALSHI_PRIVATE_KEY_PATH`, etc.). MCP search path may need a follow-up PR for full auth parity.

## 6. Onyx + Command Center

- **Onyx:** Ingest this blueprint + `MEGA_PROMPT_PREDICTION_MARKETS_V1.md` + Gamma API field notes so agents retrieve one canonical policy.  
- **Command Center:** Surface `propagate_trade_signal` status + prediction bot JSONL excerpts read-only for admins.

## 7. Failure modes (audit)

- Missing `SIGNAL_*` → propagation fails closed (no silent no-op).  
- Empty Polymarket results for category → `PredictionMarketError` surfaced to caller.  
- CLOB order path requires `py-clob-client` + real keys — import errors fail with explicit message.
