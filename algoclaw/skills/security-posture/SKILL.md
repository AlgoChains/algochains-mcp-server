# security-posture

**Tier:** 0 (safe, read-only)  
**Trigger:** Weekly (Sunday 2 AM), on-demand  
**MCP Tool:** `run_algoclaw_skill("security-posture")`

## What It Does

Runs a systematic audit of the MCP server against CoSAI 12-category threats and SAFE-MCP 81 techniques.
Returns a coverage report showing what's protected and what's still open.

## Steps

1. Check CoSAI 12 categories (hardcoded knowledge of current coverage)
2. Check SAFE-MCP critical techniques: T001, T012, T034, T051, T067, T071, T078, T089, T094
3. Call `get_rate_limit_status()` for all destructive tools
4. Call `check_all_broker_credentials()` — verify no credentials exposed
5. Check replay guard: `verify_hmac_signature` wired to all signed endpoints?
6. Check that kill-switch has owner-ID gate
7. Compute coverage score: covered/total × 100

## Output Format

```json
{
  "audit_date": "2026-04-08",
  "cosai_coverage": {
    "total_categories": 12,
    "covered": 7,
    "partial": 2,
    "open": 3,
    "score_pct": 58.3
  },
  "safe_mcp_coverage": {
    "T001_command_injection": "partial",
    "T012_fake_tool_invocation": "partial",
    "T034_path_traversal": "open",
    "T051_replay_attack": "covered",
    "T067_rate_limit_bypass": "covered",
    "T071_session_token_theft": "partial",
    "T078_tool_description_manipulation": "open",
    "T089_dos_resource_exhaustion": "open",
    "T094_injection_via_tool_output": "open"
  },
  "open_items": [
    "T034: Add path_validator() to file-writing tools",
    "T078: Hash tool descriptions at startup",
    "T089: Per-client total request budget",
    "T094: Sanitize Onyx output before agent context"
  ],
  "credential_check": "all_masked",
  "rate_limits_active": true,
  "replay_guard_active": true,
  "owner_gate_active": true
}
```
