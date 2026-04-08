# kill-switch

**Tier:** 3 (irreversible 🔴)  
**Trigger:** Emergency only — REQUIRES owner confirmation  
**MCP Tool:** `run_algoclaw_skill("kill-switch", {"confirm": "FLATTEN_ALL", "reason": "..."})`

## What It Does

Emergency flatten of ALL positions across ALL configured brokers simultaneously.
Stops all running bots. Posts full incident report to Slack.

## Owner Verification

Skill REFUSES to execute unless:
1. Caller Slack user ID matches `U09B9M94C3F` (Tyler)
2. Explicit confirm string = `"FLATTEN_ALL"` passed in args
3. Rate limit: max 1 execution per hour

## Steps

1. Verify owner ID and confirm string
2. Log emergency event to `algoclaw/state/kill_switch_log.jsonl`
3. Send pre-action alert: "KILL SWITCH ACTIVATED by {owner}" to #incident-response
4. For each broker with open positions:
   a. Tradovate: cancel all orders → flatten all positions
   b. Alpaca: cancel all orders → flatten all positions
   c. OANDA: close all open trades
5. Stop all bot processes (SIGTERM with 5s timeout then SIGKILL)
6. Verify all positions at 0 via `get_positions()` for each broker
7. Send post-action confirmation with list of closed positions + amounts
8. Update AlgoClaw state: mode = "emergency_stopped"

## Output Format

```json
{
  "kill_switch_activated": true,
  "timestamp": "2026-04-08T14:30:00Z",
  "reason": "VIX spiked to 55, manual emergency",
  "actions": {
    "tradovate": {"positions_closed": 2, "orders_cancelled": 3},
    "alpaca": {"positions_closed": 0, "orders_cancelled": 0}
  },
  "bots_stopped": ["MNQ", "CL", "MES", "NQ"],
  "verification": {
    "tradovate_open_positions": 0,
    "alpaca_open_positions": 0
  },
  "incident_posted_to_slack": true
}
```

## Recovery

After kill switch, system stays in `emergency_stopped` mode.  
To resume: `run_algoclaw_skill("resume-trading", {"confirm": "RESUME", "reason": "..."})`
