-- AlgoChains MCP Server — Subscriber Copy-Trade Tables
-- Migration: 20260523_subscriber_copytrade.sql
-- Apply via: supabase db push (mcp-server project)
--
-- Referenced by subscriber_auth.py as "20260420_subscriber_copytrade.sql" —
-- this migration supersedes that reference and creates all required tables.
--
-- Creates:
--   1. subscriber_api_keys         — hashed sub_live_*/sub_test_* keys
--   2. subscriber_paper_accounts   — hosted virtual paper account ($100K start)
--   3. subscriber_bot_assignments  — which bots a subscriber copy-trades
--   4. copy_trade_signals          — signals emitted by live bots (fan-out source)
--   5. subscriber_fills            — per-subscriber fill records
--   6. subscriber_heartbeats       — daemon liveness pings
--   7. resolve_subscriber_api_key() RPC — SECURITY DEFINER key lookup
--
-- Security model:
--   - Plaintext subscriber keys never stored; SHA-256 hash only.
--   - RLS enabled on all tables; subscribers see only their own rows.
--   - SECURITY DEFINER RPC runs as postgres for the hash lookup.
--   - copy_trade_signals: service_role writes; authenticated SELECT (any subscriber).

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. subscriber_api_keys
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.subscriber_api_keys (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key_hash         TEXT NOT NULL UNIQUE,    -- SHA-256(sub_live_* plaintext)
    key_prefix       TEXT NOT NULL,           -- first 16 chars, safe to display
    subscriber_id    TEXT NOT NULL,           -- stable identifier (e.g. Supabase auth UID or email hash)
    scopes           TEXT[] NOT NULL DEFAULT ARRAY[
        'signal_stream', 'my_pnl', 'my_fills',
        'my_assignments', 'heartbeat', 'report_fill', 'paper_trade'
    ],
    tier             TEXT NOT NULL DEFAULT 'paper'
                     CHECK (tier IN ('paper', 'live')),
    active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at       TIMESTAMPTZ,             -- NULL = no expiry (subscription active)
    revoked_at       TIMESTAMPTZ              -- NULL = not revoked
);

CREATE INDEX IF NOT EXISTS idx_sak_key_hash       ON public.subscriber_api_keys(key_hash);
CREATE INDEX IF NOT EXISTS idx_sak_subscriber_id  ON public.subscriber_api_keys(subscriber_id);
CREATE INDEX IF NOT EXISTS idx_sak_active         ON public.subscriber_api_keys(active) WHERE active = TRUE;

ALTER TABLE public.subscriber_api_keys ENABLE ROW LEVEL SECURITY;
-- No user-facing policy — SECURITY DEFINER RPC is the only read path.

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. subscriber_paper_accounts
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.subscriber_paper_accounts (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subscriber_id         TEXT NOT NULL UNIQUE,
    starting_balance_usd  NUMERIC(18, 2) NOT NULL DEFAULT 100000.00,
    current_balance_usd   NUMERIC(18, 2) NOT NULL DEFAULT 100000.00,
    realized_pnl_usd      NUMERIC(18, 2) NOT NULL DEFAULT 0.00,
    fills_count           INT NOT NULL DEFAULT 0,
    last_reset_at         TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_spa_subscriber_id ON public.subscriber_paper_accounts(subscriber_id);

ALTER TABLE public.subscriber_paper_accounts ENABLE ROW LEVEL SECURITY;

-- Subscribers can read their own account row via subscriber_id (TEXT, not UUID,
-- so we match against auth.jwt() claim or service_role context).
-- Service_role handles all writes from the daemon and webhook handler.

CREATE OR REPLACE FUNCTION public.touch_spa_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path = ''
AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$;

DROP TRIGGER IF EXISTS set_spa_updated_at ON public.subscriber_paper_accounts;
CREATE TRIGGER set_spa_updated_at
    BEFORE UPDATE ON public.subscriber_paper_accounts
    FOR EACH ROW EXECUTE FUNCTION public.touch_spa_updated_at();

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. subscriber_bot_assignments
--    Which live bots a subscriber follows (copy-trade). Paused = signals ignored.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.subscriber_bot_assignments (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subscriber_id      TEXT NOT NULL,
    bot                TEXT NOT NULL
                       CHECK (bot IN ('MNQ', 'CL', 'MES', 'NQ')),
    size_multiplier    NUMERIC(6, 3) NOT NULL DEFAULT 1.0
                       CHECK (size_multiplier > 0 AND size_multiplier <= 10),
    max_contracts      INT NOT NULL DEFAULT 10
                       CHECK (max_contracts > 0 AND max_contracts <= 100),
    daily_loss_cap_usd NUMERIC(12, 2) NOT NULL DEFAULT 5000.00,
    paused             BOOLEAN NOT NULL DEFAULT FALSE,
    assigned_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (subscriber_id, bot)
);

CREATE INDEX IF NOT EXISTS idx_sba_subscriber_id ON public.subscriber_bot_assignments(subscriber_id);
CREATE INDEX IF NOT EXISTS idx_sba_bot           ON public.subscriber_bot_assignments(bot);
CREATE INDEX IF NOT EXISTS idx_sba_active        ON public.subscriber_bot_assignments(subscriber_id) WHERE paused = FALSE;

ALTER TABLE public.subscriber_bot_assignments ENABLE ROW LEVEL SECURITY;
-- Service_role writes; subscriber reads via service_role client in subscriber_tools.py.

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. copy_trade_signals
--    Signals emitted by live bots. Subscribers read these to execute fills.
--    expires_at gate prevents stale signals from executing on daemon restart.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.copy_trade_signals (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bot          TEXT NOT NULL CHECK (bot IN ('MNQ', 'CL', 'MES', 'NQ')),
    symbol       TEXT NOT NULL,
    side         TEXT NOT NULL CHECK (side IN ('BUY', 'SELL', 'BUY_TO_COVER', 'SELL_SHORT')),
    qty          NUMERIC(10, 4) NOT NULL CHECK (qty > 0),
    entry_price  NUMERIC(12, 4),
    stop_price   NUMERIC(12, 4),
    tp_price     NUMERIC(12, 4),
    signal_type  TEXT NOT NULL DEFAULT 'entry' CHECK (signal_type IN ('entry', 'exit', 'bracket')),
    confidence   NUMERIC(5, 4),               -- 0.0–1.0 AI ensemble confidence
    expires_at   TIMESTAMPTZ NOT NULL,        -- signal invalid after this; default 30 min
    emitted_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cts_bot_emitted   ON public.copy_trade_signals(bot, emitted_at DESC);
CREATE INDEX IF NOT EXISTS idx_cts_expires       ON public.copy_trade_signals(expires_at);
CREATE INDEX IF NOT EXISTS idx_cts_active        ON public.copy_trade_signals(bot) WHERE expires_at > now();

ALTER TABLE public.copy_trade_signals ENABLE ROW LEVEL SECURITY;

-- Authenticated subscribers can read all signals (filtered by their assignments in app layer).
DROP POLICY IF EXISTS "cts_authenticated_select" ON public.copy_trade_signals;
CREATE POLICY "cts_authenticated_select" ON public.copy_trade_signals
    FOR SELECT USING (true);  -- app layer filters by assignment; signal content is non-sensitive

-- Service_role INSERT from live bots only.

-- Retention: prune signals older than 7 days
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
        PERFORM cron.schedule(
            'algochains_copy_trade_signals_retention_7d',
            '15 3 * * *',
            $job$
                DELETE FROM public.copy_trade_signals
                 WHERE emitted_at < now() - INTERVAL '7 days';
            $job$
        );
    END IF;
END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. subscriber_fills
--    Per-subscriber fill records (copy-trade and self-directed paper).
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.subscriber_fills (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subscriber_id  TEXT NOT NULL,
    signal_id      UUID REFERENCES public.copy_trade_signals(id) ON DELETE SET NULL,
    bot            TEXT,                      -- NULL for self-directed paper orders
    symbol         TEXT NOT NULL,
    side           TEXT NOT NULL CHECK (side IN ('BUY', 'SELL', 'BUY_TO_COVER', 'SELL_SHORT')),
    qty            NUMERIC(10, 4) NOT NULL CHECK (qty > 0),
    fill_price     NUMERIC(12, 4) NOT NULL,
    pnl_usd        NUMERIC(12, 2),            -- NULL until position closed
    fill_kind      TEXT NOT NULL DEFAULT 'entry'
                   CHECK (fill_kind IN ('entry', 'exit', 'bracket_tp', 'bracket_sl', 'paper')),
    filled_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sf_subscriber_filled  ON public.subscriber_fills(subscriber_id, filled_at DESC);
CREATE INDEX IF NOT EXISTS idx_sf_bot                ON public.subscriber_fills(bot) WHERE bot IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_sf_signal_id          ON public.subscriber_fills(signal_id) WHERE signal_id IS NOT NULL;

ALTER TABLE public.subscriber_fills ENABLE ROW LEVEL SECURITY;
-- Service_role reads and writes; subscribers never read this table directly
-- (they use get_my_fills tool which filters by subscriber_id via service_role).

-- ─────────────────────────────────────────────────────────────────────────────
-- 6. subscriber_heartbeats
--    Daemon liveness pings. One row per subscriber (upsert on subscriber_id).
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.subscriber_heartbeats (
    subscriber_id  TEXT PRIMARY KEY,
    last_seen      TIMESTAMPTZ NOT NULL DEFAULT now(),
    fills_today    INT NOT NULL DEFAULT 0,
    pnl_today_usd  NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.subscriber_heartbeats ENABLE ROW LEVEL SECURITY;
-- Service_role upsert only.

-- ─────────────────────────────────────────────────────────────────────────────
-- 7. resolve_subscriber_api_key — SECURITY DEFINER RPC
--    Called by subscriber_auth.py with the SHA-256 hash of the raw key.
--    Returns (subscriber_id, scopes) or empty set if not found / revoked.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION public.resolve_subscriber_api_key(p_key_hash TEXT)
RETURNS TABLE (
    subscriber_id  TEXT,
    scopes         TEXT[]
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    RETURN QUERY
    SELECT
        k.subscriber_id,
        k.scopes
    FROM public.subscriber_api_keys k
    WHERE k.key_hash   = p_key_hash
      AND k.active     = TRUE
      AND k.revoked_at IS NULL
      AND (k.expires_at IS NULL OR k.expires_at > now())
    LIMIT 1;
END;
$$;

REVOKE EXECUTE ON FUNCTION public.resolve_subscriber_api_key(TEXT) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.resolve_subscriber_api_key(TEXT) TO service_role;

-- ─────────────────────────────────────────────────────────────────────────────
-- Comments
-- ─────────────────────────────────────────────────────────────────────────────

COMMENT ON TABLE public.subscriber_api_keys IS
    'Hashed sub_live_*/sub_test_* subscriber keys. Plaintext never stored. '
    'resolve_subscriber_api_key() RPC is the only read path.';
COMMENT ON TABLE public.subscriber_paper_accounts IS
    'Hosted virtual paper account. Starting balance $100,000. Provisioned automatically '
    'after successful Stripe checkout (checkout_type=platform_subscription).';
COMMENT ON TABLE public.subscriber_bot_assignments IS
    'Which live bots a subscriber copy-trades. Default: MNQ on paper signup.';
COMMENT ON TABLE public.copy_trade_signals IS
    'Signals emitted by live bots for subscriber copy-trading. '
    'Stale after expires_at; app layer guards against late daemon restarts.';
COMMENT ON TABLE public.subscriber_fills IS
    'Per-subscriber fill ledger. Covers copy-trade fills and self-directed paper orders.';
COMMENT ON TABLE public.subscriber_heartbeats IS
    'Daemon liveness pings. Upserted on each heartbeat() tool call.';
