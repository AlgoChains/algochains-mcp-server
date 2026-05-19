-- Waitlist Slack anti-spam constraints.
--
-- Keep duplicate signup emails from reaching Database Webhooks. The canonical
-- platform migration already declares algochains_waitlist.email UNIQUE; these
-- guards cover the Django-era waitlist tables found in production.

DO $$
BEGIN
    IF to_regclass('public.home_betawaitlist') IS NOT NULL THEN
        EXECUTE '
            CREATE UNIQUE INDEX IF NOT EXISTS idx_home_betawaitlist_email_lower_unique
            ON public.home_betawaitlist (lower(email))
            WHERE email IS NOT NULL
        ';
    END IF;

    IF to_regclass('public.home_waitlist') IS NOT NULL THEN
        EXECUTE '
            CREATE UNIQUE INDEX IF NOT EXISTS idx_home_waitlist_email_lower_unique
            ON public.home_waitlist (lower(email))
            WHERE email IS NOT NULL
        ';
    END IF;
END $$;
