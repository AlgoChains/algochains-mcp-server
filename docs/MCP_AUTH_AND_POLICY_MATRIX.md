# MCP Auth And Policy Matrix

This package uses `src/algochains_mcp/tool_policy.py` as the shared policy layer
for stdio, HTTP bridge, and dynamic tool execution.

## Surfaces

| Surface | Auth | Tool surface | High-risk approval |
|---|---|---|---|
| Stdio direct | local process access | smart mode exposes tier-1 tools; full mode exposes all tools | individual tool gates plus broker guardrails |
| Stdio `execute_dynamic_tool` | `OWNER_API_TOKEN` for `ORDER_EXEC`+ | hidden tools reached after discovery | `confirm=true` for `ORDER_EXEC`+ |
| HTTP public | no key | `PUBLIC_TOOLS` only | no `ORDER_EXEC` or `DESTRUCTIVE` tools allowed |
| HTTP owner | `ALGOCHAINS_BRIDGE_API_KEY` | `PUBLIC_TOOLS` + `OWNER_TOOLS` | `confirm=true`; caller scope ceilings apply |
| HTTP subscriber | Subscriber Supabase key | `SUBSCRIBER_TOOLS` only | no owner/broker execution surface |
| HTTP developer | `ac_live_*` / `ac_test_*` Supabase key via `X-Api-Key` or bearer token | `DEVELOPER_TOOLS` only | max danger tier `WRITE_LOCAL`; `execute_dynamic_tool` blocked |

Subscriber note: local stdio only registers the subscriber onboarding/status
funnel (`accept_subscriber_terms`, `join_bot`, `get_subscriber_status`, usage,
referral, and realized-P&L helpers). The portfolio, signal-stream, fill,
paper-order, and daemon callback tools are the HTTP bridge `SUBSCRIBER_TOOLS`
surface. See `docs/SUBSCRIBER_TOOLS.md`.

## Secret Split

- `ALGOCHAINS_BRIDGE_API_KEY` authenticates owner access to the HTTP bridge.
- `OWNER_API_TOKEN` authenticates owner execution through local/dynamic paths.
- Developer keys are hashed before Supabase lookup and resolve through
  `resolve_developer_api_key`; plaintext keys are never stored by the bridge.
- Subscriber and developer keys are separate surfaces. A developer key does not
  grant copy-trade subscriber data, and a subscriber key does not grant strategy
  publishing/backtest tools.

Do not assume one key grants the other surface.

## Developer Tier Contract

Developer-tier behavior is defined by:

- `src/algochains_mcp/developer_auth.py` for key prefix detection, hashing,
  Supabase resolution, positive/negative cache TTLs, and fail-closed behavior.
- `src/algochains_mcp/developer_tools.py` for the allowlist, hard blocklist, and
  per-tool scope requirements.
- `src/algochains_mcp/developer_rate_limiter.py` for default limits:
  60 RPM, 1,000 RPH, 15 requests per 10 seconds, and 256 KB request bodies.

Hard constraints:

- Only `ac_live_*` and `ac_test_*` keys enter the developer path.
- Developer keys resolve before owner-key comparison, so a malformed developer
  key cannot fall through into owner auth.
- Developer tools must be `READ_ONLY` or `WRITE_LOCAL`; broker execution,
  live bot/account state, subscriber tools, and `execute_dynamic_tool` are
  explicitly blocked.
- Missing scopes return a `missing_scope:<scope>` denial reason instead of
  falling back to public or owner behavior.

## Manifest Contract

`mcp_tool_manifest` emits schema v2 entries with:

- `danger_tier`, `danger_label`, and `tier_source`.
- `implementation_status` and `required_env`.
- `approval` with canonical `confirm=true` and legacy aliases.
- `transports` and `visibility` for stdio, HTTP bridge, and subscriber surfaces.
