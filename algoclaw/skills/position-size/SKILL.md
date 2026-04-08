# position-size

**Tier:** 0 (safe, no live money)  
**Trigger:** Pre-trade, on-demand  
**MCP Tool:** `run_algoclaw_skill("position-size", {"symbol":"MNQ","entry":18050,"stop":17990,"capital":50000})`

## What It Does

Computes optimal position size using multiple methods, returns the conservative minimum.

1. **R-Multiple** (Van Tharp): `(capital × risk_pct) / (entry - stop) per tick × tick_value`
2. **Volatility-Targeted** (Carver/pysystemtrade): `(target_vol × capital) / (realized_vol × notional_per_contract)`
3. **Conservative dual**: min(R-multiple, vol-targeted)
4. **IDM check**: if symbol is correlated with another active position, apply IDM reduction

## Steps

1. Call `compute_r_multiple_size(symbol, entry, stop, capital, risk_pct=1.0)`
2. Call `compute_volatility_targeted_size(symbol, current_price, annualized_vol_pct, capital)`
3. Call `compute_idm(instruments=[symbol, ...active_positions...])` if >1 active instrument
4. Return conservative minimum with explanation

## Output Format

```json
{
  "symbol": "MNQ",
  "entry": 18050,
  "stop": 17990,
  "capital": 50000,
  "methods": {
    "r_multiple": {
      "contracts": 2,
      "r_points": 60,
      "r_dollars": 120,
      "risk_dollars": 500,
      "risk_pct": 1.0
    },
    "vol_targeted": {
      "contracts": 2,
      "target_vol_pct": 20,
      "realized_vol_pct": 18.5
    }
  },
  "idm": {
    "applied": false,
    "reason": "MNQ is only active instrument"
  },
  "recommended": 2,
  "method_used": "r_multiple (both methods agree)",
  "r_multiple_targets": {"1R": 500, "2R": 1000, "3R": 1500}
}
```
