# ASSP Phase 5 AppSec Closeout — MCP Server

**Node:** sonia-air (advisory) · **Date:** 2026-07-25 · **Authority:** agent_memory

Status of AC-MCP-001..010 after Phase 5 sprint remediations in `algochains-mcp-server`.

| Rule | Finding | Status | Remediation |
|------|---------|--------|-------------|
| AC-MCP-001 | Unauthenticated MCP/SSE transport | **Remediated** | `sse_server._validate_api_key` fail-closed when `ALGOCHAINS_SSE_KEY` unset; localhost-only bypass requires `ALGOCHAINS_SSE_ALLOW_UNAUTH=1`. Non-localhost bind without key raises at startup. `http_transport` fail-closed without `ALGOCHAINS_HTTP_TRANSPORT_SECRET`. |
| AC-MCP-002 | Danger-tier import fail-open | **Remediated** | `assp_policy_import_error()` in `tool_policy.py`; stdio `call_tool`, `execute_dynamic_tool`, demo-mode tier gate, and replay-guard tier lookup fail-closed with `assp_rule=AC-MCP-002` on `ImportError`. Tests in `test_assp_appsec_sprint.py`. Splunk telemetry still **gap** (`telemetry_present: false`). |
| AC-MCP-003 | Unsigned `_developer_scopes` spoof | **Remediated** | `strip_untrusted_internal_auth` + signed bridge context (`internal_auth_context.py`). Tests in `test_appsec_medium_fixes_20260721.py`. |
| AC-MCP-004 | `report_fill` forged PnL / fills | **Remediated** | PnL/order-id blocked without `daemon_authorized`; entry/exit/modify now require `signal_id` unless daemon-authorized. Tests in sprint + medium-fixes suites. |
| AC-MCP-005 | SSRF via notification webhook | **Remediated** | Shared `ssrf_guard.py`; Slack/Discord configure + send paths validate URLs; `configure_notifications` owner-gated. Learn Hub uses fixed HTTPS hosts only. |
| AC-MCP-006 | Verification outbound burst | **Remediated (Tier-1)** | Verification tools removed from Tier-1 exposure; `create_support_ticket` also escalated off Tier-1. Rate limits at provider layer still **gap** for Splunk detection. |
| AC-MCP-007 | `dispatch_tower_job` without owner | **Remediated** | Handler requires `owner_token == OWNER_API_TOKEN`; schema updated to require `owner_token`. |
| AC-MCP-008 | `run_mcpt_pipeline` non-dry-run | **Remediated** | `dry_run` defaults `True`; live runs require matching `owner_token`. Still Tier-1 but safe default. |
| AC-MCP-009 | Onyx ingest from anonymous caller | **Remediated** | `run_onyx_ingest` + `connect_onyx_docs` owner-gated; `run_onyx_ingest` removed from Tier-1. Path jail for `connect_onyx_docs` unchanged (`data_ingestion._onyx_ingest_roots`). |
| AC-MCP-010 | Bot-ops secret disclosure via `get_*` | **Remediated (Tier-1)** | `get_all_bot_ops_status` removed from Tier-1; handler redacts without owner token. Rithmic live tools removed from Tier-1 + owner-gated. Bot-ops/metrics bulk reads trimmed from Tier-1 per sprint context. |

## Additional hardening (same sprint)

- **CORS:** `http_transport` no longer defaults to wildcard; explicit allowlist or opt-in `ALGOCHAINS_HTTP_ALLOW_WILDCARD_CORS=1`.
- **SQLite writes:** `record_trade_episode` now requires owner token before writing `~/.algochains/trade_memory.db`.
- **Rithmic Tier-1:** `get_rithmic_live_*` removed from smart mode; owner token required in handler.

## Still open (honest gaps)

1. **AC-MCP-006 telemetry** — No `algochains:mcp_audit` sourcetype shipping yet (`telemetry_present: false` on all rules).
2. **`run_mcpt_pipeline` in Tier-1** — Safe by default (`dry_run=True`) but still callable; consider moving to full mode only if abuse observed.
3. **`inject_session_context`** — WRITE_LOCAL tier; not owner-gated this sprint (lower exposure than Onyx ingest).

## Test commands

```bash
cd /Users/soniaramos/Documents/CursorProjects/algochains-mcp-server
.venv/bin/python -m pytest tests/test_assp_appsec_sprint.py tests/test_appsec_medium_fixes_20260721.py tests/test_learn_hub_health_security.py -q
# 2026-07-25 sonia-air: 27 passed
```
