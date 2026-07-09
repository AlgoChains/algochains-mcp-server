-- ═══════════════════════════════════════════════════════════════════════════
-- subscriber_api_keys — cross-repo schema reconciliation
--
-- ROOT-CAUSE HYPOTHESIS for "sub_live_ key rejected as invalid/expired" reports
-- (e.g. Jeremy, 2026-07): two repos independently maintain migrations against
-- the SAME live `public.subscriber_api_keys` table with incompatible shapes:
--
--   algochains-control-tower/supabase/migrations/20260420_subscriber_copytrade.sql
--     - subscriber_id UUID NOT NULL REFERENCES auth.users(id)
--     - prefix TEXT NOT NULL, label TEXT NOT NULL DEFAULT 'default'
--     - resolve_subscriber_api_key() RETURNS TABLE (subscriber_id UUID, scopes)
--
--   algochains-mcp-server/supabase/migrations/20260523_subscriber_copytrade.sql
--   + 20260614000400_subscriber_api_keys.sql
--     - subscriber_id TEXT NOT NULL (no FK)
--     - key_prefix TEXT NOT NULL (no label column at all)
--     - resolve_subscriber_api_key() RETURNS TABLE (subscriber_id TEXT, bot_slug,
--       env, scopes, paper_account_id) — references bot_slug/env/paper_account_id
--       columns that only exist if its own ALTER TABLE ran.
--
-- Django (home/subscriber_provisioning.py, home/services/subscriber_key_service.py
-- — the actual key-issuance/rotation code path exercised in production) writes
-- `prefix` + `label`, matching control-tower's original (first-deployed) schema.
-- If the live table is still on that first schema, mcp-server's newer
-- resolve_subscriber_api_key() throws "column bot_slug/env/paper_account_id does
-- not exist" (or a uuid-cast error on `NULLIF(k.subscriber_id, '')`) on EVERY
-- lookup — subscriber_auth.py swallows that exception and returns None, which
-- surfaces to the user identically to "invalid or expired key" regardless of
-- whether the key itself is fine.
--
-- This migration is written to be safe to run regardless of which schema
-- variant is currently live: every step is conditional / idempotent, converges
-- on Django's actual column names (prefix/label — since that's the code path
-- that issues and rotates keys today), and rebuilds the resolve function to
-- tolerate both naming schemes without erroring.
-- ═══════════════════════════════════════════════════════════════════════════

BEGIN;

-- 1. subscriber_id: Django issues arbitrary stable subscriber ids (TEXT), not
--    always a literal auth.users UUID. If the column is still UUID (original
--    control-tower schema) with an auth.users FK, that FK also blocks any
--    subscriber id that isn't a real auth.users row. Relax both.
DO $$
DECLARE
    fk_name TEXT;
BEGIN
    SELECT tc.constraint_name INTO fk_name
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
      ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
    WHERE tc.table_schema = 'public'
      AND tc.table_name = 'subscriber_api_keys'
      AND tc.constraint_type = 'FOREIGN KEY'
      AND kcu.column_name = 'subscriber_id'
    LIMIT 1;

    IF fk_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE public.subscriber_api_keys DROP CONSTRAINT %I', fk_name);
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'subscriber_api_keys'
          AND column_name = 'subscriber_id' AND data_type <> 'text'
    ) THEN
        ALTER TABLE public.subscriber_api_keys
            ALTER COLUMN subscriber_id TYPE TEXT USING subscriber_id::text;
    END IF;
END $$;

-- 2. Converge on prefix/label (Django's actual write path) while keeping
--    key_prefix around (renamed data preserved, not dropped) for any reader
--    still on the newer name.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'subscriber_api_keys' AND column_name = 'key_prefix'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'subscriber_api_keys' AND column_name = 'prefix'
    ) THEN
        ALTER TABLE public.subscriber_api_keys RENAME COLUMN key_prefix TO prefix;
    END IF;
END $$;

ALTER TABLE public.subscriber_api_keys
    ADD COLUMN IF NOT EXISTS prefix TEXT,
    ADD COLUMN IF NOT EXISTS label  TEXT NOT NULL DEFAULT 'default',
    ADD COLUMN IF NOT EXISTS bot_slug TEXT,
    ADD COLUMN IF NOT EXISTS env TEXT NOT NULL DEFAULT 'live',
    ADD COLUMN IF NOT EXISTS paper_account_id TEXT,
    ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMPTZ;

-- Backfill prefix from key_hash-adjacent data if somehow still null (should
-- not happen post-rename, but guards against partial prior states).
UPDATE public.subscriber_api_keys SET prefix = 'sub_live_????' WHERE prefix IS NULL;
ALTER TABLE public.subscriber_api_keys ALTER COLUMN prefix SET NOT NULL;

-- 3. Rebuild the resolve function against the reconciled, Django-compatible
--    column set. Tolerant of active/revoked_at/expires_at being any prior
--    default state since we just ensured they exist above.
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
        k.subscriber_id::TEXT AS subscriber_id,
        k.bot_slug,
        k.env,
        k.scopes,
        k.paper_account_id
    FROM public.subscriber_api_keys AS k
    WHERE k.key_hash = p_key_hash
      AND k.active = TRUE
      AND k.revoked_at IS NULL
      AND (k.expires_at IS NULL OR k.expires_at > now())
      AND k.subscriber_id IS NOT NULL
      AND k.subscriber_id <> ''
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
    'Subscriber API keys (sub_live_*). Issued by Django (home/subscriber_provisioning.py, '
    'home/services/subscriber_key_service.py) on paper-account activation / rotation. '
    'Schema reconciled 2026-07-09 after control-tower vs mcp-server migration drift — '
    'see 20260709000100_subscriber_api_keys_schema_reconcile.sql for the incident writeup. '
    'Canonical columns going forward: prefix, label, subscriber_id (TEXT), bot_slug, env, '
    'scopes, paper_account_id, active, expires_at, revoked_at.';

COMMIT;

-- ─────────────────────────────────────────────────────────────────────────────
-- VERIFY (run manually after applying, before closing the Jeremy ticket):
--
--   select column_name, data_type, is_nullable
--   from information_schema.columns
--   where table_schema='public' and table_name='subscriber_api_keys'
--   order by ordinal_position;
--
--   -- Should show subscriber_id as text, prefix + label both present.
--
--   select * from public.resolve_subscriber_api_key(
--     encode(sha256('<JEREMYS_ACTUAL_KEY>'::bytea), 'hex')
--   );
--
--   -- Empty result + no error = key genuinely not found/revoked/expired.
--   -- A SQL error here (not empty result) means this migration did not yet
--   -- run against the environment Jeremy is hitting — check
--   -- ALGOCHAINS_BRIDGE_URL / SUPABASE_URL are pointed at the same project
--   -- the Django admin/portal writes to (separate control-tower vs mcp-server
--   -- Supabase projects would also explain this symptom and require no code
--   -- change — just confirming both services share one Supabase project).
-- ─────────────────────────────────────────────────────────────────────────────
