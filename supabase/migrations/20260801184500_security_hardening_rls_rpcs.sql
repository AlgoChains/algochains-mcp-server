-- Security hardening applied 2026-08-01 via Supabase MCP (prod: trkpzsnwjtmvgppuzlwu).
-- Re-run safely with IF EXISTS / DROP IF EXISTS patterns where possible.
-- Companion write-up: docs/SUPABASE_SECURITY_AUDIT_2026-08-01.md

-- 1) Revoke dangerous SECURITY DEFINER RPCs from API roles
REVOKE ALL ON FUNCTION public.increment_paper_account(uuid, numeric) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.increment_paper_account(uuid, numeric) TO service_role;

REVOKE ALL ON FUNCTION public.rebase_paper_accounts_50k() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.rebase_paper_accounts_50k() TO service_role;

REVOKE ALL ON FUNCTION public._reschedule_edge_fn(text, text, text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public._reschedule_edge_fn(text, text, text) TO service_role;

REVOKE ALL ON FUNCTION public.create_audit_events_partition(date) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.create_audit_events_partition(date) TO service_role;

REVOKE ALL ON FUNCTION public.get_cron_health() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.get_cron_health() TO service_role;

REVOKE ALL ON FUNCTION public.rls_auto_enable() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.rls_auto_enable() TO service_role;

REVOKE ALL ON FUNCTION public.get_slippage_events(text, integer, integer) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.get_slippage_events(text, integer, integer) TO service_role;

-- 2) copy_trade_signals: service_role UPDATE only + no client writes
DROP POLICY IF EXISTS cts_service_update ON public.copy_trade_signals;
CREATE POLICY cts_service_update
  ON public.copy_trade_signals
  FOR UPDATE
  TO service_role
  USING (true)
  WITH CHECK (true);

REVOKE INSERT, UPDATE, DELETE ON public.copy_trade_signals FROM anon, authenticated, PUBLIC;
GRANT SELECT ON public.copy_trade_signals TO anon, authenticated;
GRANT ALL ON public.copy_trade_signals TO service_role;

-- 3) subscriber_bot_assignments: clients may only update paused
DROP POLICY IF EXISTS sba_owner_update_pause ON public.subscriber_bot_assignments;
CREATE POLICY sba_owner_update_pause
  ON public.subscriber_bot_assignments
  FOR UPDATE
  TO authenticated
  USING (auth.uid() = subscriber_id)
  WITH CHECK (auth.uid() = subscriber_id);

REVOKE UPDATE ON public.subscriber_bot_assignments FROM anon, authenticated, PUBLIC;
GRANT UPDATE (paused) ON public.subscriber_bot_assignments TO authenticated;
GRANT ALL ON public.subscriber_bot_assignments TO service_role;

-- 4) subscriber_paper_orders: WITH CHECK + limited columns
DROP POLICY IF EXISTS spo_update_own ON public.subscriber_paper_orders;
CREATE POLICY spo_update_own
  ON public.subscriber_paper_orders
  FOR UPDATE
  TO authenticated
  USING (auth.uid() = subscriber_id)
  WITH CHECK (auth.uid() = subscriber_id);

REVOKE UPDATE ON public.subscriber_paper_orders FROM anon, authenticated, PUBLIC;
GRANT UPDATE (status, error_msg, updated_at) ON public.subscriber_paper_orders TO authenticated;
GRANT ALL ON public.subscriber_paper_orders TO service_role;

-- 5) algochains_runs: remove auth.uid() IS NOT NULL leak
DROP POLICY IF EXISTS auth_read_own ON public.algochains_runs;
DROP POLICY IF EXISTS service_role_select_algochains_runs ON public.algochains_runs;
CREATE POLICY service_role_select_algochains_runs
  ON public.algochains_runs
  FOR SELECT
  TO service_role
  USING (true);

-- 6) Marketplace views: security_invoker + SELECT-only grants
ALTER VIEW public.v_approved_marketplace SET (security_invoker = true);
ALTER VIEW public.v_public_marketplace SET (security_invoker = true);
ALTER VIEW public.v_marketplace_validation SET (security_invoker = true);

REVOKE ALL ON public.v_approved_marketplace FROM anon, authenticated, PUBLIC;
REVOKE ALL ON public.v_public_marketplace FROM anon, authenticated, PUBLIC;
REVOKE ALL ON public.v_marketplace_validation FROM anon, authenticated, PUBLIC;

GRANT SELECT ON public.v_approved_marketplace TO anon, authenticated;
GRANT SELECT ON public.v_public_marketplace TO anon, authenticated;
GRANT SELECT ON public.v_marketplace_validation TO authenticated, service_role;
GRANT ALL ON public.v_approved_marketplace TO service_role;
GRANT ALL ON public.v_public_marketplace TO service_role;
GRANT ALL ON public.v_marketplace_validation TO service_role;

-- 7) Pin search_path on functions flagged by advisors
ALTER FUNCTION public._reschedule_edge_fn(text, text, text) SET search_path = pg_catalog, public, cron, net;
ALTER FUNCTION public.increment_paper_account(uuid, numeric) SET search_path = pg_catalog, public;
ALTER FUNCTION public.invite_subscriber_override(text, text) SET search_path = pg_catalog, public, auth;
ALTER FUNCTION public.enforce_internal_email_allowlist() SET search_path = pg_catalog, public;
ALTER FUNCTION public._set_updated_at() SET search_path = pg_catalog, public;
ALTER FUNCTION public._trade_log_compute_pnl_delta() SET search_path = pg_catalog, public;
ALTER FUNCTION public.enforce_subscriber_cap() SET search_path = pg_catalog, public;
ALTER FUNCTION public.update_premarket_regime_cache_updated_at() SET search_path = pg_catalog, public;
ALTER FUNCTION public.rebase_paper_accounts_50k() SET search_path = pg_catalog, public;
ALTER FUNCTION public.create_audit_events_partition(date) SET search_path = pg_catalog, public;
ALTER FUNCTION public.get_slippage_events(text, integer, integer) SET search_path = pg_catalog, public;
ALTER FUNCTION public.get_cron_health() SET search_path = pg_catalog, cron, public;
ALTER FUNCTION public.rls_auto_enable() SET search_path = pg_catalog, public;

-- 8) Ops tables: service_role only (no per-user owner column)
DROP POLICY IF EXISTS authenticated_read ON public.trade_log;
DROP POLICY IF EXISTS authenticated_read_own ON public.trade_log;
DROP POLICY IF EXISTS auth_read_bot_events ON public.bot_events;
DROP POLICY IF EXISTS auth_read_bracket_audit ON public.bracket_audit;
DROP POLICY IF EXISTS auth_read_order_cancel_log ON public.order_cancel_log;
DROP POLICY IF EXISTS authenticated_read_imports ON public.broker_fill_imports;
DROP POLICY IF EXISTS authenticated_read ON public.ghost_pnl_audit;

DROP POLICY IF EXISTS service_role_select_trade_log ON public.trade_log;
CREATE POLICY service_role_select_trade_log ON public.trade_log
  FOR SELECT TO service_role USING (true);

DROP POLICY IF EXISTS service_role_select_bot_events ON public.bot_events;
CREATE POLICY service_role_select_bot_events ON public.bot_events
  FOR SELECT TO service_role USING (true);

DROP POLICY IF EXISTS service_role_select_bracket_audit ON public.bracket_audit;
CREATE POLICY service_role_select_bracket_audit ON public.bracket_audit
  FOR SELECT TO service_role USING (true);

DROP POLICY IF EXISTS service_role_select_order_cancel_log ON public.order_cancel_log;
CREATE POLICY service_role_select_order_cancel_log ON public.order_cancel_log
  FOR SELECT TO service_role USING (true);

DROP POLICY IF EXISTS service_role_select_ghost_pnl_audit ON public.ghost_pnl_audit;
CREATE POLICY service_role_select_ghost_pnl_audit ON public.ghost_pnl_audit
  FOR SELECT TO service_role USING (true);

REVOKE ALL ON public.trade_log FROM anon, authenticated;
REVOKE ALL ON public.bot_events FROM anon, authenticated;
REVOKE ALL ON public.bracket_audit FROM anon, authenticated;
REVOKE ALL ON public.order_cancel_log FROM anon, authenticated;
REVOKE ALL ON public.ghost_pnl_audit FROM anon, authenticated;
REVOKE ALL ON public.broker_fill_imports FROM anon, authenticated;

GRANT ALL ON public.trade_log TO service_role;
GRANT ALL ON public.bot_events TO service_role;
GRANT ALL ON public.bracket_audit TO service_role;
GRANT ALL ON public.order_cancel_log TO service_role;
GRANT ALL ON public.ghost_pnl_audit TO service_role;
GRANT ALL ON public.broker_fill_imports TO service_role;

-- 9) Cron for trade_evidence_rollup: set x-rollup-secret via Dashboard SQL or:
-- SELECT cron.alter_job(<jobid>, command := $$ ... x-rollup-secret ... $$);
-- Secret value lives in Edge Function secrets as TRADE_EVIDENCE_ROLLUP_SECRET
-- (and temporarily as BOOTSTRAP_SECRET in the deployed function — rotate ASAP).
