-- AlgoChains MCP Server — Usage-Based Metered Billing Infrastructure
-- Migration: 20260527_usage_metering.sql
-- Apply via: supabase db push (mcp-server project)
--
-- Local source of truth for per-subscriber monthly call counts + overage so the
-- billing reporter can mirror them to Stripe Billing Meters. This migration only
-- owns the LOCAL ledger; Stripe meter reporting is done out-of-band by the
-- billing webhook / a separate reporter (see usage_metering.py module note).
--
-- Stripe 2026 posture (for the reporter, NOT this migration):
--   - Legacy usage records are REMOVED (API >= 2025-03-31.basil). The reporter
--     must use Billing Meters + Meter Events v2:
--       client.v2.billing.meter_events.create(
--           event_name=..., payload={stripe_customer_id, value}, identifier=<dedupe id>)
--     The `identifier` enforces 24h uniqueness (dedup); aggregation formulas
--     only sum/count/last.
--   - Hybrid pricing = one subscription with a licensed base price item + a
--     tiered metered price item (first N units unit_amount 0, then overage).
--   - Current usage is read via the Meter Event Summary API.
--
-- Creates:
--   1. usage_counters  — one row per (key_hash, period_month) rollup
--   2. usage_events    — sampled audit trail; UNIQUE(event_identifier) = dedup
--   3. increment_usage() — SECURITY DEFINER atomic upsert (service_role)
--
-- Security model:
--   - RLS enabled on both tables; service_role is the only read/write path.
--   - SECURITY DEFINER RPC runs as the owner with SET search_path = ''.
--   - No user-facing policy — subscriber reads go through service-role tools.

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. usage_counters — monthly rollup, the row the reporter sums to Stripe
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.usage_counters (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key_hash        TEXT NOT NULL,                 -- stable per-subscriber identifier (SHA-256 of key, or subscriber_id)
    period_month    TEXT NOT NULL,                 -- 'YYYY-MM' (UTC)
    calls           INT  NOT NULL DEFAULT 0,       -- total metered tool calls this period
    overage_calls   INT  NOT NULL DEFAULT 0,       -- GREATEST(0, calls - included_quota)
    included_quota  INT  NOT NULL DEFAULT 1000,    -- quota snapshot at increment time (per tier)
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (key_hash, period_month)
);

CREATE INDEX IF NOT EXISTS idx_uc_key_period
    ON public.usage_counters(key_hash, period_month);

COMMENT ON TABLE public.usage_counters IS
    'Per-subscriber monthly metered-call rollup. One row per (key_hash, period_month). '
    'The billing reporter mirrors overage_calls to a Stripe Billing Meter; this table '
    'is the local source of truth for usage and is never the Stripe meter itself.';

ALTER TABLE public.usage_counters ENABLE ROW LEVEL SECURITY;
-- No user-facing policy — service_role only (reads go through service-role tools).

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. usage_events — sampled audit trail. UNIQUE(event_identifier) is the dedup
--    guard so a retried report within the same minute bucket cannot double-count.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.usage_events (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key_hash          TEXT,
    tool_name         TEXT,
    event_identifier  TEXT UNIQUE,                 -- sha256(key_hash:tool:minute_bucket)[:32] — dedup key
    occurred_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ue_key_occurred
    ON public.usage_events(key_hash, occurred_at DESC);

COMMENT ON TABLE public.usage_events IS
    'Sampled per-call audit of metered usage. event_identifier is a deterministic '
    'minute-bucketed hash; its UNIQUE constraint dedups retries within a minute and '
    'mirrors the 24h identifier-uniqueness model Stripe Meter Events v2 enforces.';

ALTER TABLE public.usage_events ENABLE ROW LEVEL SECURITY;
-- No user-facing policy — service_role only.

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. increment_usage() — SECURITY DEFINER atomic counter upsert
--    Upserts the (key_hash, period_month) rollup, bumps calls by 1, recomputes
--    overage_calls against the supplied included_quota, and returns the new
--    counts. Called by usage_metering.record_usage via the service_role client.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION public.increment_usage(
    p_key_hash       TEXT,
    p_period_month   TEXT,
    p_included_quota INT
)
RETURNS TABLE (calls INT, overage_calls INT)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    RETURN QUERY
    INSERT INTO public.usage_counters AS uc
        (key_hash, period_month, calls, overage_calls, included_quota, updated_at)
    VALUES
        (p_key_hash, p_period_month, 1, GREATEST(0, 1 - p_included_quota), p_included_quota, now())
    ON CONFLICT (key_hash, period_month) DO UPDATE
        SET calls          = uc.calls + 1,
            overage_calls  = GREATEST(0, (uc.calls + 1) - p_included_quota),
            included_quota = p_included_quota,
            updated_at     = now()
    RETURNING uc.calls, uc.overage_calls;
END;
$$;

REVOKE EXECUTE ON FUNCTION public.increment_usage(TEXT, TEXT, INT)
    FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.increment_usage(TEXT, TEXT, INT)
    TO service_role;
