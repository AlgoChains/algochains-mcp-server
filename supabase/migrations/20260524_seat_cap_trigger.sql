-- AlgoChains MCP Server — Seat Cap Trigger
-- Migration: 20260524_seat_cap_trigger.sql
-- Apply via: supabase db push (mcp-server project)
--
-- Adds a BEFORE INSERT trigger on subscriber_bot_assignments that enforces the
-- bot seat cap atomically inside the transaction. This prevents the TOCTOU
-- race condition where two concurrent requests both read count=19 and both
-- write, exceeding the cap.
--
-- The cap is controlled by app.settings.bot_max_seats (default 20).
-- Override at runtime: SET app.settings.bot_max_seats TO '5';
-- Or permanently: ALTER DATABASE postgres SET app.settings.bot_max_seats TO '20';

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. enforce_bot_seat_cap — BEFORE INSERT trigger function
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION public.enforce_bot_seat_cap()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    _seat_count  INT;
    _max_seats   INT;
BEGIN
    -- Read cap from GUC (overridable per-session); default 20.
    BEGIN
        _max_seats := current_setting('app.settings.bot_max_seats')::INT;
    EXCEPTION WHEN OTHERS THEN
        _max_seats := 20;
    END;

    -- Count active (non-paused) seats for this bot, locking the rows to
    -- prevent concurrent INSERTs from racing past the cap check.
    SELECT COUNT(*)
      INTO _seat_count
      FROM public.subscriber_bot_assignments
     WHERE bot    = NEW.bot
       AND paused = FALSE
    FOR UPDATE;

    -- On upsert (existing subscriber re-joining), seat count includes their
    -- current row; allow it through regardless of cap (idempotent re-join).
    IF EXISTS (
        SELECT 1 FROM public.subscriber_bot_assignments
         WHERE subscriber_id = NEW.subscriber_id AND bot = NEW.bot
    ) THEN
        RETURN NEW;
    END IF;

    IF _seat_count >= _max_seats THEN
        RAISE EXCEPTION 'bot_at_capacity: % has % / % seats filled',
            NEW.bot, _seat_count, _max_seats
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_enforce_bot_seat_cap ON public.subscriber_bot_assignments;
CREATE TRIGGER trg_enforce_bot_seat_cap
    BEFORE INSERT ON public.subscriber_bot_assignments
    FOR EACH ROW EXECUTE FUNCTION public.enforce_bot_seat_cap();

COMMENT ON FUNCTION public.enforce_bot_seat_cap() IS
    'Atomic seat cap guard. Fires BEFORE INSERT on subscriber_bot_assignments. '
    'Counts non-paused seats FOR UPDATE to prevent TOCTOU races. '
    'Re-joins by existing subscribers always pass (idempotent). '
    'Cap configurable via app.settings.bot_max_seats GUC (default 20).';
