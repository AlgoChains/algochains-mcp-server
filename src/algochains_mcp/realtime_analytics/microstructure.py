"""Market microstructure analysis — spread, depth, toxicity.

Real implementation computing VPIN (Volume-Synchronized Probability of
Informed Trading), Kyle's lambda (price impact), bid-ask spread, depth
imbalance, and composite toxicity scoring from Polygon trade/quote data.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger("algochains_mcp.realtime_analytics.microstructure")


class MicrostructureEngine:
    """Market microstructure analysis."""

    def __init__(self, polygon_key: str = "") -> None:
        self._polygon_key = polygon_key
        self._http = httpx.AsyncClient(timeout=30)
        self._snapshots: list[dict] = []

    async def analyze(self, symbol: str) -> dict:
        """Full microstructure snapshot — spread, VPIN, Kyle's lambda, depth."""
        trades = await self._fetch_trades(symbol)
        quote = await self._fetch_nbbo(symbol)

        if not trades:
            return {"status": "error", "error": "No trade data available"}

        bid = quote.get("bid", 0)
        ask = quote.get("ask", 0)
        mid = (bid + ask) / 2 if bid and ask else trades[-1].get("p", 0)
        spread_bps = round((ask - bid) / mid * 10000, 2) if mid > 0 and ask > bid else 0

        bid_size = quote.get("bid_size", 0)
        ask_size = quote.get("ask_size", 0)
        depth_imbalance = round((bid_size - ask_size) / (bid_size + ask_size), 4) if (bid_size + ask_size) > 0 else 0

        vpin = self._compute_vpin(trades)
        kyle_lambda = self._compute_kyle_lambda(trades)

        toxicity_score = round(vpin * 0.5 + min(abs(kyle_lambda) * 100, 1.0) * 0.3 + (1 - abs(depth_imbalance)) * 0.2, 3)
        toxicity_label = "high" if toxicity_score > 0.7 else "medium" if toxicity_score > 0.4 else "low"

        snapshot = {
            "symbol": symbol,
            "bid": bid,
            "ask": ask,
            "mid": round(mid, 4),
            "bid_ask_spread_bps": spread_bps,
            "bid_size": bid_size,
            "ask_size": ask_size,
            "depth_imbalance": depth_imbalance,
            "vpin": round(vpin, 4),
            "kyle_lambda": round(kyle_lambda, 6),
            "trade_toxicity": toxicity_label,
            "toxicity_score": toxicity_score,
            "trades_analyzed": len(trades),
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
        }
        self._snapshots.append(snapshot)
        return {"status": "ok", "data": snapshot}

    async def get_toxicity(self, symbol: str, window: int = 50) -> dict:
        """Compute VPIN and Kyle's lambda toxicity metrics."""
        trades = await self._fetch_trades(symbol)
        if not trades:
            return {"status": "error", "error": "No trade data available"}

        subset = trades[-window:] if len(trades) > window else trades
        vpin = self._compute_vpin(subset)
        kyle_lambda = self._compute_kyle_lambda(subset)

        toxicity_score = round(vpin * 0.6 + min(abs(kyle_lambda) * 100, 1.0) * 0.4, 3)
        toxicity_label = "high" if toxicity_score > 0.7 else "medium" if toxicity_score > 0.4 else "low"

        return {
            "status": "ok",
            "symbol": symbol,
            "window": window,
            "trades_used": len(subset),
            "vpin": round(vpin, 4),
            "kyle_lambda": round(kyle_lambda, 6),
            "toxicity_score": toxicity_score,
            "toxicity_label": toxicity_label,
            "interpretation": self._interpret_toxicity(vpin, kyle_lambda),
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    def _compute_vpin(self, trades: list[dict]) -> float:
        """Volume-Synchronized Probability of Informed Trading.

        Buckets trades into volume bars and measures buy/sell imbalance.
        High VPIN (>0.7) indicates informed trading flow.
        """
        if len(trades) < 20:
            return 0.0

        total_vol = sum(t.get("s", 0) for t in trades)
        if total_vol == 0:
            return 0.0

        bucket_size = total_vol // 10 or 1
        buckets: list[dict] = []
        current_buy = 0
        current_sell = 0
        current_vol = 0
        prev_price = trades[0].get("p", 0)

        for t in trades:
            price = t.get("p", 0)
            size = t.get("s", 0)
            if price >= prev_price:
                current_buy += size
            else:
                current_sell += size
            current_vol += size
            prev_price = price

            if current_vol >= bucket_size:
                buckets.append({"buy": current_buy, "sell": current_sell, "vol": current_vol})
                current_buy = current_sell = current_vol = 0

        if not buckets:
            return 0.0

        imbalance_sum = sum(abs(b["buy"] - b["sell"]) for b in buckets)
        total_bucket_vol = sum(b["vol"] for b in buckets)

        return imbalance_sum / total_bucket_vol if total_bucket_vol > 0 else 0.0

    def _compute_kyle_lambda(self, trades: list[dict]) -> float:
        """Kyle's lambda — price impact per unit of order flow.

        Measures how much price moves per unit of signed volume.
        High lambda = illiquid, low lambda = liquid.
        """
        if len(trades) < 10:
            return 0.0

        price_changes = []
        signed_volumes = []
        prev_price = trades[0].get("p", 0)

        for t in trades[1:]:
            price = t.get("p", 0)
            size = t.get("s", 0)
            dp = price - prev_price
            sign = 1 if dp >= 0 else -1
            price_changes.append(dp)
            signed_volumes.append(sign * size)
            prev_price = price

        if not signed_volumes:
            return 0.0

        sum_sv_sq = sum(sv ** 2 for sv in signed_volumes)
        if sum_sv_sq == 0:
            return 0.0

        sum_dp_sv = sum(dp * sv for dp, sv in zip(price_changes, signed_volumes))
        return sum_dp_sv / sum_sv_sq

    def _interpret_toxicity(self, vpin: float, kyle_lambda: float) -> str:
        if vpin > 0.7:
            return "High informed trading flow — likely institutional activity. Adverse selection risk elevated."
        if vpin > 0.5:
            return "Moderate informed flow. Watch for directional moves."
        if abs(kyle_lambda) > 0.01:
            return "High price impact — thin liquidity. Use limit orders."
        return "Normal market conditions. Low toxicity."

    async def _fetch_trades(self, symbol: str) -> list[dict]:
        if not self._polygon_key:
            return []
        url = f"https://api.polygon.io/v3/trades/{symbol}"
        try:
            resp = await self._http.get(url, params={"limit": 2000, "sort": "timestamp", "order": "desc", "apiKey": self._polygon_key})
            resp.raise_for_status()
            results = resp.json().get("results", [])
            results.reverse()
            return results
        except Exception as e:
            logger.warning("Trade fetch failed for %s: %s", symbol, e)
            return []

    async def _fetch_nbbo(self, symbol: str) -> dict:
        if not self._polygon_key:
            return {}
        url = f"https://api.polygon.io/v3/quotes/{symbol}"
        try:
            resp = await self._http.get(url, params={"limit": 1, "sort": "timestamp", "order": "desc", "apiKey": self._polygon_key})
            resp.raise_for_status()
            results = resp.json().get("results", [])
            if results:
                q = results[0]
                return {
                    "bid": q.get("bid_price", 0),
                    "ask": q.get("ask_price", 0),
                    "bid_size": q.get("bid_size", 0),
                    "ask_size": q.get("ask_size", 0),
                }
            return {}
        except Exception as e:
            logger.warning("NBBO fetch failed for %s: %s", symbol, e)
            return {}
