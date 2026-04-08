# cooldown-check

**Tier:** 0 (safe, pre-trade gate)  
**Trigger:** Pre-entry check; post-stop trigger  
**MCP Tool:** `run_algoclaw_skill("cooldown-check", {"bot":"MNQ","symbol":"MNQ","action":"check"})`  
**Source Pattern:** freqtrade `CooldownPeriod`

## What It Does

Enforces a mandatory cooling-off period after any stoploss event.
Prevents revenge trading — the most common way discretionary traders amplify losses.

## Actions

| Action | Description |
|--------|-------------|
| `check` | Is this symbol in cooldown? |
| `trigger` | Start cooldown after a stoploss event |
| `status` | All active cooldowns across all bots |

## Default Config

- **30 minutes cooldown** after every stop event
- Configurable via `cooldown_minutes` param

## Output (check)

```json
{
  "in_cooldown": true,
  "symbol": "MNQ", "bot": "MNQ",
  "cooldown_until": "2026-04-08T14:45:00Z",
  "minutes_remaining": 22.3
}
```
