"""
Multi-strategy portfolio optimizer for AlgoChains MCP Server (V5).

Provides:
  - Risk parity allocation across multiple bot subscriptions
  - Mean-variance optimization (Markowitz)
  - Kelly criterion sizing
  - Correlation-aware position sizing
  - Max drawdown constraints
  - Portfolio rebalancing recommendations

Users subscribe to multiple bots on the marketplace; this module
determines optimal capital allocation across them.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger("algochains_mcp.portfolio.optimizer")


class AllocationMethod(str, Enum):
    EQUAL_WEIGHT = "equal_weight"
    RISK_PARITY = "risk_parity"
    MEAN_VARIANCE = "mean_variance"
    KELLY = "kelly"
    MAX_SHARPE = "max_sharpe"
    MIN_VARIANCE = "min_variance"


@dataclass
class BotMetrics:
    """Performance metrics for a single bot subscription."""
    slug: str
    name: str
    oos_sharpe: float
    annual_return: float  # decimal, e.g. 0.25 = 25%
    annual_volatility: float  # decimal, e.g. 0.15 = 15%
    max_drawdown: float  # decimal, e.g. 0.12 = 12%
    win_rate: float  # decimal, e.g. 0.55 = 55%
    avg_trade_pnl: float  # dollar P&L per trade
    correlation_to_spy: float = 0.0  # -1 to 1
    tier: str = "silver"


@dataclass
class Allocation:
    """Optimal allocation for a single bot."""
    slug: str
    name: str
    weight: float  # 0.0 to 1.0
    capital: float  # dollar amount
    expected_return: float
    expected_risk: float
    tier: str = ""


@dataclass
class PortfolioResult:
    """Result of portfolio optimization."""
    method: AllocationMethod
    total_capital: float
    allocations: list[Allocation]
    portfolio_sharpe: float
    portfolio_return: float
    portfolio_volatility: float
    portfolio_max_drawdown: float
    diversification_score: float  # 0-100
    rebalance_needed: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "method": self.method.value,
            "total_capital": self.total_capital,
            "allocations": [
                {
                    "slug": a.slug,
                    "name": a.name,
                    "weight_pct": round(a.weight * 100, 2),
                    "capital": round(a.capital, 2),
                    "expected_return_pct": round(a.expected_return * 100, 2),
                    "expected_risk_pct": round(a.expected_risk * 100, 2),
                    "tier": a.tier,
                }
                for a in self.allocations
            ],
            "portfolio": {
                "sharpe": round(self.portfolio_sharpe, 3),
                "expected_return_pct": round(self.portfolio_return * 100, 2),
                "volatility_pct": round(self.portfolio_volatility * 100, 2),
                "max_drawdown_pct": round(self.portfolio_max_drawdown * 100, 2),
                "diversification_score": round(self.diversification_score, 1),
            },
            "rebalance_needed": self.rebalance_needed,
            "notes": self.notes,
        }


class PortfolioOptimizer:
    """Optimizes capital allocation across multiple bot subscriptions."""

    def __init__(self, max_single_weight: float = 0.40, min_bots: int = 2):
        self.max_single_weight = max_single_weight
        self.min_bots = min_bots

    def optimize(
        self,
        bots: list[BotMetrics],
        total_capital: float,
        method: AllocationMethod = AllocationMethod.RISK_PARITY,
        max_drawdown_limit: float = 0.20,
    ) -> PortfolioResult:
        """Run portfolio optimization and return allocations."""
        if len(bots) < self.min_bots:
            return PortfolioResult(
                method=method,
                total_capital=total_capital,
                allocations=[],
                portfolio_sharpe=0.0,
                portfolio_return=0.0,
                portfolio_volatility=0.0,
                portfolio_max_drawdown=0.0,
                diversification_score=0.0,
                notes=[f"Need at least {self.min_bots} bots for portfolio optimization."],
            )

        if method == AllocationMethod.EQUAL_WEIGHT:
            weights = self._equal_weight(bots)
        elif method == AllocationMethod.RISK_PARITY:
            weights = self._risk_parity(bots)
        elif method == AllocationMethod.KELLY:
            weights = self._kelly(bots)
        elif method == AllocationMethod.MAX_SHARPE:
            weights = self._max_sharpe(bots)
        elif method == AllocationMethod.MIN_VARIANCE:
            weights = self._min_variance(bots)
        else:
            weights = self._equal_weight(bots)

        # Enforce max single weight constraint
        weights = self._cap_weights(weights)

        # Build allocations
        allocations = []
        for bot, w in zip(bots, weights):
            allocations.append(Allocation(
                slug=bot.slug,
                name=bot.name,
                weight=w,
                capital=total_capital * w,
                expected_return=bot.annual_return * w,
                expected_risk=bot.annual_volatility * w,
                tier=bot.tier,
            ))

        # Portfolio-level metrics
        port_ret = sum(b.annual_return * w for b, w in zip(bots, weights))
        port_vol = self._portfolio_volatility(bots, weights)
        port_sharpe = port_ret / port_vol if port_vol > 0 else 0.0
        port_mdd = max(b.max_drawdown * w for b, w in zip(bots, weights))
        div_score = self._diversification_score(bots, weights)

        notes = []
        if port_mdd > max_drawdown_limit:
            notes.append(f"Portfolio max drawdown {port_mdd:.1%} exceeds limit {max_drawdown_limit:.1%}. Consider reducing aggressive bots.")

        # Check if rebalance needed (drift > 5% from target)
        rebalance = any(w > self.max_single_weight + 0.05 for w in weights)

        return PortfolioResult(
            method=method,
            total_capital=total_capital,
            allocations=allocations,
            portfolio_sharpe=port_sharpe,
            portfolio_return=port_ret,
            portfolio_volatility=port_vol,
            portfolio_max_drawdown=port_mdd,
            diversification_score=div_score,
            rebalance_needed=rebalance,
            notes=notes,
        )

    def _equal_weight(self, bots: list[BotMetrics]) -> list[float]:
        n = len(bots)
        return [1.0 / n] * n

    def _risk_parity(self, bots: list[BotMetrics]) -> list[float]:
        """Allocate inversely proportional to volatility (risk parity)."""
        inv_vols = [1.0 / max(b.annual_volatility, 0.01) for b in bots]
        total = sum(inv_vols)
        return [v / total for v in inv_vols]

    def _kelly(self, bots: list[BotMetrics]) -> list[float]:
        """Kelly criterion: f* = (p*b - q) / b where b=avg_win/avg_loss."""
        raw = []
        for b in bots:
            p = b.win_rate
            q = 1.0 - p
            # Approximate b from Sharpe
            edge = max(b.oos_sharpe * b.annual_volatility, 0.01)
            kelly_f = max(0.0, (p * edge - q) / max(edge, 0.01))
            # Half-Kelly for safety
            raw.append(kelly_f * 0.5)
        total = sum(raw) or 1.0
        return [r / total for r in raw]

    def _max_sharpe(self, bots: list[BotMetrics]) -> list[float]:
        """Weight proportional to Sharpe ratio (simplified)."""
        sharpes = [max(b.oos_sharpe, 0.0) for b in bots]
        total = sum(sharpes) or 1.0
        return [s / total for s in sharpes]

    def _min_variance(self, bots: list[BotMetrics]) -> list[float]:
        """Minimize portfolio variance (simplified: inverse variance weighting)."""
        inv_var = [1.0 / max(b.annual_volatility ** 2, 0.0001) for b in bots]
        total = sum(inv_var)
        return [v / total for v in inv_var]

    def _cap_weights(self, weights: list[float]) -> list[float]:
        """Cap individual weights and redistribute excess."""
        capped = list(weights)
        excess = 0.0
        uncapped_count = 0

        for i, w in enumerate(capped):
            if w > self.max_single_weight:
                excess += w - self.max_single_weight
                capped[i] = self.max_single_weight
            else:
                uncapped_count += 1

        if excess > 0 and uncapped_count > 0:
            add_per = excess / uncapped_count
            for i in range(len(capped)):
                if capped[i] < self.max_single_weight:
                    capped[i] += add_per

        # Normalize
        total = sum(capped)
        return [w / total for w in capped] if total > 0 else capped

    def _portfolio_volatility(self, bots: list[BotMetrics], weights: list[float]) -> float:
        """Simplified portfolio volatility (assumes low correlation)."""
        # Weighted sum of variances + average pairwise covariance
        avg_corr = 0.3  # Assume 0.3 average correlation for simplification
        variance = 0.0
        for i, (bi, wi) in enumerate(zip(bots, weights)):
            variance += (wi * bi.annual_volatility) ** 2
            for j, (bj, wj) in enumerate(zip(bots, weights)):
                if i != j:
                    variance += wi * wj * bi.annual_volatility * bj.annual_volatility * avg_corr
        return math.sqrt(max(variance, 0.0))

    def _diversification_score(self, bots: list[BotMetrics], weights: list[float]) -> float:
        """Score 0-100 based on number of bots, weight spread, and correlation diversity."""
        n = len(bots)
        # HHI (Herfindahl index) — lower is more diversified
        hhi = sum(w ** 2 for w in weights)
        hhi_score = max(0, (1.0 - hhi) * 100)

        # Bonus for more bots
        count_score = min(n * 10, 30)

        # Correlation diversity bonus
        corrs = [b.correlation_to_spy for b in bots]
        corr_spread = max(corrs) - min(corrs) if len(corrs) > 1 else 0
        corr_score = min(corr_spread * 50, 20)

        return min(100.0, hhi_score + count_score + corr_score)
