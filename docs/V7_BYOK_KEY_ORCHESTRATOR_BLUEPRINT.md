# AlgoChains MCP Server V7: BYOK Key Orchestrator & Proprietary Dataset Builder

## Executive Summary

**V7 introduces the industry's first autonomous key discovery and dataset pipeline for AI trading agents.**

No platform — not Composio, Nango, Arcade, or Merge — offers automatic discovery of existing API keys. They all require manual entry. AlgoChains V7 changes that: a user says **"gather my keys"** and the system scans their environment, validates every credential, maps what's missing, and guides them to provision the rest. Then it uses those keys to build proprietary datasets that power ML models feeding marketplace bots.

This is the **zero-friction onboarding layer** that turns "I have some API keys somewhere" into "I have a live ML-powered trading pipeline."

---

## Competitive Landscape

| Platform | Key Entry | Auto-Discovery | Validation | Dataset Pipeline | ML Integration |
| --- | --- | --- | --- | --- | --- |
| **Composio** | Manual (250+ tools) | No | Post-entry model list | No | No |
| **Nango** | Manual (unified API) | No | Basic health check | Sync only | No |
| **Arcade** | Manual (MCP-native) | No | No | No | No |
| **Merge** | Manual (enterprise) | No | No | No | No |
| **Vercel BYOK** | Manual (AI Gateway) | No | No | No | No |
| **Cloudflare** | Manual (dashboard) | No | Status check | No | No |
| **AlgoChains V7** | **Autonomous** | **Yes** | **Deep validation** | **Yes** | **Yes** |

### What Makes V7 Novel

1. **Autonomous Discovery** — Scans `.env`, `~/.config/`, env vars, IDE configs, shell history for existing keys
2. **Deep Validation** — Doesn't just check "key exists" — makes live API calls to verify permissions, rate limits, plan tier
3. **Gap Analysis** — Shows what you have, what you're missing, and what each missing key unlocks
4. **Guided Provisioning** — Direct signup links + instructions for each missing provider
5. **Dataset Pipeline** — Once keys are validated, auto-builds proprietary datasets from all available providers
6. **ML Feature Store** — Datasets feed directly into feature engineering for marketplace bot ML models

---

## Architecture

```
User: "gather my keys"
         │
         ▼
┌─────────────────────────────┐
│   KEY ORCHESTRATOR (V7)     │
│                             │
│  1. DISCOVER                │
│     ├─ Scan env vars        │
│     ├─ Scan .env files      │
│     ├─ Scan IDE configs     │
│     ├─ Scan ~/.config/      │
│     └─ Scan shell profiles  │
│                             │
│  2. VALIDATE                │
│     ├─ Live API health call │
│     ├─ Check permissions    │
│     ├─ Check rate limits    │
│     ├─ Detect plan tier     │
│     └─ Score coverage       │
│                             │
│  3. GAP ANALYSIS            │
│     ├─ What you have ✅      │
│     ├─ What you're missing  │
│     ├─ What each unlocks    │
│     └─ Priority ranking     │
│                             │
│  4. PROVISION               │
│     ├─ Signup URLs          │
│     ├─ Free tier available? │
│     ├─ Auto-write to .env   │
│     └─ Re-validate          │
│                             │
│  5. SECURE STORAGE          │
│     ├─ Local keyring        │
│     ├─ Encrypted .env       │
│     └─ Never transmitted    │
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│   DATASET BUILDER (V7)      │
│                             │
│  1. INGEST                  │
│     ├─ Pull from all valid  │
│     │  data providers       │
│     ├─ Normalize schemas    │
│     └─ Deduplicate          │
│                             │
│  2. ENRICH                  │
│     ├─ Technical indicators │
│     ├─ Sentiment scores     │
│     ├─ Cross-asset corr     │
│     └─ Regime labels        │
│                             │
│  3. STORE                   │
│     ├─ Parquet (local)      │
│     ├─ Feature store        │
│     └─ Versioned datasets   │
│                             │
│  4. EXPORT                  │
│     ├─ ML training sets     │
│     ├─ Backtest data        │
│     └─ Live feature feeds   │
└─────────────────────────────┘
```

---

## Module 1: Key Orchestrator

### Supported Providers (Phase 1)

| Provider | Env Var | Free Tier | Data Types |
| --- | --- | --- | --- |
| **Polygon.io** | `POLYGON_API_KEY` | Yes (5 calls/min) | Bars, quotes, trades, news, fundamentals |
| **Alpha Vantage** | `ALPHA_VANTAGE_API_KEY` | Yes (25 calls/day) | Bars, quotes, fundamentals, forex, crypto |
| **Finnhub** | `FINNHUB_API_KEY` | Yes (60 calls/min) | Bars, quotes, news, sentiment, insider |
| **Twelve Data** | `TWELVE_DATA_API_KEY` | Yes (8 calls/min) | Bars, quotes, technical indicators |
| **Yahoo Finance** | N/A (no key needed) | Yes (unlimited*) | Bars, quotes, fundamentals |
| **Databento** | `DATABENTO_API_KEY` | No | Tick data, L2, trades |
| **Unusual Whales** | `UW_API_KEY` | No | Options flow, dark pool, GEX |
| **Intrinio** | `INTRINIO_API_KEY` | Yes (limited) | Fundamentals, prices, options |
| **Quandl/Nasdaq** | `QUANDL_API_KEY` | Yes (limited) | Economic data, alternative data |
| **OpenBB** | `OPENBB_TOKEN` | Yes | Aggregated multi-source |

### Discovery Locations

```python
SCAN_LOCATIONS = [
    # Environment variables (highest priority)
    "os.environ",
    
    # .env files (project-level)
    ".env",
    ".env.local",
    ".env.production",
    
    # Home directory configs
    "~/.env",
    "~/.config/algochains/.env",
    "~/.config/polygon/config.json",
    
    # IDE MCP configs
    "~/.windsurf/mcp-config.json",
    "~/.cursor/mcp.json",
    "~/.vscode/settings.json",
    "~/.continue/config.json",
    
    # Shell profiles (export statements)
    "~/.zshrc",
    "~/.bashrc",
    "~/.bash_profile",
    "~/.zprofile",
    
    # Cloud CLI configs
    "~/.config/gcloud/",
    "~/.aws/credentials",
]
```

### Validation Protocol

For each discovered key:

1. **Existence** — Key string is non-empty and matches expected format (length, prefix pattern)
2. **Authentication** — Make a lightweight API call to verify the key authenticates
3. **Permissions** — Check what endpoints/data the key has access to
4. **Rate Limits** — Detect the user's plan tier and rate limit ceiling
5. **Freshness** — Check if the key is expired or about to expire
6. **Coverage Score** — 0-100 score based on how much data this key unlocks

### MCP Tools

| Tool | Description |
| --- | --- |
| `discover_keys` | Scan all locations for existing API keys. Returns found keys (masked), locations, and validation status |
| `validate_keys` | Deep-validate all discovered keys with live API calls. Returns permissions, rate limits, plan tier |
| `key_gap_analysis` | Show what providers are missing, what each unlocks, signup URLs, free tier availability |
| `provision_key` | Write a new key to .env and validate it. Supports provider name + key value |
| `key_health` | Real-time health check of all configured keys. Returns status, last validated, rate limit remaining |
| `export_config` | Export validated key config as .env, JSON, or MCP config format for any IDE |

---

## Module 2: Dataset Builder

### Pipeline Stages

#### Stage 1: Ingest
Pull data from every validated provider the user has keys for:
- **OHLCV bars** at multiple timeframes (1min, 5min, 15min, 1h, 4h, daily)
- **Tick data** if Databento key available
- **Options flow** if Unusual Whales key available
- **News/sentiment** if Polygon or Finnhub key available
- **Fundamentals** if Alpha Vantage or Intrinio key available
- **Economic data** if Quandl key available

#### Stage 2: Normalize
- Unified schema across all providers
- Consistent timestamp formats (UTC)
- Standardized column names (open, high, low, close, volume)
- Handle gaps, splits, dividends

#### Stage 3: Enrich
- Technical indicators (RSI, MACD, BB, EMA, ATR, ADX)
- Cross-asset correlations
- Volume profile analysis
- Regime detection labels (bull/bear/sideways/high-vol/low-vol)
- Sentiment scores from news data

#### Stage 4: Store
- Local parquet files (versioned)
- Feature store for ML training
- Incremental updates (only fetch new data)

#### Stage 5: Export
- ML training/test splits with anti-leakage guarantees
- Backtest-ready format for Rust engine
- Live feature feeds for production bots

### MCP Tools

| Tool | Description |
| --- | --- |
| `build_dataset` | Build a proprietary dataset for a symbol/timeframe using all available providers |
| `list_datasets` | List all built datasets with metadata (rows, columns, date range, sources) |
| `dataset_status` | Show what data you CAN build vs what you're missing (based on available keys) |
| `enrich_dataset` | Add technical indicators, sentiment, regime labels to an existing dataset |
| `export_dataset` | Export dataset in ML-ready format (train/test split, feature matrix, target variable) |

---

## OpenClaw Skill: `byok-key-orchestrator`

### Trigger Phrases
- "gather my keys"
- "what keys do I have"
- "set up my data providers"
- "onboard me"
- "what data can I access"
- "build my dataset"
- "check my API keys"

### Autonomous Actions (No Approval Needed)
- Scan for existing keys
- Validate discovered keys
- Generate gap analysis report
- Build datasets from validated providers

### Requires Approval
- Writing new keys to .env files
- Making paid API calls on the user's behalf
- Deleting or rotating keys

---

## V7 Tool Inventory (11 new tools)

### Key Orchestrator (6 tools)
1. `discover_keys` — Autonomous key scanner
2. `validate_keys` — Deep validation with live API calls
3. `key_gap_analysis` — What you have, what you need, what each unlocks
4. `provision_key` — Add a new key + auto-validate
5. `key_health` — Real-time health dashboard for all keys
6. `export_config` — Export config for any IDE/environment

### Dataset Builder (5 tools)
7. `build_dataset` — Build proprietary dataset from all available sources
8. `list_datasets` — Inventory of built datasets
9. `dataset_status` — Data coverage map (what you can build vs what's missing)
10. `enrich_dataset` — Add features, indicators, sentiment, regime labels
11. `export_dataset` — ML-ready export with train/test splits

### Updated Server Totals (V7)
- **54 tools** (was 43)
- **4 prompts**
- **4 resources**
- **5 broker connectors**
- **10+ data providers**
- **7 notification channels**
- **6 portfolio allocation methods**

---

## User Flow: "Gather My Keys"

```
User: "gather my keys"

Agent: Scanning your environment for API keys...

🔍 DISCOVERY RESULTS
━━━━━━━━━━━━━━━━━━━━

✅ FOUND (4 keys):
  • Polygon.io     — POLYGON_API_KEY in ~/.zshrc        → Starter plan, 5 calls/min
  • Alpha Vantage  — ALPHA_VANTAGE_API_KEY in .env      → Free tier, 25 calls/day
  • Finnhub        — FINNHUB_API_KEY in .env             → Free tier, 60 calls/min
  • Yahoo Finance  — No key needed                       → Unlimited

❌ MISSING (unlock more data):
  • Databento      — Unlocks: tick data, L2 book, trades → https://databento.com/signup
  • Unusual Whales — Unlocks: options flow, dark pool    → https://unusualwhales.com/pricing
  • Twelve Data    — Unlocks: 800+ indicators (FREE)     → https://twelvedata.com/apikey
  • Quandl/Nasdaq  — Unlocks: economic data, alt data    → https://data.nasdaq.com/sign-up

📊 COVERAGE SCORE: 62/100
  • OHLCV bars:      ████████░░ 80%  (4 providers)
  • Fundamentals:    ██████░░░░ 60%  (2 providers)
  • News/sentiment:  ██████░░░░ 60%  (2 providers)
  • Tick data:       ░░░░░░░░░░  0%  (need Databento)
  • Options flow:    ░░░░░░░░░░  0%  (need Unusual Whales)
  • Economic data:   ░░░░░░░░░░  0%  (need Quandl)

💡 QUICK WIN: Add Twelve Data (free) to unlock 800+ technical indicators.
   Get your key: https://twelvedata.com/apikey
   Then: "provision key twelve_data YOUR_KEY_HERE"

🚀 WITH YOUR CURRENT KEYS, YOU CAN BUILD:
  • Daily/hourly OHLCV for any stock, ETF, forex, crypto
  • News sentiment features for ML models
  • Fundamental features (P/E, EPS, revenue)
  • Technical indicator features (RSI, MACD, BB, etc.)

  Ready to build? Say: "build dataset AAPL daily"
```

---

## Security Model

1. **Keys never leave the user's machine** — All discovery and validation happens locally
2. **Keys are masked in all output** — Only first 4 + last 4 characters shown
3. **No cloud transmission** — Zero keys sent to AlgoChains servers
4. **Local keyring integration** — Optional macOS Keychain / Linux secret-service storage
5. **Encrypted .env** — Optional AES-256 encryption for .env files at rest
6. **Audit trail** — Every key access logged locally with timestamp and tool name

---

## Implementation Files

```
src/algochains_mcp/
├── byok/
│   ├── __init__.py
│   ├── key_orchestrator.py    # Discovery, validation, gap analysis, provisioning
│   ├── provider_registry.py   # Provider metadata (env vars, URLs, free tiers, formats)
│   └── security.py            # Key masking, local keyring, encrypted storage
├── datasets/
│   ├── __init__.py
│   ├── builder.py             # Dataset ingestion, normalization, enrichment
│   ├── feature_store.py       # Local feature store for ML training
│   └── exporters.py           # ML-ready export formats
```

---

## Why This Matters for AlgoChains

1. **Onboarding friction → zero** — New user goes from "I have some keys" to "I have a live ML pipeline" in 60 seconds
2. **Lock-in through value** — The more data providers they connect, the better their datasets, the stickier the platform
3. **Marketplace flywheel** — Better datasets → better ML models → better bots → more marketplace subscriptions
4. **Competitive moat** — No one else does autonomous key discovery. Composio/Nango require manual entry.
5. **Revenue expansion** — Users who build datasets are power users who subscribe to premium marketplace bots

---

## Timeline

- **V7.0** (this release): Key Orchestrator + Dataset Builder core
- **V7.1**: Keychain/secret-service integration, encrypted .env
- **V7.2**: Incremental dataset updates, live feature feeds
- **V7.3**: ML model training integration (scikit-learn, XGBoost, PyTorch export)
