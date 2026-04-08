# signal-health

**Tier:** 0 (safe, read-only)  
**Trigger:** Every 30 min during market hours  
**MCP Tool:** `run_algoclaw_skill("signal-health")`  
**Source Pattern:** freqtrade `LowProfitPairs` + bot log analysis

## What It Does

Checks whether bots are generating signals and identifies instruments in a bad regime.
Implements LowProfitPairs logic: instruments with < min_profit in rolling window get flagged.

## Steps

1. Scan bot logs for signal events in last 60 minutes
2. Check `LowProfitPairs` status: any instrument below profit threshold?
3. Check `StoplossGuard` locks: any instruments currently locked?
4. Check `CooldownPeriod`: any instruments in cooldown?
5. Return unified signal health report

## Output

```json
{
  "market_hours": true,
  "bots": {
    "MNQ": {
      "signals_1h": 3,
      "last_signal_ago_min": 12,
      "status": "healthy",
      "low_profit_flag": false,
      "locked": false,
      "in_cooldown": false
    },
    "CL": {
      "signals_1h": 0,
      "last_signal_ago_min": 47,
      "status": "DEGRADED",
      "low_profit_flag": true,
      "low_profit_note": "CL: $-45 in last 24h (threshold: +$0)"
    }
  },
  "alerts": ["CL: no signals in 47 min during market hours — investigate"]
}
```
