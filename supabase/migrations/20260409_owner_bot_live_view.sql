-- AlgoChains MCP Server — Owner Bot Live View
-- Migration: 20260409_owner_bot_live_view.sql
-- Apply via: Supabase dashboard → SQL Editor, or `supabase db push`
--
-- Context:
--   The control-tower Supabase project has `bot_metrics_live` — the canonical
--   live operational metrics table written by metrics_streaming_daemon.py.
--
--   The mcp-server Supabase project has `algochains_bot_performance` — a
--   subscriber-level performance tracking table (per subscription_id + bot_id).
--   These are DIFFERENT tables with DIFFERENT purposes; neither replaces the other:
--
--   bot_metrics_live          → owner's 4 live bots, real-time operational state
--   algochains_bot_performance → subscriber performance history (one row per sub+bot)
--
-- This migration creates v_owner_bot_live: a view that surfaces the owner bot
-- live data (from bot_metrics_live) in the standardized shape the MCP server
-- API uses when it needs to report live bot status alongside subscriber metrics.
--
-- Assumption: both projects share the same SUPABASE_URL (single project) OR
-- bot_metrics_live is accessible via a foreign data wrapper / cross-schema ref.
-- If two separate Supabase projects are in use, apply the RLS migration to the
-- control-tower project and query bot_metrics_live directly from MCP server code
-- using SUPABASE_URL + SUPABASE_ANON_KEY (public read, RLS allows it).


-- v_owner_bot_live: standardized live bot status in mcp-server shape
-- Maps bot_metrics_live columns to algochains_bot_performance column naming
-- so MCP server code can use a consistent schema for both owner and subscriber bots.
CREATE OR REPLACE VIEW v_owner_bot_live
    WITH (security_invoker = true)
AS
SELECT
    -- Match algochains_bot_performance shape for unified MCP queries
    NULL::UUID                  AS id,
    'owner'                     AS subscription_id,   -- sentinel for owner bots
    bot_id,
    bot_name                    AS strategy_name,
    symbol,
    daily_pnl,
    NULL::DOUBLE PRECISION      AS weekly_pnl,        -- not available from live metrics
    win_rate_today              AS win_rate,
    daily_trades                AS trade_count,
    is_running,
    'tradovate'                 AS broker,
    NULL::DOUBLE PRECISION      AS sharpe_ratio,       -- computed offline, not live
    NULL::DOUBLE PRECISION      AS max_drawdown,       -- computed offline, not live
    NULL::DOUBLE PRECISION      AS win_rate_validated, -- from backtest, not live
    last_signal_time            AS last_trade_at,
    updated_at                  AS recorded_at
FROM bot_metrics_live;

-- Grant SELECT to anon and authenticated roles (RLS on underlying table controls access)
GRANT SELECT ON v_owner_bot_live TO anon, authenticated;
