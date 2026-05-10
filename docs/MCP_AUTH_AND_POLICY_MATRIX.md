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
| HTTP subscriber | `sub_live_*` Supabase key | `SUBSCRIBER_TOOLS` only | no owner/broker execution surface |

## Secret Split

- `ALGOCHAINS_BRIDGE_API_KEY` authenticates owner access to the HTTP bridge.
- `OWNER_API_TOKEN` authenticates owner execution through local/dynamic paths.

Do not assume one key grants the other surface.

## Manifest Contract

`mcp_tool_manifest` emits schema v2 entries with:

- `danger_tier`, `danger_label`, and `tier_source`.
- `implementation_status` and `required_env`.
- `approval` with canonical `confirm=true` and legacy aliases.
- `transports` and `visibility` for stdio, HTTP bridge, and subscriber surfaces.
