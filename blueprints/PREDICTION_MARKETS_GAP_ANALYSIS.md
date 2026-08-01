<!--
BLUEPRINT_STATUS: active
LAST_REVIEWED: 2026-04-18
NOTE: Kalshi CLOB gaps are tracked as P4-17 in docs/MCP_SERVER_ENHANCEMENT_ROADMAP.md.
-->

# Gap analysis — prediction markets & trade propagation (2026-04-08)

## Research summary (open docs + industry pattern)

| Area | Best-in-class pattern |
|------|------------------------|
| Polymarket discovery | Gamma endpoint with `order=volume_24hr`, `ascending=false`, filter `active=true`, `closed=false`. Use outcome prices only when present — never invent 50/50. ([Polymarket docs](https://docs.polymarket.com/)) |
| Polymarket execution | `py-clob-client`; limit price from **live** book/midpoint computed by caller, not hardcoded |
| Kalshi reads/trading | RSA-PSS-SHA256; headers `KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-TIMESTAMP`, `KALSHI-ACCESS-SIGNATURE`; message `timestamp + METHOD + path_without_query` ([Kalshi quick start](https://docs.kalshi.com/getting_started/quick_start_authenticated_requests)) |
| Django propagation | HMAC body signature; secrets only via env (`ALGOCHAINS_SIGNAL_*` / `SIGNAL_*`) |

## Issues found & fixed (hidden killers / policy)

1. **`get_prediction_markets` / engine** — Previously wrong class + async `get_signals`; fixed in v22.6.  
2. **Synthetic YES = 0.5** when Gamma `outcomePrices` missing — **removed**; markets skipped unless prices parse.  
3. **`get_top_markets`** — Used nonstandard `sort=volume24hr`; **aligned** to `order=volume_24hr` + `ascending=false`; removed `[0.5]` JSON fallback.  
4. **Kalshi `Authorization: Token`** — Incompatible with current trade API — replaced with **`kalshi_signed.py`** (RSA-PSS). `KALSHI_API_KEY` alone now logs warning and does not send bogus auth.  
5. **`place_polymarket_order`** — Hardcoded `price=0.5` — **removed**; **`limit_price` required** in (0, 1).  
6. **Kalshi liquidity** — Removed arbitrary `open_interest * 0.01`; now raw `open_interest` as reported.  
7. **Trade propagation** — Already fail-closed without URL/secret (no default `1234` in MCP path).

## Residual gaps (explicit, not hidden)

| Gap | Mitigation |
|-----|------------|
| Kalshi host | Default `https://api.elections.kalshi.com`; operators **must** set `KALSHI_API_HOST` if Kalshi rotates regions/products. |
| Kalshi market list shape | If API returns non-dict, handler surfaces HTTP body — adjust parser when Kalshi schema changes. |
| Polymarket Gamma schema drift | If `outcomePrices` moves to nested objects, extend `_polymarket_yes_no_prices` only with **real** sample payloads. |
| CLOB `OrderArgs` / YES-NO side | Verify `py-clob-client` token_id + side enums against latest Polymarket CLOB docs before production size. |
| Onyx / Command Center | Ingest this file + `MEGA_PROMPT_PREDICTION_MARKETS_V1.md` so agents stop assuming Token Kalshi auth. |

## Verification commands

```bash
cd algochains-mcp-server && source .venv/bin/activate
python -m py_compile src/algochains_mcp/order_flow/prediction_markets.py
python -m py_compile src/algochains_mcp/order_flow/kalshi_signed.py
pytest tests/test_tool_registration.py -q
```
