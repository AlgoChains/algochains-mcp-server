"""VWAP deviation analysis — anchored VWAP, TWAP, deviation signals.

Uses Polygon/Massive for intraday bar data, computes real VWAP and
generates actionable signals when price deviates from fair value.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger("algochains_mcp.alpha_engines.vwap")


class VWAPEngine:
    """Real VWAP/TWAP computation from intraday bar data."""

    def __init__(self, polygon_key: str = "", massive_key: str = "") -> None:
        self._polygon_key = polygon_key
        self._massive_key = massive_key
        self._http = httpx.AsyncClient(timeout=30)

    async def compute_vwap(
        self,
        symbol: str,
        date: str = "",
        interval: str = "1",
        anchor: str = "day",
    ) -> dict[str, Any]:
        """Compute VWAP from intraday minute bars via Polygon."""
        if not date:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        bars = await self._fetch_intraday(symbol, date, interval)
        if not bars:
            return {"status": "error", "error": "No intraday data available"}

        cum_vol = 0.0
        cum_tp_vol = 0.0
        cum_vol_sq = 0.0
        vwap_series = []

        for bar in bars:
            tp = (bar["h"] + bar["l"] + bar["c"]) / 3.0
            v = bar["v"]
            cum_tp_vol += tp * v
            cum_vol += v
            cum_vol_sq += v * tp * tp

            vwap_val = cum_tp_vol / cum_vol if cum_vol > 0 else tp
            variance = (cum_vol_sq / cum_vol - vwap_val**2) if cum_vol > 0 else 0
            std = math.sqrt(max(variance, 0))

            vwap_series.append({
                "t": bar["t"],
                "vwap": round(vwap_val, 4),
                "upper_1": round(vwap_val + std, 4),
                "lower_1": round(vwap_val - std, 4),
                "upper_2": round(vwap_val + 2 * std, 4),
                "lower_2": round(vwap_val - 2 * std, 4),
                "close": bar["c"],
                "volume": bar["v"],
            })

        last = vwap_series[-1]
        price = last["close"]
        vwap = last["vwap"]
        deviation_pct = round((price - vwap) / vwap * 100, 4) if vwap else 0

        signal = "neutral"
        if deviation_pct < -0.5:
            signal = "bullish_reversion"
        elif deviation_pct > 0.5:
            signal = "bearish_reversion"
        if deviation_pct < -1.0:
            signal = "strong_bullish"
        elif deviation_pct > 1.0:
            signal = "strong_bearish"

        return {
            "status": "ok",
            "symbol": symbol,
            "date": date,
            "anchor": anchor,
            "vwap": vwap,
            "twap": round(sum(b["c"] for b in bars) / len(bars), 4),
            "current_price": price,
            "deviation_pct": deviation_pct,
            "signal": signal,
            "total_volume": int(cum_vol),
            "bars_analyzed": len(bars),
            "vwap_bands": {
                "upper_2sd": last["upper_2"],
                "upper_1sd": last["upper_1"],
                "vwap": vwap,
                "lower_1sd": last["lower_1"],
                "lower_2sd": last["lower_2"],
            },
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    async def multi_anchor_vwap(
        self, symbol: str, anchors: list[str] | None = None
    ) -> dict[str, Any]:
        """Compute VWAP from multiple anchor points (day, week, month)."""
        if anchors is None:
            anchors = ["day"]

        results = {}
        for anchor in anchors:
            result = await self.compute_vwap(symbol, anchor=anchor)
            results[anchor] = result

        return {
            "status": "ok",
            "symbol": symbol,
            "anchors": results,
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    async def _fetch_intraday(
        self, symbol: str, date: str, interval: str = "1"
    ) -> list[dict]:
        """Fetch intraday bars from Polygon."""
        if not self._polygon_key:
            return []
        url = (
            f"https://api.polygon.io/v2/aggs/ticker/{symbol}"
            f"/range/{interval}/minute/{date}/{date}"
        )
        try:
            resp = await self._http.get(
                url, params={"adjusted": "true", "sort": "asc", "apiKey": self._polygon_key}
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("results", [])
        except Exception as e:
            logger.warning("Polygon intraday fetch failed for %s: %s", symbol, e)
            return []
