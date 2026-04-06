"""Account Protection Module — prevent account explosions.

Provides 30+ safety checks that run BEFORE any trade execution:
- Position size limits (% of equity, max notional)
- Daily/weekly loss circuit breakers
- Drawdown protection with auto-flatten
- Fat finger detection (abnormal size/price)
- Buying power verification
- Concentration limits (single-position %)
- VIX-based market volatility killswitch
- Correlated exposure detection
- Max open positions cap
- Time-of-day restrictions (e.g., no trading in first/last 5 min)
- Consecutive loss protection
- Margin utilization caps
"""
from .engine import AccountProtectionEngine
from .guards import (
    PreTradeGuard,
    PositionSizeGuard,
    DailyLossGuard,
    DrawdownGuard,
    FatFingerGuard,
    BuyingPowerGuard,
    ConcentrationGuard,
    VolatilityKillswitch,
    CorrelationGuard,
    MaxPositionsGuard,
    TimeRestrictionGuard,
    ConsecutiveLossGuard,
    MarginGuard,
)

__all__ = [
    "AccountProtectionEngine",
    "PreTradeGuard",
    "PositionSizeGuard",
    "DailyLossGuard",
    "DrawdownGuard",
    "FatFingerGuard",
    "BuyingPowerGuard",
    "ConcentrationGuard",
    "VolatilityKillswitch",
    "CorrelationGuard",
    "MaxPositionsGuard",
    "TimeRestrictionGuard",
    "ConsecutiveLossGuard",
    "MarginGuard",
]
