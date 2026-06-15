# AlgoChains MCP Server — Revenue Platform Build Plan v1.0

> **Status:** In progress · **Owner:** AlgoChains Core · **Date:** 2026-06-13
> **Scope:** Six revenue/scale workstreams + legal compliance audit.
> **Constraint:** Server is PUBLICLY ACCESSIBLE. Every money-movement path is
> fail-closed, idempotent, owner-gated, and dry-run-capable. No real funds move
> unless live Stripe Connect keys + verified connected accounts are configured.

This plan is the index. Each workstream has its own section with schema,
engine, MCP tool surface, safety model, and compliance hooks. The companion
legal memo is `docs/LEGAL_COMPLIANCE_AUDIT.md`.

---

## Workstream Index

| # | Workstream | New module | Migration | Money? | Owner-gated | Status |
|---|-----------|-----------|-----------|--------|-------------|--------|
| 1 | Creator payouts (Stripe Connect) | `cloud_saas/connect_payouts.py` | `20260528` | ✅ real | ✅ | ✅ built |
| 2 | Usage-based metered billing | `cloud_saas/usage_metering.py` | `20260527` | ✅ real | partial | ✅ built |
| 3 | Affiliate / referral program | `cloud_saas/referrals.py` | `20260526` | ✅ real | partial | ✅ built |
| 4 | Realized-P&L + HWM perf-fee | `cloud_saas/realized_pnl.py` | `20260529` | ✅ real | ✅ | ✅ built (perf-fee OFF) |
| 5 | OAuth 2.1 (Claude.ai native) | `auth/oauth_resource.py` + `http_transport.py` | — | ❌ | ❌ | ✅ RS done; AS delegated |
| 6 | Multi-tenant isolation | `multi_tenant/isolation.py` | `20260530`, `20260531` | ❌ | ❌ | ✅ foundation + context wired |

### Completion notes (WS4–6)
- **WS4** — `get_my_realized_pnl` (subscriber, live/paper segregated, 4.41(b) on paper)
  and `reconcile_creator_pnl` (owner-gated, per-subscriber→creator attribution,
  net realized, positive-only, period-scoped). `compute_hwm_performance_fee()`
  implements the researched HWM formula but **performance fees are DISABLED by
  default** (`ALGOCHAINS_PERFORMANCE_FEE_RATE=0.0`) — per legal research a perf
  fee on directed/copied trading reads as discretionary CTA activity. Enable only
  after counsel sign-off + incentive-fee conflict disclosure.
- **WS5** — Resource-server side complete: RFC 9728 PRM + `WWW-Authenticate`
  discovery (`http_transport.py`) and JWT validation (`auth/oauth_resource.py`:
  JWKS signature + `aud`/`iss`/`exp`/scope, `sub`→identity, `app_metadata.tenant_id`
  →tenant). The Authorization Server (`/authorize`, `/token`, PKCE, DCR) is
  **delegated to an external IdP** (Supabase Auth / WorkOS) per the MCP spec —
  set `ALGOCHAINS_OAUTH_ISSUER` + `ALGOCHAINS_OAUTH_JWKS_URI` to enable.
- **WS6** — `current_tenant_id()` RLS helper, `tenant_id` columns, null-safe
  permissive RLS policy templates (`20260531`), and request-lifecycle tenant
  context (`isolation.py` contextvar set from the validated token claim in
  `http_transport._auth`). Full per-table RLS-enforced rollout is **phased**
  (do not flip live trading tables to FORCE in one step — checklist in
  `20260531_tenant_rls_policies.sql`).

---

## Cross-cutting design principles

1. **Fail closed on money.** Any payout/transfer path returns an error rather
   than guessing. Missing config = no-op + CRITICAL log, never a silent partial.
2. **Idempotency everywhere.** Every Stripe write carries an idempotency key
   derived from a stable domain id (payout id, usage event id, referral id).
   Every webhook is deduped against `webhook_events` (already exists).
3. **Owner gate on initiation.** Tools that *move* money require `OWNER_API_TOKEN`.
   Tools that *read* balances/usage are subscriber/developer-scoped.
4. **Dry-run first.** Payout tools accept `dry_run=True` (default) and return the
   computed transfer plan without executing.
5. **Compliance hooks.** Any performance/earnings display carries the
   `compliance.disclosures` disclaimer. Hypothetical/simulated results carry the
   CFTC Reg. 4.41(b) prescribed disclaimer.
6. **Audit trail.** Every money event writes an append-only ledger row before the
   external call, and reconciles status on webhook.

---

## WS1 — Creator Payouts (Stripe Connect)

**Goal:** When a subscriber pays for a marketplace strategy, the creator earns a
revenue share (default 80%), paid out automatically via Stripe Connect.

- **Accounts:** Express connected accounts (Stripe-hosted onboarding, KYC handled
  by Stripe). Creator links via `create_creator_onboarding_link()`.
- **Money split:** Destination charge with `application_fee_amount` (platform fee)
  OR separate transfer keyed to the subscription invoice. Default: record the
  subscriber payment, then `Transfer.create(amount=80%, destination=acct)` with an
  idempotency key = `payout_<ledger_id>`.
- **Schema:** `creator_connect_accounts`, `creator_payouts` (append-only ledger),
  payout status lifecycle `pending→transferred→paid→reversed`.
- **Tools:** `create_creator_onboarding_link`, `get_creator_payout_status`,
  `run_creator_payouts` (owner, dry_run default), `get_my_creator_earnings`.
- **Safety:** KYC/`payouts_enabled` checked before any transfer; negative-balance
  guard; reversal handling on refund webhook.

## WS2 — Usage-Based Metered Billing

**Goal:** Hybrid pricing — base subscription includes a monthly tool-call quota;
calls above quota bill at $0.01/call via Stripe Billing Meters.

- **Metering:** `middleware.py` already intercepts every call. Add async,
  non-blocking usage recording keyed by resolved key hash. Aggregate monthly.
- **Stripe:** Billing Meters API (`meter_events`), overage as a metered price
  component on the existing subscription. Idempotent meter event ids.
- **Schema:** `usage_counters` (per key, per month, calls + overage), append-only
  `usage_events` sample (sampled, not every call, to bound storage).
- **Tools:** `get_my_usage` (current cycle: used/quota/overage/projected cost).
- **Safety:** never block a tool call on metering failure; quota is advisory until
  Stripe meter is configured; double-count protection via event id.

## WS3 — Affiliate / Referral Program

**Goal:** Word-of-mouth flywheel. Referrers earn 20% of a referred subscriber's
first 3 months, paid via Stripe Connect (reuses WS1 rails).

- **Attribution:** `referral_code` already captured in `join_waitlist`. 30-day
  attribution window; first-touch.
- **Schema:** `referral_codes` (owner per code), `referral_attributions`
  (referred_subscriber → code, window), `referral_commissions` (ledger).
- **Tools:** `create_referral_code`, `get_my_referrals`, `get_referral_earnings`.
- **Safety:** self-referral block; one attribution per subscriber; commission only
  on realized (paid) subscription invoices.

## WS4 — Realized-P&L Payout Hooks

**Goal:** Move subscriber copy-trade from paper-only to live: realized fills from
the live bot fan-out populate `subscriber_fills.pnl_usd` from real broker fills,
and creator earnings can optionally include a performance component.

- **Source:** `trade_propagation.py` live fan-out → `copy_trade_signals` →
  subscriber execution → `subscriber_fills` with real `pnl_usd`.
- **Schema:** extend `subscriber_fills` with `is_live`, `broker`, `broker_fill_id`;
  `performance_fee_ledger` (optional high-water-mark performance fees).
- **Tools:** `get_my_realized_pnl` (live vs paper segregated, 4.41 disclaimer on
  any hypothetical/paper component), owner reconciliation tool.
- **Safety:** live payouts require explicit live-tier + risk consent (WS from prior
  commit); high-water-mark so performance fees never double-charge a drawdown.

## WS5 — OAuth 2.1 (Claude.ai Native Connector)

**Goal:** Let Claude.ai add AlgoChains as a native remote MCP connector
(Max/Team/Enterprise), unlocking the primary organic growth channel.

- **Endpoints:** `/.well-known/oauth-authorization-server` (RFC 8414),
  `/.well-known/oauth-protected-resource` (RFC 9728), `/authorize`, `/token`,
  optional `/register` (RFC 7591 dynamic client registration).
- **Flow:** Authorization Code + PKCE S256 (RFC 7636). Bearer token (signed,
  short-lived) maps to a subscriber identity.
- **Discovery:** 401 responses carry `WWW-Authenticate: Bearer
  resource_metadata="…"` so MCP clients find the auth server.
- **Schema:** `oauth_clients`, `oauth_authorization_codes`, `oauth_tokens`.
- **Safety:** PKCE required; short code TTL; token bound to scopes mapped from
  subscriber scopes; HTTPS-only.

## WS6 — Multi-Tenant Isolation

**Goal:** Enforce tenant/subscriber id on every data-access path (defense in depth
beyond RLS) so a public deployment can safely serve many orgs/subscribers.

- **Pattern:** A `TenantContext` resolved once per request from the bearer/key;
  every subscriber/developer tool receives the resolved id from the server, never
  from caller input (already true for subscriber tools — extend to all).
- **DB:** RLS policies keyed on a tenant claim; a CI test asserts no user-data
  table is readable without a tenant predicate.
- **Tools:** internal — hardening, not new surface. Adds `get_tenant_diagnostics`
  (owner) to verify isolation.
- **Safety:** BOLA/IDOR class prevention; cross-tenant access returns 404 not 403
  (no existence leak).

---

## Sequencing

1. Legal memo (`LEGAL_COMPLIANCE_AUDIT.md`) — frames everything.
2. WS1 + WS3 (share Connect rails) → WS2 (metering) → WS4 (realized P&L).
3. WS5 (OAuth) → WS6 (multi-tenant) — platform/scale.
4. CI: each migration validated by `migrations.yml`; each engine unit-tested.
