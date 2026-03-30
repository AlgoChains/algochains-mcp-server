-- AlgoChains V8 + V9 Database Schema Migration
-- Strategy Builder, Social Trading, Community Signals, Risk Dashboard, Compliance, Multi-Tenant

-- ═══════════════════════════════════════════════════════════════
-- V8: Strategy Builder SDK
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS strategy_specs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT DEFAULT '1.0.0',
    author TEXT,
    description TEXT,
    asset_class TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    symbols TEXT NOT NULL, -- JSON array
    indicators TEXT NOT NULL, -- JSON
    entry_rules TEXT NOT NULL, -- JSON
    exit_rules TEXT NOT NULL, -- JSON
    position_sizing TEXT, -- JSON
    filters TEXT, -- JSON
    status TEXT DEFAULT 'draft' CHECK (status IN ('draft','backtested','validated','deployed')),
    backtest_results TEXT, -- JSON
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS backtest_runs (
    id TEXT PRIMARY KEY,
    spec_id TEXT NOT NULL REFERENCES strategy_specs(id),
    engine TEXT DEFAULT 'rust_v2',
    results TEXT NOT NULL, -- JSON
    sharpe REAL, max_drawdown REAL, total_trades INTEGER, win_rate REAL,
    started_at TIMESTAMP, completed_at TIMESTAMP,
    duration_ms INTEGER
);
CREATE INDEX idx_backtest_spec ON backtest_runs(spec_id);

CREATE TABLE IF NOT EXISTS optimization_runs (
    id TEXT PRIMARY KEY,
    spec_id TEXT NOT NULL REFERENCES strategy_specs(id),
    n_trials INTEGER, metric TEXT,
    best_value REAL, best_params TEXT, -- JSON
    search_space TEXT, -- JSON
    started_at TIMESTAMP, completed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS walk_forward_runs (
    id TEXT PRIMARY KEY,
    spec_id TEXT NOT NULL REFERENCES strategy_specs(id),
    n_folds INTEGER, train_pct REAL,
    avg_oos_sharpe REAL, consistency REAL, wfe REAL, stability REAL,
    grade TEXT, fold_results TEXT, -- JSON
    completed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS deployments (
    id TEXT PRIMARY KEY,
    spec_id TEXT NOT NULL REFERENCES strategy_specs(id),
    broker TEXT NOT NULL, mode TEXT DEFAULT 'paper',
    capital REAL, status TEXT DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    stopped_at TIMESTAMP
);

-- ═══════════════════════════════════════════════════════════════
-- V8: Social Trading
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS leaders (
    user_id TEXT PRIMARY KEY,
    handle TEXT UNIQUE NOT NULL,
    verified BOOLEAN DEFAULT FALSE,
    ranking_score REAL DEFAULT 0,
    sharpe_12m REAL, sortino_12m REAL, max_drawdown_12m REAL,
    consistency_pct REAL, total_followers INTEGER DEFAULT 0,
    total_aum REAL DEFAULT 0, total_trades INTEGER DEFAULT 0,
    registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS copy_relationships (
    id TEXT PRIMARY KEY,
    follower_id TEXT NOT NULL, leader_id TEXT NOT NULL REFERENCES leaders(user_id),
    config TEXT NOT NULL, -- JSON: scaling_mode, scale_factor, etc.
    status TEXT DEFAULT 'active',
    total_pnl REAL DEFAULT 0, trades_copied INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    stopped_at TIMESTAMP,
    UNIQUE(follower_id, leader_id)
);
CREATE INDEX idx_copy_follower ON copy_relationships(follower_id);
CREATE INDEX idx_copy_leader ON copy_relationships(leader_id);

-- ═══════════════════════════════════════════════════════════════
-- V8: Community Signals
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS community_signals (
    signal_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    symbol TEXT NOT NULL, direction TEXT NOT NULL,
    timeframe TEXT, entry_price REAL, stop_loss REAL, take_profit REAL,
    confidence REAL, rationale TEXT,
    category TEXT DEFAULT 'unverified' CHECK (category IN ('verified','unverified','ai_generated','consensus')),
    verification_hash TEXT,
    published_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,
    outcome TEXT, resolved_at TIMESTAMP,
    upvotes INTEGER DEFAULT 0, downvotes INTEGER DEFAULT 0
);
CREATE INDEX idx_sig_symbol ON community_signals(symbol, timeframe);
CREATE INDEX idx_sig_user ON community_signals(user_id);

CREATE TABLE IF NOT EXISTS signal_subscriptions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    filters TEXT, -- JSON
    status TEXT DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_accuracy (
    user_id TEXT PRIMARY KEY,
    correct INTEGER DEFAULT 0, total INTEGER DEFAULT 0,
    score REAL DEFAULT 0.5,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ═══════════════════════════════════════════════════════════════
-- V9: Risk Dashboard
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS risk_snapshots (
    id TEXT PRIMARY KEY,
    portfolio_value REAL, var_95 REAL, var_99 REAL,
    es_95 REAL, max_drawdown_pct REAL,
    margin_utilization_pct REAL, hhi REAL,
    snapshot_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_risk_time ON risk_snapshots(snapshot_at);

CREATE TABLE IF NOT EXISTS risk_alerts (
    id TEXT PRIMARY KEY,
    rule_id TEXT, alert_type TEXT NOT NULL,
    threshold REAL, current_value REAL,
    action TEXT, channels TEXT, -- JSON
    triggered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS risk_alert_rules (
    id TEXT PRIMARY KEY,
    alert_type TEXT NOT NULL, threshold REAL NOT NULL,
    action TEXT DEFAULT 'notify', channels TEXT, -- JSON
    active BOOLEAN DEFAULT TRUE, triggered_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS stress_test_results (
    id TEXT PRIMARY KEY,
    scenario TEXT NOT NULL, portfolio_value REAL,
    portfolio_loss REAL, loss_pct REAL,
    positions TEXT, -- JSON
    run_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ═══════════════════════════════════════════════════════════════
-- V9: Compliance Module
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS compliance_profiles (
    id TEXT PRIMARY KEY,
    limits TEXT NOT NULL, -- JSON
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_trail (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    data TEXT NOT NULL, -- JSON
    hash TEXT NOT NULL, prev_hash TEXT NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_audit_action ON audit_trail(action);
CREATE INDEX idx_audit_time ON audit_trail(timestamp);

CREATE TABLE IF NOT EXISTS compliance_violations (
    id TEXT PRIMARY KEY,
    violation_type TEXT NOT NULL, symbol TEXT,
    severity TEXT CHECK (severity IN ('critical','high','medium','low')),
    detail TEXT, detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pre_trade_checks (
    id TEXT PRIMARY KEY,
    order_data TEXT NOT NULL, -- JSON
    passed BOOLEAN, violations TEXT, -- JSON
    checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ═══════════════════════════════════════════════════════════════
-- V9: Multi-Tenant White-Label
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS tenants (
    tenant_id TEXT PRIMARY KEY,
    name TEXT NOT NULL, admin_email TEXT NOT NULL,
    tier TEXT DEFAULT 'starter' CHECK (tier IN ('starter','growth','professional','enterprise')),
    status TEXT DEFAULT 'active', api_key TEXT UNIQUE,
    branding TEXT, -- JSON
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sub_accounts (
    sub_account_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
    user_id TEXT NOT NULL, name TEXT NOT NULL,
    permissions TEXT, -- JSON array
    status TEXT DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_sa_tenant ON sub_accounts(tenant_id);

CREATE TABLE IF NOT EXISTS broker_routing (
    tenant_id TEXT PRIMARY KEY REFERENCES tenants(tenant_id),
    brokers TEXT NOT NULL, -- JSON
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tenant_billing (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
    period TEXT NOT NULL, -- YYYY-MM
    base_amount REAL, usage_amount REAL, total REAL,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_billing_tenant ON tenant_billing(tenant_id);

-- ═══════════════════════════════════════════════════════════════
-- V9: Usage Metering (per-tenant API/trade tracking)
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS usage_meters (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
    billing_period TEXT NOT NULL, -- YYYY-MM
    api_calls INTEGER DEFAULT 0,
    trades_executed INTEGER DEFAULT 0,
    aum_days REAL DEFAULT 0,
    active_sub_accounts INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id, billing_period)
);
CREATE INDEX idx_usage_tenant ON usage_meters(tenant_id);

-- ═══════════════════════════════════════════════════════════════
-- V9: Compliance Events (structured violations/alerts)
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS compliance_events (
    id TEXT PRIMARY KEY,
    user_id TEXT,
    tenant_id TEXT REFERENCES tenants(tenant_id),
    event_type TEXT NOT NULL, -- pre_trade_block, wash_trade_alert, surveillance_flag
    severity TEXT NOT NULL CHECK (severity IN ('info','warning','violation')),
    order_id TEXT,
    symbol TEXT,
    details TEXT NOT NULL, -- JSON
    resolved BOOLEAN DEFAULT FALSE,
    resolved_by TEXT,
    resolved_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_compliance_events_tenant ON compliance_events(tenant_id, created_at DESC);
CREATE INDEX idx_compliance_events_type ON compliance_events(event_type, severity);

-- ═══════════════════════════════════════════════════════════════
-- Row-Level Security (PostgreSQL only — ignored by SQLite)
-- ═══════════════════════════════════════════════════════════════
-- These statements are Postgres-specific and will be skipped on SQLite.
-- Uncomment when deploying to Supabase/PostgreSQL:

-- ALTER TABLE risk_snapshots ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE compliance_events ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE sub_accounts ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE usage_meters ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE audit_trail ENABLE ROW LEVEL SECURITY;

-- CREATE POLICY tenant_isolation_risk ON risk_snapshots
--     USING (tenant_id = current_setting('app.tenant_id')::UUID);
-- CREATE POLICY tenant_isolation_compliance ON compliance_events
--     USING (tenant_id = current_setting('app.tenant_id')::UUID);
-- CREATE POLICY tenant_isolation_subaccounts ON sub_accounts
--     USING (tenant_id = current_setting('app.tenant_id')::UUID);
-- CREATE POLICY tenant_isolation_usage ON usage_meters
--     USING (tenant_id = current_setting('app.tenant_id')::UUID);
