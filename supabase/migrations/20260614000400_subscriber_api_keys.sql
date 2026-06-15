-- ═══════════════════════════════════════════════════════════════════════════
-- Subscriber API Keys Migration (2026-06-14)
-- ═══════════════════════════════════════════════════════════════════════════
--
-- Repairs/extends the subscriber_api_keys table + resolve_subscriber_api_key
-- RPC used by subscriber_auth.py. It preserves the 20260523 copy-trade schema
-- contract while adding newer identity/scope metadata columns.
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
    subscriber_id   TEXT NOT NULL,                  -- stable subscriber key used by tools
    key_hash        TEXT NOT NULL UNIQUE,           -- SHA-256 hex of plaintext key
    key_prefix      TEXT NOT NULL,                  -- First 12 chars for masked display
    bot_slug        TEXT,                           -- optional marketplace/listing slug
    env             TEXT NOT NULL DEFAULT 'live'
                    CHECK (env IN ('live', 'test')),
    scopes          TEXT[] NOT NULL DEFAULT ARRAY[
        'signal_stream', 'my_pnl', 'my_fills',
        'my_assignments', 'heartbeat', 'report_fill', 'paper_trade'
    ],
    tier            TEXT NOT NULL DEFAULT 'paper'
                    CHECK (tier IN ('paper', 'live')),
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    paper_account_id TEXT,                          -- Alpaca paper account linked to this sub
    last_used_at    TIMESTAMPTZ,
    expires_at      TIMESTAMPTZ,
    revoked_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.subscriber_api_keys
    ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES auth.users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS subscriber_id TEXT,
    ADD COLUMN IF NOT EXISTS bot_slug TEXT,
    ADD COLUMN IF NOT EXISTS env TEXT NOT NULL DEFAULT 'live'
        CHECK (env IN ('live', 'test')),
    ADD COLUMN IF NOT EXISTS scopes TEXT[] NOT NULL DEFAULT ARRAY[
        'signal_stream', 'my_pnl', 'my_fills',
        'my_assignments', 'heartbeat', 'report_fill', 'paper_trade'
    ],
    ADD COLUMN IF NOT EXISTS tier TEXT NOT NULL DEFAULT 'paper'
        CHECK (tier IN ('paper', 'live')),
    ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS paper_account_id TEXT,
    ADD COLUMN IF NOT EXISTS last_used_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMPTZ;

UPDATE public.subscriber_api_keys
   SET subscriber_id = user_id::TEXT
 WHERE (subscriber_id IS NULL OR subscriber_id = '')
   AND user_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_sub_api_keys_key_hash  ON public.subscriber_api_keys(key_hash);
CREATE INDEX IF NOT EXISTS idx_sak_key_hash           ON public.subscriber_api_keys(key_hash);
CREATE INDEX IF NOT EXISTS idx_sak_subscriber_id      ON public.subscriber_api_keys(subscriber_id);
CREATE INDEX IF NOT EXISTS idx_sak_active             ON public.subscriber_api_keys(active) WHERE active = TRUE;
CREATE INDEX IF NOT EXISTS idx_sub_api_keys_user_id   ON public.subscriber_api_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_sub_api_keys_bot_slug  ON public.subscriber_api_keys(bot_slug) WHERE bot_slug IS NOT NULL;

ALTER TABLE public.subscriber_api_keys ENABLE ROW LEVEL SECURITY;

-- Service role only — subscribers cannot self-manage keys via Supabase client
DROP POLICY IF EXISTS "Service role manages subscriber keys" ON public.subscriber_api_keys;
CREATE POLICY "Service role manages subscriber keys" ON public.subscriber_api_keys
    TO service_role
    USING (true)
    WITH CHECK (true);


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
SET search_path = ''
AS $$
    SELECT
        COALESCE(NULLIF(k.subscriber_id, ''), k.user_id::TEXT) AS subscriber_id,
        k.bot_slug,
        k.env,
        k.scopes,
        k.paper_account_id
    FROM public.subscriber_api_keys AS k
    WHERE k.key_hash = p_key_hash
      AND k.active = TRUE
      AND k.revoked_at IS NULL
      AND (k.expires_at IS NULL OR k.expires_at > now())
      AND COALESCE(NULLIF(k.subscriber_id, ''), k.user_id::TEXT) IS NOT NULL
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

REVOKE EXECUTE ON FUNCTION public.resolve_subscriber_api_key(TEXT) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.resolve_subscriber_api_key(TEXT) TO service_role;
REVOKE EXECUTE ON FUNCTION public.touch_subscriber_api_key(TEXT) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.touch_subscriber_api_key(TEXT) TO service_role;

COMMENT ON TABLE public.subscriber_api_keys IS
    'Subscriber API keys (sub_live_*). Issued by platform on marketplace subscription. '
    'Service-role only — not user-self-serve. Scoped to signal stream + paper P&L for '
    'one bot_slug.';

COMMIT;
