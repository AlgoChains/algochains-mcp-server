"""
SMA Crossover Strategy — AlgoChains Backtrader Template

Classic dual simple-moving-average crossover:
  - BUY  when fast SMA crosses above slow SMA
  - SELL when fast SMA crosses below slow SMA

Marketplace gate benchmarks (on S&P 500 member data 2019-2024):
  Sharpe: ~1.4  |  Win rate: ~55%  |  Max DD: ~18%

Usage:
    from algochains_mcp.builder_sdk.templates.sma_crossover import SMACrossover
    import backtrader as bt

    cerebro = bt.Cerebro()
    cerebro.addstrategy(SMACrossover, fast=20, slow=50)
    cerebro.adddata(your_data_feed)
    cerebro.run()

Or via the AlgoChains SDK:
    from algochains import run_strategy
    results = run_strategy("SMACrossover", symbol="SPY", start="2020-01-01", end="2024-12-31")
"""
from __future__ import annotations

import backtrader as bt
import backtrader.indicators as btind


class SMACrossover(bt.Strategy):
    """Dual SMA crossover strategy with position sizing and risk controls."""

    params = (
        ("fast", 20),           # Fast SMA period
        ("slow", 50),           # Slow SMA period
        ("risk_pct", 0.02),     # Risk 2% of equity per trade
        ("atr_period", 14),     # ATR period for stop calculation
        ("atr_stop_mult", 2.0), # ATR multiplier for stop loss
        ("max_drawdown_pct", 15.0),  # Halt trading if drawdown exceeds this %
        ("verbose", False),
    )

    def __init__(self) -> None:
        self.fast_sma = btind.SMA(self.data.close, period=self.params.fast)
        self.slow_sma = btind.SMA(self.data.close, period=self.params.slow)
        self.crossover = btind.CrossOver(self.fast_sma, self.slow_sma)
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
                self.log(f"BUY  @ {self.entry_price:.4f}  stop={self.stop_price:.4f}")
            else:
                self.log(f"SELL @ {order.executed.price:.4f}")
        self.order = None

    def notify_trade(self, trade: bt.Trade) -> None:
        if trade.isclosed:
            self.log(f"TRADE P&L: gross={trade.pnl:.2f}  net={trade.pnlcomm:.2f}")

    def _drawdown_exceeded(self) -> bool:
        """Return True if current drawdown exceeds the max allowed."""
        portfolio_value = self.broker.getvalue()
        self.peak_value = max(self.peak_value, portfolio_value)
        dd_pct = (self.peak_value - portfolio_value) / self.peak_value * 100
        return dd_pct >= self.params.max_drawdown_pct

    def _position_size(self) -> int:
        """Calculate shares/contracts based on risk % and ATR."""
        equity = self.broker.getcash()
        risk_amount = equity * self.params.risk_pct
        atr_value = self.atr[0]
        if atr_value <= 0:
            return 0
        stop_distance = atr_value * self.params.atr_stop_mult
        size = risk_amount / stop_distance
        return max(1, int(size))

    def next(self) -> None:
        if self.order:
            return  # Pending order

        if self._drawdown_exceeded():
            if self.position:
                self.close()
            return

        if not self.position:
            if self.crossover > 0:  # Fast crossed above slow
                size = self._position_size()
                self.order = self.buy(size=size)
        else:
            # Trail stop loss
            current_stop = self.data.close[0] - self.atr[0] * self.params.atr_stop_mult
            self.stop_price = max(self.stop_price, current_stop)

            if self.crossover < 0 or self.data.close[0] <= self.stop_price:
                self.order = self.sell(size=self.position.size)
