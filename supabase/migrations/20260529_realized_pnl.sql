-- AlgoChains MCP Server — Realized P&L & Creator Revenue Share
-- Migration: 20260529_realized_pnl.sql
-- Apply via: supabase db push (mcp-server project)
--
-- COMPLIANCE / ACCOUNTING NOTES
-- ─────────────────────────────────────────────────────────────────────────────
--   * Live fills (subscriber_fills.is_live = TRUE) originate from REAL broker
--     fan-out via trade_propagation. These are actual executed orders and carry
--     real realized P&L.
--   * Paper fills (is_live = FALSE) are hypothetical/simulated and MUST carry the
--     CFTC Rule 4.41 hypothetical-performance disclaimer at the display layer.
--     Never co-mingle paper and live P&L without an explicit is_live filter.
--   * Performance fees (if any) on creator_strategy_pnl MUST be computed against a
--     HIGH-WATER MARK — a creator only earns a share on NEW net profit above the
--     prior peak, never on the same gains twice. revenue_share_pct is the gross
--     split; the high-water-mark gate is enforced at the payout layer.
--
-- Creates / alters:
--   1. subscriber_fills      — add is_live, broker, broker_fill_id
--   2. copy_trade_signals    — add strategy_id (signal → marketplace strategy)
--   3. creator_strategy_pnl  — per-creator/strategy realized P&L + revenue share

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Live-fill provenance on subscriber_fills
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE public.subscriber_fills
    ADD COLUMN IF NOT EXISTS is_live        BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS broker         TEXT,
    ADD COLUMN IF NOT EXISTS broker_fill_id TEXT;

COMMENT ON COLUMN public.subscriber_fills.is_live IS
    'TRUE = real broker fill from trade_propagation fan-out; FALSE = paper/'
    'hypothetical fill (must carry CFTC 4.41 disclaimer at display layer).';
COMMENT ON COLUMN public.subscriber_fills.broker_fill_id IS
    'Broker-side fill/execution id for live fills — used for reconciliation '
    'against broker truth. NULL for paper fills.';

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Map a copy-trade signal to a marketplace strategy/creator
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE public.copy_trade_signals
    ADD COLUMN IF NOT EXISTS strategy_id TEXT;

COMMENT ON COLUMN public.copy_trade_signals.strategy_id IS
    'Marketplace strategy id this signal belongs to. Links a propagated signal '
    'back to the creator/strategy for revenue-share attribution.';

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. Creator realized P&L + revenue share
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.creator_strategy_pnl (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    creator_id             TEXT NOT NULL,
    strategy_id            TEXT NOT NULL,
    period_start           TIMESTAMPTZ NOT NULL,
    period_end             TIMESTAMPTZ NOT NULL,
    gross_realized_pnl_usd NUMERIC(18, 2) NOT NULL DEFAULT 0.00,
    creator_share_usd      NUMERIC(18, 2) NOT NULL DEFAULT 0.00,
    revenue_share_pct      NUMERIC(5, 2)  NOT NULL DEFAULT 80.00,
    triggered_payout       BOOLEAN        NOT NULL DEFAULT FALSE,
    created_at             TIMESTAMPTZ    NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_csp_creator_strategy
    ON public.creator_strategy_pnl(creator_id, strategy_id);
CREATE INDEX IF NOT EXISTS idx_csp_period_start
    ON public.creator_strategy_pnl(period_start DESC);

ALTER TABLE public.creator_strategy_pnl ENABLE ROW LEVEL SECURITY;
-- service_role only: realized P&L and payout state are computed server-side from
-- broker truth. No user-facing policy (creators read via a scoped RPC/view later).

COMMENT ON TABLE public.creator_strategy_pnl IS
    'Per-creator, per-strategy realized P&L over a settlement window and the '
    'creator revenue share. gross_realized_pnl_usd aggregates LIVE subscriber_fills '
    '(is_live = TRUE) only. Performance fees, if charged, must use a high-water '
    'mark — never pay a share on gains already credited in a prior period.';
COMMENT ON COLUMN public.creator_strategy_pnl.revenue_share_pct IS
    'Gross creator split percentage (default 80.00 = 80%). Payout layer applies '
    'the high-water-mark gate before crediting creator_share_usd.';
