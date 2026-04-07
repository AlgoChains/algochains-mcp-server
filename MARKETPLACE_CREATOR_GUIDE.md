# AlgoChains Marketplace — Bot Creator Guide
**For:** Strategy creators who want to list their bots on algochains.ai
**Version:** V22.4 | **Contact:** tyler@algochains.ai

---

## What Is the Marketplace?

The AlgoChains marketplace lets you package a validated trading strategy as a subscribable "bot card" that other traders can follow. Each bot card shows:

- Live or backtested performance metrics (Sharpe, win rate, max drawdown)
- The asset class, timeframe, and symbols traded
- Subscription price (you set this; platform takes 30%, you keep 70%)
- Academic citations and research backing
- Real-time execution metrics once the bot is live

Subscribers do NOT get your code — they get signal feeds or copy-trade execution.

---

## The 7-Step Creator Flow

### Step 1: Build a Strategy Spec

A `StrategySpec` is a JSON description of your strategy's parameters. It doesn't contain your code — just the configuration that the backtest engine understands.

```python
from algochains_mcp.strategy_builder.spec import StrategySpec

spec = StrategySpec(
    name="MNQ 5-Min EMA Scalper",
    symbols=["MNQ"],
    asset_class="futures",
    timeframe="5m",
    train_start="2023-01-01",
    train_end="2024-06-30",
    test_start="2024-07-01",
    test_end="2025-12-31",
    entry_rules=[
        {"type": "ema_crossover", "fast": 9, "slow": 21},
        {"type": "volume_threshold", "multiplier": 1.5},
    ],
    exit_rules=[
        {"type": "stop_loss_atr", "multiplier": 2.0},
        {"type": "take_profit_atr", "multiplier": 4.0},
        {"type": "time_exit", "max_minutes": 60},
    ],
    position_sizing={"method": "fixed_risk", "risk_pct": 0.01},
    description="EMA crossover scalper with ATR stops. Targets 2:1 R:R on MNQ 5-min.",
)
```

Or via the MCP tool from your AI:
```
Ask Claude: "Build a strategy spec for a 9/21 EMA crossover on MNQ with ATR stops"
→ MCP calls build_strategy() → returns a StrategySpec JSON
```

---

### Step 2: Run the Validation Gates

Your strategy MUST pass all 5 gates before it can be submitted. These gates use real historical data — there is no way to fake passing them.

```python
# Via MCP tool
result = validate_strategy(spec=spec.to_dict())
```

**The 5 gates (all must pass):**

| Gate | Minimum | Your Responsibility |
|------|---------|---------------------|
| Sharpe Ratio | > 2.0 (annualized) | On out-of-sample test period |
| Win Rate | > 55% | Across all trades in test period |
| Maximum Drawdown | < 15% | Peak-to-trough on equity curve |
| Minimum Trades | > 50 | In test period (not training) |
| Deflated Sharpe | Must pass MCPT | Corrects for multiple testing bias |

If any gate fails, you get a structured error:
```json
{
  "passed": false,
  "failed_gates": [
    {"gate": "sharpe_ratio", "required": 2.0, "actual": 1.8, "hint": "Consider tightening entry filters"}
  ]
}
```

---

### Step 3: Run MCPT Validation (Deflated Sharpe)

The Monte Carlo Permutation Test (MCPT) guards against overfitted strategies that only look good because you found lucky parameters.

```python
result = run_mcpt_validation(
    spec=spec.to_dict(),
    n_permutations=1000,  # Minimum 1000 for marketplace
)
```

**What it does:**
1. Runs your strategy on 1000 randomly permuted price paths
2. Measures what Sharpe you'd expect from random luck
3. Computes the p-value of your real Sharpe vs the random distribution
4. If p > 0.05, your strategy didn't beat random luck → **rejected**

This typically takes 5-20 minutes on the desktop GPU tower.

---

### Step 4: Submit to Marketplace (Staging)

Once validation passes:

```python
result = submit_to_marketplace(
    spec_id=spec.id,
    pricing={
        "monthly": 29.00,
        "currency": "USD",
    },
    description="5-min MNQ scalper using EMA crossover with ATR-based stops. Live since Jan 2025.",
    tags=["futures", "scalping", "mnq", "ema"],
    academic_citations=[
        "Jegadeesh & Titman (1993) — Returns to Buying Winners and Selling Losers",
        "Chan (2009) — Quantitative Trading",
    ],
)
```

This creates a listing in **STAGING** status. It will appear on algochains.ai/marketplace with a "Pending Review" badge.

**What happens next:**
- The listing is stored in `state/marketplace_listings.json`
- A Slack notification goes to `#quant-lab`
- Tyler reviews and approves (or requests changes)
- On approval, status changes to `active`

---

### Step 5: Set Up Live Execution (Optional but recommended)

A "validated" bot with only backtest results can be subscribed to, but subscribers only get alerts — not copy execution. For copy execution, you need the bot running live.

**Option A: Control Tower bots (Tyler's infrastructure)**
- Talk to Tyler to get your strategy added to one of the live bot files
- Requires code review and approval

**Option B: Autonomous Marketplace Autopilot**
```python
result = run_marketplace_autopilot(
    symbol="MNQ",
    dry_run=False,
)
# Scans all validated strategies, runs backtests, submits passing ones automatically
```

---

### Step 6: Monitor Subscriber Metrics

Once your bot is live and has subscribers, monitor performance:

```python
# See subscriber count, revenue, performance metrics
metrics = get_subscriber_metrics(listing_id="your-listing-id")
```

Metrics available:
- Subscriber count and churn rate
- Monthly revenue (your 70% share)
- Live performance vs backtested: Sharpe, win rate, max DD
- Signal latency and fill quality

---

### Step 7: Handle Performance Decay → Auto-Delist

Markets change. A strategy that worked in 2024 may decay in 2026. The marketplace has automatic decay detection:

- Every 30 days, running Sharpe is compared to backtested Sharpe
- If live Sharpe drops > 50% below backtested → `decay_warning` status
- If live Sharpe drops > 75% below backtested → automatic delist + subscriber notification

You can also manually delist:
```python
result = delist(listing_id="your-listing-id")
```

---

## Pricing Recommendations

| Strategy Type | Suggested Monthly Price | Reasoning |
|--------------|------------------------|-----------|
| Futures scalper (live, Sharpe > 3.0) | $49–$149/mo | High value, proven live |
| Futures swing (live, Sharpe > 2.5) | $29–$79/mo | Medium cadence, validated |
| Equities momentum (backtested only) | $9–$19/mo | No live track record yet |
| Forex (backtested, Sharpe > 2.0) | $9–$29/mo | Competitive market |
| Crypto (high volatility) | $19–$49/mo | Higher risk, higher reward |

Platform takes **30%**. You keep **70%**. Paid out monthly via Stripe Connect.

---

## Common Rejection Reasons

| Rejection | What to fix |
|-----------|-------------|
| Sharpe < 2.0 | Tighten entry filters, add confirmation signals, reduce trade frequency |
| Win rate < 55% | Review exit strategy — are stops too tight? |
| Max DD > 15% | Add daily loss limits, reduce position sizing |
| Too few trades (< 50) | Extend test period or loosen entry conditions |
| MCPT p-value > 0.05 | Strategy may be overfitted — try out-of-sample validation on a fresh date range |
| Lookahead bias detected | Check that no future data is used in entry/exit calculations |

---

## Code Example — Full Flow via MCP

```python
# From Claude or Cursor after connecting AlgoChains MCP

# 1. Build spec
spec = build_strategy(
    name="GBPUSD Breakout",
    symbols=["GBPUSD"],
    asset_class="forex",
    timeframe="1h",
    entry_rules=[{"type": "range_breakout", "lookback": 24}],
    exit_rules=[{"type": "stop_loss_pips", "pips": 20}, {"type": "take_profit_pips", "pips": 60}],
    train_start="2022-01-01",
    test_end="2025-12-31",
)

# 2. Validate
validation = validate_strategy(spec=spec)
if not validation["passed"]:
    print(validation["failed_gates"])
else:
    # 3. MCPT
    mcpt = run_mcpt_validation(spec=spec, n_permutations=1000)
    if mcpt["passed"]:
        # 4. Submit
        listing = submit_to_marketplace(
            spec_id=spec["id"],
            pricing={"monthly": 29.00},
            description="GBPUSD range breakout on H1 with tight stops",
            tags=["forex", "gbpusd", "breakout"],
        )
        print(f"Submitted: {listing['listing_id']}")
```

---

## FAQ

**Q: Do I need to share my strategy code?**
No. You share a `StrategySpec` (configuration JSON), not source code. Your IP is protected.

**Q: Can I list a strategy that hasn't been live yet?**
Yes, but it will be labeled "Backtested Only" on the marketplace. Live track record commands much higher subscription prices.

**Q: What if I want to update a listed strategy?**
Submit a new spec with `submit_to_marketplace(update_listing_id="existing-id")`. The old version remains active until the new one passes validation and you explicitly swap it.

**Q: How do subscribers get signals?**
Via the AlgoChains Marketplace app. Subscribers can configure alerts (Slack, email, SMS) or copy execution (Alpaca Broker API).

**Q: Who is Tyler talking to at Roo's request for the creator guide doc?**
This IS that document. Roo: this is the complete creator flow. The key gates are Step 2 (validation) and Step 3 (MCPT). Those two gates prevent us from listing garbage strategies that blow up subscriber accounts.
