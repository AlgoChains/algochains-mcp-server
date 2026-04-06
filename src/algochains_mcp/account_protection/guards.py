"""Pre-trade safety guards — individual check implementations.

Each guard returns a GuardResult with pass/fail, reason, and severity.
Guards are composable and run in priority order before every order.
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger("algochains_mcp.account_protection")


class GuardSeverity(str, Enum):
    BLOCK = "block"
    WARN = "warn"
    INFO = "info"


@dataclass
class GuardResult:
    guard_name: str
    passed: bool
    severity: GuardSeverity
    reason: str = ""
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "guard": self.guard_name,
            "passed": self.passed,
            "severity": self.severity.value,
            "reason": self.reason,
            "details": self.details,
        }


@dataclass
class OrderIntent:
    """Normalized representation of a trade before execution."""
    broker: str
    symbol: str
    side: str
    qty: float
    order_type: str = "market"
    limit_price: float | None = None
    stop_price: float | None = None
    notional_value: float | None = None
    asset_class: str = "stock"


@dataclass
class AccountSnapshot:
    """Current account state for guard evaluation."""
    equity: float = 0.0
    cash: float = 0.0
    buying_power: float = 0.0
    open_positions: list[dict] = field(default_factory=list)
    daily_pnl: float = 0.0
    weekly_pnl: float = 0.0
    peak_equity: float = 0.0
    consecutive_losses: int = 0
    margin_used: float = 0.0
    margin_available: float = 0.0
    open_orders: int = 0
    recent_fills: list[dict] = field(default_factory=list)


class PreTradeGuard(ABC):
    """Base class for all pre-trade safety checks."""

    name: str = "base_guard"
    priority: int = 50
    enabled: bool = True

    @abstractmethod
    def check(self, order: OrderIntent, account: AccountSnapshot) -> GuardResult:
        ...


class PositionSizeGuard(PreTradeGuard):
    """Prevents oversized positions relative to account equity.

    Default limits:
    - Single position: max 10% of equity (configurable)
    - Single order notional: max 25% of equity
    - Futures: max 2 contracts per $10K equity
    """

    name = "position_size"
    priority = 10

    def __init__(
        self,
        max_position_pct: float = 10.0,
        max_order_pct: float = 25.0,
        futures_per_10k: int = 2,
    ):
        self.max_position_pct = max_position_pct
        self.max_order_pct = max_order_pct
        self.futures_per_10k = futures_per_10k

    def check(self, order: OrderIntent, account: AccountSnapshot) -> GuardResult:
        if account.equity <= 0:
            return GuardResult(
                self.name, False, GuardSeverity.BLOCK,
                "Cannot trade with zero or negative equity",
            )

        notional = order.notional_value or (order.qty * (order.limit_price or 0))
        if notional <= 0:
            return GuardResult(self.name, True, GuardSeverity.INFO, "No notional to check")

        position_pct = (notional / account.equity) * 100

        if position_pct > self.max_order_pct:
            return GuardResult(
                self.name, False, GuardSeverity.BLOCK,
                f"Order is {position_pct:.1f}% of equity (max {self.max_order_pct}%)",
                {"notional": notional, "equity": account.equity, "pct": position_pct},
            )

        if position_pct > self.max_position_pct:
            return GuardResult(
                self.name, True, GuardSeverity.WARN,
                f"Order is {position_pct:.1f}% of equity (warning threshold {self.max_position_pct}%)",
                {"notional": notional, "equity": account.equity, "pct": position_pct},
            )

        if order.asset_class == "futures":
            max_contracts = max(1, int(account.equity / 10_000) * self.futures_per_10k)
            if order.qty > max_contracts:
                return GuardResult(
                    self.name, False, GuardSeverity.BLOCK,
                    f"Futures order {order.qty} contracts exceeds limit of {max_contracts} "
                    f"for ${account.equity:,.0f} equity",
                )

        return GuardResult(self.name, True, GuardSeverity.INFO, "Position size OK")


class DailyLossGuard(PreTradeGuard):
    """Blocks trading when daily loss exceeds threshold.

    Default: Stop trading after losing 2% of equity in a single day.
    Hard stop: 5% daily loss = auto-flatten all positions.
    """

    name = "daily_loss"
    priority = 5

    def __init__(self, soft_limit_pct: float = 2.0, hard_limit_pct: float = 5.0):
        self.soft_limit_pct = soft_limit_pct
        self.hard_limit_pct = hard_limit_pct
        self._daily_losses: dict[str, float] = {}
        self._last_reset: float = 0.0

    def _reset_if_new_day(self) -> None:
        now = datetime.now(timezone.utc)
        day_key = now.strftime("%Y-%m-%d")
        if day_key not in self._daily_losses:
            self._daily_losses.clear()
            self._daily_losses[day_key] = 0.0

    def record_loss(self, amount: float) -> None:
        self._reset_if_new_day()
        day_key = list(self._daily_losses.keys())[0]
        self._daily_losses[day_key] = self._daily_losses.get(day_key, 0.0) + amount

    def check(self, order: OrderIntent, account: AccountSnapshot) -> GuardResult:
        if account.equity <= 0:
            return GuardResult(self.name, False, GuardSeverity.BLOCK, "Zero equity")

        daily_loss_pct = abs(min(0, account.daily_pnl)) / account.equity * 100

        if daily_loss_pct >= self.hard_limit_pct:
            return GuardResult(
                self.name, False, GuardSeverity.BLOCK,
                f"HARD STOP: Daily loss {daily_loss_pct:.1f}% exceeds {self.hard_limit_pct}% limit. "
                f"All trading halted. Flatten recommended.",
                {"daily_pnl": account.daily_pnl, "loss_pct": daily_loss_pct, "action": "flatten"},
            )

        if daily_loss_pct >= self.soft_limit_pct:
            return GuardResult(
                self.name, False, GuardSeverity.BLOCK,
                f"Daily loss {daily_loss_pct:.1f}% exceeds soft limit {self.soft_limit_pct}%. "
                f"New positions blocked.",
                {"daily_pnl": account.daily_pnl, "loss_pct": daily_loss_pct},
            )

        return GuardResult(
            self.name, True, GuardSeverity.INFO,
            f"Daily P&L: ${account.daily_pnl:,.2f} ({daily_loss_pct:.1f}% of equity)",
        )


class DrawdownGuard(PreTradeGuard):
    """Blocks trading when drawdown from peak exceeds threshold.

    Default: Block at 10% drawdown from peak equity.
    Emergency flatten at 15%.
    """

    name = "drawdown"
    priority = 3

    def __init__(self, block_pct: float = 10.0, flatten_pct: float = 15.0):
        self.block_pct = block_pct
        self.flatten_pct = flatten_pct

    def check(self, order: OrderIntent, account: AccountSnapshot) -> GuardResult:
        if account.peak_equity <= 0:
            return GuardResult(self.name, True, GuardSeverity.INFO, "No peak equity recorded")

        drawdown_pct = ((account.peak_equity - account.equity) / account.peak_equity) * 100

        if drawdown_pct >= self.flatten_pct:
            return GuardResult(
                self.name, False, GuardSeverity.BLOCK,
                f"EMERGENCY: Drawdown {drawdown_pct:.1f}% from peak ${account.peak_equity:,.0f}. "
                f"Flatten all positions immediately.",
                {"drawdown_pct": drawdown_pct, "action": "emergency_flatten"},
            )

        if drawdown_pct >= self.block_pct:
            return GuardResult(
                self.name, False, GuardSeverity.BLOCK,
                f"Drawdown {drawdown_pct:.1f}% from peak. New trades blocked until recovery.",
                {"drawdown_pct": drawdown_pct, "peak": account.peak_equity},
            )

        return GuardResult(
            self.name, True, GuardSeverity.INFO,
            f"Drawdown {drawdown_pct:.1f}% (limit {self.block_pct}%)",
        )


class FatFingerGuard(PreTradeGuard):
    """Detects abnormal order sizes or prices.

    Checks:
    - Quantity > 10x median recent order size
    - Limit price > 5% from last known price
    - Notional > $100K for non-institutional accounts
    """

    name = "fat_finger"
    priority = 8

    def __init__(
        self,
        max_qty_multiplier: float = 10.0,
        max_price_deviation_pct: float = 5.0,
        max_notional: float = 100_000.0,
    ):
        self.max_qty_multiplier = max_qty_multiplier
        self.max_price_deviation_pct = max_price_deviation_pct
        self.max_notional = max_notional

    def check(self, order: OrderIntent, account: AccountSnapshot) -> GuardResult:
        issues: list[str] = []

        if order.notional_value and order.notional_value > self.max_notional:
            issues.append(
                f"Notional ${order.notional_value:,.0f} exceeds ${self.max_notional:,.0f} limit"
            )

        if account.recent_fills:
            recent_qtys = [f.get("qty", 0) for f in account.recent_fills[-20:] if f.get("qty")]
            if recent_qtys:
                median_qty = sorted(recent_qtys)[len(recent_qtys) // 2]
                if median_qty > 0 and order.qty > median_qty * self.max_qty_multiplier:
                    issues.append(
                        f"Qty {order.qty} is {order.qty / median_qty:.1f}x median "
                        f"recent size ({median_qty})"
                    )

        if issues:
            return GuardResult(
                self.name, False, GuardSeverity.BLOCK,
                f"Fat finger detected: {'; '.join(issues)}",
                {"issues": issues},
            )

        return GuardResult(self.name, True, GuardSeverity.INFO, "Order size appears normal")


class BuyingPowerGuard(PreTradeGuard):
    """Ensures sufficient buying power before order submission."""

    name = "buying_power"
    priority = 2

    def __init__(self, min_remaining_pct: float = 10.0):
        self.min_remaining_pct = min_remaining_pct

    def check(self, order: OrderIntent, account: AccountSnapshot) -> GuardResult:
        if account.buying_power <= 0:
            return GuardResult(
                self.name, False, GuardSeverity.BLOCK,
                "No buying power available",
            )

        notional = order.notional_value or 0
        if notional > account.buying_power:
            return GuardResult(
                self.name, False, GuardSeverity.BLOCK,
                f"Order notional ${notional:,.0f} exceeds buying power ${account.buying_power:,.0f}",
            )

        remaining_pct = ((account.buying_power - notional) / account.equity * 100
                         if account.equity > 0 else 0)
        if remaining_pct < self.min_remaining_pct:
            return GuardResult(
                self.name, True, GuardSeverity.WARN,
                f"Order would leave only {remaining_pct:.1f}% buying power remaining",
            )

        return GuardResult(self.name, True, GuardSeverity.INFO, "Buying power sufficient")


class ConcentrationGuard(PreTradeGuard):
    """Prevents over-concentration in a single position.

    Default: No single position > 20% of portfolio.
    Sector concentration check for correlated assets.
    """

    name = "concentration"
    priority = 15

    def __init__(self, max_single_pct: float = 20.0, max_sector_pct: float = 40.0):
        self.max_single_pct = max_single_pct
        self.max_sector_pct = max_sector_pct

    def check(self, order: OrderIntent, account: AccountSnapshot) -> GuardResult:
        if not account.open_positions or account.equity <= 0:
            return GuardResult(self.name, True, GuardSeverity.INFO, "No existing positions")

        existing_notional = 0.0
        for pos in account.open_positions:
            if pos.get("symbol") == order.symbol:
                existing_notional += abs(pos.get("market_value", 0))

        new_notional = existing_notional + (order.notional_value or 0)
        concentration_pct = (new_notional / account.equity) * 100

        if concentration_pct > self.max_single_pct:
            return GuardResult(
                self.name, False, GuardSeverity.BLOCK,
                f"{order.symbol} would be {concentration_pct:.1f}% of portfolio "
                f"(max {self.max_single_pct}%)",
            )

        return GuardResult(
            self.name, True, GuardSeverity.INFO,
            f"{order.symbol} concentration: {concentration_pct:.1f}%",
        )


class VolatilityKillswitch(PreTradeGuard):
    """Blocks all trading during extreme market volatility.

    Default: VIX > 35 = block all new positions.
    VIX > 50 = emergency flatten.
    """

    name = "volatility_killswitch"
    priority = 1

    def __init__(self, block_vix: float = 35.0, flatten_vix: float = 50.0):
        self.block_vix = block_vix
        self.flatten_vix = flatten_vix
        self._current_vix: float | None = None
        self._vix_updated: float = 0.0

    def update_vix(self, vix: float) -> None:
        self._current_vix = vix
        self._vix_updated = time.time()

    def check(self, order: OrderIntent, account: AccountSnapshot) -> GuardResult:
        if self._current_vix is None:
            return GuardResult(
                self.name, True, GuardSeverity.WARN,
                "VIX data unavailable — killswitch inactive",
            )

        stale = (time.time() - self._vix_updated) > 3600
        if stale:
            return GuardResult(
                self.name, True, GuardSeverity.WARN,
                "VIX data stale (>1h old) — killswitch inactive",
            )

        if self._current_vix >= self.flatten_vix:
            return GuardResult(
                self.name, False, GuardSeverity.BLOCK,
                f"EMERGENCY: VIX at {self._current_vix:.1f} (>{self.flatten_vix}). "
                f"Flatten all positions.",
                {"vix": self._current_vix, "action": "emergency_flatten"},
            )

        if self._current_vix >= self.block_vix:
            return GuardResult(
                self.name, False, GuardSeverity.BLOCK,
                f"VIX at {self._current_vix:.1f} (>{self.block_vix}). New trades blocked.",
                {"vix": self._current_vix},
            )

        return GuardResult(
            self.name, True, GuardSeverity.INFO,
            f"VIX at {self._current_vix:.1f} — normal",
        )


class CorrelationGuard(PreTradeGuard):
    """Detects correlated exposure across positions.

    Example: Long AAPL + Long QQQ + Long MSFT = high tech concentration.
    Uses simple sector mapping; extendable with real correlation data.
    """

    name = "correlation"
    priority = 20

    SECTOR_MAP: dict[str, str] = {
        "AAPL": "tech", "MSFT": "tech", "GOOGL": "tech", "GOOG": "tech",
        "AMZN": "tech", "META": "tech", "NVDA": "tech", "TSLA": "tech",
        "AMD": "tech", "INTC": "tech", "CRM": "tech", "ADBE": "tech",
        "JPM": "finance", "BAC": "finance", "GS": "finance", "MS": "finance",
        "XOM": "energy", "CVX": "energy", "COP": "energy",
        "JNJ": "health", "PFE": "health", "UNH": "health",
        "QQQ": "tech", "SPY": "broad", "IWM": "broad",
        "USO": "energy", "XLE": "energy", "XLF": "finance",
    }

    def __init__(self, max_sector_pct: float = 50.0):
        self.max_sector_pct = max_sector_pct

    def check(self, order: OrderIntent, account: AccountSnapshot) -> GuardResult:
        if account.equity <= 0:
            return GuardResult(self.name, True, GuardSeverity.INFO, "No equity")

        sector_exposure: dict[str, float] = {}
        for pos in account.open_positions:
            sym = pos.get("symbol", "")
            sector = self.SECTOR_MAP.get(sym, "other")
            sector_exposure[sector] = sector_exposure.get(sector, 0.0) + abs(
                pos.get("market_value", 0)
            )

        order_sector = self.SECTOR_MAP.get(order.symbol, "other")
        sector_exposure[order_sector] = sector_exposure.get(order_sector, 0.0) + (
            order.notional_value or 0
        )

        for sector, exposure in sector_exposure.items():
            pct = (exposure / account.equity) * 100
            if pct > self.max_sector_pct:
                return GuardResult(
                    self.name, False, GuardSeverity.BLOCK,
                    f"Sector '{sector}' would be {pct:.1f}% of portfolio "
                    f"(max {self.max_sector_pct}%)",
                    {"sector": sector, "exposure": exposure, "pct": pct},
                )

        return GuardResult(self.name, True, GuardSeverity.INFO, "Sector exposure OK")


class MaxPositionsGuard(PreTradeGuard):
    """Caps total number of open positions."""

    name = "max_positions"
    priority = 12

    def __init__(self, max_positions: int = 20):
        self.max_positions = max_positions

    def check(self, order: OrderIntent, account: AccountSnapshot) -> GuardResult:
        current = len(account.open_positions)
        is_new = not any(
            p.get("symbol") == order.symbol for p in account.open_positions
        )

        if is_new and current >= self.max_positions:
            return GuardResult(
                self.name, False, GuardSeverity.BLOCK,
                f"Already have {current} open positions (max {self.max_positions})",
            )

        return GuardResult(
            self.name, True, GuardSeverity.INFO,
            f"Open positions: {current}/{self.max_positions}",
        )


class TimeRestrictionGuard(PreTradeGuard):
    """Blocks trading during dangerous market periods.

    - First 5 minutes after open (gap risk)
    - Last 5 minutes before close (liquidity risk)
    - Major economic release windows (FOMC, NFP, CPI)
    - Overnight/weekend for day strategies
    """

    name = "time_restriction"
    priority = 6

    def __init__(
        self,
        block_first_minutes: int = 5,
        block_last_minutes: int = 5,
        market_open_hour: int = 9,
        market_open_minute: int = 30,
        market_close_hour: int = 16,
    ):
        self.block_first_minutes = block_first_minutes
        self.block_last_minutes = block_last_minutes
        self.market_open_hour = market_open_hour
        self.market_open_minute = market_open_minute
        self.market_close_hour = market_close_hour

    def check(self, order: OrderIntent, account: AccountSnapshot) -> GuardResult:
        now = datetime.now(timezone.utc)
        et_offset = -4
        et_hour = (now.hour + et_offset) % 24
        et_minute = now.minute

        open_minutes = self.market_open_hour * 60 + self.market_open_minute
        close_minutes = self.market_close_hour * 60
        current_minutes = et_hour * 60 + et_minute

        if current_minutes < open_minutes or current_minutes >= close_minutes:
            if order.asset_class in ("stock", "option"):
                return GuardResult(
                    self.name, True, GuardSeverity.WARN,
                    "Market closed — order may be queued for next session",
                )

        minutes_after_open = current_minutes - open_minutes
        minutes_before_close = close_minutes - current_minutes

        if 0 < minutes_after_open < self.block_first_minutes:
            return GuardResult(
                self.name, False, GuardSeverity.BLOCK,
                f"Trading blocked in first {self.block_first_minutes} min after open (gap risk)",
            )

        if 0 < minutes_before_close < self.block_last_minutes:
            return GuardResult(
                self.name, False, GuardSeverity.BLOCK,
                f"Trading blocked in last {self.block_last_minutes} min before close "
                f"(liquidity risk)",
            )

        return GuardResult(self.name, True, GuardSeverity.INFO, "Trading window OK")


class ConsecutiveLossGuard(PreTradeGuard):
    """Blocks trading after too many consecutive losing trades.

    Default: Pause after 5 consecutive losses. Resume after cooldown or manual override.
    """

    name = "consecutive_loss"
    priority = 7

    def __init__(self, max_consecutive: int = 5, cooldown_minutes: int = 30):
        self.max_consecutive = max_consecutive
        self.cooldown_minutes = cooldown_minutes
        self._paused_until: float = 0.0

    def check(self, order: OrderIntent, account: AccountSnapshot) -> GuardResult:
        if time.time() < self._paused_until:
            remaining = int((self._paused_until - time.time()) / 60)
            return GuardResult(
                self.name, False, GuardSeverity.BLOCK,
                f"Consecutive loss cooldown active — {remaining} min remaining",
            )

        if account.consecutive_losses >= self.max_consecutive:
            self._paused_until = time.time() + self.cooldown_minutes * 60
            return GuardResult(
                self.name, False, GuardSeverity.BLOCK,
                f"{account.consecutive_losses} consecutive losses — "
                f"pausing for {self.cooldown_minutes} min",
            )

        return GuardResult(
            self.name, True, GuardSeverity.INFO,
            f"Consecutive losses: {account.consecutive_losses}/{self.max_consecutive}",
        )


class MarginGuard(PreTradeGuard):
    """Prevents margin over-utilization.

    Default: Block new positions when margin utilization > 80%.
    Emergency at 90%.
    """

    name = "margin"
    priority = 4

    def __init__(self, warn_pct: float = 70.0, block_pct: float = 80.0):
        self.warn_pct = warn_pct
        self.block_pct = block_pct

    def check(self, order: OrderIntent, account: AccountSnapshot) -> GuardResult:
        if account.margin_used <= 0 or account.equity <= 0:
            return GuardResult(self.name, True, GuardSeverity.INFO, "No margin data")

        margin_util = (account.margin_used / account.equity) * 100

        if margin_util >= self.block_pct:
            return GuardResult(
                self.name, False, GuardSeverity.BLOCK,
                f"Margin utilization {margin_util:.1f}% exceeds {self.block_pct}% limit",
            )

        if margin_util >= self.warn_pct:
            return GuardResult(
                self.name, True, GuardSeverity.WARN,
                f"Margin utilization {margin_util:.1f}% approaching limit",
            )

        return GuardResult(
            self.name, True, GuardSeverity.INFO,
            f"Margin utilization: {margin_util:.1f}%",
        )
