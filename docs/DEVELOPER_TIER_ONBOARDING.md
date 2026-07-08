# AlgoChains Developer Tier — Onboarding Guide

## Prerequisites

- Active AlgoChains Developer Pro subscription (algochains.ai → Account → Membership)
- Developer credential created at `algochains.ai/account/developer-keys/`
- Python 3.10+

---

## 1. Install the MCP package

```bash
pip install algochains-mcp-server
```

For extra ML/data extras:

```bash
pip install "algochains-mcp-server[ml]"
```

---

## 2. Obtain your developer credential

1. Visit [algochains.ai/account/developer-keys/](https://algochains.ai/account/developer-keys/)
2. Click **Create developer key**
3. Copy the plaintext credential — it is shown **once only**. Store it in a password manager or secrets vault.

Keys use the prefix `ac_live_` (production) or `ac_test_` (sandbox).

---

## 3. Configure your MCP client

Developer keys connect to the **hosted bridge** at `https://api.algochains.ai/api/mcp`.
That endpoint uses a custom JSON shape (`{"tool","arguments"}`) for the AlgoChains SDK
and CLI — **not** standard MCP Streamable HTTP.

### Cursor / Claude Desktop / Windsurf (stdio — required)

Desktop IDEs spawn the local `algochains-mcp` process over **stdio**. Do **not** point
Cursor at `api.algochains.ai/api/mcp` — Cursor's MCP client expects Streamable HTTP at
`/mcp`, and the bridge API will return 404 or fail protocol negotiation.

Install and generate config:

```bash
pip install algochains-mcp-server
algochains-mcp --generate-config cursor   # writes ~/.cursor/mcp.json
```

The generated block uses **command only** (no `url`, no `transport`):

```json
{
  "mcpServers": {
    "algochains": {
      "command": "algochains-mcp",
      "env": {
        "ALGOCHAINS_TOOL_MODE": "smart",
        "ALGOCHAINS_BRIDGE_KEY": "ac_live_YOUR_KEY_HERE"
      }
    }
  }
}
```

Put your developer key in `ALGOCHAINS_BRIDGE_KEY` (or `ALGOCHAINS_DEVELOPER_KEY`) in the
`env` block. Restart the IDE after editing.

### Python / SDK client (HTTP bridge)

For programmatic access outside desktop IDEs, call the hosted bridge directly:

```python
import os
from mcp import ClientSession
from mcp.client.http import http_client

dev_key = os.environ["AC_DEV_KEY"]   # ac_live_... stored in env

async with http_client(
    url="https://api.algochains.ai/api/mcp",
    headers={"X-Api-Key": dev_key},
) as (read, write):
    async with ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool("detect_market_regime", {})
        print(result)
```

### Environment variable

```bash
# Store your developer credential
export AC_DEV_KEY="ac_live_..."
```

---

## 4. Available tools (developer tier)

Developer keys have access to read-only and analysis tools only.
No broker order execution access.

```
GET https://api.algochains.ai/tools
X-Api-Key: ac_live_...
```

Example response:

```json
{
  "developer_tools": [
    "browse_strategy_marketplace",
    "detect_market_regime",
    "discover_tools",
    "get_backtest_results",
    "get_bot_card_data",
    "get_earnings_catalyst",
    "get_factor_model",
    "get_historical_bars",
    "get_latency_profile",
    "get_tool_details",
    "get_macro_signals",
    "get_marketplace_listings",
    "get_monte_carlo_result",
    "get_signal_trade_correlation",
    "get_strategy_academic_citations",
    "get_tick_data_summary",
    "get_validation_gates",
    "get_vix_term_structure",
    "get_volatility_surface",
    "list_bot_research_attachments",
    "mcp_tool_manifest",
    "onyx_ask",
    "onyx_search",
    "query_data_warehouse",
    "run_builder_backtest",
    "run_hmm_regime_detection",
    "submit_to_marketplace",
    "validate_strategy_metrics"
  ],
  "scopes": ["read:market_data", "read:signals"]
}
```

Source of truth:

- `src/algochains_mcp/http_bridge.py` resolves developer keys before owner keys.
- `src/algochains_mcp/developer_tools.py` owns the allowlist, blocked tools,
  and per-tool scope requirements.

---

## 5. Rate limits

| Window | Default limit |
|--------|--------------|
| Per minute (RPM) | 60 requests |
| Per hour (RPH) | 1,000 requests |
| Burst (per 10s) | 15 requests |
| Max request body | 256 KB |

When a limit is hit, the bridge returns HTTP 429 with:

```json
{
  "error": "rate_limit_exceeded",
  "reason": "rpm_limit_exceeded (limit=60)",
  "retry_after_ms": 5200,
  "limits": { "rpm": 60, "rph": 1000, "burst_per_10s": 15 }
}
```

Always respect the `Retry-After` response header.

---

## 6. Optional: local stdio mode (advanced)

For development or air-gapped environments, you can run the MCP server locally.

> **Warning:** Local mode still uses your developer credential for bridge-remote tool
> calls. It does NOT grant access to owner-only tools or broker execution.
> Never put `OWNER_TOKEN` or broker credentials in your developer environment.

```bash
export AC_DEV_KEY="ac_live_..."
export ALGOCHAINS_TOOL_MODE=smart     # smart (default) = documented safe Tier-1 set
algochains-mcp                        # starts local stdio MCP server
```

Cursor local config for local stdio:

```json
{
  "mcpServers": {
    "algochains-local": {
      "command": "algochains-mcp",
      "env": {
        "AC_DEV_KEY": "ac_live_YOUR_KEY_HERE",
        "ALGOCHAINS_TOOL_MODE": "smart"
      }
    }
  }
}
```

---

## 7. Key rotation and revocation

- Rotate a key from `algochains.ai/account/developer-keys/` → **Rotate**
- Old key is revoked immediately; new plaintext shown once
- Update all integrations before closing the rotate dialog
- Revoke without rotation using the **Revoke** button

---

## 8. Scopes

Keys are issued with default scopes `read:market_data` and `read:signals`.
Additional scopes (`write:backtest`, `publish:listing`, `read:data_warehouse`) may
be requested via support. Scope requirements per tool are returned in the `/tools`
endpoint response.

Scope-gated tools:

| Scope | Tools |
|-------|-------|
| `read:market_data` | `get_historical_bars`, `get_tick_data_summary`, `get_volatility_surface`, `get_factor_model` |
| `read:signals` | `run_hmm_regime_detection`, `get_signal_trade_correlation` |
| `read:backtest` | `get_backtest_results`, `get_monte_carlo_result` |
| `write:backtest` | `run_builder_backtest` |
| `publish:listing` | `submit_to_marketplace` |
| `read:data_warehouse` | `query_data_warehouse` |

---

## 9. What developer keys cannot access

- Live broker order execution (`place_order`, `close_position`, etc.)
- Live bot health and metrics (`get_bot_health`, `get_positions`, etc.)
- Marketplace autopilot or bulk operations
- Copy-trade subscriber signals (requires a separate subscriber key)
- Owner-scoped system tools (`get_system_heartbeat`, `run_onyx_ingest`, etc.)
- Dynamic escalation through `execute_dynamic_tool`
- Numerai tournament upload or dry-run submit tools
