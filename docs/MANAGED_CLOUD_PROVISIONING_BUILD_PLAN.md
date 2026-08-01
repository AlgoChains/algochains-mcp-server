# Build Plan — Managed Cloud Provisioning ("spin up infra on someone's behalf, and charge for it")

> **Status:** Proposed · **Revenue lever:** infra resale margin + control-plane fee
> **Grounds on existing code:** `cloud_saas/tenant_manager.py` (TenantManager),
> `cloud_saas/white_label_engine.py`, `cloud_saas/usage_metering.py`,
> `cloud_saas/connect_payouts.py`, `multi_tenant/isolation.py`, `auth/oauth_resource.py`.
> **Constraint:** the MCP server is PUBLIC and callable by autonomous agents — so every
> provisioning path is spend-capped, plan-gated, owner/credit-gated, and fail-closed.

## 1. What it is

A set of MCP tools that let a customer (or their AI agent) **provision real cloud
infrastructure** — a Postgres DB, a VM, an object bucket, a managed AlgoChains
instance — and be **billed for it with a markup**. Two modes:

- **Resale (default, zero-config):** we provision in **our** cloud account, tag by
  tenant, meter raw cost, bill cost + markup. Fastest onboarding; we carry the bill
  and the blast radius → hard spend caps are mandatory.
- **BYOC (enterprise upgrade):** we provision into the **customer's** cloud account
  via a delegated, short-lived role. Customer owns data + bill; we charge a flat
  **control-plane fee** (no infra markup). Solves data-residency/compliance.

## 2. Architecture (per provisioning tool call)

```
agent → MCP tool → [1 authZ+tenant] → [2 credit/spend-cap] → [3 cost plan/preview]
      → [4 provision via IaC] → [5 meter + markup → Stripe] → [6 audit + TTL teardown]
```

1. **AuthN/Z + tenant:** resolve caller identity (OAuth `sub` via `auth/oauth_resource`
   or `ac_live_*`/owner key) → `tenant_id` from `app_metadata` (never caller input;
   `multi_tenant/isolation.set_tenant`). Load the tenant's allowed tools + spend cap +
   remaining prepaid credit.
2. **Credit / spend-cap gate:** read the per-task spend cap from request metadata;
   reject immediately if the op would exceed cap or remaining credit. **Prepaid
   credits with a hard stop at zero** is the default for agent callers.
3. **Cost plan/preview:** run `terraform plan` / Pulumi `preview()`, estimate monthly
   cost, reject if it breaches cap. Destructive/expensive ops require `owner_token`.
4. **Provision (IaC):** **Pulumi Automation API** (`createOrSelectStack(tenant_id)` →
   `up()`) — purpose-built to embed IaC behind an API and spin a unique stack per
   customer. One stack + one state file per tenant (S3 prefix `s3://bucket/<tenant>/…`
   with **S3 native locking**). Tag everything `tenant=<id>`. Ephemeral stacks carry a
   **TTL** so agent-created resources can't leak.
5. **Meter + markup:** emit a **Stripe Billing Meter** event (`event_name`,
   `stripe_customer_id`, `value` = raw_cost × (1+markup)); decrement prepaid credit;
   nightly-reconcile against the cloud Cost & Usage Report. (Stripe's 2026 metering
   supports automatic markup %.)
6. **Audit + teardown:** append an immutable, **customer-inspectable** audit row
   (caller, tenant, tool, plan diff, cost estimate, result); scheduled per-tenant drift
   `plan`; budget-alarm auto-quarantine; TTL `destroy()` for ephemeral stacks.

## 3. Federation & protection (BYOC — acting in the customer's cloud, no long-lived secrets)

- **AWS:** customer creates a cross-account IAM role; we `AssumeRole` with a
  **per-tenant `ExternalId`** (server-generated, stored, **validated on every assume** —
  the confused-deputy fix that 37% of vendors get wrong). STS returns 1-hour creds.
- **GCP:** **Workload Identity Federation** → service-account impersonation → 1-hour
  token. No JSON keys.
- **Azure:** **Azure Lighthouse** delegated resource management (cross-tenant RBAC to
  our service principal scoped to one resource group).
- **Least privilege:** provisioning-only role scoped to supported resource types +
  `aws:ResourceTag/tenant` conditions + permission boundaries; separate read-only role
  for drift/health.
- **Vaulting:** ExternalIds / role ARNs / config in **Vault namespaces per tenant** or
  KMS; prefer **dynamic secrets** (short TTL). Never store long-lived cloud keys.

## 4. MCP tool surface (new)

| Tool | Tier | Gate | Purpose |
|------|------|------|---------|
| `estimate_provision_cost(spec)` | READ_ONLY | tenant | plan-only monthly cost estimate (call before provisioning) |
| `provision_resource(spec, ttl?)` | WRITE_SAFE | tenant + credit + cap | create infra (resale or BYOC) |
| `get_resource(id)` / `list_resources()` | READ_ONLY | tenant | status, endpoints, cost-to-date |
| `destroy_resource(id, owner_token)` | DESTRUCTIVE | **owner** | teardown (or TTL auto-destroy) |
| `get_infra_balance()` | READ_ONLY | tenant | remaining prepaid credit + spend cap |
| `connect_cloud_account(provider, role_arn, external_id?)` | WRITE_SAFE | tenant | BYOC delegation onboarding |

All read tools fail closed; `provision_resource` fails closed if cost can't be
estimated or credit can't be confirmed. `destroy_resource` and any op over the cap are
**owner_token-gated**, mirroring `run_creator_payouts`.

## 5. New modules & migration

- `cloud_saas/provisioning_engine.py` — Pulumi Automation API wrapper; per-tenant
  stack/state; plan→cost→gate→up; TTL teardown; **fail-closed**.
- `cloud_saas/cloud_federation.py` — STS AssumeRole+ExternalId / GCP WIF / Azure
  Lighthouse credential brokers; per-tenant ExternalId issue+validate.
- `cloud_saas/spend_guard.py` — prepaid credit ledger + per-task spend cap pre-flight.
- Migration `20260532_provisioning.sql` — `cloud_accounts` (tenant→provider→role_arn→
  external_id_hash→status), `provisioned_resources` (append-only, tenant, stack, spec,
  status, cost_to_date, ttl_at), `infra_credits` (tenant prepaid balance ledger),
  `provisioning_audit` (immutable, customer-inspectable). RLS service-role only;
  `current_tenant_id()` policies.

## 6. Phased delivery

1. **P0 — resale MVP:** `estimate_provision_cost` + `provision_resource` + `get_resource`
   + `get_infra_balance` for ONE resource type (managed Postgres in our account),
   prepaid credits, hard cap, Stripe meter, TTL teardown. Owner-gated `destroy`.
2. **P1 — BYOC:** `connect_cloud_account` (AWS ExternalId first), provision into
   customer account, control-plane fee billing.
3. **P2 — breadth:** more resource types (VM, bucket, managed AlgoChains instance),
   GCP/Azure federation, drift remediation, virtual-card spend caps for agents.

## 7. Risks & mitigations

| Risk | Mitigation |
|------|-----------|
| Agent loop spawns thousands of resources | per-tenant resource quotas + provisioning rate limit + hard credit stop |
| Runaway cloud bill (resale) | prepaid credit, per-task cap, cloud budget alarm → auto-quarantine |
| Confused deputy (BYOC) | per-tenant ExternalId, validated on every assume |
| Cross-tenant access | one role + one state + one tag namespace per tenant; RLS |
| Destructive op by agent | `destroy`/expensive ops require owner_token + human approval |
| Secret leakage | Vault per-tenant dynamic secrets; никогда long-lived keys; audit every action |

> Keep this strictly OFF any bot/order/risk/auth trading path (CLAUDE.md). It is a
> separate SaaS surface; trading guardrails are untouched.
