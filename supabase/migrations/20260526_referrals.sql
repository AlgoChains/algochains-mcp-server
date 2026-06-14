-- AlgoChains MCP Server — Affiliate / Referral System
-- Migration: 20260526_referrals.sql
-- Apply via: supabase db push (mcp-server project)
--
-- Commission policy: a referrer earns 20% of the referred subscriber's
-- subscription revenue for the FIRST 3 MONTHS only (see referrals.py
-- COMMISSION_RATE / COMMISSION_MONTHS). Attribution is FIRST-TOUCH: the first
-- referral code a subscriber arrives with wins and cannot be overwritten.
--
-- Creates:
--   1. referral_codes            — one shareable code per owner subscriber
--   2. referral_attributions     — first-touch link: referred subscriber -> code
--   3. referral_commissions      — append-only commission ledger (3-month window)
--   4. record_referral_attribution() RPC — SECURITY DEFINER first-touch insert
--
-- Security model:
--   - RLS enabled on all tables; service_role is the only access path.
--   - No user-facing policies (audit + payout integrity).
--   - SECURITY DEFINER RPC validates code + blocks self-referral.
--   - Commission accrual happens in the billing webhook, NOT here. This
--     migration only provides the ledger table + attribution plumbing.

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. referral_codes
--    One active shareable code per owner subscriber.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.referral_codes (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code                 TEXT NOT NULL UNIQUE,        -- e.g. 'AC-7F3K9Q'
    owner_subscriber_id  TEXT NOT NULL,               -- subscriber who owns/shares this code
    active               BOOLEAN NOT NULL DEFAULT TRUE,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_rc_code   ON public.referral_codes(code);
CREATE INDEX IF NOT EXISTS idx_rc_owner  ON public.referral_codes(owner_subscriber_id);

ALTER TABLE public.referral_codes ENABLE ROW LEVEL SECURITY;
-- Service_role only — no user-facing policy. referrals.py reads/writes via the
-- service-role client.

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. referral_attributions
--    First-touch link from a referred subscriber to the code they arrived with.
--    UNIQUE on referred_subscriber_id enforces one attribution per subscriber.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.referral_attributions (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    referred_subscriber_id  TEXT NOT NULL UNIQUE,     -- one attribution per subscriber (first-touch)
    code                    TEXT NOT NULL REFERENCES public.referral_codes(code),
    attributed_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    window_ends_at          TIMESTAMPTZ NOT NULL       -- attribution/commission window cutoff
);

CREATE INDEX IF NOT EXISTS idx_ra_code  ON public.referral_attributions(code);

ALTER TABLE public.referral_attributions ENABLE ROW LEVEL SECURITY;
-- Service_role only — no user-facing policy.

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. referral_commissions
--    Append-only commission ledger. One row per (referred subscriber, invoice,
--    month_index). Accrued by the billing webhook for the first 3 months only.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.referral_commissions (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code                    TEXT NOT NULL,
    owner_subscriber_id     TEXT NOT NULL,
    referred_subscriber_id  TEXT NOT NULL,
    invoice_id              TEXT,                       -- Stripe invoice that produced this commission
    commission_usd          NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    month_index             INT NOT NULL
                            CHECK (month_index >= 1 AND month_index <= 3),
    status                  TEXT NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'paid', 'reversed')),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_rcm_owner     ON public.referral_commissions(owner_subscriber_id);
CREATE INDEX IF NOT EXISTS idx_rcm_referred  ON public.referral_commissions(referred_subscriber_id);
CREATE INDEX IF NOT EXISTS idx_rcm_code      ON public.referral_commissions(code);
CREATE INDEX IF NOT EXISTS idx_rcm_status    ON public.referral_commissions(status);

ALTER TABLE public.referral_commissions ENABLE ROW LEVEL SECURITY;
-- Append-only; service_role writes. No user-facing policy (payout integrity).

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. record_referral_attribution() — SECURITY DEFINER first-touch insert
--    Validates the code exists + is active, blocks self-referral, and inserts
--    the attribution ON CONFLICT DO NOTHING so the first code a subscriber
--    arrives with permanently wins. Returns the attributed code or NULL.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION public.record_referral_attribution(
    p_referred_subscriber_id  TEXT,
    p_code                    TEXT,
    p_window_days             INT DEFAULT 30
)
RETURNS TEXT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    _owner          TEXT;
    _attributed     TEXT;
    _window_days    INT := COALESCE(p_window_days, 30);
BEGIN
    IF p_referred_subscriber_id IS NULL OR p_code IS NULL THEN
        RETURN NULL;
    END IF;

    -- Validate the code exists and is active.
    SELECT owner_subscriber_id
      INTO _owner
      FROM public.referral_codes
     WHERE code = p_code
       AND active = TRUE
     LIMIT 1;

    IF _owner IS NULL THEN
        RAISE EXCEPTION 'unknown_or_inactive_referral_code: %', p_code
            USING ERRCODE = 'check_violation';
    END IF;

    -- Block self-referral.
    IF _owner = p_referred_subscriber_id THEN
        RAISE EXCEPTION 'self_referral_not_allowed'
            USING ERRCODE = 'check_violation';
    END IF;

    -- First-touch insert: the first code a subscriber arrives with wins.
    INSERT INTO public.referral_attributions
        (referred_subscriber_id, code, attributed_at, window_ends_at)
    VALUES
        (p_referred_subscriber_id, p_code, now(),
         now() + (_window_days || ' days')::INTERVAL)
    ON CONFLICT (referred_subscriber_id) DO NOTHING;

    -- Return whatever code is now attributed (existing first-touch or the new one).
    SELECT code
      INTO _attributed
      FROM public.referral_attributions
     WHERE referred_subscriber_id = p_referred_subscriber_id
     LIMIT 1;

    RETURN _attributed;
END;
$$;

REVOKE EXECUTE ON FUNCTION public.record_referral_attribution(TEXT, TEXT, INT)
    FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.record_referral_attribution(TEXT, TEXT, INT)
    TO service_role;

-- ─────────────────────────────────────────────────────────────────────────────
-- Comments
-- ─────────────────────────────────────────────────────────────────────────────

COMMENT ON TABLE public.referral_codes IS
    'One shareable referral code per owner subscriber. Service_role only.';
COMMENT ON TABLE public.referral_attributions IS
    'First-touch attribution: referred subscriber -> referral code. UNIQUE on '
    'referred_subscriber_id so the first code a subscriber arrives with wins.';
COMMENT ON TABLE public.referral_commissions IS
    'Append-only commission ledger. 20% of the first 3 monthly invoices of each '
    'referred subscriber. Accrued by the billing webhook; never updated in place '
    'except status transitions (pending -> paid / reversed).';
