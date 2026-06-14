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

CREATE TABLE IF NOT EXISTS public.subscriber_api_keys (
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

ALTER TABLE public.subscriber_api_keys
    ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES auth.users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS bot_slug TEXT,
    ADD COLUMN IF NOT EXISTS paper_account_id TEXT,
    ADD COLUMN IF NOT EXISTS last_used_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_sub_api_keys_key_hash  ON public.subscriber_api_keys(key_hash);
CREATE INDEX IF NOT EXISTS idx_sub_api_keys_user_id   ON public.subscriber_api_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_sub_api_keys_bot_slug  ON public.subscriber_api_keys(bot_slug) WHERE bot_slug IS NOT NULL;

ALTER TABLE public.subscriber_api_keys ENABLE ROW LEVEL SECURITY;

-- Service role only — subscribers cannot self-manage keys via Supabase client
DROP POLICY IF EXISTS "Service role manages subscriber keys" ON public.subscriber_api_keys;
CREATE POLICY "Service role manages subscriber keys" ON public.subscriber_api_keys
    TO service_role
    USING (true);


-- ─────────────────────────────────────────────────────────────────────────────
-- RPC: resolve_subscriber_api_key
-- Called by subscriber_auth.py during bridge auth validation.
-- ─────────────────────────────────────────────────────────────────────────────

-- The original 20260523 RPC returned only (subscriber_id, scopes). PostgreSQL
-- cannot change OUT parameters with CREATE OR REPLACE, so drop before recreate.
DROP FUNCTION IF EXISTS public.resolve_subscriber_api_key(TEXT);

CREATE OR REPLACE FUNCTION public.resolve_subscriber_api_key(p_key_hash TEXT)
RETURNS TABLE (
    subscriber_id    TEXT,
    bot_slug         TEXT,
    env              TEXT,
    scopes           TEXT[],
    paper_account_id TEXT
)
LANGUAGE sql
SECURITY DEFINER
STABLE
AS $$
    SELECT
        COALESCE(subscriber_id, user_id::TEXT) AS subscriber_id,
        bot_slug,
        env,
        scopes,
        paper_account_id
    FROM public.subscriber_api_keys
    WHERE key_hash = p_key_hash
      AND revoked_at IS NULL
    LIMIT 1;
$$;

CREATE OR REPLACE FUNCTION public.touch_subscriber_api_key(p_key_hash TEXT)
RETURNS void
LANGUAGE sql
SECURITY DEFINER
AS $$
    UPDATE public.subscriber_api_keys
    SET last_used_at = now()
    WHERE key_hash = p_key_hash;
$$;

COMMENT ON TABLE public.subscriber_api_keys IS
    'Subscriber API keys (sub_live_*). Issued by platform on marketplace subscription. '
    'Service-role only — not user-self-serve. Scoped to signal stream + paper P&L for '
    'one bot_slug.';

COMMIT;
