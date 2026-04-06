"""Tape reading engine — tick-level momentum, aggressor analysis, large print detection.

Analyzes raw trade (tick) data to identify momentum shifts, aggressive
buying/selling, and institutional footprints in real-time trade flow.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger("algochains_mcp.alpha_engines.tape_reader")


class TapeReaderEngine:
    """Tick-level tape reading and aggressor analysis."""

    def __init__(self, polygon_key: str = "") -> None:
        self._polygon_key = polygon_key
        self._http = httpx.AsyncClient(timeout=30)

    async def read_tape(
        self, symbol: str, lookback_minutes: int = 5
    ) -> dict[str, Any]:
        """Read recent tape — classify trades as buys/sells, detect momentum."""
        trades = await self._fetch_recent_trades(symbol)
        if not trades:
            return {"status": "error", "error": "No trade data available"}

        up_ticks = 0
        down_ticks = 0
        up_volume = 0
        down_volume = 0
        large_prints = []
        prices = []
        total_volume = 0

        prev_price = trades[0].get("p", 0)
        for t in trades:
            price = t.get("p", 0)
            size = t.get("s", 0)
            total_volume += size
            prices.append(price)

            if price > prev_price:
                up_ticks += 1
                up_volume += size
            elif price < prev_price:
                down_ticks += 1
                down_volume += size

            if size >= 1000:
                large_prints.append({
                    "price": price,
                    "size": size,
                    "notional": round(price * size, 2),
                    "direction": "up" if price > prev_price else "down" if price < prev_price else "flat",
                    "timestamp": t.get("t", 0),
                })

            prev_price = price

        total_ticks = up_ticks + down_ticks
        tick_ratio = round(up_ticks / down_ticks, 3) if down_ticks > 0 else float("inf")
        volume_ratio = round(up_volume / down_volume, 3) if down_volume > 0 else float("inf")

        momentum = "neutral"
        if tick_ratio > 1.5 and volume_ratio > 1.3:
            momentum = "strong_bullish"
        elif tick_ratio > 1.2:
            momentum = "bullish"
        elif tick_ratio < 0.67 and volume_ratio < 0.77:
            momentum = "strong_bearish"
        elif tick_ratio < 0.83:
            momentum = "bearish"

        price_range = max(prices) - min(prices) if prices else 0
        vwap_tape = sum(t.get("p", 0) * t.get("s", 0) for t in trades) / total_volume if total_volume else 0

        absorption = self._detect_absorption(trades)

        large_prints.sort(key=lambda x: x["size"], reverse=True)

        return {
            "status": "ok",
            "symbol": symbol,
            "ticks_analyzed": len(trades),
            "up_ticks": up_ticks,
            "down_ticks": down_ticks,
            "tick_ratio": tick_ratio,
            "up_volume": up_volume,
            "down_volume": down_volume,
            "volume_ratio": volume_ratio,
            "total_volume": total_volume,
            "momentum": momentum,
            "tape_vwap": round(vwap_tape, 4),
            "price_range": round(price_range, 4),
            "large_prints": large_prints[:10],
            "large_print_count": len(large_prints),
            "absorption_detected": absorption,
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    async def momentum_scanner(
        self, symbols: list[str]
    ) -> dict[str, Any]:
        """Scan multiple symbols for tape momentum signals."""
        results = []
        for sym in symbols[:15]:
            try:
                data = await self.read_tape(sym)
                if data.get("status") == "ok":
                    results.append({
                        "symbol": sym,
                        "momentum": data["momentum"],
                        "tick_ratio": data["tick_ratio"],
                        "volume_ratio": data["volume_ratio"],
                        "large_prints": data["large_print_count"],
                        "absorption": data["absorption_detected"],
                    })
            except Exception as e:
                logger.warning("Tape scan failed for %s: %s", sym, e)

        bullish = [r for r in results if "bullish" in r["momentum"]]
        bearish = [r for r in results if "bearish" in r["momentum"]]

        return {
            "status": "ok",
            "scanned": len(symbols),
            "bullish": sorted(bullish, key=lambda x: x["tick_ratio"], reverse=True),
            "bearish": sorted(bearish, key=lambda x: x["tick_ratio"]),
            "neutral": [r for r in results if r["momentum"] == "neutral"],
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    def _detect_absorption(self, trades: list[dict]) -> dict[str, Any]:
        """Detect absorption — large volume at a price level without movement."""
        if len(trades) < 20:
            return {"detected": False}

        price_volume: dict[float, int] = {}
        for t in trades:
            p = round(t.get("p", 0), 2)
            price_volume[p] = price_volume.get(p, 0) + t.get("s", 0)

        if not price_volume:
            return {"detected": False}

        total_vol = sum(price_volume.values())
        avg_vol = total_vol / len(price_volume) if price_volume else 0

        absorption_levels = [
            {"price": p, "volume": v, "pct_of_total": round(v / total_vol * 100, 2)}
            for p, v in price_volume.items()
            if v > avg_vol * 3
        ]

        if absorption_levels:
            absorption_levels.sort(key=lambda x: x["volume"], reverse=True)
            return {
                "detected": True,
                "levels": absorption_levels[:5],
                "interpretation": "Large volume absorbed at key levels — potential support/resistance",
            }
        return {"detected": False}

    async def _fetch_recent_trades(self, symbol: str) -> list[dict]:
        if not self._polygon_key:
            return []
        url = f"https://api.polygon.io/v3/trades/{symbol}"
        try:
            resp = await self._http.get(
                url,
                params={"limit": 5000, "sort": "timestamp", "order": "desc", "apiKey": self._polygon_key},
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
            results.reverse()
            return results
        except Exception as e:
            logger.warning("Trades fetch failed for %s: %s", symbol, e)
            return []
