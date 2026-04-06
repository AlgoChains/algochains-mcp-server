// Generated on 2026-03-30T23:58:37.368Z by mcporter@0.8.1
// Server: algochains
// Source: /Users/treycsa/CascadeProjects/algochains-mcp-server/config/mcporter.json
// Transport: STDIO algochains-mcp

import type { CallResult } from 'mcporter';

export interface AlgochainsTools {
  /**
   * Place a trading order on any connected broker. Supports market, limit, stop, stop-limit, and
   * trailing stop orders across Alpaca, IBKR, Oanda, TradersPost (Schwab/Robinhood/Tastytrade), and
   * QuantConnect.
   *
   * @param broker Broker name: alpaca, ibkr, oanda, traderspost, quantconnect
   * @param symbol Ticker symbol (e.g. AAPL, EUR_USD, ES)
   * @param qty Order quantity
   * @param limit_price? Limit price (for limit/stop-limit orders)
   * @param stop_price? Stop price (for stop/stop-limit orders)
   * @param trail_pct? Trailing stop percentage
   */
  place_order(broker: string, symbol: string, side: "buy" | "sell", qty: number, order_type?: "market" | "limit" | "stop" | "stop_limit" | "trailing_stop"): Promise<object>;
  // optional (4): limit_price, stop_price, trail_pct, time_in_force

  /**
   * Cancel an open order by ID on a specific broker.
   */
  cancel_order(broker: string, order_id: string): Promise<CallResult>;

  /**
   * Close an entire position in a symbol on a specific broker.
   */
  close_position(broker: string, symbol: string): Promise<object>;

  /**
   * Close ALL open positions on a specific broker. Use with caution.
   */
  close_all_positions(broker: string): Promise<CallResult>;

  /**
   * Get account information (equity, cash, buying power) from a broker.
   */
  get_account(broker: string): Promise<object>;

  /**
   * Get all open positions from a broker.
   */
  get_positions(broker: string): Promise<object>;

  /**
   * Get orders from a broker, optionally filtered by status.
   *
   * @param status? Filter: open, closed, all
   */
  get_orders(broker: string, status?: string): Promise<object>;

  /**
   * Get a unified portfolio summary across ALL connected brokers — total equity, positions, and P&L.
   */
  get_portfolio_summary(): Promise<CallResult>;

  /**
   * Get current quote (bid/ask/last) for a symbol from a broker.
   */
  get_quote(broker: string, symbol: string): Promise<object>;

  /**
   * List all configured and connected brokers with their status and supported asset classes.
   */
  list_brokers(): Promise<CallResult>;

  /**
   * Connect to a specific broker. Must be configured via environment variables.
   */
  connect_broker(broker: string): Promise<CallResult>;

  /**
   * Run health check on all connected brokers.
   */
  broker_health_check(): Promise<CallResult>;

  /**
   * Browse available bot listings on the AlgoChains marketplace. Filter by asset class, strategy type,
   * or minimum Sharpe.
   *
   * @param asset_class? stocks, crypto, futures, forex, options
   * @param strategy_type? trend, mean_reversion, breakout, momentum
   * @param min_sharpe? Minimum OOS Sharpe ratio
   */
  browse_marketplace(asset_class?: string, strategy_type?: string, min_sharpe?: number, limit?: number): Promise<CallResult>;

  /**
   * Get detailed information about a specific marketplace listing by slug.
   *
   * @param slug Listing slug (e.g. mktbot_AAPL_bb_mean_reversion_hour)
   */
  get_listing_detail(slug: string): Promise<CallResult>;

  /**
   * Subscribe to a marketplace bot listing for paper or live trading.
   *
   * @param broker Which broker to deploy on
   */
  subscribe_to_bot(slug: string, broker: string, mode?: "paper" | "live"): Promise<CallResult>;

  /**
   * Submit a trading strategy for MCPT validation. External AI agents use this to submit their
   * strategies to the AlgoChains marketplace. Strategies pass through 6 validation gates: schema,
   * performance, overfitting, MCPT, walk-forward, paper trading.
   *
   * @param symbol Ticker symbol
   * @param strategy_type trend, mean_reversion, breakout, momentum, scalp
   * @param timeframe 5min, 15min, hour, 4h, day
   * @param oos_sharpe Out-of-sample Sharpe ratio
   * @param oos_trades Number of OOS trades
   * @param is_sharpe? In-sample Sharpe ratio
   * @param max_drawdown_pct Maximum drawdown percentage
   * @param win_rate? Win rate percentage
   * @param parameters? Strategy parameters dict
   * @param mcpt? MCPT validation data
   * @param walk_forward? Walk-forward validation data
   * @param backtest_code? Python backtest code (will be sandboxed)
   */
  submit_strategy(symbol: string, strategy_type: string, timeframe: string, oos_sharpe: number, oos_trades: number, max_drawdown_pct: number): Promise<CallResult>;
  // optional (7): is_sharpe, win_rate, parameters, mcpt, walk_forward, ...

  /**
   * Check the validation status of a previously submitted strategy.
   */
  check_validation_status(submission_id: string): Promise<CallResult>;

  /**
   * Get the current validation gate thresholds and requirements for strategy submissions.
   */
  get_validation_gates(): Promise<CallResult>;

  /**
   * Get AlgoChains MCP server diagnostics: tool call statistics, error rates, recent call history, and
   * broker connection status.
   */
  server_diagnostics(): Promise<CallResult>;

  /**
   * Subscribe to a real-time data stream: pnl, fills, positions, quotes, trades, risk_alerts,
   * order_updates.
   *
   * @param symbols? Optional symbol filter
   * @param brokers? Optional broker filter
   */
  stream_subscribe(topic: "pnl" | "fills" | "positions" | "quotes" | "trades" | "risk_alerts" | "order_updates", symbols?: string[], brokers?: string[]): Promise<CallResult>;

  /**
   * Get the latest events from a stream topic (pnl, fills, positions, etc.).
   */
  stream_snapshot(topic: "pnl" | "fills" | "positions" | "quotes" | "trades" | "risk_alerts" | "order_updates", limit?: number): Promise<CallResult>;

  /**
   * Get real-time P&L snapshot across all connected brokers with live equity, unrealized P&L, and daily
   * change.
   */
  get_realtime_pnl(): Promise<CallResult>;

  /**
   * Get streaming system statistics: buffer sizes, active subscriptions, callback counts.
   */
  stream_stats(): Promise<CallResult>;

  /**
   * Optimize capital allocation across multiple bot subscriptions using risk parity, mean-variance,
   * Kelly criterion, or max Sharpe methods.
   *
   * @param total_capital Total capital to allocate ($)
   * @param max_drawdown_limit? Max acceptable portfolio drawdown (decimal)
   */
  optimize_portfolio(bots: string[], total_capital: number, method?: "equal_weight" | "risk_parity" | "mean_variance" | "kelly" | "max_sharpe" | "min_variance", max_drawdown_limit?: number): Promise<CallResult>;

  /**
   * Compare multiple allocation methods side-by-side for the same set of bots to find the best strategy.
   */
  compare_allocations(bots: string[], total_capital: number): Promise<CallResult>;

  /**
   * Configure notification channels: slack, email, discord, telegram, mobile push (FCM/APNS).
   *
   * @param webhook_url? Webhook URL (for Slack/Discord)
   * @param api_key? API key (for email/FCM)
   * @param bot_token? Bot token (for Telegram)
   * @param chat_id? Chat ID (for Telegram)
   */
  configure_notifications(channel: "slack" | "email" | "discord" | "telegram" | "fcm" | "apns", webhook_url?: string, api_key?: string, bot_token?: string, chat_id?: string): Promise<CallResult>;

  /**
   * Send a notification across configured channels. Supports order fills, P&L alerts, drawdown warnings,
   * and custom messages.
   *
   * @param channels? Override default channels
   */
  send_notification(event?: "order_fill" | "daily_pnl" | "drawdown_alert" | "bot_status" | "margin_warning" | "risk_alert" | "rebalance_needed" | "custom", title: string, body: string, priority?: "critical" | "high" | "medium" | "low", channels?: string[]): Promise<CallResult>;

  /**
   * Get notification history with optional event type filter.
   *
   * @param event? Filter by event type
   */
  get_notification_history(limit?: number, event?: string): Promise<CallResult>;

  /**
   * Get notification system statistics: configured channels, send counts by event and priority.
   */
  notification_stats(): Promise<CallResult>;

  /**
   * List all available and configured data providers (Polygon, Yahoo Finance, Alpha Vantage, Finnhub,
   * Twelve Data, etc.).
   */
  list_data_providers(): Promise<CallResult>;

  /**
   * Fetch OHLCV bars from any configured data provider. Falls back through providers if first one fails.
   *
   * @param symbol Ticker symbol (e.g. AAPL, EUR/USD, BTC-USD)
   * @param provider? Specific provider (polygon, yahoo, alphavantage, finnhub, twelvedata). If omitted,
   *                  uses best available.
   * @param start? Start date (YYYY-MM-DD)
   * @param end? End date (YYYY-MM-DD)
   */
  get_market_data(symbol: string, interval?: "1min" | "5min" | "15min" | "30min" | "1hour" | "4hour" | "1day" | "1week" | "1month", limit?: number, provider?: string, start?: string): Promise<CallResult>;
  // optional (1): end

  /**
   * Get a real-time quote from any configured data provider.
   *
   * @param provider? Specific provider. If omitted, uses best available.
   */
  get_realtime_quote(symbol: string, provider?: string): Promise<CallResult>;

  /**
   * Get financial news for a symbol from configured data providers (Polygon, Finnhub).
   */
  get_news(symbol: string, limit?: number, provider?: string): Promise<CallResult>;

  /**
   * Get fundamental data (P/E, EPS, market cap, revenue, etc.) for a stock from configured data
   * providers.
   */
  get_fundamentals(symbol: string, provider?: string): Promise<CallResult>;

  /**
   * Search for ticker symbols across configured data providers.
   *
   * @param query Search query (e.g. 'Apple', 'bitcoin', 'EUR')
   */
  search_symbols(query: string, provider?: string): Promise<CallResult>;

  /**
   * Run health checks on all configured data providers.
   */
  data_provider_health(): Promise<CallResult>;

  /**
   * Autonomously scan your environment for existing API keys across 10+ data providers. Checks env vars,
   * .env files, IDE configs, shell profiles, and config directories. Say 'gather my keys' to trigger.
   */
  discover_keys(): Promise<CallResult>;

  /**
   * Deep-validate all discovered API keys with live API calls. Returns permissions, rate limits, plan
   * tier, and health status for each key.
   *
   * @param providers? Optional list of provider names to validate. If empty, validates all discovered
   *                   keys.
   */
  validate_keys(providers?: string[]): Promise<CallResult>;

  /**
   * Show what data providers you're missing, what each unlocks, signup URLs, free tier availability, and
   * a quick-win recommendation.
   */
  key_gap_analysis(): Promise<CallResult>;

  /**
   * Add a new API key for a data provider. Validates the key and optionally writes it to your .env file.
   *
   * @param provider Provider name: polygon, alpha_vantage, finnhub, twelve_data, databento,
   *                 unusual_whales, intrinio, quandl, openbb
   * @param key_value The API key value
   * @param write_to_env? Whether to write the key to .env file
   */
  provision_key(provider: string, key_value: string, write_to_env?: boolean): Promise<CallResult>;

  /**
   * Real-time health check of all configured API keys. Shows which are valid, expired, rate-limited, or
   * invalid.
   */
  key_health(): Promise<CallResult>;

  /**
   * Export your validated key configuration in various formats: env, json, mcp_windsurf, mcp_cursor,
   * mcp_vscode.
   */
  export_config(format?: "env" | "json" | "mcp_windsurf" | "mcp_cursor" | "mcp_vscode"): Promise<CallResult>;

  /**
   * Build a proprietary dataset for a symbol/timeframe using all available data providers. Normalizes,
   * deduplicates, and optionally enriches with technical indicators, regime labels, and more.
   *
   * @param symbol Ticker symbol (e.g. AAPL, EURUSD, BTC)
   * @param start_date? Start date (YYYY-MM-DD)
   * @param end_date? End date (YYYY-MM-DD)
   * @param providers? Specific providers to use. If empty, uses all available.
   * @param enrichments? Feature enrichments to apply to the dataset
   */
  build_dataset(symbol: string, timeframe?: "1min" | "5min" | "15min" | "1h" | "4h" | "daily" | "weekly", start_date?: string, end_date?: string, providers?: string[]): Promise<CallResult>;
  // optional (2): enrichments, format

  /**
   * List all built proprietary datasets with metadata (rows, columns, date range, sources, size).
   */
  list_datasets(): Promise<CallResult>;

  /**
   * Show what data you CAN build vs what you're missing based on your available API keys.
   */
  dataset_status(): Promise<CallResult>;

  /**
   * Add feature enrichments (technical indicators, regime labels, calendar features, volume profile) to
   * an existing dataset.
   *
   * @param dataset_id ID of the dataset to enrich
   */
  enrich_dataset(dataset_id: string, enrichments: "technical_indicators" | "sentiment" | "cross_asset_correlation" | "regime_labels" | "volume_profile" | "calendar_features"): Promise<CallResult>;

  /**
   * Export a dataset in ML-ready format with time-based train/test split (no data leakage). Ready for
   * scikit-learn, XGBoost, PyTorch.
   *
   * @param train_test_split? Train/test ratio (0.0-1.0)
   * @param target_column? Target variable for ML prediction
   */
  export_dataset(dataset_id: string, format?: "parquet" | "csv" | "json", train_test_split?: number, target_column?: string): Promise<CallResult>;

  /**
   * Create a new AI-native declarative strategy specification (StrategySpec). Define indicators,
   * entry/exit rules, position sizing in JSON.
   */
  create_strategy(name: string, symbols: string[], timeframe: string, indicators: string[], entry_rules: Record<string, unknown>, exit_rules: Record<string, unknown>): Promise<CallResult>;
  // optional (2): asset_class, position_sizing

  /**
   * Validate a StrategySpec for schema correctness, parameter ranges, and internal consistency.
   *
   * @param spec Full StrategySpec object to validate
   */
  validate_strategy(spec: Record<string, unknown>): Promise<CallResult>;

  /**
   * Run a backtest on a StrategySpec using the Rust engine. Returns Sharpe, drawdown, win rate, P&L.
   */
  backtest_strategy(spec: Record<string, unknown>, capital?: number): Promise<CallResult>;

  /**
   * Run Optuna-based parameter optimization on a StrategySpec. Finds best params across n_trials.
   */
  optimize_strategy(spec: Record<string, unknown>, n_trials?: number, metric?: string): Promise<CallResult>;

  /**
   * Run K-fold walk-forward validation on a strategy. Tests robustness across time periods.
   */
  walk_forward_test(spec: Record<string, unknown>, n_folds?: number, train_pct?: number): Promise<CallResult>;

  /**
   * Deploy a validated strategy to paper or live trading on a connected broker.
   */
  deploy_strategy(spec: Record<string, unknown>, broker: string, mode?: "paper" | "live", capital?: number): Promise<CallResult>;

  /**
   * Browse pre-built strategy templates (RSI Momentum, BB Mean Reversion, EMA Crossover, etc).
   */
  list_templates(category?: "momentum" | "mean_reversion" | "trend" | "breakout" | "pairs", asset_class?: string): Promise<CallResult>;

  /**
   * Fork a strategy template into your own editable StrategySpec with custom parameters.
   */
  fork_template(template_id: string, new_name?: string, symbols?: string[], overrides?: Record<string, unknown>): Promise<CallResult>;

  /**
   * Register as a copy-trading leader. Requires 90+ day track record, 50+ trades, Sharpe ≥ 1.0.
   */
  become_leader(user_id: string, handle: string, track_record?: Record<string, unknown>): Promise<CallResult>;

  /**
   * Get a leader's full performance stats, followers, and recent signals.
   */
  get_leader_stats(leader_id: string): Promise<CallResult>;

  /**
   * Start copy-trading a leader with configurable scaling and risk limits.
   */
  follow_leader(follower_id: string, leader_id: string, config?: Record<string, unknown>): Promise<CallResult>;

  /**
   * Stop copy-trading a leader. Optionally close all copied positions.
   */
  unfollow_leader(follower_id: string, leader_id: string, close_positions?: boolean): Promise<CallResult>;

  /**
   * Get status of all copy-trading relationships for a follower.
   */
  get_copy_status(follower_id: string): Promise<CallResult>;

  /**
   * Update copy-trading parameters (scaling, risk limits, allowed assets).
   */
  set_copy_parameters(follower_id: string, leader_id: string, config_updates: Record<string, unknown>): Promise<CallResult>;

  /**
   * Publish a trading signal to the community feed with optional trade verification.
   */
  publish_signal(user_id: string, symbol: string, direction: "long" | "short", timeframe?: string, entry_price?: number): Promise<CallResult>;
  // optional (5): stop_loss, take_profit, confidence, rationale, trade_hash

  /**
   * Subscribe to community signals with filters (symbol, category, min accuracy).
   */
  subscribe_signals(user_id: string, filters?: Record<string, unknown>): Promise<CallResult>;

  /**
   * Verify a signal with trade proof from broker (order ID, fill price, fill time).
   */
  verify_signal(signal_id: string, trade_proof: Record<string, unknown>): Promise<CallResult>;

  /**
   * Get community consensus for a symbol — weighted by publisher accuracy scores.
   */
  get_consensus(symbol: string, timeframe?: string): Promise<CallResult>;

  /**
   * Get a user's signal accuracy score and history.
   */
  get_signal_accuracy(user_id: string): Promise<CallResult>;

  /**
   * Calculate Value-at-Risk (parametric, historical, or Monte Carlo) at given confidence level.
   */
  calculate_var(portfolio: Record<string, unknown>, method?: "parametric" | "historical" | "monte_carlo", confidence?: number, horizon_days?: number): Promise<CallResult>;

  /**
   * Calculate Expected Shortfall (CVaR) — average loss in tail scenarios beyond VaR.
   */
  calculate_expected_shortfall(portfolio: Record<string, unknown>, confidence?: number, horizon_days?: number): Promise<CallResult>;

  /**
   * Analyze portfolio factor exposures (Market, Size, Value, Momentum, Volatility, Quality).
   */
  get_factor_exposure(portfolio: Record<string, unknown>): Promise<CallResult>;

  /**
   * Run historical or custom stress tests (COVID, GFC, Flash Crash, etc) on portfolio.
   */
  run_stress_test(portfolio: Record<string, unknown>, scenario?: string, custom_shocks?: Record<string, unknown>): Promise<CallResult>;

  /**
   * Monitor current drawdown vs peak, with estimated recovery time.
   */
  get_drawdown_monitor(portfolio: Record<string, unknown>): Promise<CallResult>;

  /**
   * Check margin utilization, buffer to margin call, and status.
   */
  get_margin_utilization(account: Record<string, unknown>): Promise<CallResult>;

  /**
   * Get aggregate portfolio Greeks (delta, gamma, theta, vega, rho) for options positions.
   */
  get_greeks_exposure(portfolio: Record<string, unknown>): Promise<CallResult>;

  /**
   * Set up risk alert rules (drawdown, VaR breach, margin, concentration, loss limit).
   */
  configure_risk_alert(alert_type: "drawdown" | "var_breach" | "margin" | "concentration" | "loss_limit", threshold: number, action?: string, channels?: string[]): Promise<CallResult>;

  /**
   * Evaluate all active risk alert rules against current portfolio state.
   */
  check_risk_alerts(portfolio: Record<string, unknown>): Promise<CallResult>;

  /**
   * Analyze portfolio concentration (HHI index, top holdings weight, diversification assessment).
   */
  get_concentration_risk(portfolio: Record<string, unknown>): Promise<CallResult>;

  /**
   * Run compliance pre-trade checks (position limits, order size, daily loss, restricted list, wash
   * trade).
   */
  pre_trade_check(order: Record<string, unknown>, account: Record<string, unknown>, profile_id?: string): Promise<CallResult>;

  /**
   * Run post-trade surveillance for layering, spoofing, and momentum ignition patterns.
   */
  post_trade_surveillance(trades: string[]): Promise<CallResult>;

  /**
   * Retrieve tamper-proof blockchain-style audit trail with chain integrity verification.
   */
  get_audit_trail(limit?: number, action_filter?: string): Promise<CallResult>;

  /**
   * Activate trading kill switch — immediately halts all order submission.
   */
  activate_kill_switch(reason: string): Promise<CallResult>;

  /**
   * Deactivate trading kill switch and resume normal operations.
   */
  deactivate_kill_switch(reason: string): Promise<CallResult>;

  /**
   * Set or update a compliance profile with custom trading limits.
   */
  set_compliance_profile(profile_id: string, limits: Record<string, unknown>): Promise<CallResult>;

  /**
   * Retrieve a compliance profile's current limits and settings.
   */
  get_compliance_profile(profile_id: string): Promise<CallResult>;

  /**
   * Generate best execution analysis — slippage, venue quality, fill assessment.
   */
  best_execution_report(trades: string[]): Promise<CallResult>;

  /**
   * List potential wash trade violations detected across recent trades.
   */
  get_wash_trade_alerts(days?: number): Promise<CallResult>;

  /**
   * Update restricted securities, sectors, or countries for a compliance profile.
   */
  set_restricted_list(profile_id: string, symbols?: string[], sectors?: string[], countries?: string[]): Promise<CallResult>;

  /**
   * Trigger on-demand post-trade surveillance scan for layering, spoofing, wash trades.
   */
  run_surveillance_scan(lookback_hours?: number): Promise<CallResult>;

  /**
   * Current compliance state: daily P&L vs limits, violations, kill switch status.
   */
  get_compliance_status(account: Record<string, unknown>, profile_id?: string): Promise<CallResult>;

  /**
   * Create a new white-label tenant with tier, branding, and API key.
   */
  create_tenant(name: string, admin_email: string, tier?: "starter" | "growth" | "professional" | "enterprise", branding?: Record<string, unknown>): Promise<CallResult>;

  /**
   * Retrieve tenant details including sub-account count and configuration.
   */
  get_tenant(tenant_id: string): Promise<CallResult>;

  /**
   * Update tenant name, branding, tier, or status.
   */
  update_tenant(tenant_id: string, updates: Record<string, unknown>): Promise<CallResult>;

  /**
   * Create a sub-account under a tenant with role-based permissions.
   */
  create_sub_account(tenant_id: string, user_id: string, name: string, permissions?: string[]): Promise<CallResult>;

  /**
   * List all sub-accounts for a tenant.
   */
  list_sub_accounts(tenant_id: string): Promise<CallResult>;

  /**
   * Configure broker routing rules for a tenant (which broker handles which asset class).
   */
  configure_broker_routing(tenant_id: string, broker_config: Record<string, unknown>): Promise<CallResult>;

  /**
   * Get billing summary for a tenant (tier, usage, estimated monthly cost).
   */
  get_billing_summary(tenant_id: string): Promise<CallResult>;

  /**
   * Aggregate metrics for a tenant: AUM, active accounts, daily P&L, usage stats.
   */
  get_tenant_dashboard(tenant_id: string): Promise<CallResult>;

  /**
   * Detailed status of a sub-account: positions, P&L, compliance state, recent trades.
   */
  get_sub_account_status(tenant_id: string, sub_account_id: string): Promise<CallResult>;

  /**
   * Update sub-account permissions: trade limits, asset classes, marketplace access.
   */
  set_sub_account_permissions(tenant_id: string, sub_account_id: string, permissions: Record<string, unknown>): Promise<CallResult>;

  /**
   * Create a named feature set with indicator definitions for ML model training.
   */
  create_feature_set(name: string, features: string[], target?: string): Promise<CallResult>;

  /**
   * Compute feature values for a symbol over a date range using a saved feature set.
   */
  compute_features(feature_set_id: string, symbol: string, start_date?: string, end_date?: string): Promise<CallResult>;

  /**
   * List all saved feature sets with metadata.
   */
  list_feature_sets(): Promise<CallResult>;

  /**
   * Get feature importance rankings for a trained model's feature set.
   */
  get_feature_importance(feature_set_id: string, model_id?: string): Promise<CallResult>;

  /**
   * Train an ML model (XGBoost, LSTM, transformer) on a feature set with train/test split.
   */
  train_model(feature_set_id: string, model_type: "xgboost" | "lstm" | "transformer" | "random_forest" | "lightgbm", hyperparameters?: Record<string, unknown>, train_split?: number): Promise<CallResult>;

  /**
   * Evaluate a trained model on held-out test data with comprehensive metrics.
   */
  evaluate_model(model_id: string, test_data_id?: string): Promise<CallResult>;

  /**
   * Run inference on a trained model for a symbol to get signal predictions.
   */
  predict(model_id: string, symbol: string, features?: Record<string, unknown>): Promise<CallResult>;

  /**
   * Get SHAP-based explanation for a model prediction.
   */
  explain_prediction(model_id: string, prediction_id: string): Promise<CallResult>;

  /**
   * Register a trained model in the model registry with version and metadata.
   */
  register_model(model_id: string, name: string, version?: string, metrics?: Record<string, unknown>, tags?: string[]): Promise<CallResult>;

  /**
   * Promote a model to a target stage (staging, production, archived).
   */
  promote_model(registry_id: string, stage: "staging" | "production" | "archived"): Promise<CallResult>;

  /**
   * List all models in the registry with optional stage filter.
   */
  list_models(stage?: string, name_filter?: string): Promise<CallResult>;

  /**
   * Compare two or more models side-by-side on key metrics.
   */
  compare_models(model_ids: string[]): Promise<CallResult>;

  /**
   * Archive a model, removing it from active use.
   */
  archive_model(registry_id: string, reason?: string): Promise<CallResult>;

  /**
   * Create a reinforcement learning trading agent with environment and reward config.
   */
  create_rl_agent(name: string, algorithm: "ppo" | "dqn" | "a2c" | "sac", environment?: Record<string, unknown>, reward_config?: Record<string, unknown>): Promise<CallResult>;

  /**
   * Train an RL agent on historical or simulated market data.
   */
  train_rl_agent(agent_id: string, episodes?: number, symbol?: string): Promise<CallResult>;

  /**
   * Evaluate RL agent performance with episode statistics.
   */
  evaluate_rl_agent(agent_id: string, episodes?: number): Promise<CallResult>;

  /**
   * Get current state and policy of an RL agent.
   */
  get_rl_agent_state(agent_id: string): Promise<CallResult>;

  /**
   * Route a compute task to Mac M3 Max or Desktop RTX GPU.
   */
  dispatch_gpu_task(task_type: "training" | "inference" | "optimization" | "backtest", payload: Record<string, unknown>, prefer_gpu?: "mac_m3" | "desktop_rtx" | "auto"): Promise<CallResult>;

  /**
   * Get status of all available GPU compute nodes.
   */
  gpu_status(): Promise<CallResult>;

  /**
   * Use LLM to generate a complete strategy specification from natural language.
   */
  generate_strategy_spec(description: string, asset_class?: string, risk_tolerance?: "conservative" | "moderate" | "aggressive"): Promise<CallResult>;

  /**
   * Validate an order against institutional compliance rules and limits.
   */
  validate_institutional_order(order: Record<string, unknown>, account_id?: string): Promise<CallResult>;

  /**
   * Submit an institutional order with full audit trail and compliance checks.
   */
  submit_institutional_order(order: Record<string, unknown>, account_id?: string, compliance_override?: boolean): Promise<CallResult>;

  /**
   * Get detailed status of an institutional order including fill reports.
   */
  get_order_status(order_id: string): Promise<CallResult>;

  /**
   * Smart-route an order across venues for best execution.
   */
  route_order(order: Record<string, unknown>, routing_strategy?: "best_price" | "lowest_latency" | "dark_pool_first" | "split", max_venues?: number): Promise<CallResult>;

  /**
   * Get execution analytics per venue: fill rates, latency, slippage.
   */
  get_venue_analytics(venue_id?: string, lookback_days?: number): Promise<CallResult>;

  /**
   * Start an algorithmic execution strategy (TWAP, VWAP, iceberg, sniper).
   */
  start_algo_execution(algo_type: "twap" | "vwap" | "iceberg" | "sniper" | "pov", order: Record<string, unknown>, parameters?: Record<string, unknown>): Promise<CallResult>;

  /**
   * Stop a running algo execution and report fills.
   */
  stop_algo_execution(execution_id: string): Promise<CallResult>;

  /**
   * Get real-time status of an algo execution (progress, fills, slippage).
   */
  get_algo_execution_status(execution_id: string): Promise<CallResult>;

  /**
   * Establish a FIX protocol session to an execution venue.
   */
  connect_fix_session(venue: string, sender_comp_id: string, target_comp_id: string, config?: Record<string, unknown>): Promise<CallResult>;

  /**
   * Gracefully disconnect a FIX session.
   */
  disconnect_fix_session(session_id: string): Promise<CallResult>;

  /**
   * Get FIX session health: heartbeat, sequence numbers, message counts.
   */
  get_fix_session_status(session_id: string): Promise<CallResult>;

  /**
   * Run transaction cost analysis on completed trades.
   */
  run_tca(trades: string[], benchmark?: "vwap" | "twap" | "arrival_price" | "close"): Promise<CallResult>;

  /**
   * Get a comprehensive TCA report for a time period.
   */
  get_tca_report(start_date: string, end_date: string, account_id?: string): Promise<CallResult>;

  /**
   * Calculate implementation shortfall for a set of orders.
   */
  get_implementation_shortfall(orders: string[]): Promise<CallResult>;

  /**
   * Register a new execution venue in the venue registry.
   */
  register_venue(name: string, venue_type: "exchange" | "dark_pool" | "ats" | "otc", config?: Record<string, unknown>): Promise<CallResult>;

  /**
   * List all registered execution venues with health status.
   */
  list_venues(): Promise<CallResult>;

  /**
   * Get detailed status of a specific venue.
   */
  get_venue_status(venue_id: string): Promise<CallResult>;

  /**
   * Set routing priority for a venue.
   */
  set_venue_priority(venue_id: string, priority: number): Promise<CallResult>;

  /**
   * Start real-time P&L streaming for an account or portfolio.
   */
  start_pnl_stream(account_id: string, symbols?: string[]): Promise<CallResult>;

  /**
   * Get current P&L snapshot across all tracked positions.
   */
  get_pnl_snapshot(account_id: string): Promise<CallResult>;

  /**
   * Get historical P&L time series for charting.
   */
  get_pnl_history(account_id: string, interval?: "1m" | "5m" | "1h" | "1d", lookback?: string): Promise<CallResult>;

  /**
   * Analyze order flow for a symbol: buy/sell pressure, large trades, imbalances.
   */
  analyze_order_flow(symbol: string, lookback_minutes?: number): Promise<CallResult>;

  /**
   * Get order flow heatmap data for price levels.
   */
  get_order_flow_heatmap(symbol: string, levels?: number): Promise<CallResult>;

  /**
   * Get volume profile analysis for a symbol.
   */
  get_volume_profile(symbol: string, lookback_days?: number): Promise<CallResult>;

  /**
   * Analyze market microstructure: bid-ask spread, depth, tick patterns.
   */
  analyze_microstructure(symbol: string): Promise<CallResult>;

  /**
   * Get order flow toxicity score (VPIN) for a symbol.
   */
  get_toxicity_score(symbol: string, window?: number): Promise<CallResult>;

  /**
   * Detect current market regime using statistical methods.
   */
  detect_regime(symbol: string, method?: "hmm" | "threshold" | "ml"): Promise<CallResult>;

  /**
   * Get historical regime classifications for a symbol.
   */
  get_regime_history(symbol: string, lookback_days?: number): Promise<CallResult>;

  /**
   * Get regime transition probability matrix.
   */
  get_regime_transition_matrix(symbol: string): Promise<CallResult>;

  /**
   * Create a real-time alert with conditions and actions.
   */
  create_alert(name: string, condition: Record<string, unknown>, actions?: string[], channels?: string[]): Promise<CallResult>;

  /**
   * List all configured alerts with their status.
   */
  list_alerts(active_only?: boolean): Promise<CallResult>;

  /**
   * Delete an alert by ID.
   */
  delete_alert(alert_id: string): Promise<CallResult>;

  /**
   * Get alert trigger history.
   */
  get_alert_history(alert_id?: string, limit?: number): Promise<CallResult>;

  /**
   * Run NLP sentiment analysis on text or news for a symbol.
   */
  analyze_sentiment(symbol: string, source?: "news" | "twitter" | "reddit" | "earnings_call" | "custom", text?: string): Promise<CallResult>;

  /**
   * Get historical sentiment scores for a symbol.
   */
  get_sentiment_history(symbol: string, source?: string, lookback_days?: number): Promise<CallResult>;

  /**
   * Get aggregated sentiment signal (bullish/bearish/neutral) for a symbol.
   */
  get_sentiment_signal(symbol: string): Promise<CallResult>;

  /**
   * Analyze satellite imagery data for economic activity signals.
   */
  analyze_satellite(location: string, data_type: "parking_lots" | "shipping" | "agriculture" | "construction" | "nightlights", symbol?: string): Promise<CallResult>;

  /**
   * Get time series of satellite-derived metrics.
   */
  get_satellite_timeseries(location_id: string, metric: string, lookback_days?: number): Promise<CallResult>;

  /**
   * Scrape structured data from web sources for trading signals.
   */
  scrape_web_data(url: string, selectors?: Record<string, unknown>, schedule?: string): Promise<CallResult>;

  /**
   * List all configured web scrape jobs.
   */
  list_scrape_jobs(): Promise<CallResult>;

  /**
   * Get results from a web scrape job.
   */
  get_scrape_results(job_id: string, limit?: number): Promise<CallResult>;

  /**
   * Analyze an SEC filing (10-K, 10-Q, 8-K) for trading signals.
   */
  analyze_sec_filing(symbol: string, filing_type: "10-K" | "10-Q" | "8-K" | "13-F" | "S-1", filing_url?: string): Promise<CallResult>;

  /**
   * Get recent insider trading activity for a symbol.
   */
  get_insider_trades(symbol: string, days?: number): Promise<CallResult>;

  /**
   * Get institutional holdings changes (13-F) for a symbol.
   */
  get_institutional_holdings(symbol: string, quarter?: string): Promise<CallResult>;

  /**
   * Analyze social media signals (Twitter, Reddit, StockTwits) for a symbol.
   */
  analyze_social_media(symbol: string, platform?: "twitter" | "reddit" | "stocktwits" | "all", lookback_hours?: number): Promise<CallResult>;

  /**
   * Get social momentum score for a symbol (trending vs fading).
   */
  get_social_momentum(symbol: string): Promise<CallResult>;

  /**
   * Get real-time social sentiment feed for monitored symbols.
   */
  get_social_sentiment_feed(symbols?: string[], limit?: number): Promise<CallResult>;

  /**
   * Browse available alternative datasets in the marketplace.
   */
  browse_alt_datasets(category?: string, min_quality?: number): Promise<CallResult>;

  /**
   * Subscribe to an alternative dataset for signal generation.
   */
  subscribe_alt_dataset(dataset_id: string, config?: Record<string, unknown>): Promise<CallResult>;

  /**
   * Get a sample of data from an alternative dataset.
   */
  get_alt_dataset_sample(dataset_id: string, limit?: number): Promise<CallResult>;

  /**
   * Get quality metrics for an alternative dataset.
   */
  get_alt_data_quality(dataset_id: string): Promise<CallResult>;

  /**
   * Spawn a new autonomous trading agent with a specific role and strategy.
   */
  spawn_agent(name: string, role: "researcher" | "trader" | "risk_manager" | "analyst" | "executor", strategy?: Record<string, unknown>, capital_allocation?: number): Promise<CallResult>;

  /**
   * List all active agents in the swarm with their status.
   */
  list_agents(role_filter?: string): Promise<CallResult>;

  /**
   * Get detailed info about a specific agent: state, P&L, decisions.
   */
  get_agent_detail(agent_id: string): Promise<CallResult>;

  /**
   * Terminate an agent and close its positions.
   */
  terminate_agent(agent_id: string, reason?: string): Promise<CallResult>;

  /**
   * Create a task plan that decomposes a trading goal into agent subtasks.
   */
  create_task_plan(goal: string, constraints?: Record<string, unknown>, deadline?: string): Promise<CallResult>;

  /**
   * Get a task plan and its execution status.
   */
  get_task_plan(plan_id: string): Promise<CallResult>;

  /**
   * Store a memory/observation in shared agent memory.
   */
  store_agent_memory(agent_id: string, memory_type: "observation" | "decision" | "outcome" | "insight", content: Record<string, unknown>): Promise<CallResult>;

  /**
   * Query shared agent memory for relevant past observations.
   */
  query_agent_memory(query: string, memory_type?: string, agent_id?: string, limit?: number): Promise<CallResult>;

  /**
   * Get agent memory usage statistics.
   */
  get_memory_stats(agent_id?: string): Promise<CallResult>;

  /**
   * Route a tool call from an agent to the appropriate MCP tool.
   */
  route_tool_call(agent_id: string, tool_name: string, arguments: Record<string, unknown>): Promise<CallResult>;

  /**
   * Get tool access permissions for an agent.
   */
  get_tool_permissions(agent_id: string): Promise<CallResult>;

  /**
   * Request multi-agent consensus on a trading decision.
   */
  request_consensus(proposal: Record<string, unknown>, agent_ids: string[], method?: "majority" | "weighted" | "unanimous"): Promise<CallResult>;

  /**
   * Get the result of a consensus request.
   */
  get_consensus_result(consensus_id: string): Promise<CallResult>;

  /**
   * Get history of consensus decisions.
   */
  get_consensus_history(limit?: number): Promise<CallResult>;

  /**
   * Get health metrics for an agent: uptime, error rate, latency.
   */
  get_agent_health(agent_id: string): Promise<CallResult>;

  /**
   * Get aggregate swarm dashboard: active agents, total P&L, task status.
   */
  get_swarm_dashboard(): Promise<CallResult>;

  /**
   * Get detailed performance metrics for an agent over time.
   */
  get_agent_performance(agent_id: string, lookback_days?: number): Promise<CallResult>;

  /**
   * Update an agent's strategy parameters at runtime.
   */
  set_agent_parameters(agent_id: string, parameters: Record<string, unknown>): Promise<CallResult>;

  /**
   * Get best swap quote across decentralized exchanges.
   */
  get_dex_quote(token_in: string, token_out: string, amount: string, chain?: "ethereum" | "polygon" | "arbitrum" | "optimism" | "base" | "solana"): Promise<CallResult>;

  /**
   * Execute a token swap on the best DEX route.
   */
  execute_swap(quote_id: string, slippage_tolerance?: number, deadline_minutes?: number): Promise<CallResult>;

  /**
   * Get liquidity depth across DEXes for a token pair.
   */
  get_dex_liquidity(token_in: string, token_out: string, chain?: string): Promise<CallResult>;

  /**
   * Scan DeFi protocols for yield farming opportunities.
   */
  scan_yield_opportunities(min_apy?: number, max_risk_score?: number, chains?: string[]): Promise<CallResult>;

  /**
   * Deploy capital to a yield farming strategy.
   */
  deploy_yield_strategy(opportunity_id: string, amount: string, auto_compound?: boolean): Promise<CallResult>;

  /**
   * Get all active yield farming positions.
   */
  get_yield_positions(): Promise<CallResult>;

  /**
   * Withdraw from a yield farming position.
   */
  withdraw_yield(position_id: string, amount?: string): Promise<CallResult>;

  /**
   * Bridge tokens across chains via cross-chain bridge.
   */
  bridge_tokens(token: string, amount: string, from_chain: string, to_chain: string, bridge_protocol?: string): Promise<CallResult>;

  /**
   * Get status of a cross-chain bridge transfer.
   */
  get_bridge_status(transfer_id: string): Promise<CallResult>;

  /**
   * List available bridge routes between chains for a token.
   */
  list_bridge_routes(token: string, from_chain: string, to_chain: string): Promise<CallResult>;

  /**
   * Check MEV risk for a pending transaction.
   */
  check_mev_risk(transaction: Record<string, unknown>, chain?: string): Promise<CallResult>;

  /**
   * Submit a transaction with MEV protection (Flashbots/private mempool).
   */
  submit_protected_tx(transaction: Record<string, unknown>, protection_type?: "flashbots" | "private_mempool" | "backrun_protection"): Promise<CallResult>;

  /**
   * Get MEV analytics: sandwich attacks, front-running stats for monitored wallets.
   */
  get_mev_analytics(wallet?: string, lookback_days?: number): Promise<CallResult>;

  /**
   * Get active governance proposals for a DAO/protocol.
   */
  get_governance_proposals(protocol: string, status?: "active" | "passed" | "rejected" | "all"): Promise<CallResult>;

  /**
   * Cast a vote on a DAO governance proposal.
   */
  vote_on_proposal(proposal_id: string, vote: "for" | "against" | "abstain", reason?: string): Promise<CallResult>;

  /**
   * Get voting power and delegation status for a wallet.
   */
  get_governance_power(protocol: string, wallet?: string): Promise<CallResult>;

  /**
   * Assess risk of a DeFi protocol: smart contract, liquidity, governance.
   */
  assess_defi_risk(protocol: string, chain?: string): Promise<CallResult>;

  /**
   * Get aggregate risk assessment for all DeFi positions.
   */
  get_defi_portfolio_risk(): Promise<CallResult>;

  /**
   * Monitor liquidation risk for lending/borrowing positions.
   */
  monitor_liquidation_risk(position_id: string): Promise<CallResult>;

  /**
   * Get DeFi insurance options for protocol risk coverage.
   */
  get_defi_insurance_options(protocol: string, coverage_amount?: string): Promise<CallResult>;

  /**
   * Create a new SaaS tenant with subscription plan and configuration.
   */
  create_saas_tenant(company_name: string, admin_email: string, plan?: "free" | "starter" | "professional" | "enterprise", config?: Record<string, unknown>): Promise<CallResult>;

  /**
   * Get SaaS tenant details, usage, and subscription status.
   */
  get_saas_tenant(tenant_id: string): Promise<CallResult>;

  /**
   * Update SaaS tenant settings, plan, or configuration.
   */
  update_saas_tenant(tenant_id: string, updates: Record<string, unknown>): Promise<CallResult>;

  /**
   * Get detailed usage metrics for billing (API calls, compute, storage).
   */
  get_usage_metrics(tenant_id: string, period?: string): Promise<CallResult>;

  /**
   * Get invoice details for a billing period.
   */
  get_invoice(tenant_id: string, invoice_id?: string): Promise<CallResult>;

  /**
   * List all invoices for a tenant.
   */
  list_invoices(tenant_id: string, status?: "paid" | "pending" | "overdue" | "all"): Promise<CallResult>;

  /**
   * Update payment method for a tenant.
   */
  update_payment_method(tenant_id: string, payment_method: Record<string, unknown>): Promise<CallResult>;

  /**
   * Publish a validated strategy to the SaaS marketplace.
   */
  publish_strategy_to_marketplace(strategy_id: string, pricing: Record<string, unknown>, description?: string, tags?: string[]): Promise<CallResult>;

  /**
   * Browse the SaaS strategy marketplace with filters.
   */
  browse_strategy_marketplace(category?: string, min_sharpe?: number, max_price?: number, sort_by?: "sharpe" | "subscribers" | "newest" | "price"): Promise<CallResult>;

  /**
   * Subscribe a tenant to a marketplace strategy.
   */
  subscribe_to_strategy(tenant_id: string, strategy_id: string, allocation?: number): Promise<CallResult>;

  /**
   * Configure white-label branding for a tenant.
   */
  configure_white_label(tenant_id: string, branding: Record<string, unknown>): Promise<CallResult>;

  /**
   * Get current white-label configuration for a tenant.
   */
  get_white_label_config(tenant_id: string): Promise<CallResult>;

  /**
   * Generate an API key for tenant programmatic access.
   */
  generate_api_key(tenant_id: string, name: string, permissions?: string[], rate_limit?: number): Promise<CallResult>;

  /**
   * List all API keys for a tenant.
   */
  list_api_keys(tenant_id: string): Promise<CallResult>;

  /**
   * Revoke an API key.
   */
  revoke_api_key(key_id: string): Promise<CallResult>;

  /**
   * Get API usage statistics and rate limit status.
   */
  get_api_usage(tenant_id: string, key_id?: string): Promise<CallResult>;

  /**
   * Get overall SaaS platform health: uptime, latency, error rates.
   */
  get_platform_health(): Promise<CallResult>;

  /**
   * BM25 search over all Massive market data API endpoints. Use this FIRST to find the right endpoint
   * for stocks, options, futures, forex, crypto, or SEC filings.
   *
   * @param query Natural language query (e.g. 'stock price aggregates', 'options chain', 'forex rates')
   * @param scope? Search scope: endpoints for API, functions for built-in Greeks/returns/technicals
   */
  massive_search_endpoints(query: string, top_k?: number, scope?: "all" | "endpoints" | "functions"): Promise<CallResult>;

  /**
   * Get parameter documentation for a Massive API endpoint. Pass the docs_url from
   * massive_search_endpoints results.
   *
   * @param docs_url The docs URL from search results
   */
  massive_get_endpoint_docs(docs_url: string): Promise<CallResult>;

  /**
   * Execute a Massive market data API call. Optionally store results as an in-memory DataFrame for SQL
   * querying. Supports pagination auto-detection — check _next_page in results.
   *
   * @param path API path (e.g. /v2/aggs/ticker/AAPL/range/1/day/2024-01-01/2024-12-31)
   * @param params? Query parameters
   * @param store_as? Table name to store as DataFrame (e.g. aapl_daily)
   * @param apply? Post-processing functions: sma, ema, sharpe_ratio, bs_delta, etc.
   * @param api_key? Override API key for this request (white-label customer isolation)
   * @param llm_model? LLM model name for usage analytics
   * @param llm_provider? LLM provider name for usage analytics
   */
  massive_call_api(path: string, method?: string, params?: Record<string, unknown>, store_as?: string, apply?: string[]): Promise<CallResult>;
  // optional (3): api_key, llm_model, llm_provider

  /**
   * SQL queries over stored DataFrames from massive_call_api. Supports SHOW TABLES, DESCRIBE <table>,
   * DROP TABLE <table>, and full SQL with JOIN/GROUP BY/window functions. Use apply for server-side
   * Greeks and technicals.
   *
   * @param sql SQL query or special command
   * @param apply? Post-processing functions to apply to query results
   */
  massive_query_data(sql: string, apply?: string[]): Promise<CallResult>;

  /**
   * Composable pipeline: search→fetch→store→query→apply in 1 call (saves 4 round-trips). Describe what
   * data you want, optionally filter with SQL and apply Greeks/technicals.
   *
   * @param search_query Natural language query to find the right API endpoint
   * @param path_override? Skip search — use this API path directly
   * @param params? Query parameters for the API call
   * @param store_as? Table name (auto-generated if omitted)
   * @param sql? SQL to run after storing. Use {table} as placeholder for the table name
   * @param apply? Post-processing: [{"function": "sharpe_ratio", "inputs": {"column": "close", "window":
   *               252}, "output": "sharpe"}]
   */
  massive_run_pipeline(search_query: string, path_override?: string, params?: Record<string, unknown>, store_as?: string, sql?: string): Promise<CallResult>;
  // optional (1): apply

  /**
   * Search for relevant AlgoChains tools using natural language. Returns the top-K most relevant tools
   * with descriptions. Use this FIRST to find which tools are available for your task — 90%+ context
   * reduction vs listing all 150+ tools.
   *
   * @param query Natural language description of what you want to do
   * @param category? Filter: trading, market_data, strategy, ml, analytics, alt_data, defi, cloud
   */
  discover_tools(query: string, top_k?: number, category?: string): Promise<CallResult>;

  /**
   * Get full details for a specific tool including its input schema, parameter types, and usage
   * examples. Call after discover_tools to get the full spec before execution.
   *
   * @param tool_name Exact tool name from discover_tools results
   */
  get_tool_details(tool_name: string): Promise<CallResult>;

  /**
   * Execute any discovered tool by name with arguments. Use discover_tools first, then get_tool_details
   * for the schema, then call this to execute.
   *
   * @param tool_name Tool name to execute
   * @param arguments Arguments matching the tool's inputSchema
   */
  execute_dynamic_tool(tool_name: string, arguments: Record<string, unknown>): Promise<CallResult>;

  /**
   * Transform a natural language trading intent into a concrete plan and execute it. Example: 'Get me
   * $10K AI exposure, max 2% per stock'. Parses intent → solves constraints → presents plan for approval
   * → executes.
   *
   * @param intent Natural language trading intent
   * @param dry_run? If true, return the plan without executing (default: true for safety)
   */
  execute_intent(intent: string, dry_run?: boolean): Promise<CallResult>;

  /**
   * Get details of a previously generated intent plan by ID. Shows all steps, status, estimated cost,
   * and risk impact.
   *
   * @param plan_id Plan ID from execute_intent
   */
  get_intent_plan(plan_id: string): Promise<CallResult>;

  /**
   * Approve a pending intent plan for execution. The plan must be in 'pending_approval' status.
   *
   * @param plan_id Plan ID to approve and execute
   */
  approve_intent(plan_id: string): Promise<CallResult>;

  /**
   * Get history of executed intent plans with outcomes and lessons learned.
   */
  get_intent_history(limit?: number): Promise<CallResult>;

  /**
   * Create a shadow (paper) portfolio to forward-test a strategy without risking capital. Track P&L,
   * fills, and metrics alongside your real portfolio.
   *
   * @param name Portfolio name (e.g. 'AI Momentum Test')
   * @param strategy_id? Optional strategy ID to track
   * @param capital? Starting capital
   */
  create_shadow_portfolio(name: string, strategy_id?: string, broker?: string, capital?: number): Promise<CallResult>;

  /**
   * Get shadow portfolio results and optionally compare against live performance. Shows P&L, win rate,
   * Sharpe estimate, and promotion recommendation.
   *
   * @param shadow_id Shadow portfolio ID
   * @param compare_live? Compare against live portfolio metrics
   */
  get_shadow_results(shadow_id: string, compare_live?: boolean): Promise<CallResult>;

  /**
   * Genetic evolution of trading strategies. Initialize a population, evaluate fitness via backtest,
   * then evolve to breed better strategies. Returns top genomes ranked by fitness (Sharpe-weighted).
   *
   * @param action Evolution action
   * @param seeds? Seed strategies with known-good parameters (for initialize)
   * @param genome_id? Genome ID (for evaluate)
   * @param metrics? Backtest metrics: sharpe, max_drawdown, win_rate, trade_count (for evaluate)
   */
  evolve_strategies(action: "initialize" | "evaluate" | "evolve" | "get_top" | "get_unevaluated", strategy_type?: "momentum" | "mean_reversion" | "breakout" | "scalper", seeds?: string[], genome_id?: string, metrics?: Record<string, unknown>): Promise<CallResult>;
  // optional (1): n

  /**
   * Scan for cross-broker arbitrage opportunities. Compares prices across brokers, computes spread in
   * bps, subtracts fees and slippage, and flags profitable opportunities.
   *
   * @param symbols Symbols to scan
   * @param brokers? Brokers to compare (default: alpaca, ibkr, tradovate)
   * @param quotes? Pre-fetched quotes as {broker: {symbol: price}} — skips live fetch
   */
  detect_arbitrage(symbols: string[], brokers?: string[], quotes?: Record<string, unknown>): Promise<CallResult>;

  /**
   * Detect current market regime from VIX, SPY trend, breadth, and credit signals. Returns regime
   * classification (bull/bear/range/volatile/crisis), recommended strategies, and risk multiplier for
   * position sizing.
   */
  detect_market_regime(vix?: number, spy_price?: number, spy_sma_20?: number, spy_sma_50?: number, spy_sma_200?: number): Promise<CallResult>;
  // optional (3): advance_decline_ratio, put_call_ratio, credit_spread_bps

  /**
   * Predict what data an LLM will need based on user message intent and prefetch it in parallel. Reduces
   * average tool calls from 6.2 to 1.8. Returns pre-loaded context dict.
   *
   * @param user_message The user's message to analyze for data needs
   */
  prefetch_context(user_message: string): Promise<CallResult>;
}

