-- Waitlist Slack webhook triggers.
--
-- The Dashboard-created Database Webhook helper schema is not present in every
-- Supabase project, so this uses pg_net directly from a private trigger
-- function. The shared webhook secret is intentionally read from a private
-- single-row config table instead of being stored in each trigger definition.

CREATE EXTENSION IF NOT EXISTS pg_net WITH SCHEMA extensions;

CREATE SCHEMA IF NOT EXISTS app_private;
REVOKE ALL ON SCHEMA app_private FROM PUBLIC;

CREATE TABLE IF NOT EXISTS app_private.waitlist_notify_config (
    id boolean PRIMARY KEY DEFAULT true CHECK (id),
    secret text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

REVOKE ALL ON app_private.waitlist_notify_config FROM PUBLIC;

CREATE OR REPLACE FUNCTION app_private.waitlist_slack_notify()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, net, pg_temp
AS $$
DECLARE
    notify_secret text;
    payload jsonb;
BEGIN
    SELECT secret INTO notify_secret
    FROM app_private.waitlist_notify_config
    WHERE id = true;

    IF notify_secret IS NULL OR notify_secret = '' THEN
        RAISE WARNING 'waitlist_notify_config.secret is not configured; skipping waitlist Slack webhook';
        RETURN NEW;
    END IF;

    payload := jsonb_build_object(
        'type', TG_OP,
        'table', TG_TABLE_NAME,
        'schema', TG_TABLE_SCHEMA,
        'record', to_jsonb(NEW),
        'old_record', NULL
    );

    PERFORM net.http_post(
        url := 'https://trkpzsnwjtmvgppuzlwu.supabase.co/functions/v1/waitlist-slack-notify',
        body := payload,
        params := '{}'::jsonb,
        headers := jsonb_build_object(
            'Content-Type', 'application/json',
            'x-waitlist-secret', notify_secret
        ),
        timeout_milliseconds := 5000
    );

    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION app_private.waitlist_slack_notify() FROM PUBLIC;

DO $$
BEGIN
    IF to_regclass('public.home_betawaitlist') IS NOT NULL THEN
        DROP TRIGGER IF EXISTS waitlist_slack_notify_insert ON public.home_betawaitlist;
        CREATE TRIGGER waitlist_slack_notify_insert
        AFTER INSERT ON public.home_betawaitlist
        FOR EACH ROW EXECUTE FUNCTION app_private.waitlist_slack_notify();
    END IF;

    IF to_regclass('public.home_waitlist') IS NOT NULL THEN
        DROP TRIGGER IF EXISTS waitlist_slack_notify_insert ON public.home_waitlist;
        CREATE TRIGGER waitlist_slack_notify_insert
        AFTER INSERT ON public.home_waitlist
        FOR EACH ROW EXECUTE FUNCTION app_private.waitlist_slack_notify();
    END IF;
END $$;
