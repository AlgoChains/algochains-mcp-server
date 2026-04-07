# MEGA PROMPT — Agent playbook for prediction markets + AlgoChains MCP (V1)

Use this as system/developer context when automating **Polymarket**, **Kalshi**, Django **trade propagation**, and **marketplace** promotion.

## Absolute rules

1. **Real data only.** Never invent odds, volumes, or fills. If an API or key is missing, return the explicit error from the MCP tool.
2. **Paper-first for subscriber propagation.** Roo’s `TRADE_PROPAGATION.md`: subscriber accounts must be paper unless a human has approved live.
3. **BYOK.** Polymarket CLOB and Kalshi trading keys live in user env / vault — never embed in prompts or logs.

## Tool cheat sheet

- **Discovery:** `get_prediction_markets` (themes: fed | economic | political | crypto | all), `search_prediction_markets`, `get_polymarket_high_volume`.
- **Kalshi BYOK:** `KALSHI_ACCESS_KEY` + `KALSHI_PRIVATE_KEY_PATH` (PEM) or `KALSHI_PRIVATE_KEY_PEM`; optional `KALSHI_API_HOST` (default `https://api.elections.kalshi.com`, demo: `https://demo-api.kalshi.co`). **`KALSHI_API_KEY` Token auth is not used** — see Kalshi quick-start docs.
- **Cross-asset / equity hint:** `PredictionMarketsEngine.get_equity_signal` (Python) — maps contract wording to bullish/bearish/neutral language from **live** YES price.
- **Django fan-out:** `propagate_trade_signal(strategy_name, symbol, side, qty, ...)` with `SIGNAL_URL` + `SIGNAL_SECRET` set; `strategy_name` must match algochains.ai bot name **exactly**.
- **Audit trail for marketplace:** After each bot decision, `record_prediction_market_bot_metric`; review with `get_prediction_market_bot_metrics`.
- **Onyx:** `onyx_search` / `onyx_ask` for internal runbooks and this repo’s `blueprints/PREDICTION_MARKET_BOTS_BLUEPRINT.md`.

## Michael / AlphaLoop-style edge (latency)

When a human references “faster feed vs slower venue”:

1. Measure `latency_ms_observed` with a **real** clock skew test (same event timestamp vs your reference venue).  
2. Store in `record_prediction_market_bot_metric` → promotes only with verifiable JSONL history.  
3. Do **not** claim dollar PnL unless sourced from exchange or broker statements.

## Marketplace alignment

Prediction-market bots share the same **honesty** bar as futures: promotion requires sustained JSONL metrics + compliance + no fabricated Sharpe on unresolved contracts. Work with legal on Kalshi/Polymarket jurisdictional constraints before “subscribable” live claims.

## Related files

- `src/algochains_mcp/order_flow/prediction_markets.py`
- `src/algochains_mcp/trade_propagation.py`
- `src/algochains_mcp/prediction_market_metrics.py`
- `examples/trade_propagation/send_signal.py`
