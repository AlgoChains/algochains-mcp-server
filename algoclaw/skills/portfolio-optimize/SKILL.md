# portfolio-optimize

**Tier:** 1 (research, no live money)  
**Trigger:** Monthly, on-demand for subscriber portfolio recommendations  
**MCP Tool:** `run_algoclaw_skill("portfolio-optimize", {"bots":["MNQ","CL","MES","NQ"],"capital":50000})`  
**Source Pattern:** Riskfolio-Lib HRP + PyPortfolioOpt

## What It Does

Computes optimal capital allocation across AlgoChains bots using:
1. **HRP (Hierarchical Risk Parity)** — groups correlated bots, allocates inversely to risk
2. **Min-Variance** — minimizes portfolio volatility for given return target
3. **Conservative dual** — takes minimum weight per bot across both methods

## Input

- Bot performance history (pulled from live logs or Supabase)
- Correlation matrix between bot returns
- Subscriber's risk tolerance and capital

## Algorithm (pure Python, no Riskfolio dependency required)

```python
# Pure HRP implementation using scipy + numpy
# 1. Compute correlation matrix from bot return histories
# 2. Build hierarchical linkage tree (Ward / single)
# 3. Recursive bisection: allocate capital proportional to cluster variance
# 4. Apply min 5% / max 40% per-bot constraints
```

## Output

```json
{
  "capital": 50000,
  "method": "HRP",
  "allocations": {
    "MNQ": {"weight": 0.38, "capital_usd": 19000, "sharpe": 4.61},
    "CL":  {"weight": 0.28, "capital_usd": 14000, "sharpe": 2.8},
    "MES": {"weight": 0.18, "capital_usd": 9000,  "sharpe": 2.1},
    "NQ":  {"weight": 0.16, "capital_usd": 8000,  "sharpe": 2.3}
  },
  "portfolio_sharpe_est": 3.2,
  "correlation_note": "MNQ+NQ 96% correlated — HRP auto-reduces both",
  "rebalance_frequency": "monthly"
}
```
