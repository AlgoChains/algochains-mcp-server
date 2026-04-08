# stoploss-guard

**Tier:** 0 (safe, read-only check; Tier 1 for lock actions)  
**Trigger:** After every stoploss event; pre-trade check  
**MCP Tool:** `run_algoclaw_skill("stoploss-guard", {"bot":"MNQ","symbol":"MNQ","action":"check"})`  
**Source Pattern:** freqtrade `StoplossGuard`

## What It Does

Tracks stoploss events per bot/symbol and locks the instrument when N stops occur in X hours.
Prevents cascading losses from repeated stop-outs in a bad regime.

## Actions

| Action | Description |
|--------|-------------|
| `check` | Check if instrument is currently locked by StoplossGuard |
| `record` | Record a new stoploss event (call after every stop) |
| `status` | Full StoplossGuard status for all bots/symbols |

## Default Config

- **3 stops in 4 hours** → lock instrument for **2 hours**
- Configurable via `window_hours`, `stoploss_count`, `lock_hours` params

## Output (check)

```json
{
  "symbol": "MNQ", "bot": "MNQ",
  "locked": true,
  "lock_reason": "StoplossGuard: 3 stops in 4h window",
  "locked_until": "2026-04-08T16:30:00Z",
  "stop_events_in_window": 3
}
```

## Alert

When lock triggers: ntfy `high` + Slack `#incident-response`:  
`⚠️ StoplossGuard: MNQ locked 2h — 3 stops in 4h window`
