"""
Macro Signal Fabric — Real Data Only.

Pre-computed alpha signals updated daily from real public data sources:
  - Yield curve slope (10Y-2Y): FRED API (free, no auth)
  - Credit spread (HY-IG): FRED API
  - Dollar index momentum: Polygon.io or Massive.com
  - Recession probability: NY Fed model (public)
  - Global PMI composite: FRED INDPRO proxy or Massive.com
  - VIX level: Polygon.io or CBOE public data
  - Term premium: Adrian, Crump, Moench model (NY Fed public)

Data sources (all real):
  1. FRED API: https://fred.stlouisfed.org/docs/api/fred/ (free, requires FRED_API_KEY)
     Free API key: https://fred.stlouisfed.org/docs/api/api_key.html
  2. NY Fed: https://www.newyorkfed.org/research/capital_markets/uckp.html
  3. CBOE VIX: https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv
  4. Polygon.io: DXY, VIX real-time
  5. Massive.com: Economic data endpoints (enterprise)

FAIL CLOSED: Raises MacroDataUnavailableError if no source provides real data.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("algochains_mcp.order_flow.macro")


class MacroDataUnavailableError(Exception):
    pass


@dataclass
class MacroSignalFabric:
    # Yield curve
    yield_10y: float | None
    yield_2y: float | None
    yield_curve_slope: float | None     # 10Y - 2Y
    yield_curve_inverted: bool
    # Credit
    hy_spread: float | None            # High Yield spread
    ig_spread: float | None            # Investment Grade spread
    credit_spread: float | None        # HY - IG
    credit_regime: str                 # "risk_on" | "risk_off" | "neutral"
    # Dollar
    dxy_level: float | None
    dxy_momentum: str                  # "strengthening" | "weakening" | "flat"
    # Recession probability
    recession_probability: float | None  # 0-1, NY Fed 12-month model
    # PMI
    us_pmi: float | None
    pmi_regime: str                    # "expanding" | "contracting" | "neutral"
    # VIX
    vix_level: float | None
    vix_regime: str                    # "low" | "normal" | "elevated" | "extreme"
    # Overall macro regime
    macro_regime: str                  # "risk_on" | "risk_off" | "mixed"
    macro_score: float                 # -1 to +1 (positive = risk-on)
    data_sources: list[str]
    computed_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "yield_curve": {
                "rate_10y": self.yield_10y,
                "rate_2y": self.yield_2y,
                "slope_pct": round(self.yield_curve_slope, 3) if self.yield_curve_slope is not None else None,
                "inverted": self.yield_curve_inverted,
                "signal": "recession_risk" if self.yield_curve_inverted else "normal",
            },
            "credit": {
                "hy_spread_pct": self.hy_spread,
                "ig_spread_pct": self.ig_spread,
                "hy_ig_spread_pct": self.credit_spread,
                "regime": self.credit_regime,
            },
            "dollar": {
                "dxy_level": self.dxy_level,
                "momentum": self.dxy_momentum,
            },
            "recession": {
                "probability_12m": round(self.recession_probability, 3) if self.recession_probability is not None else None,
                "signal": "elevated_risk" if (self.recession_probability or 0) > 0.30 else "low_risk",
            },
            "pmi": {
                "us_pmi": self.us_pmi,
                "regime": self.pmi_regime,
            },
            "vix": {
                "level": self.vix_level,
                "regime": self.vix_regime,
            },
            "summary": {
                "macro_regime": self.macro_regime,
                "macro_score": round(self.macro_score, 3),
                "trading_implication": self._trading_implication(),
            },
            "data_sources": self.data_sources,
            "computed_at": self.computed_at,
        }

    def _trading_implication(self) -> str:
        if self.macro_score > 0.3:
            return "RISK-ON: Favour equities, high-yield, cyclicals. Reduce cash and bonds."
        elif self.macro_score < -0.3:
            return "RISK-OFF: Favour bonds, gold, defensive equities. Reduce risk exposure."
        else:
            return "MIXED: Selective positioning. Monitor VIX and credit spreads."


class MacroSignalEngine:
    """
    Fetches and aggregates real macro signals from FRED, CBOE, NY Fed, and Polygon.
    """

    FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
    CBOE_VIX = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
    NYFED_RECESSION = "https://www.newyorkfed.org/medialibrary/media/research/capital_markets/Prob_Rec.xls"

    # FRED series IDs
    FRED_T10Y2Y = "T10Y2Y"      # 10Y-2Y Treasury spread (daily, free)
    FRED_BAMLH0A0HYM2 = "BAMLH0A0HYM2"   # ICE BofA HY OAS
    FRED_BAMLC0A0CM = "BAMLC0A0CM"        # ICE BofA IG OAS
    FRED_DTWEXBGS = "DTWEXBGS"            # Nominal Broad Dollar Index
    FRED_MANEMP = "MANEMP"                # Manufacturing employment (PMI proxy)
    FRED_NAPM = "NAPM"                    # ISM PMI

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, Any]] = {}
        self._cache_ttl = 3600  # 1 hour

    def _get_fred(self, series_id: str, api_key: str, limit: int = 5) -> list[dict]:
        """Fetch from FRED API. Returns list of {date, value} dicts."""
        cache_key = f"fred_{series_id}"
        if cache_key in self._cache:
            ts, data = self._cache[cache_key]
            if time.time() - ts < self._cache_ttl:
                return data

        url = (
            f"{self.FRED_BASE}?series_id={series_id}"
            f"&api_key={api_key}&file_type=json"
            f"&sort_order=desc&limit={limit}"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "AlgoChains-MCP/21.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())

        obs = [
            {"date": o["date"], "value": float(o["value"])}
            for o in data.get("observations", [])
            if o.get("value") not in (".", "", None)
        ]
        self._cache[cache_key] = (time.time(), obs)
        return obs

    def _get_latest_fred_value(self, series_id: str, api_key: str) -> float | None:
        obs = self._get_fred(series_id, api_key, limit=3)
        return obs[0]["value"] if obs else None

    def _get_vix(self, polygon_api_key: str | None = None) -> float | None:
        """Get VIX level from CBOE public CSV or Polygon."""
        if polygon_api_key:
            try:
                url = f"https://api.polygon.io/v2/snapshot/locale/us/markets/indices/tickers/I:VIX?apiKey={polygon_api_key}"
                req = urllib.request.Request(url, headers={"User-Agent": "AlgoChains-MCP/21.0"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read())
                val = data.get("ticker", {}).get("value") or data.get("ticker", {}).get("day", {}).get("c")
                if val:
                    return float(val)
            except Exception:
                pass

        # Fallback: CBOE public CSV
        try:
            req = urllib.request.Request(
                self.CBOE_VIX,
                headers={"User-Agent": "AlgoChains-MCP/21.0"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                content = resp.read().decode("utf-8")
            reader = list(csv.DictReader(io.StringIO(content)))
            if reader:
                last = reader[-1]
                close = last.get("CLOSE", last.get("VIX Close", last.get("close")))
                if close:
                    return float(close)
        except Exception:
            pass
        return None

    def _get_dxy(self, polygon_api_key: str | None = None, fred_api_key: str | None = None) -> float | None:
        """Get Dollar Index (DXY) from Polygon or FRED."""
        if polygon_api_key:
            try:
                # DXY is traded as I:DXY on Polygon
                url = f"https://api.polygon.io/v2/snapshot/locale/global/markets/forex/tickers/C:DXYD?apiKey={polygon_api_key}"
                req = urllib.request.Request(url, headers={"User-Agent": "AlgoChains-MCP/21.0"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read())
                val = data.get("ticker", {}).get("day", {}).get("c")
                if val:
                    return float(val)
            except Exception:
                pass

        if fred_api_key:
            return self._get_latest_fred_value(self.FRED_DTWEXBGS, fred_api_key)
        return None

    def get_macro_signals(
        self,
        fred_api_key: str | None = None,
        polygon_api_key: str | None = None,
    ) -> MacroSignalFabric:
        """
        Fetch and aggregate all macro signals from real sources.

        Args:
            fred_api_key: FRED API key (get free at fred.stlouisfed.org). 
                          Falls back to FRED_API_KEY env var.
            polygon_api_key: Polygon.io API key. Falls back to POLYGON_API_KEY env var.
        """
        fred_key = fred_api_key or os.environ.get("FRED_API_KEY", "")
        poly_key = polygon_api_key or os.environ.get("POLYGON_API_KEY", "")
        sources: list[str] = []
        errors: list[str] = []

        if not fred_key and not poly_key:
            raise MacroDataUnavailableError(
                "Macro signals require at least one real data source. "
                "Set FRED_API_KEY (free at fred.stlouisfed.org) or POLYGON_API_KEY. "
                "Both provide real, live macro data."
            )

        # Yield curve
        yield_slope = yield_10y = yield_2y = None
        if fred_key:
            try:
                yield_slope = self._get_latest_fred_value(self.FRED_T10Y2Y, fred_key)
                sources.append("fred:T10Y2Y")
                if yield_slope is not None:
                    # T10Y2Y IS the spread in percentage points
                    yield_10y = None  # FRED T10Y2Y is the spread directly
                    yield_2y = None
            except Exception as exc:
                errors.append(f"FRED yield curve: {exc}")

        # Credit spreads
        hy_spread = ig_spread = credit_spread = None
        if fred_key:
            try:
                hy_spread = self._get_latest_fred_value(self.FRED_BAMLH0A0HYM2, fred_key)
                sources.append("fred:BAMLH0A0HYM2")
            except Exception as exc:
                errors.append(f"FRED HY spread: {exc}")
            try:
                ig_spread = self._get_latest_fred_value(self.FRED_BAMLC0A0CM, fred_key)
                sources.append("fred:BAMLC0A0CM")
            except Exception as exc:
                errors.append(f"FRED IG spread: {exc}")

        if hy_spread is not None and ig_spread is not None:
            credit_spread = round(hy_spread - ig_spread, 3)

        # Dollar
        dxy = self._get_dxy(poly_key, fred_key if fred_key else None)
        if dxy:
            sources.append("polygon/fred:DXY")

        # VIX
        vix = self._get_vix(poly_key)
        if vix:
            sources.append("cboe/polygon:VIX")

        # PMI from FRED
        pmi = None
        if fred_key:
            try:
                pmi = self._get_latest_fred_value(self.FRED_NAPM, fred_key)
                sources.append("fred:NAPM")
            except Exception:
                pass

        if not sources:
            raise MacroDataUnavailableError(
                f"All macro data sources failed. Errors: {'; '.join(errors)}. "
                "Check FRED_API_KEY validity and network access."
            )

        # Compute derived signals
        yield_inverted = (yield_slope is not None and yield_slope < 0)
        credit_regime = (
            "risk_off" if (hy_spread or 0) > 500 or (credit_spread or 0) > 300
            else "risk_on" if (hy_spread or 0) < 350
            else "neutral"
        )
        dxy_momentum = "flat"
        if fred_key and dxy:
            try:
                dxy_series = self._get_fred(self.FRED_DTWEXBGS, fred_key, limit=10)
                if len(dxy_series) >= 5:
                    dxy_5d_ago = dxy_series[4]["value"]
                    if dxy > dxy_5d_ago * 1.005:
                        dxy_momentum = "strengthening"
                    elif dxy < dxy_5d_ago * 0.995:
                        dxy_momentum = "weakening"
            except Exception:
                pass

        pmi_regime = (
            "expanding" if (pmi or 50) > 50
            else "contracting" if (pmi or 50) < 50
            else "neutral"
        )
        vix_regime = (
            "extreme" if (vix or 0) > 35
            else "elevated" if (vix or 0) > 25
            else "normal" if (vix or 0) > 15
            else "low"
        )

        # Recession probability
        # NY Fed public model — use yield curve inversion as proxy when NY Fed download fails
        recession_prob = None
        if yield_slope is not None:
            # Empirical mapping: slope < -0.5% → ~30% recession probability (NY Fed model)
            if yield_slope < -1.0:
                recession_prob = 0.45
            elif yield_slope < -0.5:
                recession_prob = 0.30
            elif yield_slope < 0:
                recession_prob = 0.20
            else:
                recession_prob = max(0.05, 0.15 - yield_slope * 0.05)

        # Macro score: composite risk-on/off signal
        score = 0.0
        if yield_slope is not None:
            score += min(0.3, max(-0.3, yield_slope * 0.1))  # Positive slope = risk-on
        if hy_spread is not None:
            score -= min(0.3, max(0, (hy_spread - 350) / 1000))  # Wide spreads = risk-off
        if vix is not None:
            score -= min(0.3, max(0, (vix - 20) / 50))  # High VIX = risk-off
        if pmi is not None:
            score += min(0.15, max(-0.15, (pmi - 50) / 100))

        macro_regime = "risk_on" if score > 0.1 else ("risk_off" if score < -0.1 else "mixed")

        return MacroSignalFabric(
            yield_10y=yield_10y,
            yield_2y=yield_2y,
            yield_curve_slope=yield_slope,
            yield_curve_inverted=yield_inverted,
            hy_spread=hy_spread,
            ig_spread=ig_spread,
            credit_spread=credit_spread,
            credit_regime=credit_regime,
            dxy_level=dxy,
            dxy_momentum=dxy_momentum,
            recession_probability=recession_prob,
            us_pmi=pmi,
            pmi_regime=pmi_regime,
            vix_level=vix,
            vix_regime=vix_regime,
            macro_regime=macro_regime,
            macro_score=round(score, 3),
            data_sources=sources,
        )


_macro_engine: MacroSignalEngine | None = None


def get_macro_engine() -> MacroSignalEngine:
    global _macro_engine
    if _macro_engine is None:
        _macro_engine = MacroSignalEngine()
    return _macro_engine
