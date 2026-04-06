"""Account Protection Engine — orchestrates all safety guards.

Runs all enabled guards before every trade, produces a composite
safety report, and blocks dangerous orders before they reach the broker.
"""
from __future__ import annotations

import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .guards import (
    AccountSnapshot,
    BuyingPowerGuard,
    ConcentrationGuard,
    ConsecutiveLossGuard,
    CorrelationGuard,
    DailyLossGuard,
    DrawdownGuard,
    FatFingerGuard,
    GuardResult,
    GuardSeverity,
    MarginGuard,
    MaxPositionsGuard,
    OrderIntent,
    PositionSizeGuard,
    PreTradeGuard,
    TimeRestrictionGuard,
    VolatilityKillswitch,
)

logger = logging.getLogger("algochains_mcp.account_protection")


@dataclass
class ProtectionConfig:
    """User-configurable protection settings."""
    max_daily_loss_pct: float = 2.0
    hard_daily_loss_pct: float = 5.0
    max_drawdown_pct: float = 10.0
    emergency_drawdown_pct: float = 15.0
    max_position_pct: float = 10.0
    max_order_pct: float = 25.0
    max_concentration_pct: float = 20.0
    max_sector_pct: float = 50.0
    max_positions: int = 20
    max_consecutive_losses: int = 5
    cooldown_minutes: int = 30
    vix_block_level: float = 35.0
    vix_flatten_level: float = 50.0
    max_notional: float = 100_000.0
    margin_warn_pct: float = 70.0
    margin_block_pct: float = 80.0
    block_first_minutes: int = 5
    block_last_minutes: int = 5
    enabled: bool = True

    @classmethod
    def from_dict(cls, data: dict) -> ProtectionConfig:
        fields = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in fields})

    @classmethod
    def conservative(cls) -> ProtectionConfig:
        """Conservative preset — tight limits for small accounts."""
        return cls(
            max_daily_loss_pct=1.0, hard_daily_loss_pct=3.0,
            max_drawdown_pct=5.0, emergency_drawdown_pct=10.0,
            max_position_pct=5.0, max_order_pct=10.0,
            max_concentration_pct=15.0, max_positions=10,
            max_consecutive_losses=3, max_notional=25_000.0,
        )

    @classmethod
    def moderate(cls) -> ProtectionConfig:
        """Moderate preset — balanced for typical retail accounts."""
        return cls()

    @classmethod
    def aggressive(cls) -> ProtectionConfig:
        """Aggressive preset — wider limits for experienced traders."""
        return cls(
            max_daily_loss_pct=5.0, hard_daily_loss_pct=10.0,
            max_drawdown_pct=20.0, emergency_drawdown_pct=30.0,
            max_position_pct=20.0, max_order_pct=40.0,
            max_concentration_pct=35.0, max_positions=50,
            max_consecutive_losses=10, max_notional=500_000.0,
            margin_warn_pct=80.0, margin_block_pct=90.0,
        )


@dataclass
class SafetyReport:
    """Composite result of all guard checks for one order."""
    order_allowed: bool
    order: dict
    guard_results: list[dict]
    blocks: list[str]
    warnings: list[str]
    actions: list[str]
    checked_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "order_allowed": self.order_allowed,
            "order": self.order,
            "guard_results": self.guard_results,
            "blocks": self.blocks,
            "warnings": self.warnings,
            "actions": self.actions,
            "checked_at": self.checked_at,
        }

    def summary(self) -> str:
        if self.order_allowed:
            warns = f" ({len(self.warnings)} warnings)" if self.warnings else ""
            return f"ORDER ALLOWED{warns}"
        return f"ORDER BLOCKED: {'; '.join(self.blocks)}"


class AccountProtectionEngine:
    """Orchestrates all pre-trade safety checks.

    Usage:
        engine = AccountProtectionEngine()
        report = engine.check_order(order_intent, account_snapshot)
        if not report.order_allowed:
            return report.blocks  # Don't execute
    """

    def __init__(self, config: ProtectionConfig | None = None):
        self.config = config or ProtectionConfig()
        self._guards: list[PreTradeGuard] = []
        self._audit_log: deque[dict] = deque(maxlen=500)
        self._peak_equity: float = 0.0
        self._consecutive_losses: int = 0
        self._setup_guards()

    def _setup_guards(self) -> None:
        c = self.config
        self._guards = sorted([
            VolatilityKillswitch(block_vix=c.vix_block_level, flatten_vix=c.vix_flatten_level),
            DailyLossGuard(soft_limit_pct=c.max_daily_loss_pct, hard_limit_pct=c.hard_daily_loss_pct),
            DrawdownGuard(block_pct=c.max_drawdown_pct, flatten_pct=c.emergency_drawdown_pct),
            BuyingPowerGuard(),
            MarginGuard(warn_pct=c.margin_warn_pct, block_pct=c.margin_block_pct),
            TimeRestrictionGuard(
                block_first_minutes=c.block_first_minutes,
                block_last_minutes=c.block_last_minutes,
            ),
            ConsecutiveLossGuard(
                max_consecutive=c.max_consecutive_losses,
                cooldown_minutes=c.cooldown_minutes,
            ),
            FatFingerGuard(max_notional=c.max_notional),
            PositionSizeGuard(
                max_position_pct=c.max_position_pct,
                max_order_pct=c.max_order_pct,
            ),
            MaxPositionsGuard(max_positions=c.max_positions),
            ConcentrationGuard(max_single_pct=c.max_concentration_pct),
            CorrelationGuard(max_sector_pct=c.max_sector_pct),
        ], key=lambda g: g.priority)

    def check_order(
        self, order: OrderIntent, account: AccountSnapshot
    ) -> SafetyReport:
        """Run all guards against an order. Returns a SafetyReport."""
        if not self.config.enabled:
            return SafetyReport(
                order_allowed=True,
                order={"symbol": order.symbol, "side": order.side, "qty": order.qty},
                guard_results=[],
                blocks=[],
                warnings=[],
                actions=[],
            )

        if account.equity > self._peak_equity:
            self._peak_equity = account.equity
        account.peak_equity = self._peak_equity
        account.consecutive_losses = self._consecutive_losses

        results: list[GuardResult] = []
        blocks: list[str] = []
        warnings: list[str] = []
        actions: list[str] = []

        for guard in self._guards:
            if not guard.enabled:
                continue
            try:
                result = guard.check(order, account)
                results.append(result)

                if not result.passed and result.severity == GuardSeverity.BLOCK:
                    blocks.append(result.reason)
                    if result.details.get("action"):
                        actions.append(result.details["action"])

                if result.severity == GuardSeverity.WARN:
                    warnings.append(result.reason)

            except Exception as e:
                logger.error("Guard %s failed: %s", guard.name, e)
                results.append(GuardResult(
                    guard.name, True, GuardSeverity.WARN,
                    f"Guard error (permitting): {e}",
                ))

        order_allowed = len(blocks) == 0
        report = SafetyReport(
            order_allowed=order_allowed,
            order={"symbol": order.symbol, "side": order.side, "qty": order.qty,
                   "broker": order.broker, "order_type": order.order_type},
            guard_results=[r.to_dict() for r in results],
            blocks=blocks,
            warnings=warnings,
            actions=actions,
        )

        self._audit_log.append(report.to_dict())
        return report

    def record_trade_result(self, pnl: float) -> None:
        """Track consecutive losses for the ConsecutiveLossGuard."""
        if pnl < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

    def update_vix(self, vix: float) -> None:
        """Update VIX for the VolatilityKillswitch."""
        for guard in self._guards:
            if isinstance(guard, VolatilityKillswitch):
                guard.update_vix(vix)
                break

    def get_config(self) -> dict:
        """Return current protection config as dict."""
        return {
            k: getattr(self.config, k)
            for k in self.config.__dataclass_fields__
        }

    def set_config(self, updates: dict) -> dict:
        """Update protection config. Returns updated config."""
        for key, value in updates.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)
        self._setup_guards()
        return self.get_config()

    def get_audit_log(self, n: int = 20) -> list[dict]:
        """Return recent safety check history."""
        return list(self._audit_log)[-n:]

    def get_presets(self) -> dict:
        """Return available protection presets."""
        return {
            "conservative": {
                "description": "Tight limits for small/new accounts",
                "max_daily_loss_pct": 1.0,
                "max_drawdown_pct": 5.0,
                "max_position_pct": 5.0,
            },
            "moderate": {
                "description": "Balanced for typical retail accounts",
                "max_daily_loss_pct": 2.0,
                "max_drawdown_pct": 10.0,
                "max_position_pct": 10.0,
            },
            "aggressive": {
                "description": "Wider limits for experienced traders",
                "max_daily_loss_pct": 5.0,
                "max_drawdown_pct": 20.0,
                "max_position_pct": 20.0,
            },
        }

    def apply_preset(self, preset: str) -> dict:
        """Apply a named protection preset."""
        presets = {
            "conservative": ProtectionConfig.conservative,
            "moderate": ProtectionConfig.moderate,
            "aggressive": ProtectionConfig.aggressive,
        }
        factory = presets.get(preset)
        if not factory:
            raise ValueError(f"Unknown preset: {preset}. Available: {list(presets)}")
        self.config = factory()
        self._setup_guards()
        return self.get_config()
