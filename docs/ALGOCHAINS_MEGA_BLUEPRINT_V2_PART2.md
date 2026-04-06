# ALGOCHAINS MEGA BLUEPRINT V2 — PART 2

> **Sections 7-12:** V13 Alt Data, V14 Agent Swarm, V15 DeFi, V16 Cloud, V17 Multi-Broker, V18 Intent Intelligence
> **Continues from:** `ALGOCHAINS_MEGA_BLUEPRINT_V2.md` (Part 1)

---

## 7. V13: Alternative Data Marketplace

> **Module:** `src/algochains_mcp/alt_data/` | **Tools:** 10 | **Lines:** ~3,000

### 7.1 Tools (10)

| Tool | Description |
|------|-------------|
| `list_data_feeds` | Browse available alt data feeds with pricing and accuracy scores |
| `subscribe_feed` | Subscribe to a data feed (sentiment, satellite, SEC, options flow) |
| `get_sentiment` | Social/news sentiment for a symbol (Twitter/X, Reddit, StockTwits, FinBERT) |
| `analyze_sec_filing` | NLP analysis of SEC filings (10-K, 10-Q, 8-K) — extract risk factors, revenue guidance, management tone |
| `get_satellite_data` | Satellite/geospatial data: parking lot fill rates, shipping container counts, crop health |
| `get_options_flow` | Unusual options activity, GEX, dark pool prints, whale sweeps |
| `scrape_web` | Structured web scraping: job postings (Indeed/LinkedIn), app rankings, Glassdoor reviews |
| `correlate_signal` | Measure alt data → price predictive power via Granger causality + information coefficient |
| `publish_data_feed` | Vendors: publish a new data feed to marketplace |
| `get_feed_performance` | Historical accuracy metrics, Sharpe contribution, and subscriber count for a feed |

### 7.2 Classes

```python
class FeedRegistry:
    async def list_feeds(self, data_type=None, sort_by="accuracy") -> list[dict]
    async def subscribe(self, user_id, feed_id) -> dict
    async def publish_feed(self, vendor_id, feed_config) -> dict

class SentimentEngine:
    async def get_sentiment(self, symbol, sources=None, lookback="24h") -> dict
    async def aggregate_sentiment(self, symbols, weighted=True) -> dict
    # Uses FinBERT on Desktop GPU (port 8002) for high-quality NLP

class SECFilingParser:
    async def analyze_filing(self, ticker, filing_type, year=None) -> dict
    async def extract_risk_factors(self, filing_id) -> list[dict]
    async def detect_guidance_changes(self, ticker) -> dict

class SatelliteAdapter:
    async def get_data(self, data_type, location=None, symbol=None) -> dict

class OptionsFlowEngine:
    async def get_unusual_activity(self, symbol=None, min_premium=100000) -> list[dict]
    async def get_gex(self, symbol) -> dict
    async def get_dark_pool_prints(self, symbol, min_size=10000) -> list[dict]
    # Integrates with existing UnusualWhales flow service

class SignalCorrelationEngine:
    async def correlate(self, signal_id, symbol, method="granger", lags=5) -> dict
    async def backtest_signal(self, signal_id, strategy_params) -> dict
```

### 7.3 Integration with V10 ML Engine

Alt data feeds pipe directly into V10's FeatureEngine:
```python
# Example: sentiment + options flow as ML features
await create_feature_set(
    symbols=["AAPL"],
    timeframe="1h",
    features=[
        {"name": "sentiment_score", "type": "alt_data", "params": {"feed": "twitter_sentiment"}},
        {"name": "gex_level", "type": "alt_data", "params": {"feed": "options_gex"}},
        {"name": "dark_pool_ratio", "type": "alt_data", "params": {"feed": "dark_pool"}},
        {"name": "rsi_14", "type": "indicator", "params": {"period": 14}},
        {"name": "bb_width", "type": "indicator", "params": {"period": 20, "std": 2}},
    ],
    target="return_1h",
    horizon="1h"
)
```

### 7.4 Tables

```sql
CREATE TABLE IF NOT EXISTS data_feeds (
    id TEXT PRIMARY KEY, vendor_id TEXT NOT NULL, name TEXT NOT NULL,
    description TEXT, data_type TEXT CHECK (data_type IN ('sentiment','satellite','sec','options','web','custom')),
    price_monthly REAL, symbols_covered TEXT, accuracy_score REAL,
    subscribers INTEGER DEFAULT 0, status TEXT DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS feed_subscriptions (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
    feed_id TEXT NOT NULL REFERENCES data_feeds(id),
    status TEXT DEFAULT 'active', started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sentiment_snapshots (
    id TEXT PRIMARY KEY, symbol TEXT NOT NULL, source TEXT NOT NULL,
    sentiment_score REAL, magnitude REAL, volume INTEGER,
    captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_sentiment_symbol ON sentiment_snapshots(symbol, captured_at DESC);

CREATE TABLE IF NOT EXISTS options_flow_events (
    id TEXT PRIMARY KEY, symbol TEXT NOT NULL, event_type TEXT NOT NULL,
    strike REAL, expiry TEXT, premium REAL, volume INTEGER,
    oi_change INTEGER, side TEXT, captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 8. V14: Autonomous Agent Swarm

> **Module:** `src/algochains_mcp/agent_swarm/` | **Tools:** 8 | **Lines:** ~2,500

### 8.1 Tools (8)

| Tool | Description |
|------|-------------|
| `create_agent` | Create autonomous trading agent with strategy spec, risk params, broker binding |
| `start_agent` | Activate agent for paper or live trading |
| `stop_agent` | Graceful stop: flatten positions first, then halt |
| `get_agent_status` | Current state: positions, P&L, health, recent decisions, uptime |
| `list_agents` | All agents with performance: Sharpe, win rate, max DD, total P&L |
| `create_swarm` | Orchestrate multiple agents with capital allocation strategy |
| `evolve_strategy` | Genetic algorithm: tournament selection, crossover, mutation on strategy params |
| `get_swarm_dashboard` | Swarm metrics: aggregate P&L, Sharpe, inter-agent correlation, capital utilization |

### 8.2 Architecture

```
Swarm Orchestrator
    │
    ├──► Capital Allocator (Kelly / Risk-Parity / Momentum-weighted)
    │         │
    │    ┌────▼────┐  ┌────────┐  ┌────────┐  ┌────────┐
    │    │ Agent A  │  │Agent B │  │Agent C │  │Agent D │
    │    │ MNQ RSI  │  │CL BB   │  │SPY EMA │  │BTC RL  │
    │    │ Alpaca   │  │Tradovte│  │Schwab  │  │Coinbase│
    │    └────┬─────┘  └───┬────┘  └───┬────┘  └───┬────┘
    │         │            │           │            │
    │    ┌────▼────────────▼───────────▼────────────▼────┐
    │    │          Agent Communicator                     │
    │    │  - Correlation alerts (agents drifting together)│
    │    │  - Capital rebalance signals                    │
    │    │  - Emergency stop propagation                   │
    │    └────────────────────────────────────────────────┘
    │
    ├──► Health Monitor (auto-restart dead agents, alert on anomalies)
    │
    └──► Strategy Evolver
              │
              ├── Generation 0: 100 random strategy variants
              ├── Backtest all on OOS data
              ├── Tournament: top 20% survive
              ├── Crossover: breed survivors
              ├── Mutate: random parameter perturbation
              ├── Generation 1: 100 evolved variants
              └── Repeat until Sharpe plateau (typically 50-200 generations)
```

### 8.3 Classes

```python
class AgentManager:
    async def create(self, name, strategy_spec, risk_params, broker, mode="paper") -> dict
    async def start(self, agent_id) -> dict
    async def stop(self, agent_id, flatten=True) -> dict
    async def get_status(self, agent_id) -> dict
    async def list_agents(self, status=None) -> list[dict]

class SwarmOrchestrator:
    async def create_swarm(self, name, agent_ids, allocation, total_capital) -> dict
    async def get_dashboard(self, swarm_id) -> dict
    async def rebalance(self, swarm_id) -> dict

class StrategyEvolver:
    async def evolve(self, base_strategy, population=100, generations=100,
                     mutation_rate=0.1, crossover_rate=0.7, fitness="oos_sharpe") -> dict
    async def get_generation(self, evolution_id, gen_number) -> list[dict]
    async def get_best(self, evolution_id, top_n=5) -> list[dict]

class AgentHealthMonitor:
    async def check_all(self) -> dict
    async def auto_restart(self, agent_id) -> dict
    async def set_alert_rules(self, rules) -> dict

class CapitalAllocator:
    async def allocate(self, swarm_id, method="kelly") -> dict
    # Methods: equal, kelly, risk_parity, momentum, inverse_volatility

class AgentCommunicator:
    async def broadcast(self, swarm_id, message_type, payload) -> dict
    async def get_correlation_matrix(self, swarm_id) -> dict
```

### 8.4 Self-Healing Protocol

```python
AGENT_HEALTH_RULES = {
    "max_consecutive_losses": 5,       # Pause agent, alert
    "max_drawdown_pct": 15,            # Stop agent, flatten
    "max_daily_loss_usd": 500,         # Stop agent for day
    "heartbeat_timeout_sec": 300,      # Auto-restart
    "correlation_threshold": 0.85,     # Warn: agents too similar
    "min_sharpe_rolling_30d": 0.5,     # Demote to paper if degraded
}
```

### 8.5 Tables

```sql
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, strategy_spec TEXT NOT NULL,
    risk_params TEXT NOT NULL,
    status TEXT DEFAULT 'stopped' CHECK (status IN ('stopped','running','paused','error')),
    mode TEXT DEFAULT 'paper' CHECK (mode IN ('paper','live')),
    broker TEXT NOT NULL, capital_allocated REAL, total_pnl REAL DEFAULT 0,
    trades_count INTEGER DEFAULT 0, sharpe_30d REAL, max_dd REAL, win_rate REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP, stopped_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS swarms (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, agent_ids TEXT NOT NULL,
    allocation_method TEXT DEFAULT 'equal',
    total_capital REAL, status TEXT DEFAULT 'stopped',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS strategy_generations (
    id TEXT PRIMARY KEY, evolution_id TEXT NOT NULL, generation INTEGER NOT NULL,
    strategy_spec TEXT NOT NULL, fitness_score REAL, oos_sharpe REAL,
    oos_max_dd REAL, parent_ids TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_generations ON strategy_generations(evolution_id, generation, fitness_score DESC);
```

---

## 9. V15: DeFi & Cross-Chain Execution

> **Module:** `src/algochains_mcp/defi/` | **Tools:** 10 | **Lines:** ~2,500

### 9.1 Tools (10)

| Tool | Description |
|------|-------------|
| `swap_tokens` | DEX swap with MEV protection (Flashbots/Jito), slippage limit, best-route |
| `get_pool_liquidity` | Liquidity across DEXes for a pair (Uniswap V3, Raydium, Jupiter) |
| `add_liquidity` | Add to pool with impermanent loss estimation + range optimization |
| `remove_liquidity` | Remove liquidity, auto-claim fees |
| `get_yield_opportunities` | Scan all chains for yield, ranked by risk-adjusted APY |
| `bridge_assets` | Cross-chain bridge (ETH↔SOL↔ARB↔BASE↔AVAX) with best-path routing |
| `get_onchain_analytics` | Whale movements, exchange flows, gas prices, MEV activity |
| `create_defi_strategy` | Automated: yield rotation, delta-neutral farming, basis trade |
| `get_mev_protection` | MEV analysis: sandwich attacks detected, protection status |
| `get_defi_portfolio` | Aggregated DeFi positions across all chains |

### 9.2 Classes

```python
class DEXAggregator:
    async def swap(self, chain, from_token, to_token, amount, slippage_pct=0.5,
                   mev_protect=True) -> dict
    async def get_quote(self, chain, from_token, to_token, amount) -> dict
    async def get_liquidity(self, chain, pair) -> dict
    # Aggregates: 1inch, Jupiter, Paraswap, 0x

class LiquidityManager:
    async def add(self, chain, pool, token_a_amount, token_b_amount, range=None) -> dict
    async def remove(self, chain, position_id) -> dict
    async def estimate_il(self, pool, price_change_pct) -> dict

class YieldScanner:
    async def scan(self, chains=None, min_apy=5.0, max_risk="medium") -> list[dict]
    async def get_historical_yield(self, pool_id, days=30) -> dict

class BridgeRouter:
    async def bridge(self, from_chain, to_chain, token, amount) -> dict
    async def get_routes(self, from_chain, to_chain, token, amount) -> list[dict]
    # Backends: Wormhole, LayerZero, Stargate, Across

class OnchainAnalytics:
    async def whale_movements(self, chain, token=None, min_usd=100000) -> list[dict]
    async def exchange_flows(self, exchange, token, direction="both") -> dict
    async def gas_tracker(self, chain) -> dict

class MEVProtector:
    async def analyze_tx(self, chain, tx_hash) -> dict
    async def protect_swap(self, chain, swap_params) -> dict
    # Flashbots (ETH), Jito (SOL), MEV Blocker
```

### 9.3 Multi-Chain Support Matrix

| Chain | DEX | Bridge | Yield | MEV Protection |
|-------|-----|--------|-------|----------------|
| Ethereum | Uniswap V3, SushiSwap, Curve | Wormhole, LayerZero | Aave, Compound, Lido | Flashbots Protect |
| Solana | Jupiter, Raydium, Orca | Wormhole | Marinade, Drift | Jito |
| Arbitrum | Uniswap V3, Camelot, GMX | Stargate, Across | GMX, Pendle | Flashbots |
| Base | Uniswap V3, Aerodrome | Across, Stargate | Aave, Morpho | Flashbots |
| Avalanche | TraderJoe, Pangolin | Wormhole | Benqi, Aave | — |

### 9.4 Tables

```sql
CREATE TABLE IF NOT EXISTS defi_positions (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, chain TEXT NOT NULL,
    protocol TEXT NOT NULL, position_type TEXT CHECK (position_type IN ('lp','stake','lend','borrow','farm')),
    tokens TEXT NOT NULL, amounts TEXT NOT NULL, value_usd REAL,
    apy_current REAL, il_pct REAL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bridge_transactions (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
    from_chain TEXT NOT NULL, to_chain TEXT NOT NULL,
    token TEXT NOT NULL, amount REAL, bridge_provider TEXT,
    status TEXT DEFAULT 'pending', fee_usd REAL,
    initiated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, completed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS defi_yields (
    id TEXT PRIMARY KEY, chain TEXT NOT NULL, protocol TEXT NOT NULL,
    pool TEXT NOT NULL, apy REAL, tvl_usd REAL, risk_score TEXT,
    captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 10. V16: AlgoChains Cloud — Full SaaS Platform

> **Module:** `src/algochains_mcp/cloud/` | **Tools:** 6 | **Lines:** ~3,000

### 10.1 Tools (6)

| Tool | Description |
|------|-------------|
| `deploy_to_cloud` | Deploy strategy/agent to managed K8s infrastructure |
| `scale_deployment` | Auto-scale compute (GPU, CPU) based on load/schedule |
| `get_cloud_status` | Infrastructure health, cost, performance per deployment |
| `configure_region` | Multi-region: US-East, US-West, EU-West, APAC (Singapore, Tokyo) |
| `get_usage_billing` | Detailed usage breakdown: compute, data, API calls, storage |
| `create_api_key_enterprise` | Enterprise API key with custom rate limits, IP whitelist, audit log |

### 10.2 Architecture

```
┌─────────────────────────────────────────────────────────┐
│  AlgoChains Cloud                                        │
│                                                          │
│  ┌──────────────────────────────────────────────────┐   │
│  │ Kubernetes Orchestration (EKS / GKE)             │   │
│  │                                                   │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐       │   │
│  │  │ Strategy │  │ Agent    │  │ Data     │       │   │
│  │  │ Pods     │  │ Pods     │  │ Pipeline │       │   │
│  │  │ (CPU/GPU)│  │ (CPU/GPU)│  │ Pods     │       │   │
│  │  └──────────┘  └──────────┘  └──────────┘       │   │
│  └──────────────────────────────────────────────────┘   │
│                                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────┐      │
│  │ Global   │  │ Billing  │  │ Enterprise API   │      │
│  │ Edge CDN │  │ (Stripe) │  │ (OpenAPI 3.0)    │      │
│  └──────────┘  └──────────┘  └──────────────────┘      │
│                                                          │
│  Regions: us-east-1, us-west-2, eu-west-1, ap-se-1     │
└─────────────────────────────────────────────────────────┘
```

### 10.3 Classes

```python
class CloudOrchestrator:
    async def deploy(self, spec_id, tier="standard", region="us-east-1") -> dict
    async def scale(self, deployment_id, replicas=None, gpu=False) -> dict
    async def undeploy(self, deployment_id) -> dict
    async def get_status(self, deployment_id=None) -> dict

class AutoScaler:
    async def configure(self, deployment_id, min_replicas=1, max_replicas=10,
                       cpu_target=70, schedule=None) -> dict

class BillingEngine:
    async def get_usage(self, tenant_id, period="current_month") -> dict
    async def create_invoice(self, tenant_id) -> dict
    async def configure_plan(self, tenant_id, plan) -> dict
    # Stripe integration for payment processing

class RegionManager:
    async def list_regions(self) -> list[dict]
    async def configure_region(self, deployment_id, regions) -> dict
    async def get_latency_map(self) -> dict

class EnterpriseAPI:
    async def create_key(self, tenant_id, name, scopes, rate_limit=1000,
                        ip_whitelist=None) -> dict
    async def revoke_key(self, key_id) -> dict
    async def get_usage(self, key_id) -> dict
```

### 10.4 Pricing Tiers

| Tier | Price | Includes |
|------|-------|---------|
| **Starter** | $49/mo | 5 strategies, 1 broker, community signals, basic risk |
| **Pro** | $149/mo | 25 strategies, 3 brokers, ML models (CPU), social trading |
| **Institutional** | $499/mo | Unlimited strategies, all brokers, GPU, RL agents, FIX protocol |
| **Enterprise** | Custom | Dedicated infra, SLA, white-label, custom integrations |

### 10.5 Tables

```sql
CREATE TABLE IF NOT EXISTS cloud_deployments (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, spec_type TEXT NOT NULL,
    spec_id TEXT NOT NULL, tier TEXT NOT NULL, region TEXT NOT NULL,
    replicas INTEGER DEFAULT 1, gpu BOOLEAN DEFAULT FALSE,
    status TEXT DEFAULT 'deploying', cost_per_hour REAL,
    deployed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS usage_records (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
    resource_type TEXT NOT NULL, quantity REAL, unit TEXT,
    cost REAL, recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS enterprise_api_keys (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT NOT NULL,
    key_hash TEXT NOT NULL, scopes TEXT, rate_limit INTEGER DEFAULT 1000,
    ip_whitelist TEXT, requests_total INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, revoked_at TIMESTAMP
);
```

---

## 11. V17: Multi-Broker Expansion + IDE Scaling + State Awareness

> **Module:** `src/algochains_mcp/tool_gateway/` + `state_engine/` + `brokers/` | **Tools:** 6 | **Lines:** ~4,000

### 11.1 Pillar 1 — Multi-Broker Expansion (20+ Brokers)

#### Existing BrokerConnector ABC Enhancement

Add to `brokers/base.py`:

```python
# New optional methods (default raises NotImplementedError)
async def get_historical(self, symbol, interval="1d", start=None, end=None) -> list[dict]
async def stream_quotes(self, symbols: list[str]) -> AsyncIterator[Quote]
async def get_option_chain(self, symbol, expiration=None) -> list[dict]
async def get_order_book(self, symbol, depth=10) -> dict
async def get_transactions(self, start=None, end=None) -> list[dict]
async def refresh_auth(self) -> bool
def normalize_symbol(self, symbol: str) -> str
def denormalize_symbol(self, symbol: str) -> str

@property
def capabilities(self) -> dict:
    return {
        "streaming": False, "options": False, "futures": False,
        "crypto": False, "forex": False, "order_book": False,
        "historical": False, "paper_trading": False,
        "bracket_orders": False, "fractional_shares": False,
    }
```

#### New Connectors Priority

| Tier | Broker | Auth | Why |
|------|--------|------|-----|
| **1** | Tradovate | OAuth2+WS | Our primary live broker |
| **1** | Coinbase | API Key+HMAC | Top crypto exchange |
| **1** | Schwab | OAuth2 | Largest US retail broker |
| **2** | Tastytrade | OAuth2 | Options-focused, growing fast |
| **2** | Binance/Kraken/Bybit | API Key | Top crypto via CCXT |
| **2** | TradeStation | OAuth2 | Futures + stocks |
| **3** | Webull/eTrade/Robinhood | Various | Mass market |
| **4** | 100+ crypto | Various | Single CCXT meta-connector |

#### CCXT Meta-Connector

```python
class CCXTConnector(BrokerConnector):
    """One connector → 100+ crypto exchanges via ccxt."""
    name = "ccxt"

    def __init__(self, exchange_id: str, api_key: str, secret: str, **kwargs):
        self.exchange_id = exchange_id
        self.name = f"ccxt_{exchange_id}"
        self.supported_asset_classes = [AssetClass.CRYPTO]
        self._exchange = getattr(ccxt, exchange_id)({
            'apiKey': api_key, 'secret': secret,
            'enableRateLimit': True, **kwargs
        })

    async def connect(self) -> bool:
        await self._exchange.load_markets()
        return True

    async def place_order(self, symbol, side, qty, order_type=OrderType.MARKET,
                         limit_price=None, **kwargs) -> Order:
        ccxt_type = "limit" if order_type == OrderType.LIMIT else "market"
        raw = await self._exchange.create_order(
            self.denormalize_symbol(symbol), ccxt_type, side.value, qty, limit_price
        )
        return self._normalize_order(raw)

    def normalize_symbol(self, symbol: str) -> str:
        return symbol.replace("/", "")  # BTC/USDT -> BTCUSDT

    def denormalize_symbol(self, symbol: str) -> str:
        # Reverse: BTCUSDT -> BTC/USDT (exchange-specific)
        return self._exchange.market(symbol)['symbol']
```

#### Tradovate MCP Connector

```python
class TradovateConnector(BrokerConnector):
    """Tradovate futures connector — ported from Control Tower."""
    name = "tradovate"
    supported_asset_classes = [AssetClass.FUTURES]

    def __init__(self, cid: str, secret: str, env: str = "live"):
        self.cid = cid
        self.secret = secret
        self.base_url = "https://live.tradovateapi.com" if env == "live" \
                       else "https://demo.tradovateapi.com"
        # NEVER use tradovate_token_auto_refresh.py — use Token Guardian pattern

    @property
    def capabilities(self) -> dict:
        return {
            "streaming": True, "futures": True, "bracket_orders": True,
            "order_book": True, "historical": True, "paper_trading": True,
        }

    async def connect(self) -> bool:
        # OAuth2 authentication, then WebSocket for streaming
        ...

    async def place_order(self, symbol, side, qty, order_type=OrderType.MARKET,
                         limit_price=None, stop_price=None, **kwargs) -> Order:
        # Normalize: MNQ -> MNQZ5 (front-month symbology)
        tradovate_symbol = self.denormalize_symbol(symbol)
        ...
```

### 11.2 Pillar 2 — Dynamic Toolset Gateway

#### The Problem (Hard Numbers)

| Client | Tool Limit | Our 129 Tools |
|--------|-----------|---------------|
| Cursor | 40 max | 69% invisible |
| GitHub Copilot | 128 max | Barely fits |
| Claude Desktop | ~120 | 55K+ tokens |
| Claude Code | No limit | 65K tokens (32% context) |

#### The Solution: 3 Meta-Tools

```python
@dataclass
class ToolEntry:
    name: str
    category: str
    description: str        # One-liner for search results
    full_description: str   # Detailed for describe_tools
    input_schema: dict      # JSON Schema for describe_tools
    tags: list[str]
    embedding: list[float]  # Pre-computed (sentence-transformers)

class DynamicToolGateway:
    def __init__(self):
        self.registry: dict[str, ToolEntry] = {}
        self.embedder = SentenceTransformer('all-MiniLM-L6-v2')

    async def search_tools(self, query: str, category: str = None,
                          limit: int = 10) -> list[dict]:
        """Semantic search + category filter + state context injection."""
        results = self._semantic_search(query, limit)
        if category:
            results = [r for r in results if r.category == category]

        # GENIUS: inject relevant state context
        context = await self._get_relevant_context(query)
        return {"tools": results, "context": context}

    async def describe_tools(self, tool_names: list[str]) -> list[dict]:
        """Return full schemas — lazy loaded, not pre-sent."""
        return [self.registry[n].to_full_schema() for n in tool_names
                if n in self.registry]

    async def execute_tool(self, tool_name: str, arguments: dict) -> dict:
        """Dispatch through full middleware pipeline."""
        # Reuse existing call_tool pipeline: sanitize → circuit → rate limit →
        # semaphore → execute → size guard → record
        return await _dispatch_tool(tool_name, arguments, self.registry)
```

**Token impact: 55,000 → 1,500 (97.3% reduction)**

#### Dual-Mode Support

```bash
ALGOCHAINS_TOOL_MODE=dynamic   # 3 meta-tools (default for Cursor/Claude Desktop)
ALGOCHAINS_TOOL_MODE=static    # All 129+ tools (for Claude Code/unlimited clients)
ALGOCHAINS_TOOLSETS=all        # Which categories to expose in static mode
```

### 11.3 Pillar 3 — Deep State Awareness

#### New MCP Resources

| Resource URI | Description | Update Frequency |
|-------------|-------------|-----------------|
| `algochains://portfolio/unified` | Cross-broker equity, cash, P&L, positions | On trade / 60s |
| `algochains://session/journal` | Today's trades, running P&L, win rate, streak | On trade |
| `algochains://market/context` | VIX, regime (bull/bear/range), market status, next event | 60s |
| `algochains://brokers/capabilities` | Per-broker feature matrix | On connect |
| `algochains://admin/tool-analytics` | Tool usage counts, error rates, avg latency | 5 min |

#### Context Injection Example

When user says "buy AAPL", `search_tools` automatically injects:
```json
{
  "tools": [
    {"name": "place_order", "description": "Place order on any connected broker"},
    {"name": "execute_algo_order", "description": "TWAP/VWAP algo execution"}
  ],
  "context": {
    "aapl_positions": [
      {"broker": "alpaca", "qty": 50, "avg_entry": 185.30}
    ],
    "brokers_supporting_stocks": ["alpaca", "ibkr", "schwab"],
    "buying_power": {"alpaca": 45000, "schwab": 120000},
    "market_status": "open",
    "aapl_last": 192.45
  }
}
```

This eliminates 4-5 discovery calls the LLM would otherwise make.

#### Unified Portfolio Resource Implementation

```python
class UnifiedStateEngine:
    async def get_portfolio(self) -> dict:
        """Aggregate positions, equity, P&L across ALL connected brokers."""
        brokers = self.registry.get_connected_brokers()
        portfolio = {"total_equity": 0, "total_pnl": 0, "positions": [], "brokers": []}

        for broker in brokers:
            try:
                account = await asyncio.wait_for(broker.get_account(), timeout=10)
                positions = await asyncio.wait_for(broker.get_positions(), timeout=10)
                portfolio["total_equity"] += account.equity
                portfolio["brokers"].append(account.to_dict())
                for pos in positions:
                    portfolio["positions"].append(pos.to_dict())
                    portfolio["total_pnl"] += pos.unrealized_pnl
            except Exception as e:
                portfolio["brokers"].append({"broker": broker.name, "status": "error", "error": str(e)})

        return portfolio

    async def get_session_journal(self) -> dict:
        """Today's trading activity across all brokers."""
        today = datetime.utcnow().date()
        journal = {"date": str(today), "trades": [], "wins": 0, "losses": 0, "pnl": 0}
        # Query all broker transaction logs for today
        ...
        return journal

    async def get_market_context(self) -> dict:
        """Current market regime and key indicators."""
        return {
            "status": "open",  # pre_market, open, after_hours, closed
            "vix": await self._get_vix(),
            "regime": await self._detect_regime(),  # bull, bear, range, volatile
            "next_event": await self._next_economic_event(),
            "spy_change_pct": await self._get_spy_change(),
        }
```

### 11.4 Tables

```sql
CREATE TABLE IF NOT EXISTS broker_connections (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, broker TEXT NOT NULL,
    connector_type TEXT NOT NULL, config_encrypted TEXT NOT NULL,
    capabilities TEXT, status TEXT DEFAULT 'disconnected',
    last_health_check TIMESTAMP, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tool_analytics (
    tool_name TEXT NOT NULL, called_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    duration_ms REAL, success BOOLEAN, error TEXT, user_id TEXT
);
CREATE INDEX idx_tool_analytics ON tool_analytics(tool_name, called_at DESC);

CREATE TABLE IF NOT EXISTS session_journal (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, date DATE NOT NULL,
    broker TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT, qty REAL,
    fill_price REAL, pnl REAL, notes TEXT,
    executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 12. V18: Intent-Based Trading + Autonomous Intelligence

> **Module:** `src/algochains_mcp/intent_engine/` | **Tools:** 8 | **Lines:** ~3,500

### 12.1 The Vision

**Stop commanding. Start intending.**

| Before (V1-V17) | After (V18) |
|-----------------|-------------|
| "Place a market order to buy 100 AAPL on Alpaca" | "Get me $10K AI exposure, max 2% per stock" |
| "Run backtest on RSI strategy for SPY daily" | "Find me a momentum strategy that works in high-VIX" |
| "Close my TSLA position on Schwab" | "Reduce tech exposure by 30%" |
| "What's my P&L today?" | "Am I on track for my monthly target?" |

### 12.2 Tools (8)

| Tool | Description |
|------|-------------|
| `execute_intent` | Natural language intent → multi-step plan → execute (with approval gate) |
| `get_intent_plan` | Dry run: preview what the system would do, without executing |
| `approve_intent` | Approve a pending intent plan |
| `get_intent_history` | Past intents, their plans, execution results, and lessons learned |
| `create_shadow_portfolio` | Forward-test a strategy in real-time without risking capital |
| `get_shadow_results` | Shadow vs live performance comparison |
| `evolve_strategies` | Genetic crossover of top-performing Strategy DNA |
| `detect_arbitrage` | Cross-broker price/spread arbitrage opportunities |

### 12.3 Intent Engine Architecture

```
User: "Get me $10K exposure to AI stocks, max 2% per stock, cheapest broker"
                    │
                    ▼
┌────────────────────────────────────────────────────────────────────┐
│  INTENT PARSER (LLM-powered)                                       │
│                                                                     │
│  Extracts:                                                          │
│  - Goal: "buy equities" ($10K notional)                            │
│  - Universe: "AI stocks" → NVDA, MSFT, GOOGL, META, AMD, AVGO...  │
│  - Constraint: max 2% per stock ($200 each → 50 stocks)            │
│  - Preference: cheapest broker → Alpaca ($0 commission)            │
│  - Risk: not specified → use default profile                       │
│  - Time: not specified → execute now                               │
└──────────────────────┬─────────────────────────────────────────────┘
                       ▼
┌────────────────────────────────────────────────────────────────────┐
│  CONSTRAINT SOLVER                                                  │
│                                                                     │
│  1. Screen AI universe → 47 stocks pass (market cap > $10B)        │
│  2. Optimize: minimize commission + slippage                       │
│  3. Check compliance: wash trade? restricted? concentration?       │
│  4. Check risk: VaR addition < max, factor exposure balanced       │
│  5. Select broker: Alpaca (zero commission, has all stocks)        │
│  6. Size positions: $200 each × 50 = $10,000                      │
│  7. Order type: limit orders, staggered over 15min (mini-TWAP)    │
└──────────────────────┬─────────────────────────────────────────────┘
                       ▼
┌────────────────────────────────────────────────────────────────────┐
│  PLAN (presented to user for approval)                              │
│                                                                     │
│  "I'll buy 50 AI stocks on Alpaca ($0 commission):                 │
│   NVDA: 1.05 shares ($200), MSFT: 0.47 shares ($200), ...         │
│   Total: $10,000 across 50 positions                               │
│   Est. slippage: $12 (0.12%)                                       │
│   VaR impact: +0.3% (within limits)                                │
│   Execution: limit orders, 15-min TWAP"                            │
│                                                                     │
│  [APPROVE] [MODIFY] [CANCEL]                                       │
└──────────────────────┬─────────────────────────────────────────────┘
                       ▼ (on approve)
┌────────────────────────────────────────────────────────────────────┐
│  EXECUTOR                                                           │
│  - Places 50 limit orders via Alpaca connector                     │
│  - Monitors fills, adjusts unfilled after 15min                    │
│  - Logs to session journal                                         │
│  - Updates unified portfolio state                                  │
│  - Records intent → plan → outcome for learning                   │
└────────────────────────────────────────────────────────────────────┘
```

### 12.4 Shadow Portfolio — Forward-Test Without Risk

```python
class ShadowPortfolioEngine:
    """Run strategies in parallel with live market data, paper fills."""

    async def create_shadow(self, name, strategy_spec, broker, capital) -> dict:
        """Creates a paper-traded mirror of a strategy."""
        ...

    async def get_results(self, shadow_id) -> dict:
        """Compare shadow vs live performance."""
        return {
            "shadow": {"sharpe": 2.1, "pnl": 1250, "max_dd": 3.2, "trades": 45},
            "live":   {"sharpe": 1.8, "pnl": 980,  "max_dd": 4.1, "trades": 38},
            "difference": {"sharpe_delta": 0.3, "pnl_delta": 270},
            "recommendation": "Shadow outperforms. Consider promoting to live."
        }

    async def auto_promote(self, shadow_id, min_sharpe=2.0, min_trades=30,
                          max_dd=10.0) -> dict:
        """Auto-promote shadow → live when quality gates pass."""
        ...
```

**MCP Resource:** `algochains://shadow/portfolio`

### 12.5 Strategy DNA — Genetic Evolution

```python
@dataclass
class Gene:
    name: str           # e.g. "RSI_Oversold", "EMA_CrossUp"
    gene_type: str      # "entry", "exit", "filter", "sizing"
    params: dict        # Numeric parameters

@dataclass
class StrategyDNA:
    entry_genes: list[Gene]     # RSI_Oversold + Volume_Spike
    exit_genes: list[Gene]      # Trailing_Stop + Time_Exit
    filter_genes: list[Gene]    # Trend_Filter + Volatility_Filter
    sizing_gene: Gene           # Kelly_Half

    def crossover(self, other: 'StrategyDNA') -> 'StrategyDNA':
        """Uniform crossover: randomly pick genes from each parent."""
        child_entries = [random.choice([a, b]) for a, b in
                        zip_longest(self.entry_genes, other.entry_genes)]
        child_exits = [random.choice([a, b]) for a, b in
                      zip_longest(self.exit_genes, other.exit_genes)]
        return StrategyDNA(
            entry_genes=[g for g in child_entries if g],
            exit_genes=[g for g in child_exits if g],
            filter_genes=random.choice([self.filter_genes, other.filter_genes]),
            sizing_gene=random.choice([self.sizing_gene, other.sizing_gene]),
        )

    def mutate(self, rate: float = 0.1) -> 'StrategyDNA':
        """Gaussian mutation of numeric params."""
        for gene in self.entry_genes + self.exit_genes + self.filter_genes:
            for key, val in gene.params.items():
                if random.random() < rate and isinstance(val, (int, float)):
                    gene.params[key] = val * random.gauss(1.0, 0.2)
        return self

    def fitness(self, backtest: dict) -> float:
        """OOS Sharpe * (1 - MaxDD/100) — penalizes drawdown."""
        return backtest["oos_sharpe"] * (1 - backtest["max_dd"] / 100)
```

### 12.6 Cross-Broker Arbitrage Detection

```python
class ArbitrageDetector:
    """Detect price discrepancies across connected brokers."""

    async def scan(self, symbols: list[str] = None, min_spread_bps: float = 5.0) -> list[dict]:
        """
        For each symbol, get quotes from all supporting brokers.
        Flag when bid on Broker A > ask on Broker B (risk-free profit).

        Common arbitrage types:
        1. Cross-exchange crypto: BTC price differs by 0.1-0.5% across exchanges
        2. Futures basis: Futures price vs spot (cash-and-carry)
        3. ETF NAV: ETF price vs underlying basket value
        4. ADR spread: US-listed ADR vs home exchange stock
        """
        opportunities = []
        for symbol in symbols:
            quotes = {}
            for broker in self.get_brokers_for_symbol(symbol):
                try:
                    q = await asyncio.wait_for(broker.get_quote(symbol), timeout=5)
                    quotes[broker.name] = q
                except Exception:
                    continue

            # Check all broker pairs for arbitrage
            for (b1, q1), (b2, q2) in combinations(quotes.items(), 2):
                if q1.bid > q2.ask:
                    spread_bps = (q1.bid - q2.ask) / q2.ask * 10000
                    if spread_bps >= min_spread_bps:
                        opportunities.append({
                            "symbol": symbol,
                            "buy_broker": b2, "buy_price": q2.ask,
                            "sell_broker": b1, "sell_price": q1.bid,
                            "spread_bps": round(spread_bps, 2),
                            "est_profit_per_unit": round(q1.bid - q2.ask, 4),
                        })

        return sorted(opportunities, key=lambda x: -x["spread_bps"])
```

### 12.7 Tables

```sql
CREATE TABLE IF NOT EXISTS intents (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
    raw_text TEXT NOT NULL, parsed_goal TEXT, parsed_constraints TEXT,
    plan TEXT, plan_status TEXT DEFAULT 'draft' CHECK (plan_status IN ('draft','approved','executing','completed','cancelled')),
    execution_result TEXT, lessons_learned TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    approved_at TIMESTAMP, completed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS shadow_portfolios (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL,
    strategy_spec TEXT NOT NULL, broker TEXT, capital REAL,
    status TEXT DEFAULT 'active', pnl REAL DEFAULT 0,
    sharpe REAL, max_dd REAL, trades_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS strategy_dna (
    id TEXT PRIMARY KEY, evolution_id TEXT,
    entry_genes TEXT NOT NULL, exit_genes TEXT NOT NULL,
    filter_genes TEXT, sizing_gene TEXT,
    generation INTEGER, fitness REAL, oos_sharpe REAL,
    parent_ids TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS arbitrage_opportunities (
    id TEXT PRIMARY KEY, symbol TEXT NOT NULL,
    buy_broker TEXT, buy_price REAL, sell_broker TEXT, sell_price REAL,
    spread_bps REAL, detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    acted_on BOOLEAN DEFAULT FALSE
);
```

---

> **END OF PART 2** — Continue to `ALGOCHAINS_MEGA_BLUEPRINT_V2_PART3.md`
