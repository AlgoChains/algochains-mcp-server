# Trade propagation — developer reference

This folder mirrors Roo Fernando’s `TRADE_PROPAGATION.md` contract for the Django listener.

- **`send_signal.py`** — HTTP + HMAC client with **no hardcoded secrets** (env only).
- For MCP integration use tool **`propagate_trade_signal`** (`algochains_mcp.trade_propagation`), which uses the same JSON body and `X-Signature` scheme.

## Environment

| Variable | Purpose |
|----------|---------|
| `SIGNAL_URL` or `ALGOCHAINS_SIGNAL_URL` | POST target (e.g. `https://your-domain/signals/signal/`) |
| `SIGNAL_SECRET` or `ALGOCHAINS_SIGNAL_SECRET` | HMAC-SHA256 key over raw JSON body |

## Paper only

Per platform policy, connect **paper** brokerage accounts for propagation tests. See the original onboarding doc from Roo for full warnings.

## Dummy loop

Use `dummy_signal_test.py` from Roo’s repo with `BOT_NAME` matching algochains.ai exactly; point env vars at your staging signal URL first.
