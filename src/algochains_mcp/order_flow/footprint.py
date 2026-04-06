"""
Footprint Chart Engine.

Computes bid/ask volume at each price level for each candle.
The most requested institutional feature in retail trading.

Key signals:
  - Absorption: large delta but price doesn't move → institutions absorbing
  - Imbalance clusters: one side dominates by 3x+ → directional signal
  - POC (Point of Control): price level with highest total volume

Requires tick data with side (bid/ask) classification.
Input source: Databento (use schema='trades' or 'mbp-1')
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PriceLevelData:
    price: float
    buy_volume: float
    sell_volume: float
    total_volume: float = 0.0
    delta: float = 0.0
    imbalance_ratio: float = 0.0
    absorption: bool = False

    def __post_init__(self):
        self.total_volume = self.buy_volume + self.sell_volume
        self.delta = self.buy_volume - self.sell_volume
        if self.sell_volume > 0:
            self.imbalance_ratio = self.buy_volume / self.sell_volume
        elif self.buy_volume > 0:
            self.imbalance_ratio = float("inf")

    def to_dict(self) -> dict[str, Any]:
        return {
            "price": self.price,
            "buy_volume": round(self.buy_volume, 2),
            "sell_volume": round(self.sell_volume, 2),
            "total_volume": round(self.total_volume, 2),
            "delta": round(self.delta, 2),
            "imbalance_ratio": round(min(self.imbalance_ratio, 99.0), 2),
            "absorption": self.absorption,
        }


@dataclass
class FootprintBar:
    timestamp: float
    open: float
    high: float
    low: float
    close: float
    bar_size: str
    price_levels: list[PriceLevelData]
    total_volume: float = 0.0
    cumulative_delta: float = 0.0
    poc_price: float = 0.0          # Point of Control
    absorption_detected: bool = False
    imbalance_signal: str = "neutral"  # "bullish" | "bearish" | "neutral"

    def __post_init__(self):
        if self.price_levels:
            self.total_volume = sum(p.total_volume for p in self.price_levels)
            self.cumulative_delta = sum(p.delta for p in self.price_levels)
            poc = max(self.price_levels, key=lambda p: p.total_volume, default=None)
            self.poc_price = poc.price if poc else self.close
            self.absorption_detected = any(p.absorption for p in self.price_levels)
            # Imbalance: >60% of price levels show buy dominance
            buy_dom = sum(1 for p in self.price_levels if p.buy_volume > p.sell_volume * 1.5)
            sell_dom = sum(1 for p in self.price_levels if p.sell_volume > p.buy_volume * 1.5)
            if len(self.price_levels) > 0:
                if buy_dom / len(self.price_levels) > 0.6:
                    self.imbalance_signal = "bullish"
                elif sell_dom / len(self.price_levels) > 0.6:
                    self.imbalance_signal = "bearish"

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "bar_size": self.bar_size,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "total_volume": round(self.total_volume, 2),
            "cumulative_delta": round(self.cumulative_delta, 2),
            "poc_price": self.poc_price,
            "absorption_detected": self.absorption_detected,
            "imbalance_signal": self.imbalance_signal,
            "price_levels": [p.to_dict() for p in self.price_levels],
        }


def compute_footprint_chart(
    tick_data: list[dict[str, Any]],
    bar_size_seconds: int = 300,
    tick_size: float = 0.01,
    absorption_delta_threshold: float = 100.0,
    absorption_price_threshold_ticks: int = 3,
) -> list[FootprintBar]:
    """
    Compute footprint chart from raw tick data.

    Args:
        tick_data: List of dicts with keys: timestamp, price, size, side ("B"/"S"/"buy"/"sell")
        bar_size_seconds: Bar duration in seconds (default 5 min = 300s)
        tick_size: Minimum price increment
        absorption_delta_threshold: Min delta magnitude to test for absorption
        absorption_price_threshold_ticks: Max price movement in ticks for absorption

    Returns:
        List of FootprintBar objects, one per time period.
    """
    if not tick_data:
        return []

    # Normalize tick data
    ticks = []
    for t in tick_data:
        side_raw = str(t.get("side", "")).lower()
        side = "buy" if side_raw in ("b", "buy", "ask", "1") else "sell"
        ticks.append({
            "ts": float(t.get("timestamp", 0)),
            "price": float(t.get("price", 0)),
            "size": float(t.get("size", t.get("volume", 1))),
            "side": side,
        })

    if not ticks:
        return []

    ticks.sort(key=lambda t: t["ts"])
    start_ts = ticks[0]["ts"]

    bars: list[FootprintBar] = []
    bar_start = start_ts
    current_levels: dict[float, dict[str, float]] = {}
    bar_ohlc = {"open": None, "high": float("-inf"), "low": float("inf"), "close": None}

    def _round_price(p: float) -> float:
        return round(round(p / tick_size) * tick_size, 10)

    def _flush_bar(bar_ts: float, levels: dict, ohlc: dict) -> FootprintBar | None:
        if not levels:
            return None
        price_levels = []
        for price, vols in sorted(levels.items()):
            buy_vol = vols.get("buy", 0)
            sell_vol = vols.get("sell", 0)
            lvl = PriceLevelData(price=price, buy_volume=buy_vol, sell_volume=sell_vol)
            price_levels.append(lvl)

        # Detect absorption: high delta but price moved little
        if price_levels:
            total_delta = sum(p.delta for p in price_levels)
            bar_range = (ohlc["high"] - ohlc["low"]) / tick_size if tick_size > 0 else 0
            if abs(total_delta) >= absorption_delta_threshold and bar_range <= absorption_price_threshold_ticks:
                for lvl in price_levels:
                    lvl.absorption = True

        return FootprintBar(
            timestamp=bar_ts,
            open=ohlc["open"] or 0,
            high=ohlc["high"],
            low=ohlc["low"],
            close=ohlc["close"] or 0,
            bar_size=f"{bar_size_seconds}s",
            price_levels=price_levels,
        )

    for tick in ticks:
        # New bar?
        if tick["ts"] >= bar_start + bar_size_seconds:
            bar = _flush_bar(bar_start, current_levels, bar_ohlc)
            if bar:
                bars.append(bar)
            # Reset
            bar_start = bar_start + bar_size_seconds * int((tick["ts"] - bar_start) / bar_size_seconds)
            current_levels = {}
            bar_ohlc = {"open": None, "high": float("-inf"), "low": float("inf"), "close": None}

        p = _round_price(tick["price"])
        if p not in current_levels:
            current_levels[p] = {"buy": 0.0, "sell": 0.0}
        current_levels[p][tick["side"]] += tick["size"]

        if bar_ohlc["open"] is None:
            bar_ohlc["open"] = tick["price"]
        bar_ohlc["high"] = max(bar_ohlc["high"], tick["price"])
        bar_ohlc["low"] = min(bar_ohlc["low"], tick["price"])
        bar_ohlc["close"] = tick["price"]

    # Flush last bar
    bar = _flush_bar(bar_start, current_levels, bar_ohlc)
    if bar:
        bars.append(bar)

    return bars


def analyze_footprint_signals(bars: list[FootprintBar]) -> dict[str, Any]:
    """Extract key signals from a sequence of footprint bars."""
    if not bars:
        return {"signal": "no_data"}

    recent = bars[-5:]
    absorptions = [b for b in recent if b.absorption_detected]
    bullish_imbalance = [b for b in recent if b.imbalance_signal == "bullish"]
    bearish_imbalance = [b for b in recent if b.imbalance_signal == "bearish"]

    avg_delta = sum(b.cumulative_delta for b in recent) / len(recent)
    signal = "neutral"
    if len(bullish_imbalance) >= 3:
        signal = "bullish_imbalance"
    elif len(bearish_imbalance) >= 3:
        signal = "bearish_imbalance"
    elif absorptions and avg_delta > 0:
        signal = "bullish_absorption"
    elif absorptions and avg_delta < 0:
        signal = "bearish_absorption"

    return {
        "signal": signal,
        "absorption_bars": len(absorptions),
        "avg_cumulative_delta": round(avg_delta, 2),
        "bullish_imbalance_bars": len(bullish_imbalance),
        "bearish_imbalance_bars": len(bearish_imbalance),
        "last_poc": bars[-1].poc_price if bars else None,
        "bar_count_analyzed": len(recent),
    }
