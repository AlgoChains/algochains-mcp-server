"""
Genius Layer: Regime-Aware Strategy Selection

Problem: Strategies that work in bull markets fail in bear markets.
Users deploy one strategy and leave it running through regime changes.

Solution: Automatic regime detection from multiple market signals →
strategy recommendation → optional auto-switch.

Regime classification uses: VIX level, SPY trend (MA cross), market breadth
(advance/decline), and credit spread (HY-IG) as multi-factor inputs.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Any, Callable, Awaitable

logger = logging.getLogger("algochains.regime_detector")


class MarketRegime(str, Enum):
    STRONG_BULL = "strong_bull"
    BULL = "bull"
    RANGE = "range"
    BEAR = "bear"
    STRONG_BEAR = "strong_bear"
    VOLATILE = "volatile"
    CRISIS = "crisis"


REGIME_STRATEGIES: dict[str, list[str]] = {
    "strong_bull": ["momentum", "breakout", "trend_following"],
    "bull": ["ema_crossover", "rsi_pullback", "buy_dip"],
    "range": ["mean_reversion", "bollinger_bands", "rsi_overbought_oversold"],
    "bear": ["short_momentum", "put_spreads", "defensive"],
    "strong_bear": ["short_momentum", "inverse_etf", "put_buying"],
    "volatile": ["straddles", "volatility_targeting", "reduced_size"],
    "crisis": ["cash", "treasury_bonds", "gold", "vix_calls"],
}

REGIME_RISK_MULTIPLIERS: dict[str, float] = {
    "strong_bull": 1.2,
    "bull": 1.0,
    "range": 0.8,
    "bear": 0.6,
    "strong_bear": 0.4,
    "volatile": 0.5,
    "crisis": 0.2,
}


@dataclass
class RegimeSignals:
    """Raw market signals used for regime classification."""
    vix: Optional[float] = None
    spy_price: Optional[float] = None
    spy_sma_20: Optional[float] = None
    spy_sma_50: Optional[float] = None
    spy_sma_200: Optional[float] = None
    advance_decline_ratio: Optional[float] = None
    new_highs_lows_ratio: Optional[float] = None
    credit_spread_bps: Optional[float] = None
    put_call_ratio: Optional[float] = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class RegimeResult:
    """Result of regime detection with confidence and recommendations."""
    regime: MarketRegime = MarketRegime.RANGE
    confidence: float = 0.0
    signals_used: int = 0
    recommended_strategies: list[str] = field(default_factory=list)
    risk_multiplier: float = 1.0
    regime_scores: dict[str, float] = field(default_factory=dict)
    signals: Optional[RegimeSignals] = None
    detected_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "regime": self.regime.value,
            "confidence": round(self.confidence, 2),
            "signals_used": self.signals_used,
            "recommended_strategies": self.recommended_strategies,
            "risk_multiplier": self.risk_multiplier,
            "regime_scores": {k: round(v, 3) for k, v in self.regime_scores.items()},
            "signals": self.signals.to_dict() if self.signals else {},
        }


class RegimeDetector:
    """Detect current market regime from multiple signals.

    Multi-factor classification:
    - VIX level: Fear gauge (low=bull, high=crisis)
    - Trend: SPY vs its 20/50/200 SMAs
    - Breadth: Advance/decline ratio
    - Credit: HY-IG spread (widening=risk-off)
    - Sentiment: Put/call ratio
    """

    def __init__(self, data_fetcher: Optional[Callable[..., Awaitable[Any]]] = None):
        self._fetcher = data_fetcher
        self._history: list[RegimeResult] = []
        self._last_result: Optional[RegimeResult] = None

    async def detect(self, signals: Optional[RegimeSignals] = None) -> RegimeResult:
        """Detect current market regime from signals.

        If signals not provided and a data_fetcher is registered,
        will attempt to fetch live market data.
        """
        if signals is None:
            signals = await self._fetch_signals()

        scores: dict[str, float] = {r.value: 0.0 for r in MarketRegime}
        signals_used = 0

        # ── Factor 1: VIX Level (weight: 0.30) ──
        if signals.vix is not None:
            signals_used += 1
            vix = signals.vix
            if vix < 12:
                scores["strong_bull"] += 0.30
            elif vix < 16:
                scores["bull"] += 0.30
            elif vix < 20:
                scores["range"] += 0.20
                scores["bull"] += 0.10
            elif vix < 25:
                scores["range"] += 0.15
                scores["bear"] += 0.15
            elif vix < 30:
                scores["bear"] += 0.20
                scores["volatile"] += 0.10
            elif vix < 40:
                scores["strong_bear"] += 0.15
                scores["volatile"] += 0.15
            else:
                scores["crisis"] += 0.30

        # ── Factor 2: Trend — SPY vs SMAs (weight: 0.30) ──
        if signals.spy_price is not None:
            price = signals.spy_price
            above_count = 0
            sma_count = 0

            for sma in (signals.spy_sma_20, signals.spy_sma_50, signals.spy_sma_200):
                if sma is not None:
                    sma_count += 1
                    if price > sma:
                        above_count += 1

            if sma_count > 0:
                signals_used += 1
                ratio = above_count / sma_count

                if ratio >= 1.0:
                    scores["strong_bull"] += 0.20
                    scores["bull"] += 0.10
                elif ratio >= 0.66:
                    scores["bull"] += 0.25
                    scores["range"] += 0.05
                elif ratio >= 0.33:
                    scores["range"] += 0.20
                    scores["bear"] += 0.10
                else:
                    scores["bear"] += 0.15
                    scores["strong_bear"] += 0.15

            # Golden/death cross
            if signals.spy_sma_50 is not None and signals.spy_sma_200 is not None:
                if signals.spy_sma_50 > signals.spy_sma_200:
                    scores["bull"] += 0.05
                else:
                    scores["bear"] += 0.05

        # ── Factor 3: Market Breadth (weight: 0.20) ──
        if signals.advance_decline_ratio is not None:
            signals_used += 1
            adr = signals.advance_decline_ratio
            if adr > 2.0:
                scores["strong_bull"] += 0.20
            elif adr > 1.2:
                scores["bull"] += 0.20
            elif adr > 0.8:
                scores["range"] += 0.20
            elif adr > 0.5:
                scores["bear"] += 0.20
            else:
                scores["strong_bear"] += 0.15
                scores["crisis"] += 0.05

        # ── Factor 4: Credit Spread (weight: 0.10) ──
        if signals.credit_spread_bps is not None:
            signals_used += 1
            spread = signals.credit_spread_bps
            if spread < 300:
                scores["bull"] += 0.10
            elif spread < 400:
                scores["range"] += 0.10
            elif spread < 600:
                scores["bear"] += 0.10
            else:
                scores["crisis"] += 0.10

        # ── Factor 5: Put/Call Ratio (weight: 0.10) ──
        if signals.put_call_ratio is not None:
            signals_used += 1
            pcr = signals.put_call_ratio
            if pcr < 0.7:
                scores["strong_bull"] += 0.05
                scores["bull"] += 0.05
            elif pcr < 0.9:
                scores["bull"] += 0.05
                scores["range"] += 0.05
            elif pcr < 1.1:
                scores["range"] += 0.10
            elif pcr < 1.3:
                scores["bear"] += 0.10
            else:
                scores["strong_bear"] += 0.05
                scores["volatile"] += 0.05

        # ── Classify ──
        if signals_used == 0:
            result = RegimeResult(
                regime=MarketRegime.RANGE,
                confidence=0.0,
                signals_used=0,
                recommended_strategies=REGIME_STRATEGIES["range"],
                risk_multiplier=1.0,
                regime_scores=scores,
                signals=signals,
            )
        else:
            best_regime = max(scores, key=lambda k: scores[k])
            best_score = scores[best_regime]
            total_score = sum(scores.values())
            confidence = best_score / total_score if total_score > 0 else 0.0

            regime = MarketRegime(best_regime)
            result = RegimeResult(
                regime=regime,
                confidence=confidence,
                signals_used=signals_used,
                recommended_strategies=REGIME_STRATEGIES.get(best_regime, []),
                risk_multiplier=REGIME_RISK_MULTIPLIERS.get(best_regime, 1.0),
                regime_scores=scores,
                signals=signals,
            )

        self._last_result = result
        self._history.append(result)
        # Keep last 500 readings
        if len(self._history) > 500:
            self._history = self._history[-500:]

        logger.info(
            "Regime: %s (conf=%.0f%%, %d signals, risk=%.1fx)",
            result.regime.value, result.confidence * 100,
            result.signals_used, result.risk_multiplier,
        )
        return result

    def get_current(self) -> Optional[dict]:
        """Get most recent regime detection result."""
        if self._last_result:
            return self._last_result.to_dict()
        return None

    def get_history(self, limit: int = 50) -> list[dict]:
        """Get regime detection history."""
        return [r.to_dict() for r in self._history[-limit:]]

    def get_risk_multiplier(self) -> float:
        """Get current regime-adjusted risk multiplier for position sizing."""
        if self._last_result:
            return self._last_result.risk_multiplier
        return 1.0

    async def _fetch_signals(self) -> RegimeSignals:
        """Fetch live market signals using registered data fetcher."""
        signals = RegimeSignals()
        if self._fetcher:
            try:
                data = await self._fetcher()
                if isinstance(data, dict):
                    for field_name in (
                        "vix", "spy_price", "spy_sma_20", "spy_sma_50",
                        "spy_sma_200", "advance_decline_ratio",
                        "new_highs_lows_ratio", "credit_spread_bps",
                        "put_call_ratio",
                    ):
                        val = data.get(field_name)
                        if val is not None:
                            setattr(signals, field_name, float(val))
            except Exception as e:
                logger.warning("Failed to fetch regime signals: %s", e)
        return signals
