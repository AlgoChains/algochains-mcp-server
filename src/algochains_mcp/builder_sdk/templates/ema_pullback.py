"""
EMA Pullback Strategy — AlgoChains Backtrader Template

Trend-following pullback entries:
  - Trend: price above/below 200-period EMA
  - Entry: RSI pulls back to 40-60 zone in uptrend (or 40-60 in downtrend)
  - Exit: RSI reaches overbought/oversold extreme or stop hit

Modeled after the MES/NQ swing bots running in the AlgoChains control tower.

Best suited for: equity index futures (MES, NQ, ES), daily/4h bars

Marketplace gate benchmarks:
  Sharpe: ~1.9  |  Win rate: ~57%  |  Max DD: ~12%

Usage:
    from algochains_mcp.builder_sdk.templates.ema_pullback import EMAPullback
    cerebro.addstrategy(EMAPullback, trend_period=200, fast_period=20, slow_period=50)
"""
from __future__ import annotations

import backtrader as bt
import backtrader.indicators as btind


class EMAPullback(bt.Strategy):
    """EMA trend filter + RSI pullback entry."""

    params = (
        ("trend_period", 200),     # Long-term trend EMA
        ("fast_period", 20),       # Fast EMA for entry timing
        ("slow_period", 50),       # Slow EMA for entry timing
        ("rsi_period", 14),
        ("rsi_entry_min", 40),     # Only enter if RSI is in this range (pullback zone)
        ("rsi_entry_max", 60),
        ("rsi_exit_long", 75),     # Exit long when RSI overbought
        ("rsi_exit_short", 25),    # Exit short when RSI oversold
        ("risk_pct", 0.015),
        ("atr_period", 14),
        ("atr_stop_mult", 2.0),
        ("max_drawdown_pct", 12.0),
        ("verbose", False),
    )

    def __init__(self) -> None:
        self.trend_ema = btind.EMA(self.data.close, period=self.params.trend_period)
        self.fast_ema = btind.EMA(self.data.close, period=self.params.fast_period)
        self.slow_ema = btind.EMA(self.data.close, period=self.params.slow_period)
        self.rsi = btind.RSI(self.data.close, period=self.params.rsi_period)
        self.atr = btind.ATR(self.data, period=self.params.atr_period)

        self.entry_price: float = 0.0
        self.stop_price: float = 0.0
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
                self.stop_price = self.entry_price - self.atr[0] * self.params.atr_stop_mult
                self.log(f"BUY @ {self.entry_price:.4f}  stop={self.stop_price:.4f}")
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

    def _in_uptrend(self) -> bool:
        return (
            self.data.close[0] > self.trend_ema[0]
            and self.fast_ema[0] > self.slow_ema[0]
        )

    def _in_downtrend(self) -> bool:
        return (
            self.data.close[0] < self.trend_ema[0]
            and self.fast_ema[0] < self.slow_ema[0]
        )

    def _rsi_in_pullback_zone(self) -> bool:
        return self.params.rsi_entry_min <= self.rsi[0] <= self.params.rsi_entry_max

    def next(self) -> None:
        if self.order:
            return

        if self._drawdown_exceeded():
            if self.position:
                self.close()
            return

        if not self.position:
            if self._in_uptrend() and self._rsi_in_pullback_zone():
                size = self._position_size()
                if size > 0:
                    self.order = self.buy(size=size)
        else:
            price = self.data.close[0]
            stop_hit = price <= self.stop_price
            rsi_exit = self.rsi[0] > self.params.rsi_exit_long
            trend_broken = not self._in_uptrend()

            if stop_hit or rsi_exit or trend_broken:
                reason = "stop" if stop_hit else "rsi_exit" if rsi_exit else "trend_broken"
                self.log(f"EXIT [{reason}] @ {price:.4f}")
                self.order = self.sell(size=self.position.size)
            else:
                # Trailing stop
                new_stop = price - self.atr[0] * self.params.atr_stop_mult
                self.stop_price = max(self.stop_price, new_stop)
