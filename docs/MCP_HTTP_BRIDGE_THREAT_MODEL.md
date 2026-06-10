# MCP HTTP Bridge — Threat Model and Pen-Test Report

**Generated:** 2026-04-21  
**Scope:** `src/algochains_mcp/http_bridge.py` and related auth modules  
**Phase:** Hidden-killers v8 Phase H  
**Status:** Findings documented; remediations applied or tracked

---

## 1. Asset Inventory

| Asset | Location | Sensitivity |
|-------|----------|-------------|
| Tradovate session / OAuth tokens | `.env` + `state/tradovate_token.json` | CRITICAL — enables live order placement |
| `ALGOCHAINS_BRIDGE_API_KEY` (owner key) | `.env` | HIGH — full owner tool access |
| `sub_live_*` subscriber keys | Supabase `subscriber_api_keys` table | HIGH — subscriber data + fill reporting |
| Supabase service role key | `.env` | HIGH — row-level security bypass capability |
| ML model artifacts (`.pkl`, `.json`) | `models/` | MEDIUM — IP; integrity tied to SHA-256 checks |
| Marketplace listing data | Supabase + file system | LOW — publicly browsable subset |

---

## 2. Actor Matrix

| Actor | Auth mechanism | Tool surface | Can call danger tiers? |
|-------|---------------|-------------|------------------------|
| Anonymous / no key | None | `PUBLIC_TOOLS` only (13 tools) | No |
| Owner | `ALGOCHAINS_BRIDGE_API_KEY` + `user_email == OWNER_EMAIL` | `PUBLIC_TOOLS` + `OWNER_TOOLS` (40 tools total) | Yes — with `confirm=true` |
| Subscriber | `sub_live_*` key resolved against Supabase | `SUBSCRIBER_TOOLS` (12 tools, scoped) | No |
| Dev mode (localhost only) | `ALGOCHAINS_BRIDGE_DEV_MODE=true` | Public tools without key | No |

Current implementation note: HTTP bridge dispatch now delegates danger tier,
caller scope, and confirmation checks to `src/algochains_mcp/tool_policy.py`.
`confirm=true` is the canonical approval argument; `confirmed=true` is a legacy
alias for older tool schemas.

### Public Tools (13)
Read-only: market data, strategy discovery, Onyx search, macro signals, VIX term structure, latency profile.

### Owner Tools (27)
Authoritative list lives in `src/algochains_mcp/http_bridge.py::OWNER_TOOLS`.
The owner surface includes live account reads, owner bot metrics, controlled
marketplace operations, Onyx ingest/status, and explicitly confirmed order
execution tools.

### Subscriber Tools (7)
`get_signal_stream`, `ack_signal`, `get_my_pnl`, `get_my_fills`, `get_my_assignments`, `report_fill`, `heartbeat`

---

## 3. Trust Boundaries

```
[Internet / algochains.ai]
        │ HTTPS (CloudFlare / nginx)
        ▼
┌─────────────────────────────────┐
│  FastAPI HTTP Bridge (port 3333)│  ← trust boundary #1
│  CORS: 5 allowed origins        │
│  Auth: key-based tier routing   │
└────────────┬────────────────────┘
             │ in-process call
             ▼
┌─────────────────────────────────┐
│  MCP Tool dispatch (server.py)  │  ← trust boundary #2 (tool code runs here)
│  300+ tools; some call brokers  │
└────────────┬────────────────────┘
             │
    ┌────────┼────────┐
    ▼        ▼        ▼
Tradovate  Supabase  Local files
(live $$)  (RLS)     (model pkl)
```

---

## 4. Abuse Paths and Findings

### H-F1 — Public tool enumeration (ACCEPTED RISK)
**Path:** `GET /tools` — no key required → lists all 13 public tools and their danger tiers.  
**Risk:** LOW — all public tools are read-only. Enumeration reveals capability surface but no credentials.  
**Verdict:** Accepted. Exposure matches intent (public strategy marketplace discovery).

### H-F2 — Subscriber calling OWNER_TOOLS (PATCHED in code)
**Path:** Subscriber key → calls `place_order` → bridge checks `tool_name not in SUBSCRIBER_TOOLS` → returns 403 with `available_tools` list.  
**Verification:** Unit-tested. `_check_auth()` returns 403 with tool list before `call_tool()` is reached.  
**Verdict:** ✅ Mitigated.

### H-F3 — Danger tier bypass — missing `confirm` (PARTIALLY MITIGATED)
**Path:** Owner calls `place_order` without `confirm=true` → `get_danger_tier()` returns `TIER_ORDER_EXEC` → bridge returns 400 with required arg hint.  
**Gap found:** `get_danger_tier()` import is inside a `try/except`; if `tool_danger_tiers` module fails to import, the gate logs a warning and **allows execution** (fail-open).  
**Finding:** H-F3-WARN — silently passes danger gate if module import fails.  
**Remediation (applied):** See section 6.1 — danger tier failure now returns explicit error rather than allowing execution.

### H-F4 — CORS origin bypass
**Path:** Attacker crafts request with `Origin: https://evil.com` → bridge allows only 5 hardcoded origins.  
**Verification:** CORS middleware rejects unknown origins (browser-enforced). Direct `curl` calls bypass CORS but still require the API key — CORS is defense-in-depth, not the primary gate.  
**Verdict:** ✅ Correctly configured. Non-browser clients need key regardless.

### H-F5 — BRIDGE_API_KEY unset in production (HIGH RISK)
**Path:** If `ALGOCHAINS_BRIDGE_API_KEY` env var is missing at startup, the bridge logs a critical warning and locks down (unless `ALGOCHAINS_BRIDGE_DEV_MODE=true`).  
**Verification:** `_DEV_MODE=false` by default → missing key → all authenticated endpoints return 401.  
**Gap:** No startup health check asserts key is set before traffic arrives.  
**Finding:** H-F5-WARN — a misconfigured deploy silently starts with no owner access; no pagerduty-style alert fires.  
**Remediation:** Document in ops runbook; add `/health` auth_mode field (implemented in Phase J).

### H-F6 — SSRF via tool arguments
**Path:** Attacker crafts `{"tool": "onyx_ask", "arguments": {"query": "..."}}`; if Onyx makes outbound HTTP based on user input, SSRF possible.  
**Verification:** `onyx_ask` calls local ChromaDB / SQLite — no outbound URL construction from user input found.  
**Verdict:** ✅ Low risk in current implementation. Flag for review if Onyx gains URL-fetch capability.

### H-F7 — Oversized payloads / DoS
**Path:** POST `/mcp` with multi-MB JSON body → no body-size limit enforced at bridge level.  
**Risk:** MEDIUM — could exhaust memory or hang tool dispatch.  
**Finding:** H-F7-OPEN — no rate limit or max body size configured.  
**Remediation:** Track in Phase J (rate limiting backlog); reverse-proxy (nginx/Cloudflare) should enforce 1 MB body limit.

### H-F8 — Replay attack on owner key
**Path:** Attacker intercepts `ALGOCHAINS_BRIDGE_API_KEY` → replays against bridge.  
**Risk:** HIGH if key is leaked. Bridge has no per-request nonce or expiry.  
**Mitigation present:** Key is static shared secret; Cloudflare WAF + IP allowlist recommended.  
**Finding:** H-F8-ACCEPTED — static key is standard for internal APIs; document key rotation SOP.

### H-F9 — Error message leakage
**Path:** Tool raises unexpected exception → `_check_auth` or dispatcher catches and returns error dict.  
**Verification:** Bridge returns `{"error": str(e)}` — stack traces not included in response body. Log files on server contain full trace.  
**Verdict:** ✅ Acceptable for internal API. Recommend redacting broker-specific error strings in subscriber-facing responses.

### H-F10 — Tool confusion (calling dangerous tool via subscriber path)
**Path:** Subscriber calls `{"tool": "run_marketplace_autopilot"}` → bridge checks `SUBSCRIBER_TOOLS` membership → 403 returned.  
**Verification:** ✅ `_check_auth` enforces whitelist before dispatch.

---

## 5. Auth Matrix Verification Results

| Scenario | Expected | Verified |
|----------|----------|---------|
| No key → `get_vix_term_structure` | 200 | ✅ (public tool) |
| No key → `place_order` | 401 | ✅ |
| Owner key → `place_order` without `confirm` | 400 require confirm | ✅ |
| Owner key → `place_order` with `confirm=true` | dispatched | ✅ |
| Subscriber key → `get_my_pnl` | 200 scoped | ✅ |
| Subscriber key → `get_account` | 403 | ✅ |
| Unknown key → any tool | 401 | ✅ |
| Dev mode + no key → public tool | 200 | ✅ (localhost only) |

---

## 6. Remediations Applied

### 6.1 — Danger tier fail-open → fail-closed (H-F3)

The `get_danger_tier()` import was in a bare `try/except` that silently allowed tool execution if the module was missing. Changed to return an explicit error on import failure.

### 6.2 — /health endpoint enriched (Phase J overlap)

`/health` now returns `auth_mode`, `version`, `server_import_ok`, and `tool_count` — providing immediate triage signal for incidents.

### 6.3 — Request-ID middleware added (Phase J overlap)

Every request now gets an `X-Request-Id` header and a structured log line with method, path, status, and elapsed_ms.

---

## 7. Open Findings (tracked)

| ID | Severity | Finding | Owner | ETA |
|----|----------|---------|-------|-----|
| H-F3-WARN | MEDIUM | Danger tier import failure allows execution | Phase J bridge tests | Sprint+1 |
| H-F5-WARN | LOW | No startup assertion that BRIDGE_API_KEY is set | Ops runbook | Sprint+1 |
| H-F7-OPEN | MEDIUM | No request body size limit at bridge layer | Rate limiting (Phase J) | Sprint+2 |
| H-F8-ACCEPTED | HIGH | Static shared secret — no per-request nonce | Key rotation SOP | Ongoing |

---

## 8. Pen-Test Checklist (run periodically)

```bash
BASE=http://localhost:3333

# Anonymous: public tool
curl -s -X POST $BASE/mcp -H 'Content-Type: application/json' \
  -d '{"tool":"get_vix_term_structure","arguments":{}}' | jq .status

# Anonymous: owner tool → expect 401
curl -s -X POST $BASE/mcp -H 'Content-Type: application/json' \
  -d '{"tool":"place_order","arguments":{}}' | jq .

# Owner without confirm → expect 400
curl -s -X POST $BASE/mcp \
  -H "X-Api-Key: $ALGOCHAINS_BRIDGE_API_KEY" \
  -H "X-User-Email: $OWNER_EMAIL" \
  -H 'Content-Type: application/json' \
  -d '{"tool":"place_order","arguments":{"symbol":"MNQ","action":"BUY","quantity":1}}' | jq .

# Subscriber: call owner tool → expect 403
curl -s -X POST $BASE/mcp \
  -H "X-Api-Key: sub_live_TESTKEY" \
  -H 'Content-Type: application/json' \
  -d '{"tool":"get_account","arguments":{}}' | jq .

# /health check
curl -s $BASE/health | jq .
```
