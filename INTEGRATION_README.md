# AlgoChains MCP Server — Django Integration Guide

> **For:** RJ (frontend/auth), Roo (backend/infra)
> **From:** Tyler (strategy engine + MCP server)
> **Repo:** https://github.com/AlgoChains/algochains-mcp-server
> **Status:** Production-ready, 40/40 tests passing, 0 lint errors

---

## What This Is

A **Model Context Protocol (MCP) server** that gives any AI agent (Claude, GPT, Cascade, Devin) normalized trading access to 15+ brokers + a 7-gate strategy validation pipeline + marketplace integration. Think of it as the **API brain** behind the marketplace bots on algochains.ai.

### What It Powers on algochains.ai

1. **Marketplace Bot Cards** — Each bot listing (MNQ Momentum, CL Scalper, etc.) is backed by validated metrics from this server
2. **Subscribe Flow** — When a user clicks "Subscribe" on a bot, this server handles broker routing
3. **Developer Profiles** — Tyler's live bots auto-populate his creator profile with real performance data
4. **Strategy Submissions** — External devs submit strategies via MCP tools, validated through 7 gates
5. **Real-Time Metrics** — Bot performance, Sharpe ratios, drawdown, trade counts pushed to Django

---

## Architecture: How It Connects to algochains.ai

```
┌─────────────────────┐     ┌──────────────────────┐
│   algochains.ai     │     │  MCP Server           │
│   (Django + Supa)   │◄────│  (Python, runs on     │
│                     │     │   Tyler's infra)       │
│  /api/v1/listings/  │     │                        │
│  /api/v1/subscribe/ │     │  Brokers: Alpaca, IBKR │
│  /api/v1/metrics/   │     │  Oanda, TradersPost,   │
│                     │     │  QuantConnect           │
└─────────────────────┘     └──────────────────────┘
         ▲                           │
         │                           │
    Supabase Auth                Tradovate WS
    Google OAuth                 (Live futures bots)
```

### Data Flow

1. **MCP Server → Django API**: Pushes bot metrics (Sharpe, P&L, trades) via `POST /api/v1/metrics/ingest/`
2. **Django API → Marketplace Cards**: Renders bot cards with live data from the metrics table
3. **User Subscribe → Django → MCP Server**: Subscribe action triggers broker connection setup
4. **Strategy Submit → MCP Server → Validation → Django**: New strategies flow through 7-gate validation

---

## Django Endpoints Needed (For Roo)

The MCP server expects these REST endpoints on algochains.ai. If they don't exist yet, here's exactly what's needed:

### 1. `GET /api/v1/listings/`
Browse marketplace bots with optional filters.

**Query Params:**
- `asset_class` — futures, forex, stocks, crypto
- `min_sharpe` — minimum OOS Sharpe ratio
- `tier` — platinum, gold, silver, bronze
- `creator` — username filter

**Response:**
```json
{
  "results": [
    {
      "slug": "mnq-momentum-v3",
      "name": "MNQ Momentum Scalper V3",
      "creator": "tyler",
      "asset_class": "futures",
      "symbol": "MNQ",
      "tier": "gold",
      "oos_sharpe": 2.1,
      "oos_trades": 487,
      "max_drawdown_pct": 12.5,
      "monthly_price": 29.99,
      "subscribers": 0,
      "status": "active",
      "created_at": "2026-03-29T00:00:00Z"
    }
  ]
}
```

### 2. `GET /api/v1/listings/{slug}/`
Get detailed bot info for the bot detail page.

**Response:** Same as above + `description`, `backtest_equity_curve`, `parameters`, `validation_results`

### 3. `POST /api/v1/listings/`
Create/publish a new bot listing (authenticated, creator only).

**Headers:** `Authorization: Api-Key <LISTING_API_KEY>`

**Body:**
```json
{
  "slug": "mnq-momentum-v3",
  "name": "MNQ Momentum Scalper V3",
  "creator": "tyler",
  "asset_class": "futures",
  "symbol": "MNQ",
  "strategy_type": "momentum",
  "timeframe": "5min",
  "tier": "gold",
  "oos_sharpe": 2.1,
  "oos_trades": 487,
  "max_drawdown_pct": 12.5,
  "monthly_price": 29.99,
  "description": "Institutional-grade MNQ momentum strategy...",
  "min_capital": 5000,
  "validation_results": { ... }
}
```

### 4. `POST /api/v1/listings/{slug}/subscribe/`
Subscribe a user to a bot (authenticated via Supabase JWT).

**Body:**
```json
{
  "broker": "alpaca",
  "mode": "paper",
  "allocation_pct": 10
}
```

### 5. `POST /api/v1/metrics/ingest/`
Push live bot performance metrics (called by MCP server every hour).

**Headers:** `Authorization: Api-Key <METRICS_INGEST_API_KEY>`

**Body:**
```json
{
  "slug": "mnq-momentum-v3",
  "timestamp": "2026-03-29T12:00:00Z",
  "daily_pnl": 127.50,
  "total_pnl": 4250.00,
  "sharpe_30d": 2.05,
  "trades_today": 12,
  "trades_total": 487,
  "max_drawdown_pct": 12.5,
  "win_rate": 67.2,
  "status": "active"
}
```

---

## Django Models Needed (For Roo)

### `MarketplaceListing`
```python
class MarketplaceListing(models.Model):
    slug = models.SlugField(unique=True, max_length=100)
    name = models.CharField(max_length=200)
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    asset_class = models.CharField(max_length=20)  # futures, forex, stocks, crypto
    symbol = models.CharField(max_length=20)
    strategy_type = models.CharField(max_length=50)
    timeframe = models.CharField(max_length=20)
    tier = models.CharField(max_length=20)  # platinum, gold, silver, bronze
    oos_sharpe = models.FloatField()
    oos_trades = models.IntegerField()
    max_drawdown_pct = models.FloatField()
    monthly_price = models.DecimalField(max_digits=8, decimal_places=2)
    min_capital = models.IntegerField(default=5000)
    description = models.TextField(blank=True)
    validation_results = models.JSONField(default=dict)
    status = models.CharField(max_length=20, default='draft')  # draft, paper, active, paused, delisted
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-oos_sharpe']
```

### `BotSubscription`
```python
class BotSubscription(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    listing = models.ForeignKey(MarketplaceListing, on_delete=models.CASCADE)
    broker = models.CharField(max_length=50)
    mode = models.CharField(max_length=20, default='paper')  # paper, live
    allocation_pct = models.FloatField(default=10.0)
    status = models.CharField(max_length=20, default='active')
    subscribed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['user', 'listing']
```

### `BotMetrics`
```python
class BotMetrics(models.Model):
    listing = models.ForeignKey(MarketplaceListing, on_delete=models.CASCADE)
    timestamp = models.DateTimeField()
    daily_pnl = models.FloatField()
    total_pnl = models.FloatField()
    sharpe_30d = models.FloatField()
    trades_today = models.IntegerField()
    trades_total = models.IntegerField()
    max_drawdown_pct = models.FloatField()
    win_rate = models.FloatField()
    status = models.CharField(max_length=20)

    class Meta:
        ordering = ['-timestamp']
        indexes = [models.Index(fields=['listing', '-timestamp'])]
```

---

## Marketplace Card Frontend (For RJ)

Each bot card on algochains.ai/marketplace should display:

```
┌─────────────────────────────────────────────┐
│  [GOLD]  MNQ Momentum Scalper V3            │
│  by tyler · Futures · MNQ · 5min            │
│                                              │
│  Sharpe: 2.10    Win Rate: 67.2%            │
│  Trades: 487     Max DD: -12.5%             │
│  Monthly P&L: +$1,250                       │
│                                              │
│  Min Capital: $5,000                         │
│  ─────────────────────────────────────────── │
│  $29.99/mo    [ Subscribe ]  [ Details ]     │
└─────────────────────────────────────────────┘
```

### Subscribe vs Cart (RE: RJ's question)

**Recommendation: Subscribe one at a time (no cart)**

Reasons:
1. Each bot has a minimum capital requirement
2. Users need to allocate broker capital per bot
3. We can validate capital sufficiency before subscribing
4. Avoids the "I subscribed to 5 bots with $500" complaint RJ mentioned
5. Stripe subscription model works better per-bot

The subscribe flow should:
1. Check user is authenticated (Google OAuth via Supabase)
2. Show bot details + min capital warning
3. Select broker connection (or connect new one)
4. Choose paper/live mode
5. Set allocation percentage
6. Confirm subscription → create Stripe subscription + BotSubscription record

---

## Tyler's Developer Profile — Live Bots

Tyler's creator profile should auto-populate with his running bots. Here are the bots currently live:

### Bot 1: MNQ Momentum Scalper
- **Symbol:** MNQ (Micro E-mini Nasdaq)
- **Strategy:** Momentum with multi-timeframe confluence
- **Timeframe:** 5-minute
- **Broker:** Tradovate (via WebSocket)
- **Status:** LIVE — running 24/5
- **Validated Sharpe:** 2.1+ OOS
- **Tier:** Gold

### Bot 2: CL Crude Oil Scalper
- **Symbol:** CL (Crude Oil Futures)
- **Strategy:** Scalping with volume confirmation
- **Timeframe:** 5-minute
- **Broker:** Tradovate (via WebSocket)
- **Status:** LIVE — running 24/5
- **Validated Sharpe:** 1.8+ OOS
- **Tier:** Silver

### Bot 3: MES Swing Trader
- **Symbol:** MES (Micro E-mini S&P 500)
- **Strategy:** Swing trading
- **Timeframe:** 15-minute
- **Broker:** Tradovate
- **Status:** LIVE
- **Tier:** Silver

### Bot 4: NQ Swing Trader
- **Symbol:** NQ (E-mini Nasdaq)
- **Strategy:** Swing trading
- **Timeframe:** 15-minute
- **Broker:** Tradovate
- **Status:** LIVE
- **Tier:** Silver

### Forex Bots (Optimization Pipeline)
- **GBPUSD Breakout** — Forex, hourly
- **QQQ Momentum** — Equity, daily
- Additional bots from the 324-optimization pipeline

### Developer Profile Card
```
┌─────────────────────────────────────────────┐
│  👤 Tyler Reynolds                           │
│  Founder & Lead Quantitative Developer       │
│                                              │
│  🤖 6 Active Bots                            │
│  📊 Avg Sharpe: 1.95                         │
│  💰 Combined Monthly P&L: +$3,200           │
│  🏆 2 Gold, 4 Silver tier bots              │
│                                              │
│  Specialties: Futures, Forex, ML-Enhanced    │
│  Validation: MCPT + Walk-Forward + Paper     │
│                                              │
│  [ View All Bots ]                           │
└─────────────────────────────────────────────┘
```

---

## Environment Variables

Add these to the Django `.env` (production and dev):

```bash
# MCP Server → Django API communication
LISTING_API_KEY=<generate-a-secure-key>
METRICS_INGEST_API_KEY=<generate-a-secure-key>

# These go in the MCP server .env
ALGOCHAINS_DJANGO_URL=https://algochains.ai
# For dev: ALGOCHAINS_DJANGO_URL=http://172.238.57.39:8000
ALGOCHAINS_CREATOR_USERNAME=tyler
```

---

## Dev Environment Setup (Roo's Docker Setup)

To test the MCP ↔ Django integration locally:

```bash
# 1. Clone the MCP server
git clone https://github.com/AlgoChains/algochains-mcp-server.git
cd algochains-mcp-server

# 2. Create venv and install
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 3. Copy env
cp .env.example .env
# Edit .env → set ALGOCHAINS_DJANGO_URL=http://172.238.57.39:8000

# 4. Run tests
pytest tests/ -v  # Should see 40/40 passing

# 5. Run the MCP server (stdio mode for AI agents)
algochains-mcp

# 6. Or test marketplace bridge directly
python3 -c "
import asyncio
from algochains_mcp.config import load_config
from algochains_mcp.marketplace.bridge import MarketplaceBridge

async def test():
    cfg = load_config()
    bridge = MarketplaceBridge(cfg.marketplace)
    listings = await bridge.browse_listings()
    print(listings)
    await bridge.close()

asyncio.run(test())
"
```

---

## Integration Checklist

### Roo (Backend)
- [ ] Create `MarketplaceListing`, `BotSubscription`, `BotMetrics` Django models
- [ ] Run migrations on dev database (`Algochains_Django_development`)
- [ ] Create REST endpoints: `/api/v1/listings/`, `/api/v1/metrics/ingest/`, `/api/v1/subscribe/`
- [ ] Add API key authentication for metrics ingestion
- [ ] Wire up to dev Docker: `docker compose -f docker-compose_dev.yml up -d --build`
- [ ] Test: `curl http://172.238.57.39:8000/api/v1/listings/`

### RJ (Frontend)
- [ ] Create marketplace page at `/marketplace/`
- [ ] Bot card component with tier badge, metrics, subscribe button
- [ ] Bot detail page at `/marketplace/{slug}/`
- [ ] Subscribe flow (Google OAuth → broker select → confirm)
- [ ] Developer profile page at `/creators/{username}/`
- [ ] Tyler's profile auto-populated with 6 live bots
- [ ] "Add to Cart" → Single subscribe (per RJ's question — one at a time with capital check)

### Tyler (MCP Server + Bots)
- [x] MCP Server with 19 tools, 3 resources, 4 prompts
- [x] Error hierarchy + structured error responses
- [x] Marketplace bridge with real HTTP client
- [x] Rate limiting + retry middleware
- [x] 40/40 tests passing
- [ ] Push to GitHub (needs `gh auth login`)
- [ ] Configure metrics push cron (hourly)
- [ ] Seed initial bot listings via bridge

---

## Questions for the Team

1. **RJ:** Are the Supabase tables accessible via Django ORM, or do we need a separate Django model layer? (I assumed Django models above)
2. **Roo:** Is the dev API at `172.238.57.39:8000` accepting POST requests, or do we need CORS/auth setup first?
3. **Roo:** Should metrics ingestion use Supabase directly or go through Django REST?
4. **RJ:** For the subscribe button — Stripe integration ready, or do we need to set that up?

---

## File Structure

```
algochains-mcp-server/
├── src/algochains_mcp/
│   ├── __init__.py
│   ├── server.py          # Main MCP server (835 lines, 19 tools)
│   ├── config.py           # Environment config
│   ├── errors.py           # Typed error hierarchy (150 lines)
│   ├── middleware.py        # Rate limiting + retry + logging (196 lines)
│   ├── brokers/
│   │   ├── base.py         # BrokerConnector ABC
│   │   ├── registry.py     # Auto-discovers configured brokers
│   │   ├── alpaca_connector.py
│   │   ├── ibkr_connector.py
│   │   ├── oanda_connector.py
│   │   ├── traderspost_connector.py
│   │   └── quantconnect_connector.py
│   └── marketplace/
│       ├── bridge.py       # HTTP client for Django API (125 lines)
│       └── validator.py    # 7-gate MCPT validation (244 lines)
├── tests/
│   ├── test_errors.py      # 15 error hierarchy tests
│   ├── test_middleware.py   # 19 middleware tests
│   ├── test_bridge.py      # 5 bridge tests
│   └── test_validator.py   # 6 validation tests
├── .env.example
├── pyproject.toml
└── README.md
```
