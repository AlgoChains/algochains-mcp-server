-- AlgoChains MCP Server — Creator Earnings & Connect Payout Ledger
-- Migration: 20260528_creator_earnings.sql
-- Apply via: supabase db push (mcp-server project)
--
-- Builds the creator-side accounting + payout ledger that sits on top of the
-- existing Stripe Connect plumbing in cloud_saas/billing_engine.py. Payouts
-- MOVE REAL MONEY, so this schema is designed to be append-only and idempotent:
--
--   1. creator_connect_accounts — one row per creator; mirrors the Stripe
--      Connect Express account + payouts_enabled flag used as a pre-transfer gate.
--   2. creator_earnings — append-only accrual ledger. Every marketplace sale
--      writes one 'accrued' row; the payout run flips matched rows to 'paid'.
--   3. creator_payouts — append-only payout ledger. A row is written BEFORE the
--      external Stripe transfer (status 'planned'), then advanced to
--      'transferred'/'paid'/'failed'. A UNIQUE idempotency_key prevents double-pay.
--
-- Revenue share: creators keep 80% (revenue_share_pct default 80.00). Research:
-- MCPize runs ~85/15, 70/30 is below market; 80/20 is the documented AlgoChains
-- target. The pct is stored per-earning-row so historical splits stay auditable
-- even if the platform default changes later.
--
-- RLS: enabled on all three tables, service_role only. These tables back
-- money movement — no anon/authenticated access. The MCP server reaches them
-- exclusively via the service-role client (cloud_saas/connect_payouts.py).

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. creator_connect_accounts — Stripe Connect account mirror + payout gate
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.creator_connect_accounts (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    creator_id        TEXT NOT NULL UNIQUE,
    stripe_account_id TEXT,                       -- acct_… from Stripe Connect Express
    payouts_enabled   BOOLEAN NOT NULL DEFAULT false,
    email             TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cca_creator
    ON public.creator_connect_accounts(creator_id);

COMMENT ON TABLE public.creator_connect_accounts IS
    'One row per strategy creator mirroring their Stripe Connect Express account. '
    'payouts_enabled is the pre-transfer gate — a payout run must NOT transfer to '
    'a creator whose Stripe account has payouts_enabled=false.';

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. creator_earnings — append-only accrual ledger
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.creator_earnings (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    creator_id        TEXT NOT NULL,
    strategy_id       TEXT,
    invoice_id        TEXT,                       -- Stripe invoice / checkout session id
    gross_usd         NUMERIC(12,2),              -- total subscriber payment
    creator_share_usd NUMERIC(12,2),              -- creator's cut (gross * revenue_share_pct)
    platform_fee_usd  NUMERIC(12,2),              -- platform's cut (gross - creator_share)
    revenue_share_pct NUMERIC(5,2) NOT NULL DEFAULT 80.00,
    status            TEXT NOT NULL DEFAULT 'accrued'
                      CHECK (status IN ('accrued', 'paid', 'reversed')),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ce_creator ON public.creator_earnings(creator_id);
CREATE INDEX IF NOT EXISTS idx_ce_status  ON public.creator_earnings(status);

COMMENT ON TABLE public.creator_earnings IS
    'Append-only accrual ledger. One row per marketplace sale at status=accrued. '
    'A payout run sums accrued creator_share_usd per creator and flips the matched '
    'rows to paid once the Stripe transfer succeeds. Never update gross/share in place.';

COMMENT ON COLUMN public.creator_earnings.revenue_share_pct IS
    'Creator revenue share at time of accrual (default 80.00 = 80/20 split). '
    'Stored per-row so historical splits remain auditable if the default changes.';

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. creator_payouts — append-only payout ledger (idempotent)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.creator_payouts (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    creator_id          TEXT,
    amount_usd          NUMERIC(12,2),
    stripe_transfer_id  TEXT,                     -- Stripe payout/transfer id once executed
    idempotency_key     TEXT NOT NULL UNIQUE,     -- stable per (creator, period) — double-pay guard
    status              TEXT NOT NULL DEFAULT 'planned'
                        CHECK (status IN ('planned', 'transferred', 'paid', 'failed', 'reversed')),
    dry_run             BOOLEAN NOT NULL DEFAULT true,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cp_creator ON public.creator_payouts(creator_id);

COMMENT ON TABLE public.creator_payouts IS
    'Append-only payout ledger. A row is inserted at status=planned BEFORE the '
    'external Stripe transfer, then advanced to transferred/paid or failed. The '
    'UNIQUE idempotency_key (payout_<creator>_<period>) is the double-pay guard: '
    'a retry of the same period collides on the unique key and is refused.';

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. RLS — service_role only on all three money-bearing tables
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE public.creator_connect_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.creator_earnings         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.creator_payouts          ENABLE ROW LEVEL SECURITY;

-- No anon / authenticated policies. The service-role key bypasses RLS; every
-- other role is denied by default (RLS enabled with zero permissive policies).
REVOKE ALL ON public.creator_connect_accounts FROM anon, authenticated;
REVOKE ALL ON public.creator_earnings         FROM anon, authenticated;
REVOKE ALL ON public.creator_payouts          FROM anon, authenticated;
