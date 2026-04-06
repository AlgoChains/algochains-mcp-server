"""
Cumulative Delta Analysis.

Cumulative delta tracks net buying vs selling pressure over time.
Divergence between price direction and delta direction is one of the
strongest institutional signals available.

Key patterns:
  - Delta divergence: price makes new high, delta makes lower high → distribution
  - Delta confirmation: both price and delta trending together → trend strength
  - Delta exhaustion: delta spikes then reverses without price follow → reversal risk
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DeltaPoint:
    timestamp: float
    price: float
    bar_delta: float           # delta for this bar/tick
    cumulative_delta: float    # running cumulative delta
    price_change: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "price": self.price,
            "bar_delta": round(self.bar_delta, 2),
            "cumulative_delta": round(self.cumulative_delta, 2),
            "price_change": round(self.price_change, 4),
        }


@dataclass
class DivergenceEvent:
    timestamp: float
    type: str          # "bullish" | "bearish" | "delta_exhaustion"
    description: str
    confidence: float  # 0-1

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "type": self.type,
            "description": self.description,
            "confidence": round(self.confidence, 2),
        }


@dataclass
class CDResult:
    symbol: str
    timeframe: str
    series: list[DeltaPoint]
    divergences: list[DivergenceEvent]
    signal: str            # "bullish" | "bearish" | "neutral"
    signal_strength: float  # 0-1
    trend_alignment: bool  # True if delta confirms price trend
    lookback_bars: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "signal": self.signal,
            "signal_strength": round(self.signal_strength, 2),
            "trend_alignment": self.trend_alignment,
            "lookback_bars": self.lookback_bars,
            "divergences": [d.to_dict() for d in self.divergences],
            "series_summary": {
                "first_price": self.series[0].price if self.series else None,
                "last_price": self.series[-1].price if self.series else None,
                "first_cd": self.series[0].cumulative_delta if self.series else None,
                "last_cd": self.series[-1].cumulative_delta if self.series else None,
                "cd_trend": "rising" if (self.series and self.series[-1].cumulative_delta > self.series[0].cumulative_delta) else "falling",
                "bar_count": len(self.series),
            },
        }


def compute_cumulative_delta(
    bars: list[dict[str, Any]],
    symbol: str = "",
    timeframe: str = "5min",
    divergence_lookback: int = 10,
) -> CDResult:
    """
    Compute cumulative delta from OHLCV bars with estimated delta.

    For bars without explicit delta, estimate using:
    delta ≈ (close - open) / (high - low) * volume   (if range > 0)

    For tick data, use compute_cumulative_delta_from_ticks().

    Args:
        bars: List of dicts with keys: timestamp, open, high, low, close, volume
              Optional key: delta (if available from footprint computation)
        symbol: Symbol name
        timeframe: Bar timeframe label
        divergence_lookback: Bars to look back for divergence detection

    Returns:
        CDResult with series, divergences, and signal
    """
    if not bars:
        return CDResult(
            symbol=symbol, timeframe=timeframe, series=[], divergences=[],
            signal="no_data", signal_strength=0.0, trend_alignment=False, lookback_bars=0
        )

    points: list[DeltaPoint] = []
    cum_delta = 0.0

    for i, bar in enumerate(bars):
        ts = float(bar.get("timestamp", i))
        o = float(bar.get("open", 0))
        h = float(bar.get("high", 0))
        l = float(bar.get("low", 0))
        c = float(bar.get("close", 0))
        v = float(bar.get("volume", 0))

        # Use explicit delta if available (from footprint)
        if "delta" in bar:
            bar_delta = float(bar["delta"])
        elif h != l:
            # Estimate: positive delta ∝ bullish candle body
            bar_delta = ((c - o) / (h - l)) * v
        else:
            bar_delta = 0.0

        cum_delta += bar_delta
        price_change = (c - points[-1].price) if points else 0.0

        points.append(DeltaPoint(
            timestamp=ts,
            price=c,
            bar_delta=bar_delta,
            cumulative_delta=cum_delta,
            price_change=price_change,
        ))

    # Divergence detection
    divergences: list[DivergenceEvent] = []
    lookback = min(divergence_lookback, len(points))
    if lookback >= 4:
        recent = points[-lookback:]
        price_trend = recent[-1].price - recent[0].price
        delta_trend = recent[-1].cumulative_delta - recent[0].cumulative_delta

        # Bearish divergence: price up but delta down
        if price_trend > 0 and delta_trend < 0:
            conf = min(1.0, abs(delta_trend) / (abs(price_trend) * 100 + 1))
            divergences.append(DivergenceEvent(
                timestamp=recent[-1].timestamp,
                type="bearish",
                description=(
                    f"Price rising ({price_trend:+.2f}) but delta falling ({delta_trend:+.0f}). "
                    "Distribution pattern — sellers absorbing buyer pressure."
                ),
                confidence=conf,
            ))

        # Bullish divergence: price down but delta up
        elif price_trend < 0 and delta_trend > 0:
            conf = min(1.0, abs(delta_trend) / (abs(price_trend) * 100 + 1))
            divergences.append(DivergenceEvent(
                timestamp=recent[-1].timestamp,
                type="bullish",
                description=(
                    f"Price falling ({price_trend:+.2f}) but delta rising ({delta_trend:+.0f}). "
                    "Accumulation pattern — buyers absorbing seller pressure."
                ),
                confidence=conf,
            ))

        # Delta exhaustion: spike then reversal without price follow
        if len(recent) >= 6:
            mid_delta = recent[len(recent)//2].cumulative_delta
            start_delta = recent[0].cumulative_delta
            end_delta = recent[-1].cumulative_delta
            spike_then_reverse = (
                abs(mid_delta - start_delta) > abs(end_delta - start_delta) * 2
                and abs(price_trend) < abs(mid_delta - start_delta) * 0.01
            )
            if spike_then_reverse:
                divergences.append(DivergenceEvent(
                    timestamp=recent[-1].timestamp,
                    type="delta_exhaustion",
                    description=(
                        "Delta spiked then reversed without significant price movement. "
                        "Potential exhaustion and reversal setup."
                    ),
                    confidence=0.65,
                ))

    # Determine signal
    price_trend = points[-1].price - points[0].price if len(points) >= 2 else 0
    delta_trend = points[-1].cumulative_delta - points[0].cumulative_delta if len(points) >= 2 else 0
    trend_alignment = (price_trend > 0 and delta_trend > 0) or (price_trend < 0 and delta_trend < 0)

    if divergences:
        last_div = divergences[-1]
        signal = last_div.type if last_div.type in ("bullish", "bearish") else "caution"
        signal_strength = last_div.confidence
    elif trend_alignment:
        signal = "bullish" if price_trend > 0 else "bearish"
        signal_strength = 0.60
    else:
        signal = "neutral"
        signal_strength = 0.30

    return CDResult(
        symbol=symbol,
        timeframe=timeframe,
        series=points,
        divergences=divergences,
        signal=signal,
        signal_strength=signal_strength,
        trend_alignment=trend_alignment,
        lookback_bars=len(points),
    )


def compute_cumulative_delta_from_ticks(
    ticks: list[dict[str, Any]],
    symbol: str = "",
    timeframe: str = "tick",
) -> CDResult:
    """Compute cumulative delta directly from tick data."""
    if not ticks:
        return CDResult(
            symbol=symbol, timeframe=timeframe, series=[], divergences=[],
            signal="no_data", signal_strength=0.0, trend_alignment=False, lookback_bars=0
        )

    points: list[DeltaPoint] = []
    cum_delta = 0.0
    for i, t in enumerate(ticks):
        side = str(t.get("side", "")).lower()
        size = float(t.get("size", t.get("volume", 1)))
        price = float(t.get("price", 0))
        ts = float(t.get("timestamp", i))
        delta = size if side in ("b", "buy", "ask") else -size
        cum_delta += delta
        points.append(DeltaPoint(
            timestamp=ts,
            price=price,
            bar_delta=delta,
            cumulative_delta=cum_delta,
        ))

    # Use same divergence logic
    return compute_cumulative_delta(
        bars=[{"timestamp": p.timestamp, "open": p.price, "high": p.price, "low": p.price,
               "close": p.price, "volume": abs(p.bar_delta), "delta": p.bar_delta}
              for p in points],
        symbol=symbol,
        timeframe=timeframe,
    )
