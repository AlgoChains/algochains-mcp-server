"""
US Economic Indicators — FRED + EIA Macro Data Layer

Adapted from danielmiessler/Personal_AI_Infrastructure USMetrics pack.
Provides 20+ critical US economic indicators for trading regime detection.

Key use cases:
  - CL bot: EIA weekly crude oil inventories (biggest crude mover)
  - MNQ/NQ: CPI, PCE, Fed Funds Rate regime context
  - All bots: VIX, 10Y-2Y spread for risk-off detection

Data Sources:
  - FRED (Federal Reserve Economic Data): https://fred.stlouisfed.org
  - EIA (Energy Information Administration): https://www.eia.gov/opendata

API Keys (both free — register at above URLs):
  FRED_API_KEY — https://fred.stlouisfed.org/docs/api/api_key.html
  EIA_API_KEY  — https://www.eia.gov/opendata/register.php

Fail-closed: returns error dict if API key missing or request fails.
Never returns synthetic/estimated values.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("algochains_mcp.us_economics")

FRED_BASE = "https://api.stlouisfed.org/fred"
EIA_BASE = "https://api.eia.gov/v2"

# State directory for 24h TTL cache
_CACHE_FILE = Path(os.getenv("ALGOCHAINS_STATE_DIR", "state")) / "us_metrics_cache.json"

# ── FRED Series definitions ──────────────────────────────────────────────────

FRED_SERIES: dict[str, dict[str, str]] = {
    # Rates & Monetary Policy
    "FEDFUNDS": {
        "name": "Federal Funds Effective Rate",
        "category": "monetary_policy",
        "trading_relevance": "Primary driver of risk-on/risk-off. Rate hikes pressure growth stocks (NQ/MNQ).",
    },
    "T10Y2Y": {
        "name": "10-Year minus 2-Year Treasury Spread",
        "category": "rates",
        "trading_relevance": "Recession signal. Inverted = risk-off. Critical regime indicator for all bots.",
    },
    "GS10": {
        "name": "10-Year Treasury Constant Maturity Rate",
        "category": "rates",
        "trading_relevance": "Rising 10Y yields pressure growth stocks. High correlation with NQ inverse.",
    },
    "GS2": {
        "name": "2-Year Treasury Constant Maturity Rate",
        "category": "rates",
        "trading_relevance": "More sensitive to Fed expectations than 10Y. Leading indicator for Fed pivot.",
    },
    "M2SL": {
        "name": "M2 Money Supply",
        "category": "monetary_policy",
        "trading_relevance": "M2 contraction correlates with equity drawdowns. Liquidity regime signal.",
    },
    # Inflation
    "CPIAUCSL": {
        "name": "Consumer Price Index for All Urban Consumers",
        "category": "inflation",
        "trading_relevance": "Monthly CPI releases are major market-moving events. Above 3% = hawkish Fed.",
    },
    "PCEPI": {
        "name": "Personal Consumption Expenditures Price Index",
        "category": "inflation",
        "trading_relevance": "Fed's preferred inflation measure. PCE > 2% drives rate policy.",
    },
    "CPILFESL": {
        "name": "Core CPI (ex food and energy)",
        "category": "inflation",
        "trading_relevance": "Strips out volatile components. Better signal for underlying inflation trend.",
    },
    # Labor
    "UNRATE": {
        "name": "Unemployment Rate",
        "category": "labor",
        "trading_relevance": "Low unemployment = hawkish Fed. Rising unemployment = Fed pivot signal.",
    },
    "IC4WSA": {
        "name": "4-Week Moving Average of Initial Claims",
        "category": "labor",
        "trading_relevance": "Leading indicator. Rising claims = labor market softening = Fed pivot signal.",
    },
    "PAYEMS": {
        "name": "Total Nonfarm Payrolls (Monthly Change)",
        "category": "labor",
        "trading_relevance": "NFP release is one of the most market-moving monthly events.",
    },
    # Growth
    "GDPC1": {
        "name": "Real Gross Domestic Product",
        "category": "growth",
        "trading_relevance": "Quarterly. Negative two consecutive quarters = recession. Risk-off signal.",
    },
    "INDPRO": {
        "name": "Industrial Production Index",
        "category": "growth",
        "trading_relevance": "Monthly economic activity. Leading indicator for corporate earnings.",
    },
    # Sentiment & Volatility
    "VIXCLS": {
        "name": "CBOE Volatility Index (VIX) Close",
        "category": "volatility",
        "trading_relevance": "CRITICAL: > 35 = ALL bot entries blocked. 20-35 = reduce size. < 20 = normal.",
    },
    "UMCSENT": {
        "name": "University of Michigan Consumer Sentiment",
        "category": "sentiment",
        "trading_relevance": "Leading indicator for consumer spending. Falling sentiment = risk-off.",
    },
    # Housing (MES signal)
    "HOUST": {
        "name": "Housing Starts",
        "category": "housing",
        "trading_relevance": "Leading economic indicator. Falling housing starts = economic slowdown.",
    },
}

# ── EIA Series definitions ───────────────────────────────────────────────────

EIA_CRUDE_SERIES: dict[str, dict[str, str]] = {
    "PET.WCRSTUS1.W": {
        "name": "US Commercial Crude Oil Stocks (weekly)",
        "category": "energy",
        "trading_relevance": "CRITICAL for CL bot. Build vs. expectation = down move; draw = up move.",
    },
    "PET.WCSSTUS1.W": {
        "name": "Cushing Oklahoma Crude Oil Stocks (weekly)",
        "category": "energy",
        "trading_relevance": "Cushing is the WTI delivery point. Low Cushing stocks = CL backwardation.",
    },
    "PET.WCRFPUS2.W": {
        "name": "US Crude Oil Field Production (weekly)",
        "category": "energy",
        "trading_relevance": "Rising US production caps oil upside. Key supply-side signal for CL.",
    },
}


def _fred_api_key() -> str | None:
    return os.getenv("FRED_API_KEY", "").strip() or None


def _eia_api_key() -> str | None:
    return os.getenv("EIA_API_KEY", "").strip() or None


def _load_cache() -> dict[str, Any]:
    try:
        if _CACHE_FILE.exists():
            return json.loads(_CACHE_FILE.read_text())
    except Exception:
        pass
    return {}


def _save_cache(data: dict[str, Any]) -> None:
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps(data, indent=2))
    except Exception as exc:
        logger.debug("Cache save failed: %s", exc)


def _fetch_fred_series(series_id: str, api_key: str) -> dict[str, Any]:
    """Fetch the latest observation for a FRED series."""
    params = urllib.parse.urlencode({
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": "3",
    })
    url = f"{FRED_BASE}/series/observations?{params}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AlgoChains-MCP/22.9"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        obs_list = data.get("observations", [])
        if not obs_list:
            return {"error": f"No data returned for {series_id}"}
        latest = next((o for o in obs_list if o.get("value") != "."), None)
        if not latest:
            return {"error": f"No valid observation for {series_id}"}
        return {
            "series_id": series_id,
            "value": latest["value"],
            "date": latest["date"],
            "prior": obs_list[1].get("value") if len(obs_list) > 1 else None,
            "prior_date": obs_list[1].get("date") if len(obs_list) > 1 else None,
        }
    except urllib.error.HTTPError as exc:
        return {"error": f"FRED HTTP {exc.code} for {series_id}"}
    except Exception as exc:
        return {"error": f"FRED request failed for {series_id}: {exc}"}


def get_us_economic_indicators(
    categories: list[str] | None = None,
    use_cache: bool = True,
    cache_ttl_hours: int = 6,
) -> dict[str, Any]:
    """
    Fetch US economic indicators from FRED.
    Adapted from PAI USMetrics pack.

    Args:
        categories: Filter by category list. None = all categories.
                    Options: monetary_policy, rates, inflation, labor,
                             growth, volatility, sentiment, housing
        use_cache: Use 6-hour local cache (True by default to avoid rate limits)
        cache_ttl_hours: Cache TTL in hours (default 6)

    Returns dict with indicator values, dates, and trading relevance.
    Requires FRED_API_KEY environment variable.
    """
    api_key = _fred_api_key()
    if not api_key:
        return {
            "error": "FRED_API_KEY not set",
            "hint": "Get a free key at https://fred.stlouisfed.org/docs/api/api_key.html",
            "configured": False,
        }

    cache = _load_cache() if use_cache else {}
    cache_ts = cache.get("_fred_fetched_at", 0)
    cache_age_hours = (time.time() - cache_ts) / 3600

    if use_cache and cache_age_hours < cache_ttl_hours and cache.get("fred_indicators"):
        indicators = cache["fred_indicators"]
        source = "cache"
    else:
        indicators: dict[str, Any] = {}
        for sid, meta in FRED_SERIES.items():
            obs = _fetch_fred_series(sid, api_key)
            indicators[sid] = {**meta, **obs}
        cache["fred_indicators"] = indicators
        cache["_fred_fetched_at"] = time.time()
        _save_cache(cache)
        source = "live"

    # Filter by categories if requested
    if categories:
        categories_lower = {c.lower() for c in categories}
        indicators = {k: v for k, v in indicators.items()
                      if v.get("category", "") in categories_lower}

    # Add human-readable summary
    vix_data = indicators.get("VIXCLS", {})
    vix_val = vix_data.get("value")
    regime_signal = "unknown"
    if vix_val and vix_val != ".":
        try:
            vix_float = float(vix_val)
            if vix_float > 35:
                regime_signal = "CRISIS — all bot entries blocked"
            elif vix_float > 20:
                regime_signal = "ELEVATED — reduce position size"
            else:
                regime_signal = "NORMAL — standard operations"
        except ValueError:
            pass

    return {
        "indicators": indicators,
        "source": source,
        "count": len(indicators),
        "vix_regime_signal": regime_signal,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "categories_filter": categories,
        "configured": True,
    }


def get_crude_oil_inventories(use_cache: bool = True) -> dict[str, Any]:
    """
    Fetch EIA weekly crude oil inventory data (critical for CL bot).
    Adapted from PAI USMetrics pack EIA integration.

    The EIA Weekly Petroleum Status Report is released every Wednesday
    and is the single biggest mover of crude oil prices.

    Requires EIA_API_KEY environment variable.
    """
    api_key = _eia_api_key()
    if not api_key:
        return {
            "error": "EIA_API_KEY not set",
            "hint": "Get a free key at https://www.eia.gov/opendata/register.php",
            "configured": False,
            "trading_note": "EIA crude inventory report (Wednesdays) is the primary CL bot signal.",
        }

    cache = _load_cache() if use_cache else {}
    cache_ts = cache.get("_eia_fetched_at", 0)
    cache_age_hours = (time.time() - cache_ts) / 3600

    if use_cache and cache_age_hours < 24 and cache.get("eia_crude"):
        return {**cache["eia_crude"], "source": "cache"}

    results: dict[str, Any] = {}
    for series_id, meta in EIA_CRUDE_SERIES.items():
        params = urllib.parse.urlencode({
            "api_key": api_key,
            "frequency": "weekly",
            "data[0]": "value",
            "sort[0][column]": "period",
            "sort[0][direction]": "desc",
            "length": "4",
        })
        # EIA v2 API format: route is series_id with dots as path separators
        route = series_id.replace(".", "/")
        url = f"{EIA_BASE}/seriesid/{route}?{params}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "AlgoChains-MCP/22.9"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read())
            response_data = data.get("response", {}).get("data", [])
            if not response_data:
                results[series_id] = {**meta, "error": "No data returned"}
                continue
            latest = response_data[0]
            prior = response_data[1] if len(response_data) > 1 else {}
            latest_val = float(latest.get("value", 0))
            prior_val = float(prior.get("value", 0)) if prior else None
            change = round(latest_val - prior_val, 1) if prior_val is not None else None
            signal = "NEUTRAL"
            if change is not None:
                if change > 2000:   # > 2M barrel build
                    signal = "BEARISH — inventory build above threshold"
                elif change < -2000:  # > 2M barrel draw
                    signal = "BULLISH — inventory draw above threshold"
            results[series_id] = {
                **meta,
                "latest_value_kbbl": latest_val,
                "latest_date": latest.get("period"),
                "prior_value_kbbl": prior_val,
                "change_kbbl": change,
                "cl_signal": signal,
            }
        except Exception as exc:
            results[series_id] = {**meta, "error": str(exc)}

    out = {
        "crude_inventories": results,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": "live",
        "configured": True,
        "trading_note": (
            "EIA reports release every Wednesday ~10:30 AM ET. "
            "Build vs analyst estimate → bearish for CL; draw → bullish. "
            "Use change_kbbl and cl_signal for trade direction context."
        ),
    }
    cache["eia_crude"] = out
    cache["_eia_fetched_at"] = time.time()
    _save_cache(cache)
    return out


def get_fed_policy_signals(use_cache: bool = True) -> dict[str, Any]:
    """
    Get Fed policy-relevant indicators for MNQ/NQ regime detection.
    Returns Fed Funds Rate, CPI, PCE, 10Y-2Y spread, and VIX in one call.

    These 5 indicators together define the monetary policy regime.
    Requires FRED_API_KEY.
    """
    api_key = _fred_api_key()
    if not api_key:
        return {
            "error": "FRED_API_KEY not set",
            "hint": "Get a free key at https://fred.stlouisfed.org/docs/api/api_key.html",
            "configured": False,
        }

    fed_series = ["FEDFUNDS", "CPIAUCSL", "PCEPI", "T10Y2Y", "VIXCLS", "GS10", "GS2"]

    cache = _load_cache() if use_cache else {}
    cache_ts = cache.get("_fed_fetched_at", 0)
    cache_age_hours = (time.time() - cache_ts) / 3600

    if use_cache and cache_age_hours < 6 and cache.get("fed_signals"):
        return {**cache["fed_signals"], "source": "cache"}

    indicators: dict[str, Any] = {}
    for sid in fed_series:
        obs = _fetch_fred_series(sid, api_key)
        meta = FRED_SERIES.get(sid, {})
        indicators[sid] = {**meta, **obs}

    # Derive regime interpretation
    regime = _interpret_fed_regime(indicators)

    out = {
        "indicators": indicators,
        "regime_interpretation": regime,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": "live",
        "configured": True,
    }
    cache["fed_signals"] = out
    cache["_fed_fetched_at"] = time.time()
    _save_cache(cache)
    return out


def _interpret_fed_regime(indicators: dict[str, Any]) -> dict[str, str]:
    """Derive a plain-English Fed policy regime from the indicators."""
    regime: dict[str, str] = {}

    def _float(sid: str) -> float | None:
        try:
            return float(indicators.get(sid, {}).get("value", ""))
        except (TypeError, ValueError):
            return None

    vix = _float("VIXCLS")
    t10y2y = _float("T10Y2Y")
    fedfunds = _float("FEDFUNDS")
    cpi = _float("CPIAUCSL")

    if vix is not None:
        if vix > 35:
            regime["vix"] = f"CRISIS ({vix:.1f}) — all bot entries blocked"
        elif vix > 20:
            regime["vix"] = f"ELEVATED ({vix:.1f}) — reduce position size"
        else:
            regime["vix"] = f"NORMAL ({vix:.1f}) — standard operations"

    if t10y2y is not None:
        if t10y2y < 0:
            regime["yield_curve"] = f"INVERTED ({t10y2y:.2f}%) — recession signal, risk-off"
        elif t10y2y < 0.5:
            regime["yield_curve"] = f"FLAT ({t10y2y:.2f}%) — caution, watch for inversion"
        else:
            regime["yield_curve"] = f"NORMAL ({t10y2y:.2f}%) — standard credit conditions"

    if fedfunds is not None:
        if fedfunds > 5.0:
            regime["fed_policy"] = f"RESTRICTIVE ({fedfunds:.2f}%) — tightening cycle, headwind for growth"
        elif fedfunds > 3.0:
            regime["fed_policy"] = f"NEUTRAL-TIGHT ({fedfunds:.2f}%) — normalizing"
        else:
            regime["fed_policy"] = f"ACCOMMODATIVE ({fedfunds:.2f}%) — supportive for equities"

    return regime
