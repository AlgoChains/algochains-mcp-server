-- ═══════════════════════════════════════════════════════════════════════════
-- RLS Hardening Migration (2026-06-14)
-- ═══════════════════════════════════════════════════════════════════════════
--
-- Tightens INSERT policies on platform tables that previously used WITH CHECK (true).
-- This prevents unauthenticated/unbound spam inserts and PII ingestion.
--
-- SAFE ROLLOUT NOTES:
-- 1. This migration only REPLACES existing permissive INSERT policies with tighter
--    versions. No SELECT or UPDATE policies are changed.
-- 2. Waitlist and analytics insertions from server-side code (using service_role)
--    are unaffected — service_role bypasses RLS.
-- 3. Frontend signup flows should include auth context (auth.uid()) or pass through
--    a service-role edge function — those flows are unaffected.
-- 4. If you have existing anonymous (anon-role) INSERT flows that rely on the open
--    policy, review before applying and consider edge-function wrappers with rate
--    limiting instead.
--
-- ════════════════════════════════════════════════════════════════════════════

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Support Tickets — require user to be authenticated and own the row
-- ─────────────────────────────────────────────────────────────────────────────

DROP POLICY IF EXISTS "Users can create tickets" ON algochains_support_tickets;

CREATE POLICY "Authenticated users can create own tickets" ON algochains_support_tickets
    FOR INSERT WITH CHECK (
        -- Authenticated users may only create tickets tied to their own UID
        (auth.uid() IS NOT NULL AND auth.uid() = user_id)
        OR
        -- Service role (server-side) may insert on behalf of any user
        auth.role() = 'service_role'
    );

COMMENT ON POLICY "Authenticated users can create own tickets" ON algochains_support_tickets IS
    'Replaced permissive WITH CHECK (true); now requires auth.uid() = user_id or service_role';


-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Analytics Events — require auth or service_role; block anonymous flooding
-- ─────────────────────────────────────────────────────────────────────────────

DROP POLICY IF EXISTS "Anyone can insert analytics events" ON algochains_analytics_events;

CREATE POLICY "Authenticated or service_role can insert analytics" ON algochains_analytics_events
    FOR INSERT WITH CHECK (
        auth.uid() IS NOT NULL
        OR auth.role() = 'service_role'
    );

COMMENT ON POLICY "Authenticated or service_role can insert analytics" ON algochains_analytics_events IS
    'Replaced permissive WITH CHECK (true); requires authenticated session or service_role';


-- ─────────────────────────────────────────────────────────────────────────────
-- 3. Waitlist — allow anon INSERT but validate email column is not null
--    Actual rate limiting must be enforced at the edge function / API layer.
--    We keep anon INSERT intentionally (public waitlist) but add column guard.
-- ─────────────────────────────────────────────────────────────────────────────

DROP POLICY IF EXISTS "Anyone can join waitlist" ON algochains_waitlist;

CREATE POLICY "Anyone can join waitlist with valid email" ON algochains_waitlist
    FOR INSERT WITH CHECK (
        email IS NOT NULL
        AND length(trim(email)) > 3
        AND email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'
    );

COMMENT ON POLICY "Anyone can join waitlist with valid email" ON algochains_waitlist IS
    'Requires non-null, non-trivial email format; actual dedup enforced by UNIQUE constraint and edge function';


-- ─────────────────────────────────────────────────────────────────────────────
-- 4. v_owner_bot_live — document public read intent
--    The anon GRANT is intentional: live bot metrics are public-facing on the
--    marketplace/bot-card pages. This comment records the conscious decision.
--    To restrict: revoke the grant below and update MCP supabase_tools.py to
--    use the service_role client for get_live_bot_metrics.
-- ─────────────────────────────────────────────────────────────────────────────

-- No schema change — documenting intent:
COMMENT ON VIEW v_owner_bot_live IS
    'Public live bot metrics view. anon SELECT granted intentionally for marketplace/bot-card '
    'display. MCP server reads this via anon client in get_live_bot_metrics. '
    'To restrict: REVOKE SELECT ON v_owner_bot_live FROM anon; and switch MCP to service-role client.';

COMMIT;
