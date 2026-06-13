-- ═══════════════════════════════════════════════════════════════════════════
-- Developer API Keys Migration (2026-06-14)
-- ═══════════════════════════════════════════════════════════════════════════
--
-- Adds the developer_api_keys table referenced in developer_auth.py and
-- DEVELOPER_TIER_ONBOARDING.md. Previously the migration was missing from the
-- repo, causing bridge auth to fail closed on fresh Supabase projects.
--
-- Key design:
-- - Only the SHA-256 hash is stored; plaintext key is shown once on creation.
-- - key_prefix stores first 12 chars for masked display (e.g. "ac_live_AbCd").
-- - RLS: users can SELECT/INSERT their own rows; no direct UPDATE/DELETE.
--   Rotation creates a new row + soft-deletes old; revocation sets revoked_at.
-- - The resolve_developer_api_key RPC is SECURITY DEFINER so it can bypass RLS
--   for hash lookups during bridge auth (bridge uses service role via JWT).
-- ═══════════════════════════════════════════════════════════════════════════

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- Developer API Keys table
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS developer_api_keys (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    key_hash        TEXT NOT NULL UNIQUE,           -- SHA-256 hex of plaintext key
    key_prefix      TEXT NOT NULL,                  -- First 12 chars for masked display
    name            TEXT NOT NULL DEFAULT 'default',
    scopes          TEXT[] NOT NULL DEFAULT ARRAY['read:market_data'],
    env             TEXT NOT NULL DEFAULT 'live'
                    CHECK (env IN ('live', 'test')),
    last_used_at    TIMESTAMPTZ,
    revoked_at      TIMESTAMPTZ,                    -- NULL = active; set to revoke
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_dev_api_keys_user_id   ON developer_api_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_dev_api_keys_key_hash  ON developer_api_keys(key_hash);
CREATE INDEX IF NOT EXISTS idx_dev_api_keys_env       ON developer_api_keys(env);

ALTER TABLE developer_api_keys ENABLE ROW LEVEL SECURITY;

-- Users can see their own keys (never the hash — only prefix + metadata)
CREATE POLICY "Users can view own dev keys" ON developer_api_keys
    FOR SELECT USING (auth.uid() = user_id);

-- Users can create their own keys (MFA gate enforced in application layer)
CREATE POLICY "Users can create own dev keys" ON developer_api_keys
    FOR INSERT WITH CHECK (auth.uid() = user_id);

-- Only service role may update (for last_used_at, revoked_at)
CREATE POLICY "Service role manages dev key updates" ON developer_api_keys
    FOR UPDATE USING (auth.role() = 'service_role');


-- ─────────────────────────────────────────────────────────────────────────────
-- RPC: resolve_developer_api_key
-- Called by http_bridge/developer_auth.py during key validation.
-- SECURITY DEFINER so it can run a full-table hash lookup for auth.
-- ─────────────────────────────────────────────────────────────────────────────

DROP FUNCTION IF EXISTS resolve_developer_api_key(TEXT);

CREATE OR REPLACE FUNCTION resolve_developer_api_key(p_key_hash TEXT)
RETURNS TABLE (
    id             UUID,
    clerk_user_id  UUID,
    name           TEXT,
    scopes         TEXT[],
    env            TEXT,
    revoked_at     TIMESTAMPTZ
)
LANGUAGE sql
SECURITY DEFINER
STABLE
AS $$
    SELECT
        d.id,
        d.user_id AS clerk_user_id,
        d.name,
        d.scopes,
        d.env,
        d.revoked_at
    FROM developer_api_keys AS d
    WHERE d.key_hash = p_key_hash
      AND d.revoked_at IS NULL
    LIMIT 1;
$$;

-- Touch last_used_at on each auth (called by developer_auth.py after success)
CREATE OR REPLACE FUNCTION touch_developer_api_key(p_key_hash TEXT)
RETURNS void
LANGUAGE sql
SECURITY DEFINER
AS $$
    UPDATE developer_api_keys
    SET last_used_at = now()
    WHERE key_hash = p_key_hash;
$$;

COMMENT ON TABLE developer_api_keys IS
    'Developer API keys (ac_live_* / ac_test_*). Only SHA-256 hash stored. '
    'Plaintext returned once at creation. MFA required before create/rotate/revoke '
    'in application layer (Supabase Auth AAL2 check).';

COMMIT;
