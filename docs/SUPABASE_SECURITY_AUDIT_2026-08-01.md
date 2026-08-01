# Supabase Security Audit — Team Follow-up (updated)

**Date:** 2026-08-01 (continued)  
**Org:** AlgoChains  
**Projects:** prod `trkpzsnwjtmvgppuzlwu`, dev `bjxdmitvyjymtneswdkw`, B2B `rgphdfbqhvkzlnbwqgyj`

---

## Status snapshot

| Area | Status |
|------|--------|
| Prod Supabase security advisors (ERROR/WARN) | **Cleared** — only INFO `rls_enabled_no_policy` remain (deny-by-default tables) |
| Anon paper-balance / cron DEFINER RPCs | **Revoked** |
| `copy_trade_signals` public UPDATE | **Locked** to service_role |
| Subscriber risk-field client UPDATE | **Locked** (`paused` only) |
| Ops tables authenticated SELECT | **Locked** to service_role |
| Marketplace SECURITY DEFINER views | **Converted** to `security_invoker=true` |
| Function `search_path` advisories | **Pinned** |
| Dev RLS off / anon API keys | **Hardened** |
| `trade_evidence_rollup` unauthenticated | **Secret header required** + cron updated |
| Key rotation (dev API keys) | **Still needs humans** |
| Edge secret promotion (remove bootstrap) | **Still needs humans** |
| Public SELECT surface review | **Still needs product/eng review** |

---

## Applied in this continuation

### Prod DB
1. Marketplace views → `security_invoker = true`; revoked write grants; `v_marketplace_validation` no longer granted to `anon`
2. Pinned `search_path` on flagged functions
3. Dropped authenticated blanket SELECT on `trade_log`, `bot_events`, `bracket_audit`, `order_cancel_log`, `broker_fill_imports`, `ghost_pnl_audit` → **service_role only**

### Edge + cron
4. Redeployed `trade_evidence_rollup` v4 with `x-rollup-secret` / Bearer secret check  
5. Updated `cron.job` `trade_evidence_rollup_daily` to send `x-rollup-secret` (via `cron.alter_job`)  
6. Checked in repo sources under `supabase/functions/trade_evidence_rollup/` (env-only; no bootstrap secret in git)

### Dev
7. `v_approved_marketplace` → security_invoker + SELECT-only grants

### Git sync
8. Migration mirror: `supabase/migrations/20260801184500_security_hardening_rls_rpcs.sql`

---

## Remaining human actions

### P0

#### 1. Rotate compromised / exposed API keys (dev)
Treat all keys previously readable via `"algochains-core"` / `get_signal_api_keys()` as burned.
- [ ] Rotate signal + developer API keys
- [ ] Confirm no production clients use the **dev** Supabase URL/anon key

#### 2. Promote rollup secret to Edge secrets (remove bootstrap)
Live function currently accepts a bootstrap secret (also stored in cron).  
Repo copy requires `TRADE_EVIDENCE_ROLLUP_SECRET` only.

```bash
# Generate new secret, set on project, update cron, redeploy from repo
supabase secrets set TRADE_EVIDENCE_ROLLUP_SECRET=<new> --project-ref trkpzsnwjtmvgppuzlwu
supabase functions deploy trade_evidence_rollup --project-ref trkpzsnwjtmvgppuzlwu --no-verify-jwt
# Then cron.alter_job to the new secret (see secrets/ note from audit)
```

Secret material for the **current** live bootstrap/cron value is in `secrets/trade_evidence_rollup_2026-08-01.txt` (gitignored). Do not paste into Slack/git.

#### 3. QA after ops-table lockdown
Anything that used a **user JWT** to read `trade_log` / `bot_events` / etc. will now fail.
- [ ] Confirm Command Center / dashboards use **service_role** or Django proxy
- [ ] Confirm MCP live metrics paths still work (they should — service_role)

#### 4. QA subscriber pause + paper paths
- [ ] Pause/unpause still works (column grant = `paused` only)
- [ ] Client PATCH of `max_contracts` / loss caps must fail; admin paths use backend

### P1

#### 5. Review intentional anon SELECT tables
Still public-read by design in many cases (`bot_metrics_live`, kalshi_*, traces, etc.). Product should mark Public vs Internal.

#### 6. `waitlist-slack-notify`
Still `verify_jwt=false` but has custom `x-waitlist-secret`.  
- [ ] Confirm secret is set non-empty in Edge secrets  
- [ ] Rotate if unsure

#### 7. Public marketplace validation UI
`v_marketplace_validation` is no longer anon-readable and uses invoker RLS.  
If the marketing site needed anon validation scores, add a **curated** public view/RPC with only safe columns.

---

## Regression checklist

1. [ ] Subscriber pause/resume  
2. [ ] Copy-trade signal reads OK; client UPDATE fails  
3. [ ] Paper balance RPC from browser fails  
4. [ ] Manual invoke rollup **without** secret → 401  
5. [ ] Manual invoke rollup **with** secret → 200  
6. [ ] Next morning cron `trade_evidence_rollup_daily` succeeds  
7. [ ] Trade log / bot metrics in internal tools still load  

---

## Project refs

| Name | Ref |
|------|-----|
| Algochains_Django (prod) | `trkpzsnwjtmvgppuzlwu` |
| Algochains_Django_development | `bjxdmitvyjymtneswdkw` |
| B2B | `rgphdfbqhvkzlnbwqgyj` |
