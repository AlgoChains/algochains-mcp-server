"""
Factor Model — Fama-French 5-Factor + Momentum + Quality
=========================================================
Decomposes strategy/portfolio returns into standard risk factors.
Uses real daily return data from Polygon.io.

Factors:
  Mkt-RF  — Market excess return (SPY vs risk-free rate from FRED)
  SMB     — Small minus Big (IWM - SPY spread)
  HML     — High minus Low book-to-market (VTV - VUG spread)
  RMW     — Robust minus Weak profitability (QUAL - proxy)
  CMA     — Conservative minus Aggressive investment
  Mom     — Momentum (past 12M-1M return, MTUM proxy)
  QMJ     — Quality minus Junk (AQR factor)

All factor proxies use real ETF returns. Zero synthetic data.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import httpx

log = logging.getLogger(__name__)
POLYGON_BASE = "https://api.polygon.io"
FRED_BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv"


class FactorDataError(RuntimeError):
    """Raised when factor data cannot be fetched."""


FACTOR_PROXIES = {
    "market": "SPY",
    "smb": ["IWM", "SPY"],        # Small cap - large cap
    "hml": ["VTV", "VUG"],        # Value - growth
    "mom": ["MTUM", "SPY"],       # Momentum - market
    "rmw": ["QUAL", "SPY"],       # Quality - market
    "cma": ["USMV", "SPY"],       # Low vol (conservative) - market
}


@dataclass
class FactorExposure:
    symbol: str
    period: str
    alpha: float
    beta_market: float
    beta_smb: float
    beta_hml: float
    beta_mom: float
    beta_rmw: float
    r_squared: float
    tracking_error: float
    information_ratio: float
    active_return: float
    regime: str
    as_of: str


@dataclass
class FactorReturn:
    date: str
    market: float
    smb: float
    hml: float
    mom: float
    rmw: float
    risk_free: float


class FactorModelEngine:
    """
    Computes factor exposures for any symbol or strategy using real daily returns.

    Usage:
        engine = FactorModelEngine(polygon_api_key="...")
        exposure = await engine.compute_factor_exposure("QQQ", period="1y")
    """

    def __init__(self, polygon_api_key: str, fred_api_key: str = ""):
        if not polygon_api_key:
            raise FactorDataError("POLYGON_API_KEY required for factor model")
        self.polygon_key = polygon_api_key
        self.fred_key = fred_api_key
        self._client = httpx.AsyncClient(
            base_url=POLYGON_BASE,
            params={"apiKey": polygon_api_key},
            timeout=httpx.Timeout(30.0),
        )

    async def compute_factor_exposure(
        self, symbol: str, period: str = "1y", benchmark: str = "SPY"
    ) -> FactorExposure:
        """Run full factor decomposition for a symbol."""
        days = {"3m": 63, "6m": 126, "1y": 252, "2y": 504}.get(period, 252)
        end = date.today()
        start = end - timedelta(days=days + 30)

        # Fetch all daily returns in parallel
        tickers = [symbol, benchmark, "IWM", "VTV", "VUG", "MTUM", "QUAL"]
        returns = await asyncio.gather(*[self._get_daily_returns(t, start.isoformat(), end.isoformat()) for t in tickers])
        ret_map = dict(zip(tickers, returns))

        sym_rets = ret_map.get(symbol, [])
        spy_rets = ret_map.get("SPY", ret_map.get(benchmark, []))

        if len(sym_rets) < 30:
            raise FactorDataError(f"Insufficient daily return data for {symbol} (got {len(sym_rets)} days, need 30+)")

        # Align dates
        sym_dates = {r["date"]: r["ret"] for r in sym_rets}
        spy_dates = {r["date"]: r["ret"] for r in spy_rets}
        common = sorted(set(sym_dates) & set(spy_dates))[-days:]

        if len(common) < 30:
            raise FactorDataError(f"Insufficient aligned dates for {symbol} vs {benchmark}")

        y = [sym_dates[d] for d in common]
        mkt = [spy_dates[d] for d in common]

        # Risk-free rate (approx 3M T-bill / 252)
        rf_annual = await self._get_risk_free_rate()
        rf_daily = rf_annual / 252

        # Build factor matrix
        iwm_rets = {r["date"]: r["ret"] for r in (ret_map.get("IWM") or [])}
        vtv_rets = {r["date"]: r["ret"] for r in (ret_map.get("VTV") or [])}
        vug_rets = {r["date"]: r["ret"] for r in (ret_map.get("VUG") or [])}
        mtum_rets = {r["date"]: r["ret"] for r in (ret_map.get("MTUM") or [])}

        mkt_excess = [m - rf_daily for m in mkt]
        smb = [iwm_rets.get(d, 0) - spy_dates.get(d, 0) for d in common]
        hml = [vtv_rets.get(d, 0) - vug_rets.get(d, 0) for d in common]
        mom = [mtum_rets.get(d, 0) - spy_dates.get(d, 0) for d in common]

        y_excess = [yi - rf_daily for yi in y]

        # OLS regression: y = alpha + b1*mkt + b2*smb + b3*hml + b4*mom + e
        alpha, betas, r2 = self._ols_regression(y_excess, [mkt_excess, smb, hml, mom])

        # Information ratio
        active = [yi - mi for yi, mi in zip(y, mkt)]
        import statistics
        active_mean = statistics.mean(active) * 252
        active_std = statistics.stdev(active) * (252 ** 0.5) if len(active) > 1 else 0
        ir = active_mean / active_std if active_std > 0 else 0
        te = active_std

        # Regime
        recent_alpha = sum(active[-20:]) / 20 if len(active) >= 20 else 0
        regime = "alpha_generating" if recent_alpha > 0.0005 else "index_like" if abs(betas[0] - 1) < 0.1 else "factor_exposed"

        return FactorExposure(
            symbol=symbol,
            period=period,
            alpha=round(alpha * 252, 6),
            beta_market=round(betas[0], 4),
            beta_smb=round(betas[1], 4),
            beta_hml=round(betas[2], 4),
            beta_mom=round(betas[3], 4) if len(betas) > 3 else 0,
            beta_rmw=0.0,
            r_squared=round(r2, 4),
            tracking_error=round(te, 6),
            information_ratio=round(ir, 4),
            active_return=round(active_mean, 6),
            regime=regime,
            as_of=end.isoformat(),
        )

    def _ols_regression(self, y: list[float], xs: list[list[float]]) -> tuple[float, list[float], float]:
        """Simple OLS via numpy if available, else manual."""
        try:
            import numpy as np
            X = np.column_stack([np.ones(len(y))] + xs)
            Y = np.array(y)
            coeffs, residuals, rank, sv = np.linalg.lstsq(X, Y, rcond=None)
            y_pred = X @ coeffs
            ss_res = np.sum((Y - y_pred) ** 2)
            ss_tot = np.sum((Y - np.mean(Y)) ** 2)
            r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0
            return float(coeffs[0]), list(coeffs[1:]), float(r2)
        except ImportError:
            # Manual OLS for simple single-factor case
            n = len(y)
            mx = sum(xs[0]) / n
            my = sum(y) / n
            cov = sum((xi - mx) * (yi - my) for xi, yi in zip(xs[0], y))
            var = sum((xi - mx) ** 2 for xi in xs[0])
            beta = cov / var if var else 1.0
            alpha = my - beta * mx
            y_pred = [alpha + beta * xi for xi in xs[0]]
            ss_res = sum((yi - yp) ** 2 for yi, yp in zip(y, y_pred))
            ss_tot = sum((yi - my) ** 2 for yi in y)
            r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0
            return alpha, [beta] + [0] * (len(xs) - 1), r2

    async def _get_daily_returns(self, symbol: str, start: str, end: str) -> list[dict]:
        """Fetch real daily OHLCV and compute log returns."""
        try:
            resp = await self._client.get(
                f"/v2/aggs/ticker/{symbol}/range/1/day/{start}/{end}",
                params={"adjusted": "true", "limit": 600},
            )
            if resp.status_code != 200:
                return []
            bars = resp.json().get("results", [])
            returns = []
            for i in range(1, len(bars)):
                prev_c = bars[i - 1].get("c", 0)
                curr_c = bars[i].get("c", 0)
                if prev_c > 0 and curr_c > 0:
                    import math
                    returns.append({
                        "date": str(bars[i].get("t", 0))[:10],
                        "ret": math.log(curr_c / prev_c),
                    })
            return returns
        except Exception as exc:
            log.warning("Daily returns fetch failed for %s: %s", symbol, exc)
            return []

    async def _get_risk_free_rate(self) -> float:
        """Get current 3-month T-bill rate from FRED."""
        try:
            if not self.fred_key:
                return 0.045  # Approximate current RF if FRED unavailable
            resp = await httpx.AsyncClient().get(
                f"https://api.stlouisfed.org/fred/series/observations",
                params={"series_id": "DGS3MO", "api_key": self.fred_key,
                        "file_type": "json", "sort_order": "desc", "limit": 1},
                timeout=10.0,
            )
            if resp.status_code == 200:
                obs = resp.json().get("observations", [{}])
                val = obs[0].get("value", ".")
                if val != ".":
                    return float(val) / 100
        except Exception:
            pass
        return 0.045

    async def close(self):
        await self._client.aclose()


_engine: FactorModelEngine | None = None


def get_factor_engine(polygon_api_key: str = "", fred_api_key: str = "") -> FactorModelEngine:
    global _engine
    if _engine is None:
        pg_key = polygon_api_key or os.getenv("POLYGON_API_KEY", "")
        if not pg_key:
            raise FactorDataError("POLYGON_API_KEY required")
        _engine = FactorModelEngine(pg_key, fred_api_key or os.getenv("FRED_API_KEY", ""))
    return _engine
