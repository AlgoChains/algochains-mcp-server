-- AlgoChains MCP Server — Multi-Tenant White-Label Foundation
-- Migration: 20260530_multi_tenant.sql
-- Apply via: supabase db push (mcp-server project)
--
-- DEFENSE-IN-DEPTH TENANT ISOLATION MODEL
-- ─────────────────────────────────────────────────────────────────────────────
-- Tenant isolation is enforced at multiple layers so no single failure leaks
-- one tenant's data to another:
--
--   1. TOKEN CLAIM     — the validated JWT carries app_metadata.tenant_id. This
--                        is set by the IdP at login and CANNOT be supplied by the
--                        caller's request body (anti-BOLA, OWASP API1:2023).
--   2. RLS PREDICATE   — per-table Row Level Security policies compare the row's
--                        tenant_id against public.current_tenant_id(), which reads
--                        the claim above. A user can only see/write their tenant.
--   3. NEVER service_role ON USER PATHS — service_role BYPASSES RLS. User-facing
--                        request handlers must use the authenticated (anon+JWT)
--                        client so RLS applies. service_role is for trusted
--                        server-side jobs only.
--   4. CONTEXTVAR      — the application propagates tenant_id through async call
--                        chains via a contextvar (src/algochains_mcp/multi_tenant/
--                        isolation.py) so server-side code never re-derives tenant
--                        from caller input.
--
-- Per-table tenant RLS policies are added INCREMENTALLY in later migrations so
-- existing access is not broken in a single sweep. This migration ships only the
-- tenants registry, tenant_id columns on key-bearing tables, and the
-- current_tenant_id() building block.

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Tenant registry
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.tenants (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         TEXT UNIQUE NOT NULL,
    name              TEXT,
    tier              TEXT NOT NULL DEFAULT 'starter'
                      CHECK (tier IN ('starter', 'growth', 'professional', 'enterprise')),
    stripe_account_id TEXT,
    admin_email       TEXT,
    active            BOOLEAN NOT NULL DEFAULT TRUE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tenants_tenant_id ON public.tenants(tenant_id);
CREATE INDEX IF NOT EXISTS idx_tenants_active    ON public.tenants(active);

ALTER TABLE public.tenants ENABLE ROW LEVEL SECURITY;
-- service_role manages the registry; tenant-scoped read policy added incrementally.

COMMENT ON TABLE public.tenants IS
    'White-label tenant registry. tenant_id is the stable string identifier carried '
    'in JWT app_metadata.tenant_id and matched by RLS via current_tenant_id().';

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Associate API keys with tenants
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE public.developer_api_keys
    ADD COLUMN IF NOT EXISTS tenant_id TEXT;

ALTER TABLE public.subscriber_api_keys
    ADD COLUMN IF NOT EXISTS tenant_id TEXT;

COMMENT ON COLUMN public.developer_api_keys.tenant_id IS
    'Owning tenant for this developer key. Building block for tenant-scoped RLS.';
COMMENT ON COLUMN public.subscriber_api_keys.tenant_id IS
    'Owning tenant for this subscriber key. Building block for tenant-scoped RLS.';

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. current_tenant_id() — RLS building block
--    Reads the tenant_id from the validated JWT's app_metadata claim. NULL-safe:
--    returns NULL on any missing claim / parse error so an absent claim never
--    accidentally widens access (predicates of the form `tenant_id = current_tenant_id()`
--    match nothing when this returns NULL).
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION public.current_tenant_id()
RETURNS TEXT
LANGUAGE plpgsql
STABLE
SET search_path = ''
AS $$
BEGIN
    RETURN current_setting('request.jwt.claims', true)::jsonb
               -> 'app_metadata' ->> 'tenant_id';
EXCEPTION
    WHEN OTHERS THEN
        RETURN NULL;
END;
$$;

REVOKE EXECUTE ON FUNCTION public.current_tenant_id() FROM PUBLIC, anon;
GRANT  EXECUTE ON FUNCTION public.current_tenant_id() TO authenticated, service_role;

COMMENT ON FUNCTION public.current_tenant_id() IS
    'Returns the caller tenant_id from JWT app_metadata.tenant_id (NULL-safe). '
    'Defense-in-depth: token claim -> RLS predicate via this fn -> never '
    'service_role on user paths -> contextvar propagation in the app layer. '
    'Per-table tenant RLS policies are added incrementally to avoid breaking '
    'existing access.';
