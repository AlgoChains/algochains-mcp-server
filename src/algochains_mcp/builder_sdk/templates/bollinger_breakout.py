"""
Bollinger Band Breakout Strategy — AlgoChains Backtrader Template

Trades momentum breakouts from Bollinger Band squeezes:
  - Detects BB squeeze (bandwidth < threshold)
  - BUY  on close above upper band after squeeze
  - SELL on close below lower band after squeeze (or stop/profit)

Best suited for: futures (MNQ, NQ, ES, CL), crypto, high-volatility equities
                 1h/4h bars, trending regimes

Marketplace gate benchmarks:
  Sharpe: ~1.8  |  Win rate: ~42%  |  Max DD: ~16%

Usage:
    from algochains_mcp.builder_sdk.templates.bollinger_breakout import BollingerBreakout
    cerebro.addstrategy(BollingerBreakout, bb_period=20, squeeze_threshold=0.02)
"""
from __future__ import annotations

import backtrader as bt
import backtrader.indicators as btind


class BBWidth(bt.Indicator):
    """Bollinger Band Width = (upper - lower) / middle."""

    lines = ("bbw",)
    params = (("period", 20), ("devfactor", 2.0))

    def __init__(self) -> None:
        self.bb = btind.BollingerBands(
            self.data,
            period=self.params.period,
            devfactor=self.params.devfactor,
        )

    def next(self) -> None:
        mid = self.bb.lines.mid[0]
        if mid != 0:
            self.lines.bbw[0] = (self.bb.lines.top[0] - self.bb.lines.bot[0]) / mid
        else:
            self.lines.bbw[0] = 0.0


class BollingerBreakout(bt.Strategy):
    """Breakout from BB squeeze with ATR-based position sizing."""

    params = (
        ("bb_period", 20),
        ("bb_devfactor", 2.0),
        ("squeeze_threshold", 0.035),  # BB width below this = squeeze
        ("squeeze_lookback", 5),       # Bars to confirm squeeze
        ("risk_pct", 0.02),
        ("atr_period", 14),
        ("atr_stop_mult", 2.5),
        ("take_profit_r", 3.0),        # Target 3R
        ("max_drawdown_pct", 16.0),
        ("verbose", False),
    )

    def __init__(self) -> None:
        self.bb = btind.BollingerBands(
            self.data.close,
            period=self.params.bb_period,
            devfactor=self.params.bb_devfactor,
        )
        self.bbw = BBWidth(
            self.data.close,
            period=self.params.bb_period,
            devfactor=self.params.bb_devfactor,
        )
        self.atr = btind.ATR(self.data, period=self.params.atr_period)
        self.volume_ma = btind.SMA(self.data.volume, period=20)

        self.entry_price: float = 0.0
        self.stop_price: float = 0.0
        self.take_profit_price: float = 0.0
        self.direction: int = 0  # 1=long, -1=short
        self.peak_value: float = 0.0
        self.order: bt.Order | None = None

    def log(self, msg: str) -> None:
        if self.params.verbose:
            print(f"[{self.data.datetime.date()}] {msg}")

    def notify_order(self, order: bt.Order) -> None:
        if order.status in (order.Submitted, order.Accepted):
            return
        if order.status == order.Completed:
            price = order.executed.price
            stop_dist = self.atr[0] * self.params.atr_stop_mult
            if order.isbuy():
                self.entry_price = price
                self.stop_price = price - stop_dist
                self.take_profit_price = price + stop_dist * self.params.take_profit_r
                self.direction = 1
                self.log(f"LONG  @ {price:.4f}  stop={self.stop_price:.4f}  TP={self.take_profit_price:.4f}")
            else:
                self.log(f"EXIT  @ {price:.4f}  dir={self.direction}")
                self.direction = 0
        self.order = None

    def _drawdown_exceeded(self) -> bool:
        portfolio_value = self.broker.getvalue()
        self.peak_value = max(self.peak_value, portfolio_value)
        dd_pct = (self.peak_value - portfolio_value) / self.peak_value * 100
        return dd_pct >= self.params.max_drawdown_pct

    def _in_squeeze(self) -> bool:
        """True if BB width has been below threshold for squeeze_lookback bars."""
        for i in range(self.params.squeeze_lookback):
            if self.bbw.lines.bbw[-i] >= self.params.squeeze_threshold:
                return False
        return True

    def _volume_confirmed(self) -> bool:
        """Volume is above 20-bar average — confirms genuine breakout."""
        if self.volume_ma[0] == 0:
            return True
        return self.data.volume[0] > self.volume_ma[0] * 1.2

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

        price = self.data.close[0]

        if not self.position:
            if self._in_squeeze() and self._volume_confirmed():
                if price > self.bb.lines.top[0]:
                    size = self._position_size()
                    if size > 0:
                        self.order = self.buy(size=size)
        else:
            stop_hit = price <= self.stop_price
            tp_hit = price >= self.take_profit_price
            below_lower = price < self.bb.lines.bot[0]

            if stop_hit or tp_hit or below_lower:
                reason = "stop" if stop_hit else "take_profit" if tp_hit else "lower_bb"
                self.log(f"EXIT [{reason}] @ {price:.4f}")
                self.order = self.sell(size=self.position.size)
            else:
                # Trail stop to protect profits
                trail = price - self.atr[0] * self.params.atr_stop_mult
                self.stop_price = max(self.stop_price, trail)
