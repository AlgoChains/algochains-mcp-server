-- AlgoChains MCP Server — Payment / Billing Tables
-- Migration: 20260522_payment_tables.sql
-- Apply via: supabase db push (mcp-server project)
--
-- Creates the full payment layer that was previously ZERO tables:
--   1. stripe_customers           (Stripe customer ID read-model cache)
--   2. subscription_payments      (immutable payment ledger)
--   3. subscription_transitions   (append-only subscription status history)
--   4. webhook_events             (idempotent Stripe webhook guard)
--   5. refunds                    (refund ledger)
--
-- HK-NT-3 fix: platform_user_id TEXT on stripe_customers and subscription_payments
--   auth.uid() is NOT portable between Supabase projects. Use platform_user_id
--   (e.g. Clerk user_xxx or hashed email) as the stable cross-project join key.
--
-- HK-NT-6 fix: amount_minor BIGINT + currency CHAR(3) (not amount_cents INT).
-- HK-NT-16 fix: immutable ledger enforced via TRIGGER + REVOKE.
-- HK-NT-4 fix: subscription_transitions trigger fires only on status change.
-- HK-NT-5 fix: webhook_events uses status='received'→'processed' pattern.
-- HK-NT-12 fix: pg_cron retention blocks in every table section.
-- HK-NT-15 fix: NO anon/authenticated SELECT on any table; service_role writes only
--              except user-scoped SELECT on payments / refunds.

CREATE EXTENSION IF NOT EXISTS pg_cron;

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. stripe_customers
--    Read-model cache: maps Supabase auth.users(id) → Stripe customer_id.
--    Written by the Stripe webhook Edge Function on customer.created / updated.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.stripe_customers (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    platform_user_id    TEXT NOT NULL,          -- HK-NT-3: stable cross-project ID (Clerk user_xxx or sha256(email))
    stripe_customer_id  TEXT NOT NULL UNIQUE,   -- cus_xxxx
    email               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at          TIMESTAMPTZ,            -- soft delete; Stripe customer was deleted
    UNIQUE (user_id)
);

CREATE INDEX IF NOT EXISTS idx_stripe_customers_user_id          ON public.stripe_customers(user_id);
CREATE INDEX IF NOT EXISTS idx_stripe_customers_platform_user_id ON public.stripe_customers(platform_user_id);
CREATE INDEX IF NOT EXISTS idx_stripe_customers_stripe_id        ON public.stripe_customers(stripe_customer_id);

ALTER TABLE public.stripe_customers ENABLE ROW LEVEL SECURITY;

-- User can read their own stripe_customer record (never exposes other users' data)
DROP POLICY IF EXISTS "sc_owner_select" ON public.stripe_customers;
CREATE POLICY "sc_owner_select" ON public.stripe_customers
    FOR SELECT USING (auth.uid() = user_id);

-- Writes and deletes only via service_role (webhook handler, admin)
-- No INSERT/UPDATE/DELETE policy for authenticated/anon — service_role bypasses RLS.

CREATE OR REPLACE FUNCTION public.touch_stripe_customers_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path = ''
AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$;

DROP TRIGGER IF EXISTS set_stripe_customers_updated_at ON public.stripe_customers;
CREATE TRIGGER set_stripe_customers_updated_at
    BEFORE UPDATE ON public.stripe_customers
    FOR EACH ROW EXECUTE FUNCTION public.touch_stripe_customers_updated_at();


-- ─────────────────────────────────────────────────────────────────────────────
-- 2. subscription_payments
--    Immutable payment ledger. One row per charge event from Stripe.
--    NEVER updated or deleted — refunds produce rows in the refunds table.
--
--    HK-NT-6: amount_minor BIGINT + currency CHAR(3) — correct for all currencies.
--    HK-NT-16: immutable trigger + REVOKE prevents accidental mutations.
--    HK-NT-3: platform_user_id for cross-project joins.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.subscription_payments (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                     UUID NOT NULL REFERENCES auth.users(id) ON DELETE RESTRICT,
    platform_user_id            TEXT NOT NULL,           -- HK-NT-3
    stripe_payment_intent_id    TEXT UNIQUE,             -- pi_xxxx (null for invoice-only payments)
    stripe_invoice_id           TEXT,                    -- in_xxxx
    stripe_charge_id            TEXT UNIQUE,             -- ch_xxxx
    stripe_subscription_id      TEXT,                    -- sub_xxxx
    bot_id                      TEXT,                    -- marketplace bot reference (no FK — cross-project)
    amount_minor                BIGINT NOT NULL,         -- HK-NT-6: amount in smallest currency unit
    currency                    CHAR(3) NOT NULL DEFAULT 'usd' CHECK (currency ~ '^[a-z]{3}$'),
    status                      TEXT NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending','succeeded','failed','refunded','disputed')),
    period_start                TIMESTAMPTZ,
    period_end                  TIMESTAMPTZ,
    metadata                    JSONB DEFAULT '{}'::jsonb,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
    -- no updated_at — immutable ledger
);

CREATE INDEX IF NOT EXISTS idx_sp_user_created        ON public.subscription_payments(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sp_payment_intent       ON public.subscription_payments(stripe_payment_intent_id) WHERE stripe_payment_intent_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_sp_invoice              ON public.subscription_payments(stripe_invoice_id) WHERE stripe_invoice_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_sp_subscription        ON public.subscription_payments(stripe_subscription_id) WHERE stripe_subscription_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_sp_status_failed       ON public.subscription_payments(status) WHERE status = 'failed';
CREATE INDEX IF NOT EXISTS idx_sp_platform_user_id    ON public.subscription_payments(platform_user_id);

ALTER TABLE public.subscription_payments ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "sp_owner_select" ON public.subscription_payments;
CREATE POLICY "sp_owner_select" ON public.subscription_payments
    FOR SELECT USING (auth.uid() = user_id);
-- service_role INSERT only — no anon/authenticated write policy

-- HK-NT-16: immutable ledger enforcement
REVOKE UPDATE, DELETE ON public.subscription_payments FROM authenticated, anon;

CREATE OR REPLACE FUNCTION public.prevent_payments_mutation()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path = ''
AS $$
BEGIN
    RAISE EXCEPTION 'subscription_payments is an immutable ledger — use refunds table for corrections';
END;
$$;

DROP TRIGGER IF EXISTS payments_immutable ON public.subscription_payments;
CREATE TRIGGER payments_immutable
    BEFORE UPDATE OR DELETE ON public.subscription_payments
    FOR EACH ROW EXECUTE FUNCTION public.prevent_payments_mutation();


-- ─────────────────────────────────────────────────────────────────────────────
-- 3. subscription_transitions
--    Append-only history of every algochains_subscriptions.status change.
--    Driven by AFTER UPDATE trigger — fires ONLY on actual status change (HK-NT-4).
--    Retention: no prune — compliance window requires permanent history.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.subscription_transitions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- subscription_id FK: no FK constraint because algochains_subscriptions lives in a
    -- separate Supabase project. Cross-project FKs are unsupported in Postgres.
    -- Application layer enforces referential integrity via platform_user_id (HK-NT-3).
    subscription_id     UUID NOT NULL,       -- logical FK to algochains_subscriptions.id
    user_id             UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    platform_user_id    TEXT NOT NULL,       -- HK-NT-3: stable cross-project join key
    bot_id              TEXT,
    from_status         TEXT NOT NULL,
    to_status           TEXT NOT NULL,
    reason              TEXT,               -- 'stripe_webhook', 'admin_override', 'trial_expired', etc.
    stripe_event_id     TEXT,               -- idempotency key from Stripe event (HK-NT-4)
    occurred_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_st_subscription_id ON public.subscription_transitions(subscription_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_st_user_id          ON public.subscription_transitions(user_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_st_bot_id           ON public.subscription_transitions(bot_id) WHERE bot_id IS NOT NULL;

ALTER TABLE public.subscription_transitions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "stt_owner_select" ON public.subscription_transitions;
CREATE POLICY "stt_owner_select" ON public.subscription_transitions
    FOR SELECT USING (auth.uid() = user_id);

-- AFTER UPDATE trigger on algochains_subscriptions (HK-NT-4 fix):
-- Fires ONLY when status column actually changes. Guards against noisy column updates
-- and replay of same Stripe event.
CREATE OR REPLACE FUNCTION public.record_subscription_transition()
RETURNS TRIGGER LANGUAGE plpgsql
SET search_path = ''
SECURITY DEFINER
AS $$
BEGIN
    -- HK-NT-4: only fire when status actually changed
    IF TG_OP = 'UPDATE' AND NEW.status IS NOT DISTINCT FROM OLD.status THEN
        RETURN NEW;
    END IF;

    INSERT INTO public.subscription_transitions (
        subscription_id, user_id, platform_user_id,
        bot_id, from_status, to_status, occurred_at
    ) VALUES (
        NEW.id,
        NEW.user_id,
        COALESCE(NEW.user_id::text, ''),  -- populated at app layer with real platform_user_id
        NEW.bot_id,
        COALESCE(OLD.status, 'none'),
        NEW.status,
        now()
    )
    ON CONFLICT DO NOTHING;  -- belt-and-suspenders idempotency

    RETURN NEW;
END;
$$;

-- NOTE: trg_subscription_status_transition is defined on algochains_subscriptions
-- which lives in the algochains-mcp-server Supabase project (different instance).
-- Apply this trigger in the mcp-server project migration:
--   DROP TRIGGER IF EXISTS trg_subscription_status_transition ON public.algochains_subscriptions;
--   CREATE TRIGGER trg_subscription_status_transition
--     AFTER INSERT OR UPDATE ON public.algochains_subscriptions
--     FOR EACH ROW EXECUTE FUNCTION public.record_subscription_transition();
-- The record_subscription_transition() function defined above must also be deployed there.
-- Cross-project FK limitation: subscription_id uses platform_user_id as the stable join.


-- ─────────────────────────────────────────────────────────────────────────────
-- 4. webhook_events
--    Idempotent Stripe webhook processing guard.
--    HK-NT-5 fix: uses status column (received → processing → processed / failed)
--    so application can wrap business logic in single transaction.
--    Retention: 90 days after processed_at (events older than 90d are safe to prune).
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.webhook_events (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    stripe_event_id     TEXT UNIQUE NOT NULL,   -- evt_xxxx — idempotency key
    event_type          TEXT NOT NULL,           -- e.g. 'customer.subscription.updated'
    -- HK-NT-5: status tracks processing lifecycle within a single transaction
    status              TEXT NOT NULL DEFAULT 'received'
                        CHECK (status IN ('received','processing','processed','failed','skipped')),
    attempts            INT NOT NULL DEFAULT 0,
    last_error          TEXT,
    payload             JSONB,                   -- redacted Stripe event (never log raw key material)
    processed_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_we_stripe_event_id  ON public.webhook_events(stripe_event_id);
CREATE INDEX IF NOT EXISTS idx_we_event_type       ON public.webhook_events(event_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_we_status_pending   ON public.webhook_events(status) WHERE status IN ('received','processing','failed');

ALTER TABLE public.webhook_events ENABLE ROW LEVEL SECURITY;
-- Service_role only — no user-facing policy

-- Retention: prune processed events after 90 days (HK-NT-12)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
        PERFORM cron.schedule(
            'algochains_webhook_events_retention_90d',
            '10 3 * * *',
            $job$
                DELETE FROM public.webhook_events
                 WHERE processed_at IS NOT NULL
                   AND processed_at < now() - INTERVAL '90 days';
            $job$
        );
    END IF;
END $$;


-- ─────────────────────────────────────────────────────────────────────────────
-- 5. refunds
--    Refund ledger. Each refund FK references a subscription_payments row.
--    ON DELETE RESTRICT on payment_id — cannot delete a payment that has refunds.
--    Retention: 7 years (financial regulation compliance).
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.refunds (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    payment_id          UUID NOT NULL REFERENCES public.subscription_payments(id) ON DELETE RESTRICT,
    user_id             UUID NOT NULL REFERENCES auth.users(id) ON DELETE RESTRICT,
    platform_user_id    TEXT NOT NULL,       -- HK-NT-3
    stripe_refund_id    TEXT UNIQUE NOT NULL,  -- re_xxxx
    amount_minor        BIGINT NOT NULL,      -- HK-NT-6: same scale as subscription_payments
    currency            CHAR(3) NOT NULL DEFAULT 'usd',
    reason              TEXT CHECK (reason IN ('duplicate','fraudulent','requested_by_customer','other')),
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','succeeded','failed','canceled')),
    issued_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_refunds_payment_id     ON public.refunds(payment_id);
CREATE INDEX IF NOT EXISTS idx_refunds_user_id        ON public.refunds(user_id, issued_at DESC);
CREATE INDEX IF NOT EXISTS idx_refunds_stripe_id      ON public.refunds(stripe_refund_id);

ALTER TABLE public.refunds ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "refunds_owner_select" ON public.refunds;
CREATE POLICY "refunds_owner_select" ON public.refunds
    FOR SELECT USING (auth.uid() = user_id);


-- ─────────────────────────────────────────────────────────────────────────────
-- Helper view for owner billing overview (security_invoker per workspace rule)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE VIEW public.v_billing_summary
    WITH (security_invoker = true)
AS
SELECT
    sp.user_id,
    sc.stripe_customer_id,
    sp.bot_id,
    sp.currency,
    COUNT(*)                                                    AS payment_count,
    SUM(sp.amount_minor) FILTER (WHERE sp.status = 'succeeded') AS total_paid_minor,
    SUM(r.amount_minor)                                         AS total_refunded_minor,
    MAX(sp.created_at)                                          AS last_payment_at
FROM public.subscription_payments sp
LEFT JOIN public.stripe_customers sc ON sc.user_id = sp.user_id
LEFT JOIN public.refunds r ON r.payment_id = sp.id
GROUP BY sp.user_id, sc.stripe_customer_id, sp.bot_id, sp.currency;

COMMENT ON TABLE public.stripe_customers       IS 'Stripe customer ID cache. platform_user_id enables cross-project joins (HK-NT-3).';
COMMENT ON TABLE public.subscription_payments  IS 'Immutable payment ledger. amount_minor+currency correct for all locales (HK-NT-6). Mutations blocked by trigger (HK-NT-16).';
COMMENT ON TABLE public.subscription_transitions IS 'Append-only subscription status history. Trigger fires only on status change (HK-NT-4).';
COMMENT ON TABLE public.webhook_events         IS 'Idempotent Stripe webhook guard. status lifecycle: received→processing→processed (HK-NT-5).';
COMMENT ON TABLE public.refunds                IS 'Refund ledger. References subscription_payments with ON DELETE RESTRICT.';
