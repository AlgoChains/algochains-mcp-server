-- AlgoChains Platform Tables Migration
-- Version: 2026-04-06 (Oleg / Planex task list)
-- Apply via: Supabase dashboard → SQL Editor, or supabase db push
-- All tables use Row Level Security (RLS) — policies defined below.

-- ─────────────────────────────────────────────────────────────────────
-- 1. Support Tickets
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS algochains_support_tickets (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticket_id           TEXT UNIQUE NOT NULL,        -- e.g. TKT-A1B2C3D4
    subject             TEXT NOT NULL,
    description         TEXT NOT NULL,
    user_email          TEXT NOT NULL,
    user_id             UUID REFERENCES auth.users(id) ON DELETE SET NULL,
    category            TEXT NOT NULL DEFAULT 'other'
                        CHECK (category IN ('broker_connection','bot_performance','billing',
                                            'account','onboarding','bug','feature_request','other')),
    priority            TEXT NOT NULL DEFAULT 'medium'
                        CHECK (priority IN ('low','medium','high','critical')),
    status              TEXT NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open','in_progress','resolved','closed')),
    notion_page_id      TEXT,
    attachments         JSONB DEFAULT '[]',
    metadata            JSONB DEFAULT '{}',
    responses           JSONB DEFAULT '[]',
    last_agent_response JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at         TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_tickets_status    ON algochains_support_tickets(status);
CREATE INDEX IF NOT EXISTS idx_tickets_priority  ON algochains_support_tickets(priority);
CREATE INDEX IF NOT EXISTS idx_tickets_email     ON algochains_support_tickets(user_email);
CREATE INDEX IF NOT EXISTS idx_tickets_user_id   ON algochains_support_tickets(user_id);

ALTER TABLE algochains_support_tickets ENABLE ROW LEVEL SECURITY;

-- Users can see and create their own tickets; service role can see all
CREATE POLICY "Users can view own tickets" ON algochains_support_tickets
    FOR SELECT USING (auth.uid() = user_id OR auth.role() = 'service_role');

CREATE POLICY "Users can create tickets" ON algochains_support_tickets
    FOR INSERT WITH CHECK (true);  -- open to any user (rate limiting at app level)

CREATE POLICY "Service role can update tickets" ON algochains_support_tickets
    FOR UPDATE USING (auth.role() = 'service_role');


-- ─────────────────────────────────────────────────────────────────────
-- 2. OAuth Tokens
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS algochains_oauth_tokens (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    broker          TEXT NOT NULL,
    user_id         TEXT NOT NULL,              -- Supabase user ID or owner identifier
    access_token    TEXT NOT NULL,
    refresh_token   TEXT,
    token_type      TEXT DEFAULT 'Bearer',
    scope           TEXT,
    issued_at       DOUBLE PRECISION,
    expires_at      DOUBLE PRECISION,
    connected_at    TIMESTAMPTZ DEFAULT now(),
    UNIQUE (broker, user_id)
);

-- Tokens are sensitive — only service_role can read/write
ALTER TABLE algochains_oauth_tokens ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service role only for oauth tokens" ON algochains_oauth_tokens
    USING (auth.role() = 'service_role');


-- ─────────────────────────────────────────────────────────────────────
-- 3. Waitlist
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS algochains_waitlist (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           TEXT UNIQUE NOT NULL,
    first_name      TEXT DEFAULT '',
    last_name       TEXT DEFAULT '',
    broker_interest TEXT DEFAULT '',
    use_case        TEXT DEFAULT '',
    referral_code   TEXT,
    position        INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'waiting'
                    CHECK (status IN ('waiting','invited','joined','unsubscribed')),
    invite_code     TEXT UNIQUE,
    invited_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_waitlist_status ON algochains_waitlist(status);
CREATE INDEX IF NOT EXISTS idx_waitlist_email  ON algochains_waitlist(email);

ALTER TABLE algochains_waitlist ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service role manages waitlist" ON algochains_waitlist
    USING (auth.role() = 'service_role');

CREATE POLICY "Anyone can join waitlist" ON algochains_waitlist
    FOR INSERT WITH CHECK (true);


-- ─────────────────────────────────────────────────────────────────────
-- 4. Verification Codes
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS algochains_verification_codes (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    destination     TEXT NOT NULL,  -- email or phone
    purpose         TEXT NOT NULL,
    code_hash       TEXT NOT NULL,  -- SHA-256 hash (never store plaintext codes)
    attempts        INTEGER NOT NULL DEFAULT 0,
    used            BOOLEAN NOT NULL DEFAULT false,
    created_at      DOUBLE PRECISION NOT NULL,
    expires_at      DOUBLE PRECISION NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_verif_dest_purpose ON algochains_verification_codes(destination, purpose, used);

ALTER TABLE algochains_verification_codes ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service role only for verification codes" ON algochains_verification_codes
    USING (auth.role() = 'service_role');

-- Auto-clean expired codes daily
CREATE OR REPLACE FUNCTION cleanup_expired_verification_codes()
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM algochains_verification_codes
    WHERE expires_at < extract(epoch FROM now()) OR used = true;
END;
$$;


-- ─────────────────────────────────────────────────────────────────────
-- 5. Analytics Events
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS algochains_analytics_events (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id    TEXT UNIQUE NOT NULL,
    event_type  TEXT NOT NULL,
    session_id  TEXT,
    user_id     UUID REFERENCES auth.users(id) ON DELETE SET NULL,
    page        TEXT,
    referrer    TEXT,
    properties  JSONB DEFAULT '{}',
    ip_country  TEXT,
    device      TEXT,
    tracked_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_analytics_event_type ON algochains_analytics_events(event_type);
CREATE INDEX IF NOT EXISTS idx_analytics_tracked_at  ON algochains_analytics_events(tracked_at);
CREATE INDEX IF NOT EXISTS idx_analytics_user_id     ON algochains_analytics_events(user_id);
CREATE INDEX IF NOT EXISTS idx_analytics_session_id  ON algochains_analytics_events(session_id);

ALTER TABLE algochains_analytics_events ENABLE ROW LEVEL SECURITY;

-- Anyone can write events; only service role can read all
CREATE POLICY "Anyone can insert analytics events" ON algochains_analytics_events
    FOR INSERT WITH CHECK (true);

CREATE POLICY "Service role can read all analytics" ON algochains_analytics_events
    FOR SELECT USING (auth.role() = 'service_role');

CREATE POLICY "Users can read own analytics" ON algochains_analytics_events
    FOR SELECT USING (auth.uid() = user_id);


-- ─────────────────────────────────────────────────────────────────────
-- 6. Bot Performance (Multi-Account Metrics)
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS algochains_bot_performance (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subscription_id     TEXT NOT NULL,
    bot_id              TEXT NOT NULL,
    daily_pnl           DOUBLE PRECISION,
    weekly_pnl          DOUBLE PRECISION,
    win_rate            DOUBLE PRECISION,       -- 0.0 to 100.0
    trade_count         INTEGER DEFAULT 0,
    is_running          BOOLEAN DEFAULT false,
    broker              TEXT,
    sharpe_ratio        DOUBLE PRECISION,
    max_drawdown        DOUBLE PRECISION,
    win_rate_validated  DOUBLE PRECISION,
    last_trade_at       TIMESTAMPTZ,
    recorded_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (subscription_id, bot_id)
);

CREATE INDEX IF NOT EXISTS idx_perf_subscription ON algochains_bot_performance(subscription_id);
CREATE INDEX IF NOT EXISTS idx_perf_bot_id        ON algochains_bot_performance(bot_id);
CREATE INDEX IF NOT EXISTS idx_perf_recorded_at   ON algochains_bot_performance(recorded_at);

ALTER TABLE algochains_bot_performance ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service role manages bot performance" ON algochains_bot_performance
    USING (auth.role() = 'service_role');


-- ─────────────────────────────────────────────────────────────────────
-- 7. Subscriptions (for multi-bot account metrics)
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS algochains_subscriptions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    bot_id              TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active','trial','paused','cancelled','expired')),
    broker              TEXT,
    broker_connected    BOOLEAN DEFAULT false,
    log_path            TEXT,               -- for self-hosted bots
    started_at          TIMESTAMPTZ DEFAULT now(),
    expires_at          TIMESTAMPTZ,
    cancelled_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_subs_user_id ON algochains_subscriptions(user_id);
CREATE INDEX IF NOT EXISTS idx_subs_bot_id  ON algochains_subscriptions(bot_id);
CREATE INDEX IF NOT EXISTS idx_subs_status  ON algochains_subscriptions(status);

ALTER TABLE algochains_subscriptions ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view own subscriptions" ON algochains_subscriptions
    FOR SELECT USING (auth.uid() = user_id OR auth.role() = 'service_role');

CREATE POLICY "Service role manages subscriptions" ON algochains_subscriptions
    FOR ALL USING (auth.role() = 'service_role');


-- ─────────────────────────────────────────────────────────────────────
-- Updated_at trigger (reusable for tickets table)
-- ─────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS set_tickets_updated_at ON algochains_support_tickets;
CREATE TRIGGER set_tickets_updated_at
    BEFORE UPDATE ON algochains_support_tickets
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
