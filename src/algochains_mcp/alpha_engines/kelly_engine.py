"""Kelly criterion position sizing — optimal f, fractional Kelly, risk-adjusted sizing.

Computes mathematically optimal position sizes based on edge and odds,
with fractional Kelly for practical risk management.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("algochains_mcp.alpha_engines.kelly")


class KellyEngine:
    """Kelly criterion and optimal position sizing."""

    def __init__(self) -> None:
        pass

    async def compute_kelly(
        self,
        win_rate: float,
        avg_win: float,
        avg_loss: float,
        fraction: float = 0.5,
        account_equity: float = 100000,
        max_risk_pct: float = 5.0,
    ) -> dict[str, Any]:
        """Compute Kelly criterion position size.

        Args:
            win_rate: Historical win rate (0-1)
            avg_win: Average winning trade size ($)
            avg_loss: Average losing trade size ($, positive number)
            fraction: Kelly fraction (0.25=quarter, 0.5=half, 1.0=full)
            account_equity: Current account equity
            max_risk_pct: Maximum risk per trade as % of equity
        """
        if win_rate <= 0 or win_rate >= 1:
            return {"status": "error", "error": "win_rate must be between 0 and 1"}
        if avg_win <= 0 or avg_loss <= 0:
            return {"status": "error", "error": "avg_win and avg_loss must be positive"}

        loss_rate = 1 - win_rate
        win_loss_ratio = avg_win / avg_loss

        full_kelly = win_rate - (loss_rate / win_loss_ratio)
        fractional_kelly = full_kelly * fraction

        if full_kelly <= 0:
            return {
                "status": "ok",
                "signal": "no_edge",
                "full_kelly_pct": round(full_kelly * 100, 4),
                "message": "Negative edge — Kelly says do not trade",
                "win_rate": win_rate,
                "win_loss_ratio": round(win_loss_ratio, 4),
                "expected_value": round(win_rate * avg_win - loss_rate * avg_loss, 2),
                "as_of": datetime.now(timezone.utc).isoformat(),
            }

        risk_pct = min(fractional_kelly * 100, max_risk_pct)
        position_size = account_equity * (risk_pct / 100)

        sharpe_approx = (win_rate * avg_win - loss_rate * avg_loss) / (
            math.sqrt(win_rate * avg_win**2 + loss_rate * avg_loss**2)
        ) if (win_rate * avg_win**2 + loss_rate * avg_loss**2) > 0 else 0

        growth_rate = win_rate * math.log(1 + fractional_kelly * win_loss_ratio) + loss_rate * math.log(1 - fractional_kelly)

        return {
            "status": "ok",
            "signal": "trade",
            "full_kelly_pct": round(full_kelly * 100, 4),
            "fractional_kelly_pct": round(fractional_kelly * 100, 4),
            "applied_risk_pct": round(risk_pct, 4),
            "position_size_dollars": round(position_size, 2),
            "kelly_fraction_used": fraction,
            "max_risk_cap_pct": max_risk_pct,
            "was_capped": risk_pct >= max_risk_pct,
            "win_rate": win_rate,
            "win_loss_ratio": round(win_loss_ratio, 4),
            "expected_value_per_trade": round(win_rate * avg_win - loss_rate * avg_loss, 2),
            "expected_growth_rate": round(growth_rate, 6),
            "approx_sharpe": round(sharpe_approx, 4),
            "account_equity": account_equity,
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    async def multi_strategy_kelly(
        self,
        strategies: list[dict[str, Any]],
        account_equity: float = 100000,
        max_total_risk_pct: float = 20.0,
    ) -> dict[str, Any]:
        """Compute Kelly allocation across multiple strategies.

        Each strategy dict should have: name, win_rate, avg_win, avg_loss
        """
        allocations = []
        total_risk = 0.0

        for strat in strategies:
            result = await self.compute_kelly(
                win_rate=strat["win_rate"],
                avg_win=strat["avg_win"],
                avg_loss=strat["avg_loss"],
                fraction=0.5,
                account_equity=account_equity,
            )
            if result.get("signal") == "trade":
                allocations.append({
                    "name": strat.get("name", "unnamed"),
                    "kelly_pct": result["fractional_kelly_pct"],
                    "ev_per_trade": result["expected_value_per_trade"],
                    "sharpe": result["approx_sharpe"],
                })
                total_risk += result["fractional_kelly_pct"]

        if total_risk > max_total_risk_pct and total_risk > 0:
            scale = max_total_risk_pct / total_risk
            for a in allocations:
                a["kelly_pct"] = round(a["kelly_pct"] * scale, 4)
                a["scaled"] = True
            total_risk = max_total_risk_pct

        for a in allocations:
            a["position_size"] = round(account_equity * a["kelly_pct"] / 100, 2)

        allocations.sort(key=lambda x: x["ev_per_trade"], reverse=True)

        return {
            "status": "ok",
            "strategy_count": len(strategies),
            "tradeable_count": len(allocations),
            "total_risk_pct": round(total_risk, 4),
            "max_total_risk_pct": max_total_risk_pct,
            "account_equity": account_equity,
            "allocations": allocations,
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    async def drawdown_risk(
        self,
        kelly_pct: float,
        num_trades: int = 100,
        confidence: float = 0.95,
    ) -> dict[str, Any]:
        """Estimate maximum drawdown risk for a given Kelly fraction."""
        if kelly_pct <= 0:
            return {"status": "error", "error": "kelly_pct must be positive"}

        ruin_prob = (1 - kelly_pct / 100) ** num_trades if kelly_pct < 100 else 0
        max_dd_est = 1 - (1 - kelly_pct / 100) ** (math.log(1 - confidence) / math.log(0.5))

        return {
            "status": "ok",
            "kelly_pct": kelly_pct,
            "num_trades": num_trades,
            "confidence_level": confidence,
            "estimated_max_drawdown_pct": round(max_dd_est * 100, 2),
            "ruin_probability": round(ruin_prob, 6),
            "recommendation": "safe" if max_dd_est < 0.25 else "moderate" if max_dd_est < 0.50 else "aggressive",
            "as_of": datetime.now(timezone.utc).isoformat(),
        }
