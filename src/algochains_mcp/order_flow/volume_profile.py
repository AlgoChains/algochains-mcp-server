"""
Volume at Price (VAP) / Volume Profile / TPO Analysis.

Key outputs:
  - Point of Control (POC): price level with highest traded volume
  - Value Area (VA): price range containing 70% of volume (High + Low = VAH/VAL)
  - High Volume Nodes (HVN): support/resistance clusters
  - Low Volume Nodes (LVN): breakout acceleration zones
  - TPO Profile: Time at Price histogram

These levels are the most widely used institutional reference points
for day trading, scalping, and swing trading.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass
class VolumeNode:
    price: float
    volume: float
    volume_pct: float  # percentage of total volume
    node_type: str     # "hvn" | "lvn" | "poc" | "vah" | "val" | "normal"
    tpo_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "price": self.price,
            "volume": round(self.volume, 2),
            "volume_pct": round(self.volume_pct, 2),
            "node_type": self.node_type,
            "tpo_count": self.tpo_count,
        }


@dataclass
class VAPResult:
    symbol: str
    session: str
    bar_count: int
    tick_size: float
    poc_price: float
    vah: float           # Value Area High
    val: float           # Value Area Low
    value_area_pct: float
    nodes: list[VolumeNode]
    total_volume: float
    hvn_levels: list[float]
    lvn_levels: list[float]
    signal: str          # "above_poc" | "below_poc" | "at_poc"
    current_price: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "session": self.session,
            "poc_price": self.poc_price,
            "vah": self.vah,
            "val": self.val,
            "value_area_pct": self.value_area_pct,
            "hvn_levels": self.hvn_levels,
            "lvn_levels": self.lvn_levels,
            "total_volume": round(self.total_volume, 2),
            "bar_count": self.bar_count,
            "signal": self.signal,
            "current_price": self.current_price,
            "nodes": [n.to_dict() for n in self.nodes[:50]],  # top 50 nodes
        }


def compute_volume_profile(
    bars: list[dict[str, Any]],
    symbol: str = "",
    session: str = "regular",
    n_levels: int = 50,
    value_area_pct: float = 0.70,
    tick_size: float = 0.01,
    current_price: float | None = None,
) -> VAPResult:
    """
    Compute Volume at Price profile from OHLCV bars.

    Args:
        bars: List of dicts with keys: open, high, low, close, volume, timestamp
        symbol: Symbol name
        session: Session label ("regular", "extended", "overnight")
        n_levels: Number of price levels to bucket into
        value_area_pct: Percentage of volume defining the value area (default 70%)
        tick_size: Price rounding increment
        current_price: Most recent price for signal generation

    Returns:
        VAPResult with POC, VAH, VAL, HVN/LVN levels
    """
    if not bars:
        return VAPResult(
            symbol=symbol, session=session, bar_count=0, tick_size=tick_size,
            poc_price=0, vah=0, val=0, value_area_pct=value_area_pct,
            nodes=[], total_volume=0, hvn_levels=[], lvn_levels=[],
            signal="no_data", current_price=current_price
        )

    # Find price range
    all_highs = [float(b.get("high", 0)) for b in bars]
    all_lows = [float(b.get("low", 0)) for b in bars]
    price_high = max(all_highs)
    price_low = min(l for l in all_lows if l > 0)
    price_range = price_high - price_low

    if price_range <= 0:
        price_low = min(all_highs) * 0.99
        price_high = max(all_highs) * 1.01
        price_range = price_high - price_low

    level_size = price_range / n_levels
    level_size = max(level_size, tick_size)

    # Build volume dict
    vol_map: dict[float, float] = {}
    tpo_map: dict[float, int] = {}

    def _bucket(p: float) -> float:
        return round(price_low + math.floor((p - price_low) / level_size) * level_size, 10)

    for bar in bars:
        h = float(bar.get("high", 0))
        l = float(bar.get("low", 0))
        v = float(bar.get("volume", 0))
        if h <= 0 or l <= 0 or v <= 0:
            continue

        # Distribute volume uniformly across price range
        bar_range = h - l
        if bar_range <= 0:
            bkt = _bucket(float(bar.get("close", h)))
            vol_map[bkt] = vol_map.get(bkt, 0) + v
            tpo_map[bkt] = tpo_map.get(bkt, 0) + 1
            continue

        # Iterate through price levels covered by this bar
        p = l
        while p <= h + level_size:
            bkt = _bucket(p)
            portion = min(level_size, h - max(l, bkt)) / bar_range if bar_range > 0 else 1.0
            vol_map[bkt] = vol_map.get(bkt, 0) + v * portion
            tpo_map[bkt] = tpo_map.get(bkt, 0) + 1
            p += level_size

    if not vol_map:
        return VAPResult(
            symbol=symbol, session=session, bar_count=len(bars), tick_size=tick_size,
            poc_price=0, vah=0, val=0, value_area_pct=value_area_pct,
            nodes=[], total_volume=0, hvn_levels=[], lvn_levels=[],
            signal="no_data", current_price=current_price
        )

    total_volume = sum(vol_map.values())
    if total_volume == 0:
        total_volume = 1.0

    # Find POC
    poc_price = max(vol_map.keys(), key=lambda p: vol_map[p])

    # Build sorted node list
    sorted_prices = sorted(vol_map.keys())
    nodes_raw = [(p, vol_map[p]) for p in sorted_prices]

    # Value Area: expand from POC until 70% of volume is covered
    poc_vol = vol_map[poc_price]
    va_vol = poc_vol
    va_prices = [poc_price]
    lower_idx = sorted_prices.index(poc_price)
    upper_idx = lower_idx
    target_vol = total_volume * value_area_pct

    while va_vol < target_vol and (lower_idx > 0 or upper_idx < len(sorted_prices) - 1):
        lower_add = sorted_prices[lower_idx - 1] if lower_idx > 0 else None
        upper_add = sorted_prices[upper_idx + 1] if upper_idx < len(sorted_prices) - 1 else None

        lower_vol = vol_map.get(lower_add, 0) if lower_add else 0
        upper_vol = vol_map.get(upper_add, 0) if upper_add else 0

        if upper_vol >= lower_vol and upper_add:
            va_vol += upper_vol
            va_prices.append(upper_add)
            upper_idx += 1
        elif lower_add:
            va_vol += lower_vol
            va_prices.append(lower_add)
            lower_idx -= 1
        else:
            break

    vah = max(va_prices) if va_prices else poc_price
    val = min(va_prices) if va_prices else poc_price

    # Classify nodes: HVN / LVN
    vol_values = list(vol_map.values())
    vol_mean = sum(vol_values) / len(vol_values)
    vol_std = math.sqrt(sum((v - vol_mean) ** 2 for v in vol_values) / len(vol_values)) if len(vol_values) > 1 else vol_mean * 0.3

    hvn_threshold = vol_mean + vol_std * 0.5
    lvn_threshold = vol_mean - vol_std * 0.5

    nodes: list[VolumeNode] = []
    hvn_levels: list[float] = []
    lvn_levels: list[float] = []

    for price, vol in nodes_raw:
        if price == poc_price:
            nt = "poc"
        elif price == vah:
            nt = "vah"
        elif price == val:
            nt = "val"
        elif vol >= hvn_threshold:
            nt = "hvn"
            hvn_levels.append(price)
        elif vol <= lvn_threshold and vol > 0:
            nt = "lvn"
            lvn_levels.append(price)
        else:
            nt = "normal"

        nodes.append(VolumeNode(
            price=price,
            volume=round(vol, 2),
            volume_pct=round(vol / total_volume * 100, 2),
            node_type=nt,
            tpo_count=tpo_map.get(price, 0),
        ))

    # Signal based on current price vs POC
    signal = "neutral"
    if current_price:
        if current_price > vah:
            signal = "above_value_area"
        elif current_price < val:
            signal = "below_value_area"
        elif abs(current_price - poc_price) < level_size:
            signal = "at_poc"
        elif current_price > poc_price:
            signal = "above_poc"
        else:
            signal = "below_poc"

    return VAPResult(
        symbol=symbol,
        session=session,
        bar_count=len(bars),
        tick_size=tick_size,
        poc_price=poc_price,
        vah=vah,
        val=val,
        value_area_pct=value_area_pct,
        nodes=nodes,
        total_volume=total_volume,
        hvn_levels=sorted(hvn_levels),
        lvn_levels=sorted(lvn_levels),
        signal=signal,
        current_price=current_price,
    )
