# V9 Blueprint — Risk Dashboard, Compliance Module & Multi-Tenant White-Label

> **Status:** Planned · **Target:** Q4 2026 / Q1 2027 · **Owner:** AlgoChains Core Team

---

## Executive Summary

V9 elevates AlgoChains from a developer tool into an **institutional-grade platform**. Three components close the gap between retail algo-trading infrastructure and what hedge funds, RIAs, and fintech companies require before deploying capital at scale:

1. **Risk Dashboard** — Real-time portfolio risk analytics (VaR, stress testing, factor exposure, drawdown monitoring) accessible via MCP tools and a web interface.
2. **Compliance Module** — Automated pre-trade and post-trade regulatory checks (SEC/FINRA, MiFID II), wash trade detection, position limits, and audit trail generation.
3. **Multi-Tenant White-Label** — Tenant-isolated infrastructure that lets fintech companies offer AlgoChains-powered trading under their own brand, with sub-account management, API key scoping, and custom broker routing.

### What ships in V9

| Component | Description | New MCP Tools |
|---|---|---|
| **Risk Dashboard** | Real-time risk analytics, stress testing, factor decomposition, margin monitoring | 10 |
| **Compliance Module** | Pre-trade checks, post-trade surveillance, wash trade detection, audit trails | 8 |
| **Multi-Tenant White-Label** | Tenant isolation, sub-account management, branded experiences, usage billing | 7 |

**Total new tools: 25** (bringing the server from 73 → 98 tools across V7–V9)

---

## Part 1 — Risk Dashboard

### Why This Matters

Institutional allocators won't touch a platform that can't answer: "What's my VaR at the 99th percentile?" and "How does my portfolio behave if the S&P drops 15% tomorrow?" Bloomberg PORT, Axioma Risk (SimCorp), MSCI RiskMetrics, and Venn by Two Sigma set the standard. AlgoChains needs a risk layer that speaks the same language — but is AI-agent-accessible via MCP.

### Risk Metrics Engine

The risk engine computes the following metrics in real-time (updated every 60 seconds during market hours):

#### Value at Risk (VaR)

Three computation methods, each serving a different use case:

```text
┌─────────────────────────────────────────────────────────────┐
│  PARAMETRIC VaR (fastest, assumes normal returns)           │
│                                                             │
│  VaR_α = μ - z_α × σ                                       │
│                                                             │
│  Where:                                                     │
│    μ = portfolio mean return (rolling 252-day)              │
│    σ = portfolio standard deviation                         │
│    z_α = z-score for confidence level (1.645 for 95%,      │
│           2.326 for 99%)                                    │
│                                                             │
│  Portfolio σ = √(w' Σ w)                                    │
│    w = position weight vector                               │
│    Σ = asset covariance matrix (exponentially weighted,     │
│        λ = 0.94, RiskMetrics standard)                      │
├─────────────────────────────────────────────────────────────┤
│  HISTORICAL VaR (non-parametric, uses actual return dist)   │
│                                                             │
│  Sort 252 daily portfolio returns ascending                 │
│  VaR_95 = return at index ⌊252 × 0.05⌋ = 12th worst day   │
│  VaR_99 = return at index ⌊252 × 0.01⌋ = 2nd worst day    │
│                                                             │
│  Advantages: captures fat tails, skewness, no dist. assume │
├─────────────────────────────────────────────────────────────┤
│  MONTE CARLO VaR (most flexible, scenario-based)            │
│                                                             │
│  1. Fit multivariate distribution to return history         │
│  2. Generate 10,000 simulated return paths (1-day horizon)  │
│  3. Revalue portfolio under each scenario                   │
│  4. VaR = percentile of simulated P&L distribution          │
│                                                             │
│  Supports: correlated jumps, regime-switching,              │
│            non-normal marginals (Student-t, Clayton copula) │
└─────────────────────────────────────────────────────────────┘
```

#### Expected Shortfall (CVaR)

The average loss beyond VaR — answers "when things go wrong, *how wrong*?"

```text
ES_α = E[Loss | Loss > VaR_α]
     = (1 / (1-α)) × ∫_{α}^{1} VaR_u du

For Historical: average of all returns worse than VaR threshold
For Monte Carlo: average of simulated losses beyond VaR
```

#### Factor Exposure Analysis

Decompose portfolio risk into systematic factors (modeled after Barra/Axioma):

```text
Portfolio Return = Σ (β_i × Factor_i) + α + ε

Factors:
├── Market (SPY beta)
├── Size (SMB — small minus big)
├── Value (HML — high minus low book/market)
├── Momentum (UMD — up minus down)
├── Volatility (low vol minus high vol)
├── Quality (profitable minus unprofitable)
├── Sector exposures (11 GICS sectors)
└── Currency exposure (for multi-currency portfolios)

Output:
┌────────────────┬──────────┬───────────────┬──────────────┐
│ Factor         │ Exposure │ Contribution  │ % of Risk    │
├────────────────┼──────────┼───────────────┼──────────────┤
│ Market         │ 1.15     │ $12,450       │ 42.3%        │
│ Momentum       │ 0.38     │ $5,200        │ 17.7%        │
│ Technology     │ 0.62     │ $4,800        │ 16.3%        │
│ Volatility     │ -0.21    │ -$2,100       │ 7.1%         │
│ Idiosyncratic  │ —        │ $4,880        │ 16.6%        │
└────────────────┴──────────┴───────────────┴──────────────┘
```

#### Stress Testing

Pre-built and custom scenario analysis:

| Scenario | Description | Method |
|---|---|---|
| **2008 Financial Crisis** | Apply Sep-Nov 2008 factor returns to current portfolio | Historical replay |
| **COVID Crash** | Apply Feb-Mar 2020 returns | Historical replay |
| **Rate Shock +200bp** | Instantaneous 200bp rate increase; duration-weighted bond impact | Parametric |
| **Tech Selloff -30%** | Technology sector drops 30%, correlations spike to 0.8 | Factor shock |
| **Flash Crash** | All positions gap down 5% in 15 minutes, liquidity evaporates | Monte Carlo |
| **Stagflation** | CPI +8%, GDP -2%, rates +300bp, commodities +40% | Multi-factor |
| **Custom** | User-defined factor shocks, correlation overrides, time horizons | Configurable |

```text
Stress Test Output:
┌─────────────────────┬────────────┬──────────────┬─────────────┐
│ Scenario            │ Portfolio  │ Worst        │ Recovery    │
│                     │ Impact     │ Position     │ (est. days) │
├─────────────────────┼────────────┼──────────────┼─────────────┤
│ 2008 Crisis         │ -18.4%     │ NVDA: -34%   │ 145         │
│ COVID Crash         │ -22.1%     │ TSLA: -41%   │ 89          │
│ Rate Shock +200bp   │ -6.2%      │ TLT: -14%    │ 30          │
│ Tech Selloff -30%   │ -19.8%     │ AMD: -35%    │ 120         │
└─────────────────────┴────────────┴──────────────┴─────────────┘
```

#### Additional Real-Time Metrics

| Metric | Description | Frequency |
|---|---|---|
| **Drawdown Monitor** | Current, max, and average drawdown with duration tracking | Tick-level |
| **Margin Utilization** | Used margin / available margin across all brokers | Every 30s |
| **Concentration Risk** | Herfindahl index, top-5 position weight, sector concentration | Every 60s |
| **Correlation Matrix** | Rolling 60-day pairwise asset correlations with regime detection | Hourly |
| **Greeks Exposure** | Delta, gamma, theta, vega for options positions (aggregate) | Every 30s |
| **Liquidity Score** | Bid-ask spread, average daily volume, market impact estimate | Every 60s |
| **Beta Exposure** | Portfolio beta to SPY, QQQ, sector ETFs | Every 60s |
| **Sharpe (Rolling)** | 30-day, 90-day, 252-day rolling Sharpe ratio | Daily |

### New MCP Tools — Risk Dashboard

| Tool | Description |
|---|---|
| `get_portfolio_var` | Compute VaR at specified confidence level (95%, 99%) using parametric, historical, or Monte Carlo method. |
| `get_expected_shortfall` | Compute CVaR/Expected Shortfall beyond a given VaR threshold. |
| `get_factor_exposure` | Decompose portfolio risk into systematic factor exposures (Barra-style). |
| `run_stress_test` | Run a named or custom stress scenario against the current portfolio. Returns position-level and portfolio-level impact. |
| `get_concentration_risk` | Herfindahl index, top-N position weights, sector/geography concentration scores. |
| `get_correlation_matrix` | Rolling pairwise correlation matrix for portfolio assets. Detects regime shifts (correlation breakdowns). |
| `get_drawdown_report` | Current drawdown, max drawdown, drawdown duration, and underwater equity curve. |
| `get_margin_status` | Real-time margin utilization across all connected brokers. Warns when approaching margin call thresholds. |
| `get_greeks_summary` | Aggregate options Greeks (delta, gamma, theta, vega) across all options positions. |
| `set_risk_alerts` | Configure alerts: drawdown > X%, VaR breach, margin > Y%, concentration > Z%. Alerts via MCP callback, Slack, or email. |

### Implementation Architecture

```text
src/algochains_mcp/risk/
├── __init__.py
├── engine.py              # Core risk computation engine
├── var.py                 # Parametric, Historical, Monte Carlo VaR
├── expected_shortfall.py  # CVaR computation
├── factor_model.py        # Barra-style factor decomposition
├── stress_testing.py      # Scenario library + custom scenario runner
├── concentration.py       # HHI, position weight analysis
├── correlation.py         # Rolling correlation + regime detection
├── drawdown.py            # Underwater equity, peak tracking
├── margin.py              # Multi-broker margin aggregation
├── greeks.py              # Options Greeks aggregation
├── alerts.py              # Threshold-based alert system
├── cache.py               # Redis cache for real-time metric snapshots
└── scenarios/             # Pre-built stress test definitions
    ├── financial_crisis_2008.json
    ├── covid_crash_2020.json
    ├── rate_shock.json
    ├── tech_selloff.json
    ├── flash_crash.json
    └── stagflation.json
```

### Dependencies

```text
numpy >= 1.24         # Matrix operations for covariance, VaR
scipy >= 1.11         # Statistical distributions, optimization
pandas >= 2.0         # Time series manipulation
statsmodels >= 0.14   # Factor regression, econometric models
arch >= 6.0           # GARCH models for volatility forecasting
redis >= 5.0          # Real-time metric caching
```

---

## Part 2 — Compliance Module

### Regulatory Landscape

Algorithmic trading is governed by overlapping regulations that apply based on jurisdiction, asset class, and firm type:

| Regulation | Jurisdiction | Key Requirements |
|---|---|---|
| **SEC Rule 15c3-5** (Market Access Rule) | US | Pre-trade risk controls, erroneous order prevention, credit/capital thresholds |
| **FINRA Rule 3110** | US | Supervision of algorithmic strategies, written supervisory procedures |
| **FINRA Notice 15-09** | US | Software testing, code review, risk assessment, compliance oversight of algo strategies |
| **FINRA Rule 5310** | US | Best execution — reasonable diligence for most favorable terms |
| **MiFID II (RTS 6)** | EU | Algorithm registration, kill switches, maximum order-to-trade ratios |
| **MiFID II (RTS 25)** | EU | Clock synchronization (100μs for high-frequency, 1s for others) |
| **MAR** (Market Abuse Regulation) | EU | Surveillance for market manipulation, insider trading detection |
| **Reg SHO** | US | Short-selling locate requirements, close-out obligations |

### Pre-Trade Risk Controls

Every order passes through a compliance gate before reaching the broker:

```text
Order from AI Agent
        │
        ▼
┌───────────────────────────────────────────────────────────┐
│  PRE-TRADE COMPLIANCE ENGINE                              │
│                                                           │
│  Gate 1: Position Limits                                  │
│  ├── Single-name: max 10% of portfolio (configurable)     │
│  ├── Sector: max 30% (configurable)                       │
│  ├── Asset class: max 50% (configurable)                  │
│  └── Total gross exposure: max 200% (configurable)        │
│                                                           │
│  Gate 2: Order Size Limits                                │
│  ├── Max notional per order: $50K (configurable)          │
│  ├── Max % of ADV: 5% (avg daily volume)                  │
│  ├── Fat-finger check: reject if > 3σ from recent price   │
│  └── Duplicate order detection (same sym/side within 5s)  │
│                                                           │
│  Gate 3: Daily Loss Limits                                │
│  ├── Max daily loss: -$5K or -2% of AUM (configurable)    │
│  ├── Max drawdown from peak: -5% (configurable)           │
│  └── Kill switch: halt all trading if breached             │
│                                                           │
│  Gate 4: Wash Trade Prevention                            │
│  ├── Detect buy/sell of same symbol within 30-day window  │
│  ├── Substantially identical securities check             │
│  ├── Flag and block or flag and log (configurable)        │
│  └── Tax lot matching (FIFO, LIFO, specific ID)           │
│                                                           │
│  Gate 5: Restricted List Check                            │
│  ├── Firm restricted list (insider knowledge)             │
│  ├── OFAC sanctioned entity check                         │
│  └── Client-specific exclusions (ESG, sector, etc.)       │
│                                                           │
│  Gate 6: Best Execution                                   │
│  ├── Compare fill price to NBBO at time of order          │
│  ├── Track execution quality metrics (slippage, speed)    │
│  └── Generate quarterly best execution reports            │
│                                                           │
│  Result: PASS → route to broker                           │
│          SOFT_BLOCK → flag + route (post-trade review)    │
│          HARD_BLOCK → reject order + log + alert          │
└───────────────────────────────────────────────────────────┘
```

### Post-Trade Surveillance

Continuous monitoring of executed trades for regulatory violations:

```text
┌───────────────────────────────────────────────────────────┐
│  POST-TRADE SURVEILLANCE                                  │
│                                                           │
│  1. Pattern Detection                                     │
│  ├── Layering/Spoofing: large orders placed then          │
│  │   cancelled before execution (cancel rate > 90%)       │
│  ├── Quote stuffing: excessive order modifications        │
│  │   (>100 modifications/second)                          │
│  ├── Momentum ignition: aggressive orders to trigger      │
│  │   other algos, then reverse                            │
│  └── Marking the close: unusual activity in final         │
│      minutes of trading session                           │
│                                                           │
│  2. Transaction Reporting                                 │
│  ├── Trade Reporting Facility (TRF) submissions           │
│  ├── OATS (Order Audit Trail System) compliance           │
│  ├── CAT (Consolidated Audit Trail) records               │
│  └── Transaction cost analysis (TCA) reports              │
│                                                           │
│  3. Audit Trail                                           │
│  ├── Every order: timestamp, symbol, side, qty, price,    │
│  │   algo_id, decision_reason, compliance_result          │
│  ├── Immutable log (append-only, cryptographic chaining)  │
│  ├── Retention: 6 years (SEC/FINRA requirement)           │
│  └── Export: CSV, JSON, OATS format                       │
└───────────────────────────────────────────────────────────┘
```

### Compliance Configuration

```json
{
  "compliance_profile": "us_retail_algo",
  "jurisdiction": "US",
  "regulations": ["SEC_15c3_5", "FINRA_3110", "FINRA_15_09", "REG_SHO"],

  "pre_trade": {
    "position_limits": {
      "single_name_pct": 10,
      "sector_pct": 30,
      "gross_exposure_pct": 200
    },
    "order_limits": {
      "max_notional_usd": 50000,
      "max_adv_pct": 5,
      "fat_finger_sigma": 3,
      "duplicate_window_sec": 5
    },
    "loss_limits": {
      "max_daily_loss_pct": 2,
      "max_drawdown_pct": 5,
      "kill_switch_enabled": true
    },
    "wash_trade": {
      "detection_window_days": 30,
      "action": "hard_block",
      "substantially_identical": true
    }
  },

  "post_trade": {
    "surveillance_enabled": true,
    "pattern_detection": ["layering", "spoofing", "momentum_ignition", "marking_close"],
    "best_execution_reporting": "quarterly",
    "audit_trail_retention_years": 6
  },

  "restricted_list": {
    "symbols": [],
    "sectors": [],
    "countries": ["IR", "KP", "SY", "CU"]
  }
}
```

### New MCP Tools — Compliance

| Tool | Description |
|---|---|
| `check_order_compliance` | Run pre-trade compliance checks on a proposed order. Returns pass/soft_block/hard_block with violation details. |
| `get_compliance_status` | Current compliance state: daily P&L vs limits, position concentrations, pending violations. |
| `get_audit_trail` | Export the immutable audit trail for a date range, symbol, or strategy. Formats: JSON, CSV, OATS. |
| `set_compliance_profile` | Configure compliance rules: jurisdiction, position limits, loss limits, wash trade settings. |
| `get_best_execution_report` | Generate best execution analysis: fill quality vs NBBO, slippage stats, venue analysis. |
| `get_wash_trade_alerts` | List potential wash trade violations flagged by the detection engine. |
| `set_restricted_list` | Update the restricted securities/sectors/countries list. |
| `run_surveillance_scan` | Trigger an on-demand post-trade surveillance scan for pattern detection. |

### Implementation Architecture

```text
src/algochains_mcp/compliance/
├── __init__.py
├── engine.py                # Core compliance orchestrator
├── pre_trade/
│   ├── position_limits.py   # Concentration checks
│   ├── order_limits.py      # Size, fat-finger, duplicate detection
│   ├── loss_limits.py       # Daily P&L, drawdown, kill switch
│   ├── wash_trade.py        # 30-day wash trade detection
│   ├── restricted_list.py   # OFAC, firm restricted list
│   └── best_execution.py    # NBBO comparison, fill quality
├── post_trade/
│   ├── surveillance.py      # Pattern detection (layering, spoofing)
│   ├── reporting.py         # TCA, best execution reports
│   └── audit_trail.py       # Immutable append-only log
├── profiles/
│   ├── us_retail_algo.json
│   ├── us_institutional.json
│   ├── eu_mifid2.json
│   └── custom_template.json
└── config.py                # Compliance configuration loader
```

---

## Part 3 — Multi-Tenant White-Label

### The Business Case

Fintech companies want to offer trading capabilities without building infrastructure from scratch. The white-label market is dominated by:

- **DriveWealth** — Powers 100+ neobrokers globally (Revolut, Stake, Hatch) via API-first BaaS
- **Alpaca for Business** — Brokerage-as-a-service for apps wanting to embed stock trading
- **Tradier** — Brokerage API with white-label capabilities
- **Interactive Brokers** — Institutional white-label with full clearing

AlgoChains V9 adds a multi-tenant layer so **any fintech company can offer AI-powered algo trading under their own brand**, powered by AlgoChains MCP infrastructure.

### Architecture

```text
┌───────────────────────────────────────────────────────────┐
│  TENANT: FinApp Inc.  (tenant_id: "finapp_abc")          │
│  Brand: FinApp Trading · Logo: finapp.png                │
│                                                           │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐      │
│  │ Sub-Account  │  │ Sub-Account  │  │ Sub-Account  │     │
│  │ user_001     │  │ user_002     │  │ user_003     │     │
│  │ Alpaca       │  │ Alpaca       │  │ IBKR         │     │
│  │ $25K AUM     │  │ $100K AUM    │  │ $500K AUM    │     │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘    │
│         │                 │                 │             │
│         └────────────┬────┘─────────────────┘             │
│                      ▼                                    │
│  ┌────────────────────────────────────────────────┐       │
│  │  Tenant MCP Server Instance                    │       │
│  │  ├── Scoped API key (tenant-level)             │       │
│  │  ├── Compliance profile (tenant-configured)    │       │
│  │  ├── Risk limits (tenant-level overrides)      │       │
│  │  ├── Marketplace access (curated subset)       │       │
│  │  └── Usage metering (API calls, trades, AUM)   │       │
│  └────────────────────────────────────────────────┘       │
└───────────────────────────────────────────────────────────┘
                        │
                        │ Tenant-scoped API calls
                        ▼
┌───────────────────────────────────────────────────────────┐
│  ALGOCHAINS MULTI-TENANT CONTROL PLANE                    │
│                                                           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐    │
│  │ Tenant       │  │ Isolation    │  │ Usage        │    │
│  │ Registry     │  │ Enforcer     │  │ Billing      │    │
│  │ (Supabase)   │  │ (Row-Level)  │  │ (Stripe)     │    │
│  └──────────────┘  └──────────────┘  └──────────────┘    │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐    │
│  │ Sub-Account  │  │ Broker       │  │ Audit        │    │
│  │ Manager      │  │ Router       │  │ Logger       │    │
│  └──────────────┘  └──────────────┘  └──────────────┘    │
└───────────────────────────────────────────────────────────┘
```

### Tenant Isolation Model

Every tenant operates in complete data isolation:

```text
Database Isolation Strategy:
├── Row-Level Security (RLS) on Supabase/PostgreSQL
│   ├── Every table has tenant_id column
│   ├── RLS policies enforce: SELECT/INSERT/UPDATE/DELETE WHERE tenant_id = auth.tenant()
│   └── No cross-tenant data leakage possible at database level
│
├── API Key Scoping
│   ├── Tenant admin key: manage sub-accounts, configure compliance, set risk limits
│   ├── Sub-account key: trade, view positions, access marketplace (scoped to sub-account)
│   └── Read-only key: dashboards, reporting, analytics
│
├── Broker Credential Isolation
│   ├── Each sub-account has its own broker credentials
│   ├── Encrypted at rest (AES-256) with tenant-specific encryption key
│   └── Never accessible to tenant admin — only the sub-account owner
│
└── Network Isolation (optional, enterprise tier)
    ├── Dedicated MCP server instance per tenant
    ├── VPC peering for enterprise tenants
    └── Custom domain (trading.finapp.com → AlgoChains backend)
```

### Sub-Account Management

```json
{
  "tenant_id": "finapp_abc",
  "sub_account": {
    "id": "sa_user_001",
    "user_id": "usr_xyz",
    "display_name": "John D.",
    "broker": {
      "provider": "alpaca",
      "account_type": "paper",
      "credentials_encrypted": "AES256:abc...",
      "buying_power": 25000.00
    },
    "permissions": {
      "can_trade": true,
      "can_use_marketplace": true,
      "can_copy_trade": true,
      "max_daily_trades": 50,
      "max_position_size_usd": 5000,
      "allowed_asset_classes": ["equity", "etf"]
    },
    "compliance_overrides": {
      "max_daily_loss_pct": 3,
      "restricted_symbols": ["GME", "AMC"]
    }
  }
}
```

### White-Label Customization

Tenants can customize the user-facing experience:

| Customization | Description |
|---|---|
| **Branding** | Logo, colors, domain (CNAME), email templates |
| **Marketplace Curation** | Select which bots/strategies are visible to sub-accounts |
| **Risk Profiles** | Pre-configured compliance profiles (conservative, moderate, aggressive) |
| **Fee Structure** | Tenant sets markup on AlgoChains fees (e.g., AlgoChains charges $10/mo, tenant charges $29/mo) |
| **Broker Selection** | Restrict which brokers are available (e.g., Alpaca-only for US, Oanda-only for forex) |
| **Feature Flags** | Toggle social trading, community signals, dataset builder per tenant |

### Revenue Model

| Tier | Monthly Base | Per Sub-Account | Revenue Share | Includes |
|---|---|---|---|---|
| **Starter** | $199 | $2/active account | 5% of marketplace fees | 100 sub-accounts, 1 broker, basic compliance |
| **Growth** | $999 | $1/active account | 3% of marketplace fees | 1,000 sub-accounts, 3 brokers, full compliance |
| **Enterprise** | Custom | Custom | Custom | Unlimited, dedicated instance, VPC, SLA |

### New MCP Tools — Multi-Tenant White-Label

| Tool | Description |
|---|---|
| `create_tenant` | Provision a new tenant with branding, broker config, and compliance profile. |
| `create_sub_account` | Create a sub-account under a tenant with broker credentials and permissions. |
| `get_tenant_dashboard` | Aggregate metrics for the tenant: total AUM, active accounts, daily P&L, usage stats. |
| `set_tenant_config` | Update tenant configuration: branding, marketplace curation, feature flags, fee structure. |
| `get_sub_account_status` | Detailed status of a sub-account: positions, P&L, compliance state, recent trades. |
| `set_sub_account_permissions` | Update sub-account permissions: trade limits, asset classes, marketplace access. |
| `get_usage_billing` | Current billing cycle usage: API calls, trades executed, AUM-days, estimated invoice. |

### Implementation Architecture

```text
src/algochains_mcp/multitenant/
├── __init__.py
├── control_plane.py        # Tenant lifecycle management
├── tenant_registry.py      # Supabase-backed tenant store
├── sub_account_manager.py  # Sub-account CRUD + permissions
├── isolation/
│   ├── rls_enforcer.py     # Row-level security enforcement
│   ├── key_scoping.py      # API key generation + scoping
│   └── credential_vault.py # AES-256 encrypted credential storage
├── branding/
│   ├── config.py           # Branding configuration (logo, colors, domain)
│   └── templates/          # Email templates, webhook payloads
├── billing/
│   ├── metering.py         # Usage tracking (API calls, trades, AUM-days)
│   ├── stripe_integration.py # Stripe billing + invoicing
│   └── plans.py            # Tier definitions (Starter, Growth, Enterprise)
├── broker_router.py        # Route orders to tenant-configured brokers
└── feature_flags.py        # Per-tenant feature toggles
```

---

## Database Schema Additions

```sql
-- Risk Dashboard
CREATE TABLE risk_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    tenant_id UUID REFERENCES tenants(id),
    snapshot_time TIMESTAMPTZ NOT NULL,
    var_95 DECIMAL(12,4),
    var_99 DECIMAL(12,4),
    expected_shortfall DECIMAL(12,4),
    max_drawdown DECIMAL(8,4),
    current_drawdown DECIMAL(8,4),
    sharpe_30d DECIMAL(6,4),
    beta_spy DECIMAL(6,4),
    concentration_hhi DECIMAL(6,4),
    margin_utilization DECIMAL(6,4),
    factor_exposures JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_risk_snapshots_user_time
    ON risk_snapshots(user_id, snapshot_time DESC);

-- Compliance
CREATE TABLE compliance_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    tenant_id UUID REFERENCES tenants(id),
    event_type VARCHAR(30) NOT NULL,     -- pre_trade_block, wash_trade_alert, surveillance_flag
    severity VARCHAR(10) NOT NULL,        -- info, warning, violation
    order_id UUID,
    symbol VARCHAR(20),
    details JSONB NOT NULL,
    resolved BOOLEAN DEFAULT FALSE,
    resolved_by UUID,
    resolved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE audit_trail (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL,
    tenant_id UUID,
    action VARCHAR(50) NOT NULL,          -- order_submitted, order_filled, compliance_check, etc.
    payload JSONB NOT NULL,               -- Full order/event details
    prev_hash VARCHAR(64),                -- SHA-256 of previous entry (chain integrity)
    entry_hash VARCHAR(64) NOT NULL,      -- SHA-256 of this entry
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_audit_trail_user_time
    ON audit_trail(user_id, created_at DESC);

-- Multi-Tenant
CREATE TABLE tenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(100) UNIQUE NOT NULL,    -- URL-safe identifier
    branding JSONB,                       -- logo_url, colors, domain
    compliance_profile VARCHAR(50),       -- us_retail_algo, eu_mifid2, custom
    broker_config JSONB,                  -- Allowed brokers, default broker
    feature_flags JSONB,                  -- social_trading, community_signals, etc.
    billing_tier VARCHAR(20),             -- starter, growth, enterprise
    stripe_customer_id VARCHAR(100),
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE sub_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id),
    broker_provider VARCHAR(50) NOT NULL,
    credentials_encrypted TEXT NOT NULL,   -- AES-256 encrypted
    permissions JSONB NOT NULL,
    compliance_overrides JSONB,
    status VARCHAR(20) DEFAULT 'active',  -- active, suspended, closed
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, user_id)
);

CREATE TABLE usage_meters (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id),
    billing_period DATE NOT NULL,         -- First day of billing month
    api_calls INTEGER DEFAULT 0,
    trades_executed INTEGER DEFAULT 0,
    aum_days DECIMAL(14,2) DEFAULT 0,     -- Sum of daily AUM values
    active_sub_accounts INTEGER DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, billing_period)
);

-- Row-Level Security
ALTER TABLE risk_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE compliance_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE sub_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE usage_meters ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_risk ON risk_snapshots
    USING (tenant_id = current_setting('app.tenant_id')::UUID);
CREATE POLICY tenant_isolation_compliance ON compliance_events
    USING (tenant_id = current_setting('app.tenant_id')::UUID);
CREATE POLICY tenant_isolation_subaccounts ON sub_accounts
    USING (tenant_id = current_setting('app.tenant_id')::UUID);
CREATE POLICY tenant_isolation_usage ON usage_meters
    USING (tenant_id = current_setting('app.tenant_id')::UUID);
```

---

## Implementation Plan

### Phase 1 — Risk Dashboard (6 weeks)

| Week | Deliverable |
|---|---|
| 1 | VaR engine (parametric + historical) with 252-day rolling window |
| 2 | Monte Carlo VaR, Expected Shortfall, correlation matrix |
| 3 | Factor model (6-factor Fama-French + 11 sectors), factor decomposition |
| 4 | Stress testing engine with 6 pre-built scenarios + custom scenario builder |
| 5 | Real-time metrics pipeline: drawdown, margin, concentration, Greeks, alerts |
| 6 | MCP tool registration, Redis caching, end-to-end testing |

### Phase 2 — Compliance Module (5 weeks)

| Week | Deliverable |
|---|---|
| 1 | Pre-trade engine: position limits, order limits, fat-finger, duplicate detection |
| 2 | Loss limits with kill switch, wash trade detection (30-day window) |
| 3 | Restricted list, OFAC check, best execution tracking (NBBO comparison) |
| 4 | Post-trade surveillance: layering, spoofing, momentum ignition pattern detection |
| 5 | Audit trail (immutable, SHA-256 chained), reporting, MCP tools |

### Phase 3 — Multi-Tenant White-Label (7 weeks)

| Week | Deliverable |
|---|---|
| 1 | Tenant registry, control plane, RLS enforcement on all tables |
| 2 | Sub-account manager: CRUD, permissions, credential vault (AES-256) |
| 3 | Broker router: tenant-configured broker selection, order routing |
| 4 | Branding system: logo, colors, custom domain (CNAME), email templates |
| 5 | Billing: Stripe integration, usage metering, tier enforcement |
| 6 | Feature flags, marketplace curation per tenant |
| 7 | End-to-end testing, documentation, MCP tools |

**Total: 18 weeks**

---

## Security Considerations

- **Tenant isolation:** Row-level security on PostgreSQL. No API call can access another tenant's data.
- **Credential encryption:** Sub-account broker credentials encrypted with AES-256 using tenant-specific keys derived from a master key (AWS KMS or Vault).
- **Audit immutability:** Each audit trail entry includes a SHA-256 hash of the previous entry, creating a tamper-evident chain. Any modification breaks the chain.
- **Compliance data retention:** 6-year retention per SEC Rule 17a-4. Automatic archival to cold storage after 2 years.
- **Rate limiting:** Per-tenant and per-sub-account rate limits to prevent abuse and ensure fair usage.
- **SOC 2 preparation:** V9 architecture designed with SOC 2 Type II controls in mind (access logging, change management, incident response).

---

## Success Metrics

| Metric | Target (6 months post-launch) |
|---|---|
| White-label tenants onboarded | 10+ |
| Sub-accounts across all tenants | 5,000+ |
| Daily VaR computations | 50,000+ |
| Compliance checks per day | 100,000+ |
| Wash trade alerts (true positives) | > 90% precision |
| MRR from white-label subscriptions | $25K+ |
| Enterprise pipeline | 3+ signed LOIs |

---

## Research Sources

- **Risk Analytics:** Bloomberg PORT, Axioma Risk (SimCorp), MSCI RiskMetrics, Venn by Two Sigma, FactSet Risk, Orion Risk Intelligence, Charles River IMS
- **Compliance:** FINRA Notice 15-09, SEC Rule 15c3-5 (Market Access Rule), FINRA Rule 3110, MiFID II RTS 6/25, SEC Rule 17a-4
- **White-Label:** DriveWealth (powers Revolut, Stake, Hatch), Alpaca for Business (OAuth + sub-accounts), Tradier BaaS, Interactive Brokers white-label
- **Security:** SOC 2 Type II framework, AWS KMS, HashiCorp Vault, PostgreSQL RLS
