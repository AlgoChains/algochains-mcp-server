-- AlgoChains MCP Server — Developer API Keys
-- Migration: 20260521_developer_api_keys.sql
-- Apply via: supabase db push (mcp-server project)
--
-- Creates:
--   1. developer_api_keys      — hashed ac_live_* / ac_test_* keys issued by
--                                the Stripe APP provisioning endpoint.
--   2. resolve_developer_api_key() RPC — SECURITY DEFINER lookup used by
--                                developer_auth.py (plaintext key never stored).
--
-- Security model:
--   - Plaintext key never persisted; only SHA-256 hex digest stored.
--   - Service_role writes only. No anon/authenticated INSERT policy.
--   - RLS enabled; authenticated user can read their own row via clerk_user_id.
--   - SECURITY DEFINER RPC runs as postgres to bypass RLS for the hash lookup.

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. developer_api_keys
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.developer_api_keys (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key_hash             TEXT NOT NULL UNIQUE,          -- SHA-256(plaintext key)
    key_prefix           TEXT NOT NULL,                 -- first 12 chars, e.g. "ac_live_xY9z" — safe to display
    clerk_user_id        TEXT,                          -- Clerk user_xxx; null until linked
    stripe_customer_id   TEXT,                          -- cus_xxxx from Stripe APP provision
    stripe_account_id    TEXT,                          -- acc_xxxx from Stripe APP provision
    email                TEXT NOT NULL DEFAULT '',
    product_id           TEXT NOT NULL DEFAULT 'developer-tier'
                         CHECK (product_id IN ('developer-tier', 'paper-tier', 'live-tier')),
    scopes               TEXT[] NOT NULL DEFAULT ARRAY['read:market_data', 'read:signals'],
    env                  TEXT NOT NULL DEFAULT 'live'
                         CHECK (env IN ('live', 'test')),
    active               BOOLEAN NOT NULL DEFAULT TRUE,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    trial_ends_at        TIMESTAMPTZ,                   -- NULL = no trial (paid tier)
    revoked_at           TIMESTAMPTZ                    -- NULL = not revoked
);

CREATE INDEX IF NOT EXISTS idx_dak_key_hash        ON public.developer_api_keys(key_hash);
CREATE INDEX IF NOT EXISTS idx_dak_clerk_user_id   ON public.developer_api_keys(clerk_user_id) WHERE clerk_user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_dak_email           ON public.developer_api_keys(email) WHERE email <> '';
CREATE INDEX IF NOT EXISTS idx_dak_active          ON public.developer_api_keys(active) WHERE active = TRUE;

ALTER TABLE public.developer_api_keys ENABLE ROW LEVEL SECURITY;

-- No user-facing SELECT policy — key_hash must never be exposed to authenticated users.
-- Service_role bypasses RLS for writes and the SECURITY DEFINER RPC handles reads.

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. resolve_developer_api_key — SECURITY DEFINER RPC
--    Called by developer_auth.py with the SHA-256 hash of the raw key.
--    Returns (clerk_user_id, scopes, env) or empty if not found / revoked.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION public.resolve_developer_api_key(p_key_hash TEXT)
RETURNS TABLE (
    clerk_user_id   TEXT,
    scopes          TEXT[],
    env             TEXT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    RETURN QUERY
    SELECT
        d.clerk_user_id,
        d.scopes,
        d.env
    FROM public.developer_api_keys d
    WHERE d.key_hash     = p_key_hash
      AND d.active       = TRUE
      AND d.revoked_at   IS NULL
      AND (d.trial_ends_at IS NULL OR d.trial_ends_at > now())
    LIMIT 1;
END;
$$;

-- Restrict direct execute to service_role only.
REVOKE EXECUTE ON FUNCTION public.resolve_developer_api_key(TEXT) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.resolve_developer_api_key(TEXT) TO service_role;

COMMENT ON TABLE public.developer_api_keys IS
    'Hashed developer API keys (ac_live_*/ac_test_*) issued by the Stripe APP provisioning endpoint. '
    'Plaintext key is never stored. resolve_developer_api_key() RPC used for auth lookups.';
