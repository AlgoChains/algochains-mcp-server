"""Order flow analysis — delta, footprint, CVD, absorption.

Real implementation using Polygon trade data to compute cumulative
volume delta, buy/sell classification, absorption detection, volume
profile with POC/value area, and price-level heatmaps.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx

logger = logging.getLogger("algochains_mcp.realtime_analytics.order_flow")


class OrderFlowAnalyzer:
    """Analyze order flow: delta, footprint, CVD, absorption."""

    def __init__(self, polygon_key: str = "") -> None:
        self._polygon_key = polygon_key
        self._http = httpx.AsyncClient(timeout=30)
        self._cache: dict = {}

    async def analyze(self, symbol: str, lookback_minutes: int = 60) -> dict:
        """Compute order flow delta, CVD, and absorption from recent trades."""
        trades = await self._fetch_trades(symbol)
        if not trades:
            return {"status": "error", "error": "No trade data available"}

        buy_volume = 0
        sell_volume = 0
        cvd_series: list[float] = []
        running_cvd = 0.0
        prev_price = trades[0].get("p", 0)

        price_volume: dict[float, dict] = {}

        for t in trades:
            price = t.get("p", 0)
            size = t.get("s", 0)
            bucket = round(price, 2)

            if bucket not in price_volume:
                price_volume[bucket] = {"buy": 0, "sell": 0, "total": 0}

            if price >= prev_price:
                buy_volume += size
                running_cvd += size
                price_volume[bucket]["buy"] += size
            else:
                sell_volume += size
                running_cvd -= size
                price_volume[bucket]["sell"] += size

            price_volume[bucket]["total"] += size
            cvd_series.append(running_cvd)
            prev_price = price

        total_volume = buy_volume + sell_volume
        delta = buy_volume - sell_volume
        delta_pct = round(delta / total_volume * 100, 2) if total_volume else 0

        absorption_zones = self._find_absorption(price_volume, total_volume)

        cvd_trend = "neutral"
        if len(cvd_series) > 10:
            recent = cvd_series[-10:]
            if recent[-1] > recent[0] * 1.1:
                cvd_trend = "accumulating"
            elif recent[-1] < recent[0] * 0.9:
                cvd_trend = "distributing"

        return {
            "status": "ok",
            "symbol": symbol,
            "lookback_minutes": lookback_minutes,
            "trades_analyzed": len(trades),
            "buy_volume": buy_volume,
            "sell_volume": sell_volume,
            "delta": delta,
            "delta_pct": delta_pct,
            "cumulative_delta": round(running_cvd, 0),
            "cvd_trend": cvd_trend,
            "total_volume": total_volume,
            "absorption_zones": absorption_zones,
            "signal": "bullish" if delta_pct > 15 else "bearish" if delta_pct < -15 else "neutral",
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    async def get_heatmap(self, symbol: str, levels: int = 20) -> dict:
        """Build price-level volume heatmap from trade data."""
        trades = await self._fetch_trades(symbol)
        if not trades:
            return {"status": "error", "error": "No trade data available"}

        price_volume: dict[float, dict] = {}
        for t in trades:
            price = round(t.get("p", 0), 2)
            size = t.get("s", 0)
            if price not in price_volume:
                price_volume[price] = {"buy": 0, "sell": 0, "total": 0}
            price_volume[price]["total"] += size

        sorted_levels = sorted(price_volume.items(), key=lambda x: x[1]["total"], reverse=True)
        max_vol = sorted_levels[0][1]["total"] if sorted_levels else 1

        heatmap = []
        for price, vols in sorted_levels[:levels]:
            intensity = round(vols["total"] / max_vol, 3)
            heatmap.append({
                "price": price,
                "volume": vols["total"],
                "intensity": intensity,
                "label": "hot" if intensity > 0.7 else "warm" if intensity > 0.3 else "cool",
            })

        heatmap.sort(key=lambda x: x["price"])

        return {
            "status": "ok",
            "symbol": symbol,
            "levels": len(heatmap),
            "heatmap_data": heatmap,
            "total_trades": len(trades),
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    async def get_volume_profile(self, symbol: str, lookback_days: int = 5) -> dict:
        """Compute volume profile with POC and value area from daily bars."""
        bars = await self._fetch_bars(symbol, lookback_days)
        if not bars:
            return {"status": "error", "error": "No bar data available"}

        price_volume: dict[float, int] = {}
        for bar in bars:
            h = bar.get("h", 0)
            l_val = bar.get("l", 0)
            v = bar.get("v", 0)
            mid = round((h + l_val) / 2, 2)
            step = round((h - l_val) / 10, 2) if h > l_val else 0.01
            if step <= 0:
                step = 0.01

            for i in range(10):
                level = round(l_val + i * step, 2)
                price_volume[level] = price_volume.get(level, 0) + v // 10

        if not price_volume:
            return {"status": "error", "error": "Could not build volume profile"}

        total_vol = sum(price_volume.values())
        poc_price = max(price_volume, key=price_volume.get)

        sorted_pv = sorted(price_volume.items(), key=lambda x: x[1], reverse=True)
        va_vol = 0
        va_prices = []
        for price, vol in sorted_pv:
            va_vol += vol
            va_prices.append(price)
            if va_vol >= total_vol * 0.7:
                break

        va_high = max(va_prices) if va_prices else 0
        va_low = min(va_prices) if va_prices else 0

        profile = [{"price": p, "volume": v, "pct": round(v / total_vol * 100, 2) if total_vol else 0}
                    for p, v in sorted(price_volume.items())]

        return {
            "status": "ok",
            "symbol": symbol,
            "lookback_days": lookback_days,
            "bars_analyzed": len(bars),
            "poc_price": poc_price,
            "value_area_high": va_high,
            "value_area_low": va_low,
            "profile": profile[-30:],
            "total_volume": total_vol,
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    def _find_absorption(self, price_volume: dict, total_volume: int) -> list[dict]:
        """Detect absorption — high volume at price with no movement."""
        if not price_volume or total_volume == 0:
            return []
        avg_vol = total_volume / len(price_volume)
        zones = []
        for price, vols in price_volume.items():
            if vols["total"] > avg_vol * 3:
                net = vols["buy"] - vols["sell"]
                zones.append({
                    "price": price,
                    "volume": vols["total"],
                    "buy_pct": round(vols["buy"] / vols["total"] * 100, 1) if vols["total"] else 0,
                    "type": "bid_absorption" if net > 0 else "ask_absorption",
                    "strength": round(vols["total"] / avg_vol, 1),
                })
        zones.sort(key=lambda x: x["volume"], reverse=True)
        return zones[:5]

    async def _fetch_trades(self, symbol: str) -> list[dict]:
        if not self._polygon_key:
            return []
        url = f"https://api.polygon.io/v3/trades/{symbol}"
        try:
            resp = await self._http.get(url, params={"limit": 5000, "sort": "timestamp", "order": "desc", "apiKey": self._polygon_key})
            resp.raise_for_status()
            results = resp.json().get("results", [])
            results.reverse()
            return results
        except Exception as e:
            logger.warning("Trade fetch failed for %s: %s", symbol, e)
            return []

    async def _fetch_bars(self, symbol: str, days: int) -> list[dict]:
        if not self._polygon_key:
            return []
        end = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        url = f"https://api.polygon.io/v2/aggs/ticker/{symbol}/range/1/day/{start}/{end}"
        try:
            resp = await self._http.get(url, params={"adjusted": "true", "sort": "asc", "apiKey": self._polygon_key})
            resp.raise_for_status()
            return resp.json().get("results", [])
        except Exception as e:
            logger.warning("Bar fetch failed for %s: %s", symbol, e)
            return []
