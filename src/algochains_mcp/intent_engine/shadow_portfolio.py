"""
V18 Shadow Portfolio Engine — Forward-test strategies without capital risk.

Creates paper-traded mirrors of strategies running against live market data.
Tracks shadow P&L, fills, and metrics alongside the real portfolio for
side-by-side comparison before promoting to live.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional, Any

logger = logging.getLogger("algochains.shadow_portfolio")


@dataclass
class ShadowFill:
    """A simulated fill in a shadow portfolio."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    symbol: str = ""
    side: str = "buy"
    qty: float = 0.0
    price: float = 0.0
    timestamp: float = field(default_factory=time.time)
    slippage_bps: float = 2.0

    def to_dict(self) -> dict:
        return {
            "id": self.id, "symbol": self.symbol, "side": self.side,
            "qty": self.qty, "price": self.price,
            "timestamp": self.timestamp, "slippage_bps": self.slippage_bps,
        }


@dataclass
class ShadowPosition:
    """A position in a shadow portfolio."""
    symbol: str = ""
    qty: float = 0.0
    avg_entry: float = 0.0
    current_price: float = 0.0
    unrealized_pnl: float = 0.0

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol, "qty": self.qty,
            "avg_entry": self.avg_entry, "current_price": self.current_price,
            "unrealized_pnl": self.unrealized_pnl,
        }


@dataclass
class ShadowPortfolio:
    """A complete shadow (paper) portfolio for forward-testing."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    strategy_id: Optional[str] = None
    broker: str = ""
    initial_capital: float = 100_000.0
    cash: float = 100_000.0
    positions: dict[str, ShadowPosition] = field(default_factory=dict)
    fills: list[ShadowFill] = field(default_factory=list)
    realized_pnl: float = 0.0
    peak_equity: float = 100_000.0
    max_drawdown: float = 0.0
    trade_count: int = 0
    win_count: int = 0
    created_at: float = field(default_factory=time.time)
    active: bool = True

    @property
    def equity(self) -> float:
        unrealized = sum(p.unrealized_pnl for p in self.positions.values())
        return self.cash + unrealized + sum(
            p.qty * p.current_price for p in self.positions.values()
        )

    @property
    def total_pnl(self) -> float:
        return self.equity - self.initial_capital

    @property
    def total_return_pct(self) -> float:
        if self.initial_capital == 0:
            return 0.0
        return (self.equity / self.initial_capital - 1) * 100

    @property
    def win_rate(self) -> float:
        if self.trade_count == 0:
            return 0.0
        return self.win_count / self.trade_count

    @property
    def sharpe_estimate(self) -> float:
        if self.trade_count < 5:
            return 0.0
        days = max((time.time() - self.created_at) / 86400, 1)
        daily_return = self.total_return_pct / 100 / days
        # Rough estimate — proper Sharpe needs return series
        return daily_return * (252 ** 0.5) / max(abs(self.max_drawdown / 100), 0.01)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "strategy_id": self.strategy_id,
            "broker": self.broker,
            "initial_capital": self.initial_capital,
            "cash": round(self.cash, 2),
            "equity": round(self.equity, 2),
            "total_pnl": round(self.total_pnl, 2),
            "total_return_pct": round(self.total_return_pct, 2),
            "realized_pnl": round(self.realized_pnl, 2),
            "peak_equity": round(self.peak_equity, 2),
            "max_drawdown_pct": round(self.max_drawdown, 2),
            "trade_count": self.trade_count,
            "win_rate": round(self.win_rate, 4),
            "sharpe_estimate": round(self.sharpe_estimate, 2),
            "positions": {k: v.to_dict() for k, v in self.positions.items()},
            "position_count": len(self.positions),
            "fill_count": len(self.fills),
            "active": self.active,
            "age_hours": round((time.time() - self.created_at) / 3600, 1),
        }


class ShadowPortfolioEngine:
    """Manage shadow portfolios for forward-testing strategies without risk."""

    MAX_SHADOWS = 20

    def __init__(self):
        self._shadows: dict[str, ShadowPortfolio] = {}

    async def create(
        self,
        name: str,
        strategy_id: Optional[str] = None,
        broker: str = "alpaca",
        capital: float = 100_000.0,
    ) -> dict:
        """Create a new shadow portfolio."""
        if len(self._shadows) >= self.MAX_SHADOWS:
            oldest = min(self._shadows.values(), key=lambda s: s.created_at)
            del self._shadows[oldest.id]

        shadow = ShadowPortfolio(
            name=name,
            strategy_id=strategy_id,
            broker=broker,
            initial_capital=capital,
            cash=capital,
            peak_equity=capital,
        )
        self._shadows[shadow.id] = shadow
        logger.info("Created shadow portfolio '%s' ($%,.0f)", name, capital)
        return shadow.to_dict()

    async def paper_fill(
        self,
        shadow_id: str,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        slippage_bps: float = 2.0,
    ) -> dict:
        """Record a simulated fill in a shadow portfolio."""
        shadow = self._shadows.get(shadow_id)
        if not shadow:
            return {"error": f"Shadow portfolio '{shadow_id}' not found"}
        if not shadow.active:
            return {"error": "Shadow portfolio is inactive"}

        # Apply slippage
        adj_price = price * (1 + slippage_bps / 10000) if side == "buy" else price * (1 - slippage_bps / 10000)

        fill = ShadowFill(
            symbol=symbol, side=side, qty=qty,
            price=adj_price, slippage_bps=slippage_bps,
        )
        shadow.fills.append(fill)

        pos = shadow.positions.get(symbol)
        if side == "buy":
            cost = adj_price * qty
            if shadow.cash < cost:
                return {"error": f"Insufficient cash: ${shadow.cash:,.2f} < ${cost:,.2f}"}
            shadow.cash -= cost

            if pos:
                total_qty = pos.qty + qty
                pos.avg_entry = (pos.avg_entry * pos.qty + adj_price * qty) / total_qty
                pos.qty = total_qty
            else:
                pos = ShadowPosition(symbol=symbol, qty=qty, avg_entry=adj_price, current_price=adj_price)
                shadow.positions[symbol] = pos

        elif side == "sell":
            if not pos or pos.qty < qty:
                return {"error": f"Insufficient position in {symbol}"}

            proceeds = adj_price * qty
            shadow.cash += proceeds

            trade_pnl = (adj_price - pos.avg_entry) * qty
            shadow.realized_pnl += trade_pnl
            shadow.trade_count += 1
            if trade_pnl > 0:
                shadow.win_count += 1

            pos.qty -= qty
            if pos.qty <= 0.001:
                del shadow.positions[symbol]

        # Update peak/drawdown
        eq = shadow.equity
        if eq > shadow.peak_equity:
            shadow.peak_equity = eq
        dd = (shadow.peak_equity - eq) / shadow.peak_equity * 100 if shadow.peak_equity > 0 else 0
        if dd > shadow.max_drawdown:
            shadow.max_drawdown = dd

        return {"fill": fill.to_dict(), "portfolio": shadow.to_dict()}

    async def update_prices(self, shadow_id: str, prices: dict[str, float]) -> dict:
        """Update current prices for all positions in a shadow portfolio."""
        shadow = self._shadows.get(shadow_id)
        if not shadow:
            return {"error": f"Shadow portfolio '{shadow_id}' not found"}

        updated = 0
        for symbol, pos in shadow.positions.items():
            if symbol in prices:
                pos.current_price = prices[symbol]
                pos.unrealized_pnl = (pos.current_price - pos.avg_entry) * pos.qty
                updated += 1

        return {"updated": updated, "equity": round(shadow.equity, 2)}

    async def get_results(self, shadow_id: str) -> dict:
        """Get detailed results for a shadow portfolio."""
        shadow = self._shadows.get(shadow_id)
        if not shadow:
            return {"error": f"Shadow portfolio '{shadow_id}' not found"}
        return shadow.to_dict()

    async def compare(self, shadow_id: str, live_metrics: Optional[dict] = None) -> dict:
        """Compare shadow portfolio performance against live metrics."""
        shadow = self._shadows.get(shadow_id)
        if not shadow:
            return {"error": f"Shadow portfolio '{shadow_id}' not found"}

        shadow_metrics = {
            "pnl": round(shadow.total_pnl, 2),
            "return_pct": round(shadow.total_return_pct, 2),
            "max_drawdown_pct": round(shadow.max_drawdown, 2),
            "trade_count": shadow.trade_count,
            "win_rate": round(shadow.win_rate, 4),
            "sharpe_estimate": round(shadow.sharpe_estimate, 2),
        }

        result = {"shadow": shadow_metrics}

        if live_metrics:
            result["live"] = live_metrics
            pnl_delta = shadow_metrics["pnl"] - live_metrics.get("pnl", 0)
            result["difference"] = {
                "pnl_delta": round(pnl_delta, 2),
                "return_delta": round(
                    shadow_metrics["return_pct"] - live_metrics.get("return_pct", 0), 2
                ),
            }
            if pnl_delta > 0:
                result["recommendation"] = "Shadow outperforms live. Consider promoting."
            elif pnl_delta < 0:
                result["recommendation"] = "Live outperforms shadow. Keep current strategy."
            else:
                result["recommendation"] = "Performance is comparable."

        return result

    async def list_shadows(self) -> list[dict]:
        """List all shadow portfolios."""
        return [s.to_dict() for s in sorted(
            self._shadows.values(), key=lambda s: s.created_at, reverse=True
        )]

    async def deactivate(self, shadow_id: str) -> dict:
        """Stop a shadow portfolio from accepting new fills."""
        shadow = self._shadows.get(shadow_id)
        if not shadow:
            return {"error": f"Shadow portfolio '{shadow_id}' not found"}
        shadow.active = False
        return {"deactivated": shadow_id, "final_pnl": round(shadow.total_pnl, 2)}
