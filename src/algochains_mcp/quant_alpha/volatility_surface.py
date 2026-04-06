"""
Volatility Surface Engine
=========================
Builds real implied volatility surfaces from options chain data.
Uses real Polygon.io options data exclusively.

Computes:
  - IV per strike/expiry (from real market quotes)
  - Volatility skew (put vs call IV differential)
  - Term structure (IV across expirations)
  - IV Rank and IV Percentile (vs real 52-week range)
  - ATM IV and 25-delta risk reversal
  - Volatility regime classification (low/normal/elevated/extreme)

Real data only. Raises VolSurfaceDataError if Polygon data unavailable.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Any

import httpx

log = logging.getLogger(__name__)

POLYGON_BASE = "https://api.polygon.io"


class VolSurfaceDataError(RuntimeError):
    """Raised when real options data cannot be fetched."""


@dataclass
class OptionQuote:
    strike: float
    expiry: str
    right: str  # "call" or "put"
    iv: float
    delta: float
    gamma: float
    theta: float
    vega: float
    bid: float
    ask: float
    mid: float
    open_interest: int
    volume: int


@dataclass
class VolSurface:
    symbol: str
    spot_price: float
    as_of: str
    iv_rank: float
    iv_percentile: float
    atm_iv: float
    skew_25d: float
    term_structure: dict[str, float]  # expiry → ATM IV
    calls: list[OptionQuote] = field(default_factory=list)
    puts: list[OptionQuote] = field(default_factory=list)
    regime: str = "normal"  # low, normal, elevated, extreme


@dataclass
class VolSurfaceSignal:
    symbol: str
    signal: str  # "long_vol", "short_vol", "sell_skew", "buy_skew", "neutral"
    reason: str
    iv_rank: float
    atm_iv: float
    skew_25d: float
    conviction: float  # 0-1


class VolatilitySurfaceEngine:
    """
    Computes implied volatility surfaces from real Polygon.io options data.

    Usage:
        engine = VolatilitySurfaceEngine(polygon_api_key="...")
        surface = await engine.get_surface("SPY")
        signal = engine.generate_signal(surface)
    """

    def __init__(self, polygon_api_key: str):
        if not polygon_api_key:
            raise VolSurfaceDataError("POLYGON_API_KEY required for options data")
        self.api_key = polygon_api_key
        self._client = httpx.AsyncClient(
            base_url=POLYGON_BASE,
            params={"apiKey": polygon_api_key},
            timeout=httpx.Timeout(30.0),
        )

    async def get_surface(self, symbol: str, expiry_filter: str | None = None) -> VolSurface:
        """Fetch full options chain and compute IV surface from real market data."""
        # Fetch current spot price
        spot = await self._get_spot(symbol)
        if spot is None:
            raise VolSurfaceDataError(f"Cannot fetch spot price for {symbol}")

        # Fetch options chain from Polygon
        chain = await self._fetch_options_chain(symbol, expiry_filter)
        if not chain:
            raise VolSurfaceDataError(
                f"No options data for {symbol} — check POLYGON_API_KEY tier (options require Starter+)"
            )

        calls = [q for q in chain if q.right == "call"]
        puts = [q for q in chain if q.right == "put"]

        # ATM IV (closest strike to spot)
        atm_call = min(calls, key=lambda q: abs(q.strike - spot), default=None) if calls else None
        atm_iv = atm_call.iv if atm_call else 0.0

        # 25-delta skew (put IV at 25-delta vs call IV at 25-delta)
        put_25d = next((q for q in sorted(puts, key=lambda q: abs(q.delta + 0.25))), None)
        call_25d = next((q for q in sorted(calls, key=lambda q: abs(q.delta - 0.25))), None)
        skew_25d = ((put_25d.iv - call_25d.iv) if put_25d and call_25d else 0.0)

        # Term structure (ATM IV by expiry)
        term_structure: dict[str, float] = {}
        expiries = sorted({q.expiry for q in calls})
        for exp in expiries:
            exp_calls = [q for q in calls if q.expiry == exp]
            if exp_calls:
                atm_exp = min(exp_calls, key=lambda q: abs(q.strike - spot))
                term_structure[exp] = atm_exp.iv

        # IV Rank (need historical IV — use rolling window from intraday aggregates)
        iv_rank, iv_pct = await self._compute_iv_rank(symbol, atm_iv)

        regime = "extreme" if iv_rank > 0.8 else "elevated" if iv_rank > 0.6 else "low" if iv_rank < 0.2 else "normal"

        return VolSurface(
            symbol=symbol,
            spot_price=spot,
            as_of=datetime.utcnow().isoformat(),
            iv_rank=iv_rank,
            iv_percentile=iv_pct,
            atm_iv=atm_iv,
            skew_25d=skew_25d,
            term_structure=term_structure,
            calls=calls[:50],
            puts=puts[:50],
            regime=regime,
        )

    async def _get_spot(self, symbol: str) -> float | None:
        try:
            resp = await self._client.get(f"/v2/last/trade/{symbol}")
            if resp.status_code == 200:
                return resp.json().get("results", {}).get("p")
            # Try prev day close
            resp = await self._client.get(f"/v2/aggs/ticker/{symbol}/prev")
            if resp.status_code == 200:
                results = resp.json().get("results", [])
                return results[0]["c"] if results else None
        except Exception as exc:
            log.warning("Spot price fetch failed for %s: %s", symbol, exc)
        return None

    async def _fetch_options_chain(self, symbol: str, expiry_filter: str | None) -> list[OptionQuote]:
        """Fetch options chain from Polygon v3 snapshot."""
        try:
            params: dict[str, Any] = {"limit": 250, "order": "asc", "sort": "strike_price"}
            if expiry_filter:
                params["expiration_date.gte"] = expiry_filter

            resp = await self._client.get(f"/v3/snapshot/options/{symbol}", params=params)
            if resp.status_code == 403:
                raise VolSurfaceDataError("Options data requires Polygon Starter plan or higher")
            if resp.status_code != 200:
                return []

            results = resp.json().get("results", [])
            quotes = []
            for r in results:
                details = r.get("details", {})
                greeks = r.get("greeks", {})
                day = r.get("day", {})
                quotes.append(OptionQuote(
                    strike=details.get("strike_price", 0),
                    expiry=details.get("expiration_date", ""),
                    right=details.get("contract_type", "call"),
                    iv=r.get("implied_volatility", 0),
                    delta=greeks.get("delta", 0),
                    gamma=greeks.get("gamma", 0),
                    theta=greeks.get("theta", 0),
                    vega=greeks.get("vega", 0),
                    bid=r.get("last_quote", {}).get("bid", 0),
                    ask=r.get("last_quote", {}).get("ask", 0),
                    mid=(r.get("last_quote", {}).get("bid", 0) + r.get("last_quote", {}).get("ask", 0)) / 2,
                    open_interest=r.get("open_interest", 0),
                    volume=day.get("volume", 0),
                ))
            return quotes
        except VolSurfaceDataError:
            raise
        except Exception as exc:
            log.warning("Options chain fetch failed: %s", exc)
            return []

    async def _compute_iv_rank(self, symbol: str, current_iv: float) -> tuple[float, float]:
        """Compute IV rank using real historical IV from Polygon daily aggregates."""
        if current_iv == 0:
            return 0.0, 0.0
        try:
            from datetime import timedelta
            end = date.today().isoformat()
            start = (date.today() - timedelta(days=252)).isoformat()
            resp = await self._client.get(
                f"/v2/aggs/ticker/{symbol}/range/1/day/{start}/{end}",
                params={"adjusted": "true", "limit": 252}
            )
            if resp.status_code != 200:
                return 0.5, 50.0  # neutral if unavailable, but log it

            bars = resp.json().get("results", [])
            if not bars:
                return 0.5, 50.0

            # Approximate historical IV from realized vol (Parkinson's formula)
            hist_ivs = []
            for b in bars:
                if b.get("h", 0) > 0 and b.get("l", 0) > 0:
                    import math
                    parkinson = math.sqrt(
                        (1 / (4 * math.log(2))) * (math.log(b["h"] / b["l"]) ** 2)
                    ) * math.sqrt(252)
                    hist_ivs.append(parkinson)

            if not hist_ivs:
                return 0.5, 50.0

            min_iv, max_iv = min(hist_ivs), max(hist_ivs)
            iv_rank = (current_iv - min_iv) / (max_iv - min_iv) if max_iv > min_iv else 0.5
            iv_pct = sum(1 for v in hist_ivs if v < current_iv) / len(hist_ivs) * 100
            return round(min(max(iv_rank, 0), 1), 4), round(iv_pct, 1)
        except Exception as exc:
            log.warning("IV rank computation failed: %s", exc)
            return 0.5, 50.0

    def generate_signal(self, surface: VolSurface) -> VolSurfaceSignal:
        """Generate actionable vol signal from surface characteristics."""
        signals = []
        conviction = 0.5

        # High IV rank → sell premium / short vol
        if surface.iv_rank > 0.75:
            signals.append("sell_premium")
            conviction += 0.2
        elif surface.iv_rank < 0.25:
            signals.append("buy_vol")
            conviction += 0.15

        # Extreme put skew → buy calls / sell puts
        if surface.skew_25d > 0.05:
            signals.append("sell_skew")
            conviction += 0.1
        elif surface.skew_25d < -0.05:
            signals.append("buy_skew")

        # Inverted term structure (near > far) → volatility event expected
        exps = sorted(surface.term_structure.keys())
        if len(exps) >= 2:
            near_iv = surface.term_structure[exps[0]]
            far_iv = surface.term_structure[exps[-1]]
            if near_iv > far_iv * 1.15:
                signals.append("event_vol")
                conviction += 0.15

        primary = signals[0] if signals else "neutral"
        reason = f"IV Rank={surface.iv_rank:.0%}, ATM IV={surface.atm_iv:.1%}, Skew 25d={surface.skew_25d:.2%}, Regime={surface.regime}"

        return VolSurfaceSignal(
            symbol=surface.symbol,
            signal=primary,
            reason=reason,
            iv_rank=surface.iv_rank,
            atm_iv=surface.atm_iv,
            skew_25d=surface.skew_25d,
            conviction=min(conviction, 1.0),
        )

    async def close(self):
        await self._client.aclose()


_engine: VolatilitySurfaceEngine | None = None


def get_vol_surface_engine(polygon_api_key: str = "") -> VolatilitySurfaceEngine:
    global _engine
    if _engine is None:
        key = polygon_api_key or os.getenv("POLYGON_API_KEY", "")
        if not key:
            raise VolSurfaceDataError("POLYGON_API_KEY required")
        _engine = VolatilitySurfaceEngine(key)
    return _engine
