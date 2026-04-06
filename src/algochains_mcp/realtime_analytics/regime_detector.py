"""Market regime detection — volatility & trend regime classification.

Real implementation using daily returns from Polygon to classify market
regimes via rolling volatility, trend strength (SMA slope), and mean-
reversion metrics.  Builds empirical transition matrix from history.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx

logger = logging.getLogger("algochains_mcp.realtime_analytics.regime_detector")


class RegimeDetector:
    """Market regime detection using volatility and trend analysis."""

    REGIMES = ("trending_up", "trending_down", "mean_reverting", "high_vol", "low_vol")

    def __init__(self, polygon_key: str = "") -> None:
        self._polygon_key = polygon_key
        self._http = httpx.AsyncClient(timeout=30)
        self._detection_history: list[dict] = []

    async def detect(self, symbol: str, method: str = "hmm") -> dict:
        """Detect current market regime from daily bar data."""
        bars = await self._fetch_bars(symbol, 120)
        if len(bars) < 30:
            return {"status": "error", "error": f"Insufficient data: {len(bars)} bars (need 30+)"}

        closes = [b["c"] for b in bars]
        returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes)) if closes[i - 1] != 0]

        vol_20 = self._rolling_std(returns, 20)
        vol_60 = self._rolling_std(returns, min(60, len(returns)))

        sma_20 = sum(closes[-20:]) / 20
        sma_50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else sma_20
        current = closes[-1]

        trend_strength = (current - sma_50) / sma_50 if sma_50 > 0 else 0
        mean_rev_score = abs(current - sma_20) / sma_20 if sma_20 > 0 else 0

        regime, confidence, probs = self._classify(vol_20, vol_60, trend_strength, mean_rev_score)

        duration = self._estimate_duration(returns, regime)

        result = {
            "symbol": symbol,
            "method": method,
            "current_regime": regime,
            "confidence": round(confidence, 3),
            "regime_probabilities": {r: round(p, 3) for r, p in probs.items()},
            "regime_duration_days": duration,
            "vol_20d": round(vol_20 * math.sqrt(252) * 100, 2),
            "vol_60d": round(vol_60 * math.sqrt(252) * 100, 2),
            "trend_strength": round(trend_strength * 100, 2),
            "current_price": current,
            "sma_20": round(sma_20, 2),
            "sma_50": round(sma_50, 2),
            "bars_analyzed": len(bars),
            "detected_at": datetime.now(timezone.utc).isoformat(),
        }
        self._detection_history.append(result)
        return {"status": "ok", "data": result}

    async def get_history(self, symbol: str, lookback_days: int = 90) -> dict:
        """Compute regime history — classify each window and track transitions."""
        bars = await self._fetch_bars(symbol, lookback_days + 60)
        if len(bars) < 40:
            return {"status": "error", "error": "Insufficient data for history"}

        closes = [b["c"] for b in bars]
        returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes)) if closes[i - 1] != 0]

        transitions: list[dict] = []
        prev_regime = None
        streak = 0

        for i in range(30, len(returns)):
            window = returns[max(0, i - 20):i]
            vol_short = self._rolling_std(window, len(window))
            long_window = returns[max(0, i - 60):i]
            vol_long = self._rolling_std(long_window, len(long_window))

            c_slice = closes[max(0, i - 19):i + 1]
            sma20 = sum(c_slice) / len(c_slice) if c_slice else 0
            c50 = closes[max(0, i - 49):i + 1]
            sma50 = sum(c50) / len(c50) if c50 else sma20
            cur = closes[i]

            trend = (cur - sma50) / sma50 if sma50 > 0 else 0
            mr = abs(cur - sma20) / sma20 if sma20 > 0 else 0

            regime, conf, _ = self._classify(vol_short, vol_long, trend, mr)

            if regime != prev_regime:
                if prev_regime is not None:
                    transitions.append({
                        "from": prev_regime,
                        "to": regime,
                        "day_index": i,
                        "duration": streak,
                    })
                prev_regime = regime
                streak = 1
            else:
                streak += 1

        regime_counts: dict[str, list[int]] = {r: [] for r in self.REGIMES}
        for t in transitions:
            regime_counts[t["from"]].append(t["duration"])

        regime_stats = {}
        for r in self.REGIMES:
            durations = regime_counts[r]
            regime_stats[r] = {
                "avg_duration_days": round(sum(durations) / len(durations), 1) if durations else 0,
                "frequency": len(durations),
            }

        return {
            "status": "ok",
            "symbol": symbol,
            "lookback_days": lookback_days,
            "transitions": transitions[-20:],
            "regime_stats": regime_stats,
            "total_transitions": len(transitions),
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    async def get_transition_matrix(self, symbol: str) -> dict:
        """Build empirical regime transition probability matrix."""
        history = await self.get_history(symbol, lookback_days=180)
        if history.get("status") != "ok":
            return history

        transitions = history.get("transitions", [])
        counts: dict[str, dict[str, int]] = {r: {r2: 0 for r2 in self.REGIMES} for r in self.REGIMES}

        for t in transitions:
            fr, to = t["from"], t["to"]
            if fr in counts and to in counts[fr]:
                counts[fr][to] += 1

        matrix: dict[str, dict[str, float]] = {}
        for r in self.REGIMES:
            total = sum(counts[r].values())
            if total > 0:
                matrix[r] = {r2: round(counts[r][r2] / total, 3) for r2 in self.REGIMES}
            else:
                matrix[r] = {r2: round(1.0 / len(self.REGIMES), 3) for r2 in self.REGIMES}

        return {
            "status": "ok",
            "symbol": symbol,
            "transition_matrix": matrix,
            "sample_transitions": len(transitions),
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    def _classify(
        self, vol_short: float, vol_long: float, trend: float, mean_rev: float
    ) -> tuple[str, float, dict[str, float]]:
        """Classify regime from metrics. Returns (regime, confidence, probabilities)."""
        scores: dict[str, float] = {r: 0.0 for r in self.REGIMES}

        ann_vol = vol_short * math.sqrt(252)

        if ann_vol > 0.30:
            scores["high_vol"] += 3.0
        elif ann_vol < 0.12:
            scores["low_vol"] += 2.5

        if trend > 0.03:
            scores["trending_up"] += 2.0 + min(trend * 10, 3.0)
        elif trend < -0.03:
            scores["trending_down"] += 2.0 + min(abs(trend) * 10, 3.0)

        if mean_rev < 0.02 and ann_vol < 0.20:
            scores["mean_reverting"] += 2.5
        if mean_rev < 0.01:
            scores["mean_reverting"] += 1.5

        if vol_long > 0 and vol_short / vol_long > 1.5:
            scores["high_vol"] += 1.5
        elif vol_long > 0 and vol_short / vol_long < 0.7:
            scores["low_vol"] += 1.0

        total = sum(scores.values()) or 1.0
        probs = {r: scores[r] / total for r in self.REGIMES}
        regime = max(probs, key=probs.get)
        confidence = probs[regime]

        return regime, confidence, probs

    def _estimate_duration(self, returns: list[float], regime: str) -> int:
        """Estimate how many days the current regime has persisted."""
        if len(returns) < 5:
            return 0
        duration = 0
        for i in range(len(returns) - 1, max(len(returns) - 60, -1), -1):
            window = returns[max(0, i - 19):i + 1]
            if len(window) < 5:
                break
            vol = self._rolling_std(window, len(window))
            ann = vol * math.sqrt(252)
            ret_sum = sum(window)

            if regime == "high_vol" and ann > 0.25:
                duration += 1
            elif regime == "low_vol" and ann < 0.15:
                duration += 1
            elif regime == "trending_up" and ret_sum > 0:
                duration += 1
            elif regime == "trending_down" and ret_sum < 0:
                duration += 1
            elif regime == "mean_reverting" and abs(ret_sum) < 0.01:
                duration += 1
            else:
                break
        return duration

    @staticmethod
    def _rolling_std(data: list[float], window: int) -> float:
        if len(data) < 2:
            return 0.0
        subset = data[-window:]
        n = len(subset)
        if n < 2:
            return 0.0
        mean = sum(subset) / n
        var = sum((x - mean) ** 2 for x in subset) / (n - 1)
        return math.sqrt(var)

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
