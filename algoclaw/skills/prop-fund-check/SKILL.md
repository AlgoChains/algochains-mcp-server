# prop-fund-check

**Tier:** 2 (prop fund pipeline)  
**Trigger:** Every 30 min during market hours (9:30–16:00 ET, Mon–Fri)  
**MCP Tool:** `run_algoclaw_skill("prop-fund-check")`

## What It Does

Checks all registered prop fund evaluation accounts against their fund rules.
Sends ntfy + Slack alerts at 70%/85%/95% of daily loss limits.
Emergency flattens all positions at 95%.

## Steps

1. Call `get_prop_fund_monitor_status()` — get all registered accounts
2. For each active account, call `run_prop_fund_check(account_id)` 
3. Parse alert tiers: daily loss utilization + trailing drawdown utilization
4. If any account at > 70% daily limit → post to #incident-response
5. If any account at > 95% daily limit → emergency flatten + escalate to owner
6. Log result to `algoclaw/state/prop_fund_checks.jsonl`

## Output Format

```json
{
  "checked_at": "2026-04-08T14:30:00Z",
  "accounts": [
    {
      "account_id": "ABC123",
      "fund": "apex",
      "status": "active",
      "daily_pnl": -450,
      "daily_limit": -2500,
      "daily_utilization_pct": 18.0,
      "trailing_dd": 800,
      "trailing_limit": 3000,
      "trailing_utilization_pct": 26.7,
      "days_traded": 7,
      "min_days_required": 10,
      "profit": 1200,
      "profit_target": 3000,
      "profit_pct": 40.0,
      "alerts": []
    }
  ],
  "overall_safe": true
}
```

## Alert Thresholds

| Utilization | Action |
|-------------|--------|
| < 70% | Silent (log only) |
| 70–85% | ⚠️ ntfy warn + Slack #quant-lab |
| 85–95% | 🚨 ntfy high + Slack #incident-response |
| ≥ 95% | 🔴 EMERGENCY FLATTEN + owner alert |
