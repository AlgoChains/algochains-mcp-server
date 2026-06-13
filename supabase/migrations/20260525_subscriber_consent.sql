-- AlgoChains MCP Server — Subscriber Consent & Risk Acknowledgment
-- Migration: 20260525_subscriber_consent.sql
-- Apply via: supabase db push (mcp-server project)
--
-- CFTC/NFA posture: a subscriber must explicitly acknowledge the futures risk
-- disclosure BEFORE actively copy-trading any live futures bot. This migration
-- adds persisted consent stamps to subscriber_api_keys and an append-only
-- audit log so we can prove, per subscriber, what was acknowledged and when.
--
-- Creates / alters:
--   1. subscriber_api_keys  — add tos_* and risk_disclosure_* consent columns
--   2. subscriber_consent_log — append-only consent audit trail
--   3. record_subscriber_consent() — SECURITY DEFINER upsert helper (service_role)

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Consent columns on subscriber_api_keys
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE public.subscriber_api_keys
    ADD COLUMN IF NOT EXISTS tos_version                   TEXT,
    ADD COLUMN IF NOT EXISTS tos_accepted_at               TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS risk_disclosure_version       TEXT,
    ADD COLUMN IF NOT EXISTS risk_disclosure_accepted_at   TIMESTAMPTZ;

COMMENT ON COLUMN public.subscriber_api_keys.risk_disclosure_accepted_at IS
    'Timestamp the subscriber explicitly acknowledged the futures risk disclosure. '
    'NULL = not acknowledged; active copy-trade (join_bot) is gated on this being set.';

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Append-only consent audit log
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.subscriber_consent_log (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subscriber_id  TEXT NOT NULL,
    consent_type   TEXT NOT NULL
                   CHECK (consent_type IN ('tos', 'risk_disclosure')),
    version        TEXT NOT NULL,
    acknowledgment TEXT,                       -- the phrase the user echoed, if any
    source         TEXT NOT NULL DEFAULT 'mcp' -- 'mcp' | 'stripe_checkout' | 'web'
                   CHECK (source IN ('mcp', 'stripe_checkout', 'web')),
    accepted_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_scl_subscriber  ON public.subscriber_consent_log(subscriber_id, accepted_at DESC);
CREATE INDEX IF NOT EXISTS idx_scl_type        ON public.subscriber_consent_log(consent_type);

ALTER TABLE public.subscriber_consent_log ENABLE ROW LEVEL SECURITY;
-- Append-only; service_role writes. No user-facing policy (audit integrity).

COMMENT ON TABLE public.subscriber_consent_log IS
    'Append-only audit trail of subscriber consent events (ToS + futures risk '
    'disclosure). One row per acknowledgment. Never updated or deleted.';

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. record_subscriber_consent() — SECURITY DEFINER upsert
--    Writes the consent stamp onto subscriber_api_keys AND appends to the log
--    atomically. Called by subscriber_tools.accept_subscriber_terms via the
--    service_role client (or directly as an RPC).
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION public.record_subscriber_consent(
    p_subscriber_id  TEXT,
    p_consent_type   TEXT,
    p_version        TEXT,
    p_acknowledgment TEXT DEFAULT NULL,
    p_source         TEXT DEFAULT 'mcp'
)
RETURNS TIMESTAMPTZ
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    _now TIMESTAMPTZ := now();
BEGIN
    IF p_consent_type NOT IN ('tos', 'risk_disclosure') THEN
        RAISE EXCEPTION 'invalid consent_type: %', p_consent_type;
    END IF;

    INSERT INTO public.subscriber_consent_log
        (subscriber_id, consent_type, version, acknowledgment, source, accepted_at)
    VALUES
        (p_subscriber_id, p_consent_type, p_version, p_acknowledgment,
         COALESCE(p_source, 'mcp'), _now);

    IF p_consent_type = 'tos' THEN
        UPDATE public.subscriber_api_keys
           SET tos_version = p_version, tos_accepted_at = _now
         WHERE subscriber_id = p_subscriber_id;
    ELSE
        UPDATE public.subscriber_api_keys
           SET risk_disclosure_version = p_version,
               risk_disclosure_accepted_at = _now
         WHERE subscriber_id = p_subscriber_id;
    END IF;

    RETURN _now;
END;
$$;

REVOKE EXECUTE ON FUNCTION public.record_subscriber_consent(TEXT, TEXT, TEXT, TEXT, TEXT)
    FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.record_subscriber_consent(TEXT, TEXT, TEXT, TEXT, TEXT)
    TO service_role;
