-- ═══════════════════════════════════════════════════════════════════════════
-- Subscriber API Keys Migration (2026-06-14)
-- ═══════════════════════════════════════════════════════════════════════════
--
-- Adds the subscriber_api_keys table + resolve_subscriber_api_key RPC that is
-- referenced in subscriber_auth.py (L31–33) but was missing from the repo.
-- Without this migration, Supabase bridge auth fails closed on fresh projects.
--
-- Key design:
-- - sub_live_* keys map to a subscriber's scoped paper portfolio.
-- - bot_slug identifies which bot's signal stream the subscriber is following.
-- - RLS: service_role only — subscriber keys are never user-self-serve; they
--   are issued by the platform on subscription via the marketplace/Stripe flow.
-- ═══════════════════════════════════════════════════════════════════════════

BEGIN;

CREATE TABLE IF NOT EXISTS subscriber_api_keys (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID REFERENCES auth.users(id) ON DELETE SET NULL,
    key_hash        TEXT NOT NULL UNIQUE,           -- SHA-256 hex of plaintext key
    key_prefix      TEXT NOT NULL,                  -- First 12 chars for masked display
    bot_slug        TEXT NOT NULL,                  -- e.g. "mnq-scalper"
    env             TEXT NOT NULL DEFAULT 'live'
                    CHECK (env IN ('live', 'test')),
    scopes          TEXT[] NOT NULL DEFAULT ARRAY['read:signals', 'read:pnl'],
    paper_account_id TEXT,                          -- Alpaca paper account linked to this sub
    last_used_at    TIMESTAMPTZ,
    revoked_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sub_api_keys_key_hash  ON subscriber_api_keys(key_hash);
CREATE INDEX IF NOT EXISTS idx_sub_api_keys_user_id   ON subscriber_api_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_sub_api_keys_bot_slug  ON subscriber_api_keys(bot_slug);

ALTER TABLE subscriber_api_keys ENABLE ROW LEVEL SECURITY;

-- Service role only — subscribers cannot self-manage keys via Supabase client
CREATE POLICY "Service role manages subscriber keys" ON subscriber_api_keys
    USING (auth.role() = 'service_role');


-- ─────────────────────────────────────────────────────────────────────────────
-- RPC: resolve_subscriber_api_key
-- Called by subscriber_auth.py during bridge auth validation.
-- ─────────────────────────────────────────────────────────────────────────────

DROP FUNCTION IF EXISTS resolve_subscriber_api_key(TEXT);

CREATE OR REPLACE FUNCTION resolve_subscriber_api_key(p_key_hash TEXT)
RETURNS TABLE (
    key_id           UUID,
    subscriber_id    UUID,
    bot_slug         TEXT,
    env              TEXT,
    scopes           TEXT[],
    paper_account_id TEXT,
    revoked_at       TIMESTAMPTZ
)
LANGUAGE sql
SECURITY DEFINER
STABLE
AS $$
    SELECT
        s.id AS key_id,
        s.user_id AS subscriber_id,
        s.bot_slug,
        s.env,
        s.scopes,
        s.paper_account_id,
        s.revoked_at
    FROM subscriber_api_keys AS s
    WHERE s.key_hash = p_key_hash
      AND s.revoked_at IS NULL
    LIMIT 1;
$$;

CREATE OR REPLACE FUNCTION touch_subscriber_api_key(p_key_hash TEXT)
RETURNS void
LANGUAGE sql
SECURITY DEFINER
AS $$
    UPDATE subscriber_api_keys
    SET last_used_at = now()
    WHERE key_hash = p_key_hash;
$$;

COMMENT ON TABLE subscriber_api_keys IS
    'Subscriber API keys (sub_live_*). Issued by platform on marketplace subscription. '
    'Service-role only — not user-self-serve. Scoped to signal stream + paper P&L for '
    'one bot_slug.';

COMMIT;
