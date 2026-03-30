-- AlgoChains V10-V16 Database Schema Migration
-- ML/AI Engine, Execution, Analytics, Alt Data, Agent Swarm, DeFi, Cloud SaaS

-- ═══════════════════════════════════════════════════════════════
-- V10: ML/AI-Native Strategy Engine
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS feature_sets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    features TEXT NOT NULL,
    target TEXT,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS feature_computations (
    id TEXT PRIMARY KEY,
    feature_set_id TEXT NOT NULL REFERENCES feature_sets(id),
    symbol TEXT NOT NULL,
    start_date TEXT, end_date TEXT,
    row_count INTEGER,
    computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_feat_comp_set ON feature_computations(feature_set_id);

CREATE TABLE IF NOT EXISTS ml_models (
    id TEXT PRIMARY KEY,
    feature_set_id TEXT NOT NULL REFERENCES feature_sets(id),
    model_type TEXT NOT NULL,
    hyperparameters TEXT,
    metrics TEXT,
    train_split REAL DEFAULT 0.8,
    status TEXT DEFAULT 'trained',
    trained_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ml_model_type ON ml_models(model_type);

CREATE TABLE IF NOT EXISTS ml_predictions (
    id TEXT PRIMARY KEY,
    model_id TEXT NOT NULL REFERENCES ml_models(id),
    symbol TEXT NOT NULL,
    prediction TEXT NOT NULL,
    explanation TEXT,
    predicted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_pred_model ON ml_predictions(model_id);

CREATE TABLE IF NOT EXISTS model_registry (
    id TEXT PRIMARY KEY,
    model_id TEXT NOT NULL REFERENCES ml_models(id),
    name TEXT NOT NULL,
    version TEXT DEFAULT '1.0.0',
    stage TEXT DEFAULT 'development' CHECK (stage IN ('development','staging','production','archived')),
    metrics TEXT,
    tags TEXT,
    registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    promoted_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_registry_stage ON model_registry(stage);

CREATE TABLE IF NOT EXISTS rl_agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    algorithm TEXT NOT NULL CHECK (algorithm IN ('ppo','dqn','a2c','sac')),
    environment TEXT,
    reward_config TEXT,
    state TEXT,
    total_episodes INTEGER DEFAULT 0,
    avg_reward REAL DEFAULT 0,
    status TEXT DEFAULT 'created',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS gpu_tasks (
    id TEXT PRIMARY KEY,
    task_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    target_gpu TEXT,
    status TEXT DEFAULT 'queued' CHECK (status IN ('queued','running','completed','failed')),
    result TEXT,
    submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS llm_strategy_specs (
    id TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    asset_class TEXT,
    risk_tolerance TEXT,
    generated_spec TEXT,
    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ═══════════════════════════════════════════════════════════════
-- V11: Institutional-Grade Execution
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS institutional_orders (
    id TEXT PRIMARY KEY,
    account_id TEXT,
    order_data TEXT NOT NULL,
    compliance_status TEXT DEFAULT 'pending',
    compliance_override BOOLEAN DEFAULT FALSE,
    fill_qty REAL DEFAULT 0,
    avg_fill_price REAL,
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending','validated','submitted','partial','filled','rejected','cancelled')),
    submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    filled_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_inst_order_status ON institutional_orders(status);

CREATE TABLE IF NOT EXISTS order_routes (
    id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    venue_id TEXT NOT NULL,
    routing_strategy TEXT,
    allocated_qty REAL,
    fill_qty REAL DEFAULT 0,
    avg_price REAL,
    latency_ms INTEGER,
    status TEXT DEFAULT 'pending',
    routed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_route_order ON order_routes(order_id);

CREATE TABLE IF NOT EXISTS algo_executions (
    id TEXT PRIMARY KEY,
    algo_type TEXT NOT NULL CHECK (algo_type IN ('twap','vwap','iceberg','sniper','pov')),
    order_data TEXT NOT NULL,
    parameters TEXT,
    progress_pct REAL DEFAULT 0,
    filled_qty REAL DEFAULT 0,
    slippage_bps REAL DEFAULT 0,
    status TEXT DEFAULT 'running' CHECK (status IN ('running','paused','completed','stopped','failed')),
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS fix_sessions (
    id TEXT PRIMARY KEY,
    venue TEXT NOT NULL,
    sender_comp_id TEXT NOT NULL,
    target_comp_id TEXT NOT NULL,
    config TEXT,
    seq_num_in INTEGER DEFAULT 0,
    seq_num_out INTEGER DEFAULT 0,
    msg_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'disconnected' CHECK (status IN ('connecting','connected','disconnected','error')),
    connected_at TIMESTAMP,
    last_heartbeat TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tca_reports (
    id TEXT PRIMARY KEY,
    account_id TEXT,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    benchmark TEXT DEFAULT 'vwap',
    trades_analyzed INTEGER DEFAULT 0,
    avg_slippage_bps REAL,
    total_cost REAL,
    report_data TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS execution_venues (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    venue_type TEXT NOT NULL CHECK (venue_type IN ('exchange','dark_pool','ats','otc')),
    config TEXT,
    priority INTEGER DEFAULT 100,
    fill_rate REAL DEFAULT 0,
    avg_latency_ms REAL DEFAULT 0,
    status TEXT DEFAULT 'active',
    registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ═══════════════════════════════════════════════════════════════
-- V12: Real-Time Analytics
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS pnl_snapshots (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    realized_pnl REAL DEFAULT 0,
    unrealized_pnl REAL DEFAULT 0,
    total_pnl REAL DEFAULT 0,
    positions TEXT,
    snapshot_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_pnl_account ON pnl_snapshots(account_id, snapshot_at);

CREATE TABLE IF NOT EXISTS order_flow_snapshots (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    buy_volume REAL, sell_volume REAL,
    net_flow REAL, large_trade_count INTEGER,
    imbalance_ratio REAL,
    analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_oflow_symbol ON order_flow_snapshots(symbol);

CREATE TABLE IF NOT EXISTS microstructure_snapshots (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    bid_ask_spread REAL,
    depth_imbalance REAL,
    toxicity_score REAL,
    tick_direction_ratio REAL,
    analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS regime_detections (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    regime TEXT NOT NULL,
    confidence REAL,
    method TEXT DEFAULT 'hmm',
    transition_probs TEXT,
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_regime_symbol ON regime_detections(symbol, detected_at);

CREATE TABLE IF NOT EXISTS alert_configs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    condition TEXT NOT NULL,
    actions TEXT,
    channels TEXT,
    active BOOLEAN DEFAULT TRUE,
    triggered_count INTEGER DEFAULT 0,
    last_triggered TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS alert_history (
    id TEXT PRIMARY KEY,
    alert_id TEXT NOT NULL REFERENCES alert_configs(id),
    condition_snapshot TEXT,
    action_taken TEXT,
    triggered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_alert_hist ON alert_history(alert_id, triggered_at);

-- ═══════════════════════════════════════════════════════════════
-- V13: Alternative Data Marketplace
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS sentiment_scores (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    source TEXT NOT NULL,
    score REAL NOT NULL,
    magnitude REAL,
    text_snippet TEXT,
    analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sentiment_sym ON sentiment_scores(symbol, source, analyzed_at);

CREATE TABLE IF NOT EXISTS satellite_analyses (
    id TEXT PRIMARY KEY,
    location TEXT NOT NULL,
    data_type TEXT NOT NULL,
    symbol TEXT,
    metrics TEXT NOT NULL,
    analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS scrape_jobs (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    selectors TEXT,
    schedule TEXT,
    last_run TIMESTAMP,
    result_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS scrape_results (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES scrape_jobs(id),
    data TEXT NOT NULL,
    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_scrape_job ON scrape_results(job_id);

CREATE TABLE IF NOT EXISTS sec_filing_analyses (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    filing_type TEXT NOT NULL,
    filing_url TEXT,
    summary TEXT,
    signals TEXT,
    analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sec_symbol ON sec_filing_analyses(symbol);

CREATE TABLE IF NOT EXISTS social_media_signals (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    platform TEXT NOT NULL,
    mention_count INTEGER,
    sentiment_score REAL,
    momentum_score REAL,
    analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_social_symbol ON social_media_signals(symbol, platform);

CREATE TABLE IF NOT EXISTS alt_data_datasets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    provider TEXT,
    quality_score REAL DEFAULT 0.5,
    description TEXT,
    schema TEXT,
    row_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'available',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS alt_data_subscriptions (
    id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES alt_data_datasets(id),
    subscriber_id TEXT,
    config TEXT,
    status TEXT DEFAULT 'active',
    subscribed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ═══════════════════════════════════════════════════════════════
-- V14: Autonomous Agent Swarm
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS swarm_agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    strategy TEXT,
    capital_allocation REAL,
    parameters TEXT,
    status TEXT DEFAULT 'active' CHECK (status IN ('active','paused','terminated')),
    pnl REAL DEFAULT 0,
    trades INTEGER DEFAULT 0,
    spawned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    terminated_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_agent_role ON swarm_agents(role);

CREATE TABLE IF NOT EXISTS task_plans (
    id TEXT PRIMARY KEY,
    goal TEXT NOT NULL,
    constraints TEXT,
    deadline TEXT,
    steps TEXT NOT NULL,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_memories (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES swarm_agents(id),
    memory_type TEXT NOT NULL CHECK (memory_type IN ('episodic','semantic','procedural')),
    content TEXT NOT NULL,
    relevance_score REAL DEFAULT 1.0,
    stored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mem_agent ON agent_memories(agent_id, memory_type);

CREATE TABLE IF NOT EXISTS tool_call_log (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    arguments TEXT,
    result TEXT,
    latency_ms INTEGER,
    called_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_tool_agent ON tool_call_log(agent_id);

CREATE TABLE IF NOT EXISTS consensus_records (
    id TEXT PRIMARY KEY,
    proposal TEXT NOT NULL,
    agent_ids TEXT NOT NULL,
    method TEXT DEFAULT 'weighted',
    votes TEXT,
    result TEXT,
    consensus_reached BOOLEAN DEFAULT FALSE,
    requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_health_snapshots (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES swarm_agents(id),
    cpu_pct REAL, memory_mb REAL,
    error_rate REAL, avg_latency_ms REAL,
    status TEXT,
    checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_health_agent ON agent_health_snapshots(agent_id, checked_at);

-- ═══════════════════════════════════════════════════════════════
-- V15: DeFi & Cross-Chain
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS dex_quotes (
    id TEXT PRIMARY KEY,
    token_in TEXT NOT NULL,
    token_out TEXT NOT NULL,
    amount REAL NOT NULL,
    chain TEXT,
    best_route TEXT,
    expected_output REAL,
    price_impact_pct REAL,
    expires_at TIMESTAMP,
    quoted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS swap_executions (
    id TEXT PRIMARY KEY,
    quote_id TEXT REFERENCES dex_quotes(id),
    tx_hash TEXT,
    actual_output REAL,
    slippage_pct REAL,
    gas_cost REAL,
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending','confirmed','failed','reverted')),
    executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS yield_positions (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT,
    protocol TEXT NOT NULL,
    chain TEXT,
    amount REAL NOT NULL,
    current_apy REAL,
    accrued_yield REAL DEFAULT 0,
    auto_compound BOOLEAN DEFAULT TRUE,
    status TEXT DEFAULT 'active',
    deployed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    withdrawn_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bridge_transfers (
    id TEXT PRIMARY KEY,
    token TEXT NOT NULL,
    amount REAL NOT NULL,
    from_chain TEXT NOT NULL,
    to_chain TEXT NOT NULL,
    bridge_protocol TEXT,
    tx_hash_source TEXT,
    tx_hash_dest TEXT,
    fee REAL,
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending','in_transit','completed','failed')),
    initiated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mev_analyses (
    id TEXT PRIMARY KEY,
    transaction TEXT NOT NULL,
    chain TEXT,
    risk_level TEXT CHECK (risk_level IN ('low','medium','high','critical')),
    sandwich_risk REAL,
    frontrun_risk REAL,
    protection_type TEXT,
    tx_hash TEXT,
    analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS governance_votes (
    id TEXT PRIMARY KEY,
    protocol TEXT NOT NULL,
    proposal_id TEXT NOT NULL,
    vote TEXT NOT NULL,
    reason TEXT,
    tx_hash TEXT,
    voted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS defi_risk_assessments (
    id TEXT PRIMARY KEY,
    protocol TEXT NOT NULL,
    chain TEXT,
    risk_score REAL,
    smart_contract_risk TEXT,
    liquidity_risk TEXT,
    oracle_risk TEXT,
    details TEXT,
    assessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ═══════════════════════════════════════════════════════════════
-- V16: Cloud SaaS Platform
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS saas_tenants (
    id TEXT PRIMARY KEY,
    company_name TEXT NOT NULL,
    admin_email TEXT NOT NULL,
    plan TEXT DEFAULT 'free' CHECK (plan IN ('free','starter','professional','enterprise')),
    config TEXT,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS saas_invoices (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES saas_tenants(id),
    period TEXT NOT NULL,
    subtotal REAL, tax REAL, total REAL,
    line_items TEXT,
    status TEXT DEFAULT 'draft' CHECK (status IN ('draft','issued','paid','overdue','void')),
    issued_at TIMESTAMP,
    paid_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_invoice_tenant ON saas_invoices(tenant_id);

CREATE TABLE IF NOT EXISTS saas_usage (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES saas_tenants(id),
    period TEXT NOT NULL,
    api_calls INTEGER DEFAULT 0,
    trades INTEGER DEFAULT 0,
    data_gb REAL DEFAULT 0,
    compute_hours REAL DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id, period)
);
CREATE INDEX IF NOT EXISTS idx_usage_tenant_v16 ON saas_usage(tenant_id);

CREATE TABLE IF NOT EXISTS saas_payment_methods (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES saas_tenants(id),
    method_type TEXT NOT NULL,
    details TEXT,
    is_default BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS marketplace_strategies (
    id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    publisher_id TEXT,
    name TEXT, description TEXT,
    pricing TEXT NOT NULL,
    tags TEXT,
    sharpe REAL, max_drawdown REAL, total_trades INTEGER,
    subscribers INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active',
    published_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS strategy_subscriptions (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES saas_tenants(id),
    marketplace_strategy_id TEXT NOT NULL REFERENCES marketplace_strategies(id),
    allocation REAL,
    status TEXT DEFAULT 'active',
    subscribed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS white_label_configs (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES saas_tenants(id),
    branding TEXT NOT NULL,
    custom_domain TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id)
);

CREATE TABLE IF NOT EXISTS api_keys (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES saas_tenants(id),
    name TEXT NOT NULL,
    key_hash TEXT NOT NULL,
    permissions TEXT,
    rate_limit INTEGER DEFAULT 1000,
    calls_today INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active',
    last_used TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    revoked_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_apikey_tenant ON api_keys(tenant_id);
