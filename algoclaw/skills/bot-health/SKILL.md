# bot-health

**Tier:** 0 (safe, no live money)  
**Trigger:** Morning routine, on-demand  
**MCP Tool:** `run_algoclaw_skill("bot-health")`

## What It Does

Checks all 4 live trading bots: process status, last signal time, WebSocket health, token validity, and log errors in the last hour.

## Steps

1. Call `get_live_bot_metrics()` — returns P&L, uptime, last signal for all 4 bots
2. Check process PIDs via system status (look for FUTURES_SCALPER, CL_FUTURES, mes_swing, nq_swing)
3. Scan bot logs for errors in last 60 minutes
4. Check Tradovate token status via `check_tradovate_token()`
5. Return structured health report with ✅/⚠️/🔴 status per bot

## Output Format

```json
{
  "overall": "healthy|degraded|critical",
  "bots": {
    "MNQ": {"status": "running", "pid": 12332, "last_signal_ago_min": 3, "errors_1h": 0},
    "CL":  {"status": "running", "pid": 12598, "last_signal_ago_min": 1, "errors_1h": 0},
    "MES": {"status": "running", "pid": 12333, "last_signal_ago_min": 25, "errors_1h": 0},
    "NQ":  {"status": "running", "pid": 12334, "last_signal_ago_min": 25, "errors_1h": 0}
  },
  "token": {"status": "valid", "expires_in_min": 187},
  "alerts": []
}
```

## Alerts

- ⚠️ Any bot PID missing → post to #incident-response Slack
- ⚠️ Last signal > 30 min ago during market hours → investigate signal blockers
- 🔴 Token expired → run Token Guardian immediately
- 🔴 Errors > 5 in last hour → escalate to owner
