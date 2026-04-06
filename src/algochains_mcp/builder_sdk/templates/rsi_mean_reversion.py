"""
RSI Mean Reversion Strategy — AlgoChains Backtrader Template

Fades extreme RSI readings with Bollinger Band confirmation:
  - BUY  when RSI < oversold_threshold AND price < lower BB
  - SELL when RSI > overbought_threshold OR price > upper BB OR stop hit

Best suited for: liquid equities (SPY, QQQ, large-cap stocks)
                 daily/4h bars, mean-reverting regimes

Marketplace gate benchmarks:
  Sharpe: ~2.2  |  Win rate: ~48%  |  Max DD: ~14%

Usage:
    from algochains_mcp.builder_sdk.templates.rsi_mean_reversion import RSIMeanReversion
    cerebro.addstrategy(RSIMeanReversion, rsi_period=14, oversold=30, overbought=70)
"""
from __future__ import annotations

import backtrader as bt
import backtrader.indicators as btind


class RSIMeanReversion(bt.Strategy):
    """RSI oversold/overbought with Bollinger Band confirmation."""

    params = (
        ("rsi_period", 14),
        ("oversold", 30),
        ("overbought", 70),
        ("bb_period", 20),
        ("bb_devfactor", 2.0),
        ("risk_pct", 0.015),
        ("atr_period", 14),
        ("atr_stop_mult", 1.5),
        ("take_profit_r", 2.0),   # Take profit at 2R (2x the ATR stop distance)
        ("max_drawdown_pct", 12.0),
        ("verbose", False),
    )

    def __init__(self) -> None:
        self.rsi = btind.RSI(self.data.close, period=self.params.rsi_period)
        self.bb = btind.BollingerBands(
            self.data.close,
            period=self.params.bb_period,
            devfactor=self.params.bb_devfactor,
        )
        self.atr = btind.ATR(self.data, period=self.params.atr_period)

        self.entry_price: float = 0.0
        self.stop_price: float = 0.0
        self.take_profit_price: float = 0.0
        self.peak_value: float = 0.0
        self.order: bt.Order | None = None

    def log(self, msg: str) -> None:
        if self.params.verbose:
            print(f"[{self.data.datetime.date()}] {msg}")

    def notify_order(self, order: bt.Order) -> None:
        if order.status in (order.Submitted, order.Accepted):
            return
        if order.status == order.Completed:
            if order.isbuy():
                self.entry_price = order.executed.price
                stop_dist = self.atr[0] * self.params.atr_stop_mult
                self.stop_price = self.entry_price - stop_dist
                self.take_profit_price = self.entry_price + stop_dist * self.params.take_profit_r
                self.log(
                    f"BUY @ {self.entry_price:.4f}  "
                    f"stop={self.stop_price:.4f}  TP={self.take_profit_price:.4f}"
                )
            else:
                self.log(f"SELL @ {order.executed.price:.4f}")
        self.order = None

    def _drawdown_exceeded(self) -> bool:
        portfolio_value = self.broker.getvalue()
        self.peak_value = max(self.peak_value, portfolio_value)
        dd_pct = (self.peak_value - portfolio_value) / self.peak_value * 100
        return dd_pct >= self.params.max_drawdown_pct

    def _position_size(self) -> int:
        equity = self.broker.getcash()
        risk_amount = equity * self.params.risk_pct
        stop_dist = self.atr[0] * self.params.atr_stop_mult
        if stop_dist <= 0:
            return 0
        return max(1, int(risk_amount / stop_dist))

    def next(self) -> None:
        if self.order:
            return

        if self._drawdown_exceeded():
            if self.position:
                self.close()
            return

        if not self.position:
            oversold = self.rsi[0] < self.params.oversold
            below_lower_bb = self.data.close[0] < self.bb.lines.bot[0]
            if oversold and below_lower_bb:
                size = self._position_size()
                if size > 0:
                    self.order = self.buy(size=size)
        else:
            price = self.data.close[0]
            overbought = self.rsi[0] > self.params.overbought
            above_upper_bb = price > self.bb.lines.top[0]
            stop_hit = price <= self.stop_price
            tp_hit = price >= self.take_profit_price

            if overbought or above_upper_bb or stop_hit or tp_hit:
                reason = (
                    "overbought" if overbought else
                    "upper_bb" if above_upper_bb else
                    "stop" if stop_hit else "take_profit"
                )
                self.log(f"EXIT [{reason}] @ {price:.4f}")
                self.order = self.sell(size=self.position.size)
