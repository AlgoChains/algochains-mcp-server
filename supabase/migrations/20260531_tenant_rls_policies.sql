-- AlgoChains MCP Server — Tenant RLS Policy Templates (phased rollout)
-- Migration: 20260531_tenant_rls_policies.sql
-- Apply via: supabase db push (mcp-server project)
--
-- Adds the FIRST, NON-BREAKING layer of Postgres-enforced tenant isolation on
-- the tenant-keyed tables (developer_api_keys, subscriber_api_keys), using the
-- null-safe permissive pattern so existing access is never broken during rollout.
--
-- IMPORTANT — current enforcement model:
--   * User data is accessed via the SECURITY DEFINER RPCs and the service_role
--     client, which BYPASS RLS. So RLS is NOT yet the authoritative isolation
--     layer; the app layer (validated token claim -> contextvar -> per-query
--     tenant filter) + the SECURITY DEFINER RPCs are.
--   * These policies are therefore a TEMPLATE + the first defense-in-depth layer.
--     Flipping every user-data table to RLS-enforced (FORCE + service_role
--     retirement on user paths) is a PHASED change tracked in
--     docs/REVENUE_PLATFORM_BUILD_PLAN_V1.md (WS6) — do NOT force-enable RLS on
--     live trading tables in one step or you may break data access.
--
-- Null-safe permissive pattern: a row is visible when there is NO tenant context
-- (legacy single-tenant / service_role) OR the tenant matches. This lets us
-- backfill tenant_id and migrate clients incrementally without an outage.

-- current_tenant_id() is defined in 20260530_multi_tenant.sql (STABLE, NULL-safe).

-- ─────────────────────────────────────────────────────────────────────────────
-- developer_api_keys
-- ─────────────────────────────────────────────────────────────────────────────
DROP POLICY IF EXISTS tenant_isolation_select ON public.developer_api_keys;
CREATE POLICY tenant_isolation_select ON public.developer_api_keys
    FOR SELECT
    USING (
        public.current_tenant_id() IS NULL
        OR tenant_id IS NULL
        OR tenant_id = public.current_tenant_id()
    );

-- ─────────────────────────────────────────────────────────────────────────────
-- subscriber_api_keys
-- ─────────────────────────────────────────────────────────────────────────────
DROP POLICY IF EXISTS tenant_isolation_select ON public.subscriber_api_keys;
CREATE POLICY tenant_isolation_select ON public.subscriber_api_keys
    FOR SELECT
    USING (
        public.current_tenant_id() IS NULL
        OR tenant_id IS NULL
        OR tenant_id = public.current_tenant_id()
    );

-- ─────────────────────────────────────────────────────────────────────────────
-- Phased rollout checklist (per docs WS6) — do these table-by-table, with the
-- cross-tenant integration test green at each step:
--   1. ADD tenant_id column + backfill from owning tenant.
--   2. ADD null-safe permissive SELECT policy (this file's pattern).
--   3. Migrate that table's reads off service_role onto the authenticated role
--      carrying the JWT (so RLS actually applies).
--   4. Tighten policy to strict equality; ADD WITH CHECK on write paths.
--   5. ALTER TABLE ... FORCE ROW LEVEL SECURITY.
-- ─────────────────────────────────────────────────────────────────────────────

COMMENT ON POLICY tenant_isolation_select ON public.subscriber_api_keys IS
    'Null-safe permissive tenant isolation (phase 2 of WS6 rollout). Visible when '
    'no tenant context (service_role/legacy) or tenant matches. Tighten to strict '
    'equality + FORCE only after reads move off service_role.';
