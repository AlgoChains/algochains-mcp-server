"""
Hidden Markov Model Regime Detector
=====================================
Detects market regimes using a Gaussian HMM on real daily returns.

Regimes:
  bull_trending   — Low vol, positive drift, upward bias
  bear_trending   — Low vol, negative drift, downward bias
  choppy          — High vol, mean-reverting, no clear trend
  crisis          — Extreme vol, negative drift, tail risk

Uses real Polygon daily OHLCV data. Falls back to rolling-window
statistics if hmmlearn is unavailable.

Real data only. Raises RegimeDataError if Polygon unavailable.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import httpx

log = logging.getLogger(__name__)
POLYGON_BASE = "https://api.polygon.io"


class RegimeDataError(RuntimeError):
    pass


@dataclass
class RegimeState:
    symbol: str
    current_regime: str
    regime_probability: float
    days_in_regime: int
    transition_probability: dict[str, float]
    volatility_regime: str  # low, normal, high, extreme
    trend_bias: str  # bullish, bearish, neutral
    mean_return_daily: float
    volatility_daily: float
    sharpe_annualized: float
    as_of: str
    method: str  # "hmm" or "rolling_stats"


REGIME_LABELS = {
    0: "bull_trending",
    1: "bear_trending",
    2: "choppy",
    3: "crisis",
}


class RegimeHMMDetector:
    """
    Identifies market regime using real historical returns.

    If hmmlearn is installed, uses full Gaussian HMM.
    Otherwise, uses statistical heuristics (rolling mean/vol).
    Both modes use REAL data only.
    """

    def __init__(self, polygon_api_key: str):
        if not polygon_api_key:
            raise RegimeDataError("POLYGON_API_KEY required for regime detection")
        self.api_key = polygon_api_key
        self._client = httpx.AsyncClient(
            base_url=POLYGON_BASE,
            params={"apiKey": polygon_api_key},
            timeout=httpx.Timeout(30.0),
        )

    async def detect_regime(self, symbol: str = "SPY", lookback_days: int = 252) -> RegimeState:
        """Detect current market regime using real return data."""
        end = date.today()
        start = end - timedelta(days=lookback_days + 30)

        returns, dates = await self._get_returns(symbol, start.isoformat(), end.isoformat())
        if len(returns) < 40:
            raise RegimeDataError(
                f"Need 40+ days of return data for {symbol}, got {len(returns)}. "
                "Check POLYGON_API_KEY and symbol validity."
            )

        try:
            return self._hmm_detect(symbol, returns, dates)
        except ImportError:
            return self._rolling_stats_detect(symbol, returns, dates)

    def _hmm_detect(self, symbol: str, returns: list[float], dates: list[str]) -> RegimeState:
        """HMM-based detection using hmmlearn."""
        from hmmlearn import hmm
        import numpy as np

        X = np.array(returns).reshape(-1, 1)
        model = hmm.GaussianHMM(n_components=4, covariance_type="diag", n_iter=200, random_state=42)
        model.fit(X)

        hidden_states = model.predict(X)
        probs = model.predict_proba(X)

        # Label regimes by mean return and volatility
        state_stats = {}
        for s in range(4):
            mask = hidden_states == s
            if mask.sum() > 0:
                state_returns = X[mask, 0]
                state_stats[s] = {
                    "mean": float(state_returns.mean()),
                    "std": float(state_returns.std()),
                    "count": int(mask.sum()),
                }

        # Sort: positive high = bull, negative high = bear, low vol = choppy, extreme neg = crisis
        def label_state(s: int) -> str:
            stats = state_stats.get(s, {"mean": 0, "std": 0.01})
            m, v = stats["mean"], stats["std"]
            if v > 0.02:
                return "crisis" if m < -0.001 else "choppy"
            return "bull_trending" if m > 0 else "bear_trending"

        current_state = int(hidden_states[-1])
        current_label = label_state(current_state)
        current_prob = float(probs[-1, current_state])

        # Days in current regime
        days_in = 0
        for s in reversed(hidden_states):
            if s == current_state:
                days_in += 1
            else:
                break

        # Transition probs from current state
        trans = {label_state(j): float(model.transmat_[current_state, j]) for j in range(4)}

        # Volatility and trend
        recent = returns[-20:]
        import statistics
        vol = statistics.stdev(recent) * (252 ** 0.5)
        drift = statistics.mean(recent) * 252
        sharpe = drift / vol if vol > 0 else 0

        vol_regime = "extreme" if vol > 0.35 else "high" if vol > 0.25 else "low" if vol < 0.12 else "normal"
        trend_bias = "bullish" if drift > 0.05 else "bearish" if drift < -0.05 else "neutral"

        return RegimeState(
            symbol=symbol,
            current_regime=current_label,
            regime_probability=current_prob,
            days_in_regime=days_in,
            transition_probability=trans,
            volatility_regime=vol_regime,
            trend_bias=trend_bias,
            mean_return_daily=float(statistics.mean(recent)),
            volatility_daily=float(statistics.stdev(recent)) if len(recent) > 1 else 0,
            sharpe_annualized=round(sharpe, 4),
            as_of=dates[-1] if dates else date.today().isoformat(),
            method="hmm",
        )

    def _rolling_stats_detect(self, symbol: str, returns: list[float], dates: list[str]) -> RegimeState:
        """Regime detection using rolling statistics (no hmmlearn required)."""
        import statistics

        window = min(40, len(returns))
        recent = returns[-window:]
        vol = statistics.stdev(recent) * (252 ** 0.5) if len(recent) > 1 else 0
        drift = statistics.mean(recent) * 252
        sharpe = drift / vol if vol > 0 else 0

        # Classify regime
        if vol > 0.35 and drift < 0:
            regime = "crisis"
            prob = 0.85
        elif vol > 0.25:
            regime = "choppy"
            prob = 0.75
        elif drift > 0.08:
            regime = "bull_trending"
            prob = 0.7 + min(drift / 0.5, 0.25)
        elif drift < -0.05:
            regime = "bear_trending"
            prob = 0.7 + min(abs(drift) / 0.5, 0.25)
        else:
            regime = "choppy"
            prob = 0.6

        vol_regime = "extreme" if vol > 0.35 else "high" if vol > 0.25 else "low" if vol < 0.12 else "normal"
        trend_bias = "bullish" if drift > 0.05 else "bearish" if drift < -0.05 else "neutral"

        return RegimeState(
            symbol=symbol,
            current_regime=regime,
            regime_probability=round(prob, 4),
            days_in_regime=window,
            transition_probability={"bull_trending": 0.25, "bear_trending": 0.25, "choppy": 0.35, "crisis": 0.15},
            volatility_regime=vol_regime,
            trend_bias=trend_bias,
            mean_return_daily=round(statistics.mean(recent), 6),
            volatility_daily=round(statistics.stdev(recent), 6) if len(recent) > 1 else 0,
            sharpe_annualized=round(sharpe, 4),
            as_of=dates[-1] if dates else date.today().isoformat(),
            method="rolling_stats",
        )

    async def _get_returns(self, symbol: str, start: str, end: str) -> tuple[list[float], list[str]]:
        try:
            resp = await self._client.get(
                f"/v2/aggs/ticker/{symbol}/range/1/day/{start}/{end}",
                params={"adjusted": "true", "limit": 500},
            )
            if resp.status_code != 200:
                raise RegimeDataError(f"Polygon returned {resp.status_code} for {symbol}")
            bars = resp.json().get("results", [])
            if not bars:
                raise RegimeDataError(f"No bar data returned for {symbol}")

            returns, dates = [], []
            for i in range(1, len(bars)):
                prev, curr = bars[i - 1].get("c", 0), bars[i].get("c", 0)
                if prev > 0 and curr > 0:
                    import math
                    returns.append(math.log(curr / prev))
                    dates.append(str(bars[i].get("t", ""))[:10])
            return returns, dates
        except RegimeDataError:
            raise
        except Exception as exc:
            raise RegimeDataError(f"Failed to fetch daily data for {symbol}: {exc}") from exc

    async def close(self):
        await self._client.aclose()


_engine: RegimeHMMDetector | None = None


def get_regime_detector(polygon_api_key: str = "") -> RegimeHMMDetector:
    global _engine
    if _engine is None:
        key = polygon_api_key or os.getenv("POLYGON_API_KEY", "")
        if not key:
            raise RegimeDataError("POLYGON_API_KEY required")
        _engine = RegimeHMMDetector(key)
    return _engine
