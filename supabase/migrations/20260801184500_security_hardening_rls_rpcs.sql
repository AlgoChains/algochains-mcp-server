-- Security hardening applied 2026-08-01 via Supabase MCP (prod: trkpzsnwjtmvgppuzlwu).
-- Idempotent / CI-safe: skip objects that do not exist in the local schema.
-- Companion write-up: docs/SUPABASE_SECURITY_AUDIT_2026-08-01.md

-- Helper: revoke + grant EXECUTE on a function only when it exists.
CREATE OR REPLACE FUNCTION pg_temp._sec_lock_fn(p_identity text)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
  IF to_regprocedure(p_identity) IS NULL THEN
    RAISE NOTICE 'security hardening: skip missing function %', p_identity;
    RETURN;
  END IF;
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC, anon, authenticated', p_identity);
  EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO service_role', p_identity);
END;
$$;

SELECT pg_temp._sec_lock_fn('public.increment_paper_account(uuid, numeric)');
SELECT pg_temp._sec_lock_fn('public.rebase_paper_accounts_50k()');
SELECT pg_temp._sec_lock_fn('public._reschedule_edge_fn(text, text, text)');
SELECT pg_temp._sec_lock_fn('public.create_audit_events_partition(date)');
SELECT pg_temp._sec_lock_fn('public.get_cron_health()');
SELECT pg_temp._sec_lock_fn('public.rls_auto_enable()');
SELECT pg_temp._sec_lock_fn('public.get_slippage_events(text, integer, integer)');

-- Pin search_path when the function exists.
CREATE OR REPLACE FUNCTION pg_temp._sec_set_search_path(p_identity text, p_path text)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
  IF to_regprocedure(p_identity) IS NULL THEN
    RAISE NOTICE 'security hardening: skip search_path for missing function %', p_identity;
    RETURN;
  END IF;
  EXECUTE format('ALTER FUNCTION %s SET search_path = %s', p_identity, p_path);
END;
$$;

SELECT pg_temp._sec_set_search_path('public._reschedule_edge_fn(text, text, text)', 'pg_catalog, public, cron, net');
SELECT pg_temp._sec_set_search_path('public.increment_paper_account(uuid, numeric)', 'pg_catalog, public');
SELECT pg_temp._sec_set_search_path('public.invite_subscriber_override(text, text)', 'pg_catalog, public, auth');
SELECT pg_temp._sec_set_search_path('public.enforce_internal_email_allowlist()', 'pg_catalog, public');
SELECT pg_temp._sec_set_search_path('public._set_updated_at()', 'pg_catalog, public');
SELECT pg_temp._sec_set_search_path('public._trade_log_compute_pnl_delta()', 'pg_catalog, public');
SELECT pg_temp._sec_set_search_path('public.enforce_subscriber_cap()', 'pg_catalog, public');
SELECT pg_temp._sec_set_search_path('public.update_premarket_regime_cache_updated_at()', 'pg_catalog, public');
SELECT pg_temp._sec_set_search_path('public.rebase_paper_accounts_50k()', 'pg_catalog, public');
SELECT pg_temp._sec_set_search_path('public.create_audit_events_partition(date)', 'pg_catalog, public');
SELECT pg_temp._sec_set_search_path('public.get_slippage_events(text, integer, integer)', 'pg_catalog, public');
SELECT pg_temp._sec_set_search_path('public.get_cron_health()', 'pg_catalog, cron, public');
SELECT pg_temp._sec_set_search_path('public.rls_auto_enable()', 'pg_catalog, public');

-- copy_trade_signals: service_role UPDATE only + no client writes
DO $$
BEGIN
  IF to_regclass('public.copy_trade_signals') IS NULL THEN
    RAISE NOTICE 'security hardening: skip copy_trade_signals (missing)';
    RETURN;
  END IF;

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
END $$;

-- subscriber_bot_assignments: clients may only update paused
DO $$
BEGIN
  IF to_regclass('public.subscriber_bot_assignments') IS NULL THEN
    RAISE NOTICE 'security hardening: skip subscriber_bot_assignments (missing)';
    RETURN;
  END IF;

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
END $$;

-- subscriber_paper_orders: WITH CHECK + limited columns
DO $$
BEGIN
  IF to_regclass('public.subscriber_paper_orders') IS NULL THEN
    RAISE NOTICE 'security hardening: skip subscriber_paper_orders (missing)';
    RETURN;
  END IF;

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
END $$;

-- algochains_runs: remove auth.uid() IS NOT NULL leak (prod-only table)
DO $$
BEGIN
  IF to_regclass('public.algochains_runs') IS NULL THEN
    RAISE NOTICE 'security hardening: skip algochains_runs (missing)';
    RETURN;
  END IF;

  DROP POLICY IF EXISTS auth_read_own ON public.algochains_runs;
  DROP POLICY IF EXISTS service_role_select_algochains_runs ON public.algochains_runs;
  CREATE POLICY service_role_select_algochains_runs
    ON public.algochains_runs
    FOR SELECT
    TO service_role
    USING (true);
END $$;

-- Marketplace views: security_invoker + SELECT-only grants
DO $$
DECLARE
  v text;
BEGIN
  FOREACH v IN ARRAY ARRAY[
    'v_approved_marketplace',
    'v_public_marketplace',
    'v_marketplace_validation'
  ]
  LOOP
    IF to_regclass('public.' || v) IS NULL THEN
      RAISE NOTICE 'security hardening: skip view % (missing)', v;
      CONTINUE;
    END IF;
    EXECUTE format('ALTER VIEW public.%I SET (security_invoker = true)', v);
    EXECUTE format('REVOKE ALL ON public.%I FROM anon, authenticated, PUBLIC', v);
    EXECUTE format('GRANT ALL ON public.%I TO service_role', v);
  END LOOP;

  IF to_regclass('public.v_approved_marketplace') IS NOT NULL THEN
    GRANT SELECT ON public.v_approved_marketplace TO anon, authenticated;
  END IF;
  IF to_regclass('public.v_public_marketplace') IS NOT NULL THEN
    GRANT SELECT ON public.v_public_marketplace TO anon, authenticated;
  END IF;
  IF to_regclass('public.v_marketplace_validation') IS NOT NULL THEN
    GRANT SELECT ON public.v_marketplace_validation TO authenticated, service_role;
  END IF;
END $$;

-- Ops tables: service_role only (no per-user owner column; often prod-only)
DO $$
DECLARE
  t text;
  policy_name text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'trade_log',
    'bot_events',
    'bracket_audit',
    'order_cancel_log',
    'ghost_pnl_audit',
    'broker_fill_imports'
  ]
  LOOP
    IF to_regclass('public.' || t) IS NULL THEN
      RAISE NOTICE 'security hardening: skip ops table % (missing)', t;
      CONTINUE;
    END IF;

    -- Drop known overly-broad authenticated read policies when present
    FOR policy_name IN
      SELECT p.policyname
      FROM pg_policies p
      WHERE p.schemaname = 'public'
        AND p.tablename = t
        AND p.policyname IN (
          'authenticated_read',
          'authenticated_read_own',
          'auth_read_bot_events',
          'auth_read_bracket_audit',
          'auth_read_order_cancel_log',
          'authenticated_read_imports',
          'service_role_select_trade_log',
          'service_role_select_bot_events',
          'service_role_select_bracket_audit',
          'service_role_select_order_cancel_log',
          'service_role_select_ghost_pnl_audit'
        )
    LOOP
      EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', policy_name, t);
    END LOOP;

    IF t <> 'broker_fill_imports' THEN
      EXECUTE format(
        'CREATE POLICY %I ON public.%I FOR SELECT TO service_role USING (true)',
        'service_role_select_' || t,
        t
      );
    END IF;

    EXECUTE format('REVOKE ALL ON public.%I FROM anon, authenticated', t);
    EXECUTE format('GRANT ALL ON public.%I TO service_role', t);
  END LOOP;
END $$;

-- Cron for trade_evidence_rollup: set x-rollup-secret via Dashboard SQL or:
-- SELECT cron.alter_job(<jobid>, command := $$ ... x-rollup-secret ... $$);
-- Secret value lives in Edge Function secrets as TRADE_EVIDENCE_ROLLUP_SECRET.
