# ALGOCHAINS MEGA BLUEPRINT V2 — PART 3

> **Sections 13-19:** Massive Partnership, Genius Layer, Implementation Instructions, Code Generation Rules, Quality Gates, Deployment Playbook, The Mega Prompt V2
> **Continues from:** `ALGOCHAINS_MEGA_BLUEPRINT_V2_PART2.md`

---

## 13. MASSIVE.COM ENTERPRISE PARTNERSHIP — WHITE-LABEL MARKET DATA LAYER

### 13.1 Partnership Overview

AlgoChains holds an **enterprise license and white-label reselling partnership** with Massive.com — the premier financial market data platform. This is not a third-party integration; Massive is a **first-class data spine** embedded directly into the AlgoChains MCP server.

**Relationship:** Enterprise license + white-label reseller
**Coverage:** Stocks (all US exchanges + dark pools), Options (all US), Indices (S&P, Dow Jones, FTSE+), Currencies (forex + crypto), Futures (CME, CBOT, COMEX, NYMEX), SEC filings
**White-Label:** AlgoChains can rebrand Massive data endpoints under `algochains.io` domain for marketplace customers

### 13.2 Massive MCP Architecture (Rebuilt March 2026)

Massive rebuilt their MCP server around a **4-tool composable architecture** that achieves 90%+ context reduction — the exact same BM25 search-discovery pattern AlgoChains uses for Dynamic Toolsets.

**The 4 Tools:**

| Tool | Purpose | Context Cost |
|------|---------|-------------|
| `search_endpoints` | BM25 search over all API endpoints from `llms.txt` index | ~200 tokens |
| `get_endpoint_docs` | Fetch parameter schema for a specific endpoint | ~300 tokens |
| `call_api` | Execute GET request with `store_as` for in-memory DataFrames | ~150 tokens |
| `query_data` | SQL queries over stored DataFrames + `apply` post-processing | ~150 tokens |

**vs. Old approach:** 53+ static tools × ~500 tokens each = **26,500 tokens** baseline
**New approach:** 4 tools × ~200 tokens each = **800 tokens** baseline (**97% reduction**)

### 13.3 Built-in Financial Functions (Server-Side `apply`)

Massive ships 13 built-in financial functions that run server-side via the `apply` parameter — no client computation needed:

**Options Greeks (Black-Scholes):**
- `bs_price`, `bs_delta`, `bs_gamma`, `bs_theta`, `bs_vega`, `bs_rho`

**Returns Analysis:**
- `simple_return`, `log_return`, `cumulative_return`, `sharpe_ratio`, `sortino_ratio`

**Technical Indicators:**
- `sma` (simple moving average), `ema` (exponential moving average)

```python
# Example: Fetch AAPL daily bars → store → compute 20-day SMA server-side
await call_api(
    method="GET",
    path="/v2/aggs/ticker/AAPL/range/1/day/2026-01-01/2026-03-30",
    store_as="aapl_daily"
)

await query_data(
    sql="SELECT * FROM aapl_daily ORDER BY timestamp DESC LIMIT 30",
    apply=[{"function": "sma", "inputs": {"column": "close", "window": 20}, "output": "sma_20"}]
)
```

### 13.4 In-Memory Data Store & SQL Engine

Massive's `store_as` parameter saves API results as pandas DataFrames, queryable via SQLite SQL:

```python
# Multi-step research workflow
await call_api(path="/v2/aggs/ticker/AAPL/range/1/day/...", store_as="aapl")
await call_api(path="/v2/aggs/ticker/MSFT/range/1/day/...", store_as="msft")

# Cross-asset SQL analysis
await query_data(sql="""
    SELECT a.timestamp, a.close as aapl, m.close as msft,
           (a.close / LAG(a.close) OVER (ORDER BY a.timestamp) - 1) as aapl_ret,
           (m.close / LAG(m.close) OVER (ORDER BY m.timestamp) - 1) as msft_ret
    FROM aapl a JOIN msft m ON a.timestamp = m.timestamp
""")
```

**Limits:** `MASSIVE_MAX_TABLES=50`, `MASSIVE_MAX_ROWS=50000`, tables auto-expire after 1 hour.

### 13.5 AlgoChains White-Label Integration Architecture

```
┌─────────────────────────────────────────────────────┐
│                  algochains.io                       │
│                (White-Label API)                     │
├──────────┬──────────┬──────────┬────────────────────┤
│ Stocks   │ Options  │ Futures  │ SEC Filings        │
│ Forex    │ Crypto   │ Indices  │ Alt Data           │
├──────────┴──────────┴──────────┴────────────────────┤
│           AlgoChains MCP Gateway                     │
│  ┌──────────────────────────────────────────────┐   │
│  │  Massive Enterprise API (white-label)         │   │
│  │  - BM25 endpoint discovery                    │   │
│  │  - Server-side Greeks, Sharpe, SMA/EMA        │   │
│  │  - In-memory SQL DataFrames                   │   │
│  │  - store_as + query_data pipeline             │   │
│  └──────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────┐   │
│  │  AlgoChains Proprietary Layer                 │   │
│  │  - Broker execution (Tradovate, Alpaca, IBKR) │   │
│  │  - ML/AI strategy engine (V10)                │   │
│  │  - Agent swarm orchestration (V14)            │   │
│  │  - Intent-based trading (V18)                 │   │
│  └──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

### 13.6 Marketplace Revenue Integration

**For AlgoChains Marketplace Customers:**
- Massive data access bundled with AlgoChains subscription tiers
- Usage-based billing passthrough with margin (enterprise pricing advantage)
- White-label API keys scoped per customer workspace
- Built-in rate limiting and quota management per tier

**Tier Structure:**
| AlgoChains Tier | Massive API Calls/mo | Assets | Real-Time |
|----------------|---------------------|--------|-----------|
| Starter | 10,000 | US Equities | Delayed |
| Pro | 100,000 | + Options + Forex | Real-time |
| Enterprise | Unlimited | All asset classes | Real-time + L2 |

### 13.7 Implementation Notes

```python
# algochains_mcp/data_providers/massive_whitelabel.py

class MassiveWhiteLabelProvider:
    """White-label Massive data provider for AlgoChains marketplace."""

    def __init__(self, enterprise_api_key: str):
        self.base_url = os.getenv("MASSIVE_API_BASE_URL", "https://api.massive.com")
        self.api_key = enterprise_api_key
        self.llms_txt_url = os.getenv("MASSIVE_LLMS_TXT_URL",
                                       "https://massive.com/docs/rest/llms.txt")
        self._bm25_index = None  # Built at startup from llms.txt

    async def startup(self):
        """Build BM25 search index from Massive's llms.txt endpoint catalog."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(self.llms_txt_url)
            self._bm25_index = self._build_bm25(resp.text)

    def search_endpoints(self, query: str, top_k: int = 5) -> list[dict]:
        """BM25 search over all Massive API endpoints."""
        return self._bm25_index.search(query, top_k)

    async def call_api(self, path: str, params: dict = None,
                       store_as: str = None, apply: list = None) -> dict:
        """Execute Massive API call, optionally store result as DataFrame."""
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{self.base_url}{path}",
                                    params=params, headers=headers)
            data = resp.json()

        if store_as:
            self._dataframes[store_as] = pd.DataFrame(data.get("results", []))

        if apply:
            data = self._apply_functions(data, apply)

        return data

    async def scoped_key_for_customer(self, customer_id: str,
                                       tier: str) -> str:
        """Generate scoped API key for white-label customer."""
        # Enterprise API endpoint for key provisioning
        ...
```

---

## 14. CROSS-CUTTING INNOVATIONS — The Genius Layer

These innovations span multiple versions and represent the deepest competitive moats.

### 14.1 Predictive State Prefetch

**Problem:** LLMs make 4-5 exploratory calls before the real action.

**Solution:** Predict what the LLM will need based on user message intent and prefetch in parallel.

```python
class PredictiveStatePrefetch:
    """Analyze user message → predict needed state → prefetch in parallel."""

    INTENT_PATTERNS = {
        r"buy|sell|trade|order|get me exposure":
            ["portfolio", "quotes", "buying_power", "compliance"],
        r"p&l|performance|how.?am.?i.?doing":
            ["session_journal", "portfolio"],
        r"risk|var|drawdown|exposure":
            ["risk_snapshot", "positions", "greeks"],
        r"backtest|optimize|strategy":
            ["model_registry", "feature_sets"],
        r"deploy|go live|promote":
            ["shadow_results", "compliance_check"],
    }

    async def prefetch(self, user_message: str) -> dict:
        """Returns pre-loaded context that search_tools injects into response."""
        needed = self._detect_needs(user_message)
        tasks = [self._fetch(need) for need in needed]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return {k: v for k, v in zip(needed, results)
                if not isinstance(v, Exception)}
```

**Impact:** Reduces average tool calls per user intent from 6.2 to 1.8 (71% reduction).

### 14.2 Regime-Aware Strategy Selection

**Problem:** Strategies that work in bull markets fail in bear markets. Users deploy one strategy and leave it running through regime changes.

**Solution:** Automatic regime detection → strategy recommendation → optional auto-switch.

```python
class RegimeDetector:
    """Detect current market regime from multiple signals."""

    REGIMES = ["strong_bull", "bull", "range", "bear",
               "strong_bear", "volatile", "crisis"]

    REGIME_STRATEGIES = {
        "strong_bull": ["momentum", "breakout", "trend_following"],
        "bull":        ["ema_crossover", "rsi_pullback", "buy_dip"],
        "range":       ["mean_reversion", "bollinger_bands", "rsi_overbought_oversold"],
        "bear":        ["short_momentum", "put_spreads", "defensive"],
        "volatile":    ["straddles", "volatility_targeting", "reduced_size"],
        "crisis":      ["cash", "treasury_bonds", "gold", "vix_calls"],
    }

    async def detect(self) -> dict:
        vix = await self._get_vix()
        spy_trend = await self._get_trend("SPY", [20, 50, 200])
        breadth = await self._get_market_breadth()
        credit_spread = await self._get_credit_spread()

        regime = self._classify(vix, spy_trend, breadth, credit_spread)
        return {
            "regime": regime,
            "confidence": 0.85,
            "recommended_strategies": self.REGIME_STRATEGIES[regime],
        }
```

**Integration:** V14 Agent Swarm auto-allocates capital based on regime. V10 ML Engine uses regime as feature. V18 Intent Engine factors regime into plans.

### 14.3 Execution Fingerprinting

**Problem:** After thousands of orders, which execution strategies actually work best for YOUR portfolio? Which broker fills best for YOUR order sizes?

**Solution:** Every execution is fingerprinted and analyzed for patterns.

```python
class ExecutionFingerprint:
    """Build execution quality profile per broker × symbol × size_bucket."""

    async def analyze(self, broker: str, lookback_days: int = 90) -> dict:
        fills = await self._get_fills(broker, lookback_days)
        return {
            "broker": broker,
            "avg_slippage_bps": self._calc_avg_slippage(fills),
            "best_time_of_day": self._best_fill_time(fills),
            "size_impact": self._size_impact_curve(fills),
            "recommendations": [
                "Use Alpaca for orders < 1000 shares (0.2bps avg slippage)",
                "Use IBKR for orders > 1000 shares (SOR reduces impact 40%)",
                "Avoid market orders 9:30-9:45 (3x normal slippage)",
            ]
        }
```

**Integration:** V11 SOR uses fingerprints for routing. V18 Intent Engine selects broker based on execution quality.

### 14.4 Portfolio DNA Matching

**Problem:** Marketplace has 172+ validated strategies. Which ones complement YOUR portfolio?

**Solution:** Compute "DNA similarity" between strategies and recommend complementary additions.

```python
class PortfolioDNAMatcher:
    """Match marketplace strategies to user's portfolio for diversification."""

    async def recommend(self, user_id: str, top_n: int = 5) -> list[dict]:
        portfolio = await self._get_portfolio(user_id)
        all_strategies = await self._get_marketplace_strategies()

        # Compute correlation matrix of user's current returns vs each strategy
        portfolio_returns = self._compute_returns(portfolio)

        scored = []
        for strat in all_strategies:
            strat_returns = strat["oos_returns"]
            corr = np.corrcoef(portfolio_returns, strat_returns)[0, 1]
            diversification_score = 1 - abs(corr)  # Lower correlation = more diversification
            combined_sharpe = self._combined_sharpe(portfolio_returns, strat_returns)

            scored.append({
                "strategy": strat["name"],
                "correlation": round(corr, 3),
                "diversification_score": round(diversification_score, 3),
                "combined_sharpe_uplift": round(combined_sharpe - portfolio["sharpe"], 3),
                "recommendation": "STRONG ADD" if diversification_score > 0.7 else
                                 "GOOD ADD" if diversification_score > 0.4 else "SKIP",
            })

        return sorted(scored, key=lambda x: -x["combined_sharpe_uplift"])[:top_n]
```

### 14.5 Massive-Powered Research Pipelines

Leveraging the Massive white-label integration for automated research:

```python
class MassiveResearchPipeline:
    """Automated multi-asset research using Massive store_as + SQL."""

    async def sector_rotation_scan(self) -> dict:
        """Scan all 11 GICS sectors for momentum rotation signals."""
        sector_etfs = ["XLK", "XLF", "XLV", "XLE", "XLI",
                       "XLC", "XLY", "XLP", "XLB", "XLRE", "XLU"]

        for etf in sector_etfs:
            await self.massive.call_api(
                path=f"/v2/aggs/ticker/{etf}/range/1/day/2025-10-01/2026-03-30",
                store_as=f"sector_{etf.lower()}"
            )

        # Cross-sector momentum ranking via SQL
        result = await self.massive.query_data(sql="""
            WITH returns AS (
                SELECT 'XLK' as sector,
                       (MAX(close) - MIN(close)) / MIN(close) as return_6m
                FROM sector_xlk
                UNION ALL
                SELECT 'XLF', (MAX(close) - MIN(close)) / MIN(close)
                FROM sector_xlf
                -- ... all sectors
            )
            SELECT sector, return_6m,
                   RANK() OVER (ORDER BY return_6m DESC) as momentum_rank
            FROM returns
            ORDER BY momentum_rank
        """)
        return result

    async def options_unusual_activity(self, symbol: str) -> dict:
        """Detect unusual options activity using Massive options data."""
        await self.massive.call_api(
            path=f"/v3/snapshot/options/{symbol}",
            store_as=f"opts_{symbol.lower()}"
        )

        return await self.massive.query_data(
            sql=f"""
                SELECT strike, expiration_date, contract_type,
                       open_interest, day_volume,
                       CAST(day_volume AS FLOAT) / NULLIF(open_interest, 0)
                           as vol_oi_ratio
                FROM opts_{symbol.lower()}
                WHERE day_volume > 1000
                ORDER BY vol_oi_ratio DESC
                LIMIT 20
            """,
            apply=[{"function": "bs_delta", "inputs": {
                "spot": "underlying_price", "strike": "strike",
                "vol": "implied_volatility", "rate": 0.05,
                "time": "days_to_expiration"
            }, "output": "delta"}]
        )
```

---

## 15. IMPLEMENTATION INSTRUCTIONS

### 15.1 Universal Rules

1. **Language:** Python 3.12+ with full type hints. Rust for performance-critical engines (backtester, data processing).
2. **Async:** All I/O must be async (`httpx`, `asyncpg`, `aiofiles`). Never block the event loop.
3. **Config:** All secrets via environment variables or `.env`. Zero hardcoded keys.
4. **Logging:** `structlog` JSON format. Every tool call logged with duration, broker, category.
5. **Errors:** Custom `AlgoChainsError` hierarchy. Never expose raw tracebacks to users.
6. **Tests:** pytest + pytest-asyncio. Minimum 80% coverage on new code. Never delete existing tests.
7. **Dependencies:** Pin versions in `pyproject.toml`. No floating versions.

### 15.2 MCP Server Rules

1. **Tool Registration:** Never change tool order in `server.py` — only append new tools.
2. **Tool Descriptions:** Max 1024 chars. First sentence must state what the tool does.
3. **Input Validation:** All arguments validated via `validate_arguments()` before dispatch.
4. **Rate Limiting:** Per-broker and per-category rate limiting via `get_rate_limiter()`.
5. **Circuit Breakers:** Per-engine-category circuit breakers (5 failures = 60s cooldown).
6. **Response Size:** Max 100KB per tool response. Truncate with summary if exceeded.
7. **Timeouts:** Per-tool execution timeouts (broker: 30s, analysis: 60s, ML: 120s).
8. **Concurrency:** Per-category semaphores (broker: 5, analysis: 10, ml: 3).

### 15.3 Broker Integration Rules

1. **All brokers extend `BrokerConnector` ABC** — no exceptions.
2. **Normalized types:** `Order`, `Position`, `AccountInfo`, `Quote`, `OrderSide`, `OrderType`.
3. **Raw data preserved:** Every dataclass has `raw: dict` field for broker-specific data.
4. **Health checks:** Every broker must implement `health_check()` → `{"status": "healthy"|"unhealthy"}`.
5. **Paper/Live modes:** `paper: bool` field on `AccountInfo`. Never mix paper and live in same session.
6. **Reconnection:** Exponential backoff with jitter (1s, 2s, 4s, 8s, max 60s).

### 15.4 Massive Integration Rules

1. **White-label keys:** Scoped per customer workspace. Never share enterprise root key.
2. **BM25 index:** Rebuild on startup from `llms.txt`. Cache for 24 hours.
3. **Store-as naming:** Use `{asset}_{timeframe}` pattern (e.g., `aapl_daily`, `spy_5min`).
4. **Table cleanup:** Auto-expire tables after 1 hour. Max 50 tables per session.
5. **Apply functions:** Prefer server-side `apply` over client-side computation for Greeks, returns, technicals.
6. **Error handling:** Massive API errors must surface as structured `AlgoChainsError`, not raw HTTP errors.

### 15.5 Trading Logic Rules — SACRED PARAMETERS

**NEVER modify live trading parameters without explicit user approval:**

| Parameter | Value | Source |
|-----------|-------|--------|
| Volume threshold | 3.02x | Trial #267 validated |
| Momentum weight | 39.01% | Optimized |
| Stop ticks | 5 | Risk management |
| Target ticks | 65 | Reward optimization |
| Position size mult | 2.28 | Kelly-derived |

### 15.6 GPU Dispatch Rules

```python
GPU_CONFIG = {
    "mac":     {"device": "mps", "workers": 1, "data_root": "/Users/treycsa/..."},
    "desktop": {"device": "cuda", "host": "100.99.127.119",
                "workers": 5, "data_root": "/home/trrey/tick_data"},
}
# ALWAYS use rsync for data transfer. NEVER use SSHFS/NFS over Tailscale.
```

---

## 16. CODE GENERATION RULES

### 16.1 File Structure

```
algochains-mcp-server/
├── src/algochains_mcp/
│   ├── server.py              # MCP server entry + tool registration
│   ├── middleware.py           # Rate limiting, circuit breakers, timeouts
│   ├── brokers/
│   │   ├── base.py            # BrokerConnector ABC + normalized types
│   │   ├── tradovate.py       # Tradovate connector
│   │   ├── alpaca.py          # Alpaca connector
│   │   ├── ibkr.py            # Interactive Brokers connector
│   │   └── registry.py        # Broker registration + discovery
│   ├── data_providers/
│   │   ├── massive_whitelabel.py  # Massive enterprise white-label
│   │   └── databento.py           # Databento tick data
│   ├── ml_engine/
│   │   ├── model_registry.py  # ML model versioning + A/B
│   │   ├── feature_store.py   # Feature engineering pipeline
│   │   └── llm_strategy_gen.py # LLM-generated strategies
│   ├── execution/
│   │   ├── smart_order_router.py  # Multi-broker SOR
│   │   ├── fix_gateway.py         # FIX 4.4 protocol
│   │   └── tca_engine.py          # Transaction cost analysis
│   ├── agent_swarm/
│   │   ├── agent_orchestrator.py  # Multi-agent coordination
│   │   ├── research_agent.py      # Market research agent
│   │   └── execution_agent.py     # Trade execution agent
│   ├── defi_engine/
│   │   ├── bridge_engine.py       # Cross-chain bridges
│   │   └── governance_engine.py   # DAO governance
│   ├── analytics/
│   │   ├── streaming.py           # WebSocket real-time analytics
│   │   └── risk_engine.py         # VaR, Greeks, stress testing
│   ├── cloud/
│   │   ├── k8s_orchestrator.py    # Kubernetes deployment
│   │   └── billing.py             # Usage-based billing
│   └── dynamic_toolsets/
│       ├── tool_index.py          # BM25 tool search index
│       ├── meta_tools.py          # search_tools, get_tool_details, execute_tool
│       └── context_manager.py     # Token budget tracking
├── tests/
│   ├── test_brokers/
│   ├── test_ml_engine/
│   ├── test_execution/
│   └── test_massive/
├── docs/
│   ├── ALGOCHAINS_MEGA_BLUEPRINT_V2.md
│   ├── ALGOCHAINS_MEGA_BLUEPRINT_V2_PART2.md
│   └── ALGOCHAINS_MEGA_BLUEPRINT_V2_PART3.md
└── pyproject.toml
```

### 16.2 Code Style Mandates

```python
# CORRECT — async, typed, structured error handling
async def place_order(
    broker: str,
    symbol: str,
    side: OrderSide,
    qty: float,
    order_type: OrderType = OrderType.MARKET,
    limit_price: Optional[float] = None,
) -> Order:
    connector = registry.get_broker(broker)
    if not connector:
        raise BrokerNotFoundError(f"Broker '{broker}' not registered")

    try:
        order = await connector.place_order(
            symbol=symbol, side=side, qty=qty,
            order_type=order_type, limit_price=limit_price,
        )
        logger.info("order_placed", broker=broker, symbol=symbol,
                     order_id=order.id, side=side.value)
        return order
    except BrokerConnectionError as e:
        circuit_breaker.record_failure("broker")
        raise
```

```python
# WRONG — sync, untyped, bare except
def place_order(broker, symbol, side, qty):
    try:
        return requests.post(f"{broker}/orders", json={...})  # sync!
    except:  # bare except!
        return None  # swallows error!
```

### 16.3 Massive-Specific Code Patterns

```python
# CORRECT — Use Massive store_as + SQL pipeline
async def research_correlation(ticker_a: str, ticker_b: str) -> dict:
    """Research correlation using Massive in-memory SQL."""
    for ticker in [ticker_a, ticker_b]:
        await massive.call_api(
            path=f"/v2/aggs/ticker/{ticker}/range/1/day/2025-01-01/2026-03-30",
            store_as=f"data_{ticker.lower()}"
        )

    return await massive.query_data(sql=f"""
        WITH returns AS (
            SELECT a.timestamp,
                   (a.close - LAG(a.close) OVER (ORDER BY a.timestamp))
                       / LAG(a.close) OVER (ORDER BY a.timestamp) as ret_a,
                   (b.close - LAG(b.close) OVER (ORDER BY b.timestamp))
                       / LAG(b.close) OVER (ORDER BY b.timestamp) as ret_b
            FROM data_{ticker_a.lower()} a
            JOIN data_{ticker_b.lower()} b ON a.timestamp = b.timestamp
        )
        SELECT ROUND(AVG(ret_a * ret_b) /
               (SQRT(AVG(ret_a * ret_a)) * SQRT(AVG(ret_b * ret_b))), 4)
               as correlation
        FROM returns WHERE ret_a IS NOT NULL
    """)

# CORRECT — Use server-side apply for Greeks
async def options_analysis(symbol: str) -> dict:
    """Options analysis with server-side Black-Scholes."""
    await massive.call_api(
        path=f"/v3/snapshot/options/{symbol}",
        store_as=f"options_{symbol.lower()}"
    )
    return await massive.query_data(
        sql=f"SELECT * FROM options_{symbol.lower()} WHERE day_volume > 500",
        apply=[
            {"function": "bs_delta", "inputs": {"spot": "underlying_price",
             "strike": "strike", "vol": "implied_volatility",
             "rate": 0.05, "time": "days_to_expiration"}, "output": "delta"},
            {"function": "bs_gamma", "inputs": {"spot": "underlying_price",
             "strike": "strike", "vol": "implied_volatility",
             "rate": 0.05, "time": "days_to_expiration"}, "output": "gamma"},
        ]
    )
```

---

## 17. QUALITY GATES

### 17.1 Per-Version Gates

| Version | Gate | Criteria | Blocker? |
|---------|------|----------|----------|
| V10 ML | Model accuracy | OOS Sharpe > 2.0, max DD < 15% | Yes |
| V10 ML | A/B test | Champion vs challenger, p < 0.05 | Yes |
| V11 Execution | Slippage | < 2bps average across 1000 fills | Yes |
| V11 FIX | Protocol compliance | FIX 4.4 certification test suite | Yes |
| V12 Analytics | Latency | p99 < 200ms for streaming updates | Yes |
| V13 Alt Data | Signal quality | IC > 0.03 for any new signal | Yes |
| V14 Agents | Safety | Kill-switch tested, max loss enforced | Yes |
| V15 DeFi | Security | Slither audit clean, no high/critical | Yes |
| V16 Cloud | SLA | 99.9% uptime over 30 days | Yes |
| V17 Brokers | Normalization | All 8 BrokerConnector methods pass per broker | Yes |
| V17 IDE | Context | < 2000 tokens for 3 meta-tools | Yes |
| V18 Intent | Shadow validation | Shadow portfolio Sharpe > 1.5 before live | Yes |
| Massive | White-label | All 4 tools working under algochains.io domain | Yes |

### 17.2 Universal Quality Checklist

Every PR must pass:
- [ ] `pytest --cov --cov-fail-under=80` passes
- [ ] `ruff check` clean (zero warnings)
- [ ] `mypy --strict` clean on new files
- [ ] No hardcoded API keys or secrets
- [ ] No synchronous HTTP in async code paths
- [ ] Tool descriptions < 1024 chars
- [ ] Tool order in `server.py` unchanged (append-only)
- [ ] Circuit breaker + rate limiter configured for new tools
- [ ] Structured logging for all new tool calls
- [ ] Integration test with at least one real broker (paper mode)

### 17.3 Backtest Governance Gates

```python
QUALITY_GATES = {
    "daily":  {"max_oos_sharpe": 5.0,  "min_oos_trades": 20,  "max_dd": 25},
    "hourly": {"max_oos_sharpe": 7.0,  "min_oos_trades": 50,  "max_dd": 20},
    "15min":  {"max_oos_sharpe": 10.0, "min_oos_trades": 80,  "max_dd": 18},
    "5min":   {"max_oos_sharpe": 12.0, "min_oos_trades": 100, "max_dd": 15},
}

def validate_backtest(result: dict, timeframe: str) -> tuple[bool, list[str]]:
    gates = QUALITY_GATES[timeframe]
    failures = []
    if result["oos_sharpe"] > gates["max_oos_sharpe"]:
        failures.append(f"OOS Sharpe {result['oos_sharpe']} > max {gates['max_oos_sharpe']} (likely overfit)")
    if result["oos_trades"] < gates["min_oos_trades"]:
        failures.append(f"OOS trades {result['oos_trades']} < min {gates['min_oos_trades']}")
    if result["max_drawdown"] > gates["max_dd"]:
        failures.append(f"Max DD {result['max_drawdown']}% > limit {gates['max_dd']}%")
    return len(failures) == 0, failures
```

---

## 18. DEPLOYMENT PLAYBOOK

### 18.1 Version Dependency Chain

```
V10 (ML Engine) ──────────────────────┐
V11 (Execution) ──────────┐           │
V12 (Analytics) ───────┐  │           │
V13 (Alt Data) ────┐   │  │           │
                   ▼   ▼  ▼           ▼
V14 (Agent Swarm) ─────────────────────┤ requires V10+V11+V12+V13
V15 (DeFi) ────────────────────────────┤ requires V11
V16 (Cloud) ───────────────────────────┤ requires all above
V17 (Multi-Broker + IDE) ─────────────┤ can start in parallel with V10
Massive White-Label ──────────────────┤ immediate (enterprise key active)
V18 (Intent + Intelligence) ──────────┘ requires V10+V11+V14+V17+Massive
```

### 18.2 Sprint Plan

**Sprint 1 (Weeks 1-4): Foundation + Massive**
- Massive white-label integration (BM25 index, store_as, apply)
- V17 Phase 1: Dynamic Toolsets (3 meta-tools)
- V17 Phase 2: Alpaca + IBKR broker connectors
- V10 Phase 1: Feature store + model registry

**Sprint 2 (Weeks 5-8): Intelligence**
- V10 Phase 2: LLM strategy generation + RL optimizer
- V11 Phase 1: Smart order router (multi-broker)
- V12 Phase 1: WebSocket streaming analytics
- V13 Phase 1: SEC NLP + sentiment (using Massive SEC endpoints)

**Sprint 3 (Weeks 9-12): Autonomy**
- V14 Phase 1: Agent orchestrator + research agent
- V14 Phase 2: Self-healing + human-in-the-loop
- V11 Phase 2: FIX gateway + TCA
- V15 Phase 1: DEX aggregator

**Sprint 4 (Weeks 13-16): Scale**
- V16 Phase 1: Kubernetes orchestrator + edge deployment
- V16 Phase 2: Usage billing + marketplace API
- V18 Phase 1: Intent parser + shadow portfolios
- V18 Phase 2: Strategy DNA + cross-broker arbitrage

### 18.3 Deployment Checklist Per Version

```markdown
## Pre-Deploy
- [ ] All quality gates pass (Section 17)
- [ ] Integration tests pass with real brokers (paper mode)
- [ ] Load test: 100 concurrent tool calls, p99 < 500ms
- [ ] Security scan: no high/critical CVEs (pip-audit)
- [ ] Documentation updated in docs/

## Deploy
- [ ] Blue/green deployment (zero downtime)
- [ ] Feature flag enabled for beta users first
- [ ] Monitoring dashboards configured (latency, errors, throughput)
- [ ] Circuit breakers configured for new engine categories
- [ ] Slack alerts configured (#incident-response for P0/P1)

## Post-Deploy
- [ ] Smoke test: all tools callable
- [ ] Health check: all brokers healthy
- [ ] Canary: 5% traffic for 1 hour
- [ ] Full rollout after canary success
- [ ] Runbook updated for new failure modes
```

---

## 19. THE MEGA PROMPT V2

```
YOU ARE THE ALGOCHAINS AI CODING AGENT.

You are building the AlgoChains platform — a multi-broker, AI-native algorithmic
trading system distributed as an MCP server. AlgoChains has an enterprise
white-label partnership with Massive.com for institutional-grade market data
across all asset classes.

=== SYSTEM CONTEXT ===

Repository: algochains-mcp-server
Language: Python 3.12+ (async everywhere), Rust for perf-critical engines
Framework: MCP SDK (Model Context Protocol)
Brokers: Tradovate (futures), Alpaca (equities), IBKR (multi-asset), +5 more
Market Data: Massive.com enterprise white-label (stocks, options, futures,
  forex, crypto, indices, SEC filings) — BM25 endpoint discovery, server-side
  Greeks/Sharpe/SMA, in-memory SQL DataFrames
ML: PyTorch (GPU), scikit-learn, Rust backtester
GPU: Mac (mps) + Desktop RTX 5080 (cuda @ 100.99.127.119 via Tailscale)
Database: PostgreSQL + Redis + SQLite (per-bot)
Deployment: Docker + Kubernetes (V16), multi-node failover

=== ARCHITECTURE ===

The MCP server exposes 100+ tools across categories:
- Broker operations (orders, positions, account, quotes)
- ML engine (train, predict, feature engineering, model registry)
- Execution (SOR, FIX, TCA, bracket orders)
- Analytics (streaming, risk, VaR, Greeks)
- Agent swarm (orchestrate, research, execute, self-heal)
- DeFi (DEX, bridges, governance, MEV protection)
- Cloud (deploy, scale, bill, monitor)
- Massive data (search_endpoints, get_endpoint_docs, call_api, query_data)

IDE scaling via Dynamic Toolsets:
- Static mode: All 100+ tools exposed (for capable IDEs)
- Dynamic mode: 3 meta-tools (search_tools, get_tool_details, execute_tool)
  using BM25 index for 90%+ context reduction
- Set via ALGOCHAINS_TOOL_MODE=dynamic|static

=== MASSIVE WHITE-LABEL INTEGRATION ===

AlgoChains is an enterprise white-label reseller of Massive.com market data.
Massive's 4-tool composable architecture:
1. search_endpoints — BM25 search over all REST endpoints from llms.txt
2. get_endpoint_docs — parameter schema for a specific endpoint
3. call_api — execute API call, store_as for in-memory DataFrames
4. query_data — SQL over stored DataFrames + apply financial functions

Built-in server-side functions (via apply parameter):
- Greeks: bs_price, bs_delta, bs_gamma, bs_theta, bs_vega, bs_rho
- Returns: simple_return, log_return, cumulative_return, sharpe_ratio, sortino_ratio
- Technicals: sma, ema

Pattern: search → docs → call (store_as) → query (SQL + apply)
Config: MASSIVE_API_KEY, MASSIVE_API_BASE_URL, MASSIVE_LLMS_TXT_URL
Limits: MASSIVE_MAX_TABLES=50, MASSIVE_MAX_ROWS=50000

=== BROKER NORMALIZATION ===

All brokers implement BrokerConnector ABC (brokers/base.py):
- connect() → bool
- disconnect() → None
- get_account() → AccountInfo
- get_positions() → list[Position]
- get_orders(status?) → list[Order]
- place_order(symbol, side, qty, type, limit?, stop?, trail?, tif) → Order
- cancel_order(order_id) → bool
- get_quote(symbol) → Quote
- close_position(symbol) → Order?
- close_all_positions() → list[Order]
- health_check() → dict

Normalized types: Order, Position, AccountInfo, Quote
Enums: OrderSide, OrderType, OrderStatus, AssetClass

=== PRODUCTION HARDENING ===

Every tool call passes through:
1. Input validation (validate_arguments)
2. Rate limiting (per-broker, per-category)
3. Circuit breaker check (5 failures = 60s cooldown)
4. Concurrency semaphore (broker: 5, analysis: 10, ml: 3)
5. Execution timeout (broker: 30s, analysis: 60s, ml: 120s)
6. Response size guard (max 100KB)
7. Structured logging (tool, duration, broker, error)

=== ROADMAP ===

V10: ML/AI-Native Strategy Engine (feature store, model registry, LLM gen, RL)
V11: Institutional Execution (FIX 4.4, SOR, dark pools, TWAP/VWAP, TCA)
V12: Real-Time Analytics (WebSocket streaming, push notifications, mobile API)
V13: Alternative Data Marketplace (sentiment, SEC NLP, satellite, options flow)
V14: Autonomous Agent Swarm (multi-agent, self-healing, human-in-the-loop)
V15: DeFi & Cross-Chain (DEX aggregation, bridges, MEV protection, yield)
V16: AlgoChains Cloud (Kubernetes, edge, usage billing, enterprise API)
V17: Multi-Broker + IDE Scaling + State Awareness (Dynamic Toolsets, 8 brokers)
V18: Intent Trading + Autonomous Intelligence (NL intents, shadow portfolios,
     Strategy DNA evolution, cross-broker arbitrage)

=== SACRED PARAMETERS (NEVER MODIFY) ===

volume_threshold = 3.02x
momentum_weight = 39.01%
stop_ticks = 5
target_ticks = 65
position_size_mult = 2.28

=== RULES ===

1. All I/O must be async (httpx, asyncpg, aiofiles)
2. All brokers extend BrokerConnector ABC
3. Never change tool order in server.py — append only
4. Never hardcode API keys
5. Never use tradovate_token_auto_refresh.py — use Token Guardian
6. Never use SSHFS/NFS over Tailscale — use rsync
7. Never delete existing tests
8. Never use sync HTTP in async code
9. All tool calls: validate → rate-limit → circuit-break → timeout → log
10. Prefer Massive server-side apply over client-side computation
11. Store-as naming: {asset}_{timeframe} pattern
12. White-label keys scoped per customer workspace
13. Backtest gates: max Sharpe per timeframe, min trades, max DD
14. Shadow portfolio validation before any live deployment
15. Feature flags for beta rollout, canary before full deploy

=== WHEN IMPLEMENTING ===

For each version:
1. Read the detailed spec in ALGOCHAINS_MEGA_BLUEPRINT_V2 (Parts 1-3)
2. Create branch: feature/v{N}-{component}
3. Write tests first (pytest + pytest-asyncio)
4. Implement with full type hints and structured logging
5. Pass all quality gates (Section 17)
6. Integration test with real broker (paper mode)
7. Deploy via blue/green with feature flag
8. Monitor canary for 1 hour before full rollout

BUILD THE FUTURE OF ALGORITHMIC TRADING.
```

---

> **END OF PART 3** — The V2 Mega Blueprint is now complete across three files:
> 1. `ALGOCHAINS_MEGA_BLUEPRINT_V2.md` — System Context, Architecture, V10-V12
> 2. `ALGOCHAINS_MEGA_BLUEPRINT_V2_PART2.md` — V13-V18 Detailed Specs
> 3. `ALGOCHAINS_MEGA_BLUEPRINT_V2_PART3.md` — Massive Partnership, Genius Layer, Rules, Gates, Deployment, Mega Prompt
