"""Volatility surface analysis — skew, term structure, vol-of-vol, smile fitting.

Analyzes implied volatility across strikes and expirations to detect
mispricing, skew shifts, and term structure anomalies.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx

logger = logging.getLogger("algochains_mcp.alpha_engines.vol_surface")


class VolSurfaceEngine:
    """Volatility surface, skew, and term structure analysis."""

    def __init__(self, polygon_key: str = "") -> None:
        self._polygon_key = polygon_key
        self._http = httpx.AsyncClient(timeout=30)

    async def analyze_skew(self, symbol: str, expiry: str = "") -> dict[str, Any]:
        """Analyze volatility skew — OTM put vs OTM call IV spread."""
        chain = await self._fetch_chain_with_iv(symbol, expiry)
        if not chain:
            return {"status": "error", "error": "No options chain data"}

        spot = await self._fetch_spot(symbol)
        if not spot:
            return {"status": "error", "error": "Could not fetch spot price"}

        calls = [c for c in chain if c.get("cp") == "call" and c.get("iv", 0) > 0]
        puts = [c for c in chain if c.get("cp") == "put" and c.get("iv", 0) > 0]

        atm_strike = min(
            set(c["strike"] for c in chain if c.get("iv", 0) > 0),
            key=lambda s: abs(s - spot),
            default=spot,
        )

        atm_iv = self._get_iv_at_strike(chain, atm_strike)

        otm_put_25d = self._get_delta_strike(puts, spot, -0.25)
        otm_call_25d = self._get_delta_strike(calls, spot, 0.25)

        put_25d_iv = self._get_iv_at_strike(chain, otm_put_25d) if otm_put_25d else 0
        call_25d_iv = self._get_iv_at_strike(chain, otm_call_25d) if otm_call_25d else 0

        skew_25d = round(put_25d_iv - call_25d_iv, 4) if put_25d_iv and call_25d_iv else 0
        risk_reversal = round(call_25d_iv - put_25d_iv, 4) if put_25d_iv and call_25d_iv else 0
        butterfly = round((put_25d_iv + call_25d_iv) / 2 - atm_iv, 4) if atm_iv else 0

        signal = "neutral"
        if skew_25d > 0.05:
            signal = "fear_elevated"
        elif skew_25d > 0.10:
            signal = "crash_protection_bid"
        elif skew_25d < -0.02:
            signal = "call_skew_elevated"

        return {
            "status": "ok",
            "symbol": symbol,
            "spot": spot,
            "atm_strike": atm_strike,
            "atm_iv": round(atm_iv, 4),
            "put_25d_strike": otm_put_25d,
            "put_25d_iv": round(put_25d_iv, 4),
            "call_25d_strike": otm_call_25d,
            "call_25d_iv": round(call_25d_iv, 4),
            "skew_25d": skew_25d,
            "risk_reversal_25d": risk_reversal,
            "butterfly_25d": butterfly,
            "signal": signal,
            "contracts_analyzed": len(chain),
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    async def term_structure(self, symbol: str) -> dict[str, Any]:
        """Analyze IV term structure across expirations."""
        expirations = await self._fetch_expirations(symbol)
        if not expirations:
            return {"status": "error", "error": "No expirations available"}

        spot = await self._fetch_spot(symbol)
        term_points = []

        for exp in expirations[:8]:
            chain = await self._fetch_chain_with_iv(symbol, exp)
            if not chain:
                continue
            atm_strike = min(
                set(c["strike"] for c in chain if c.get("iv", 0) > 0),
                key=lambda s: abs(s - spot),
                default=spot,
            )
            atm_iv = self._get_iv_at_strike(chain, atm_strike)
            if atm_iv > 0:
                dte = (datetime.strptime(exp, "%Y-%m-%d") - datetime.now()).days
                term_points.append({
                    "expiry": exp,
                    "dte": max(dte, 0),
                    "atm_iv": round(atm_iv, 4),
                    "atm_strike": atm_strike,
                })

        if len(term_points) < 2:
            return {"status": "error", "error": "Insufficient expirations for term structure"}

        term_points.sort(key=lambda x: x["dte"])
        front_iv = term_points[0]["atm_iv"]
        back_iv = term_points[-1]["atm_iv"]

        structure = "contango" if back_iv > front_iv else "backwardation"
        slope = round((back_iv - front_iv) / max(term_points[-1]["dte"] - term_points[0]["dte"], 1) * 30, 4)

        signal = "neutral"
        if structure == "backwardation" and front_iv > 0.30:
            signal = "event_risk_elevated"
        elif structure == "contango" and slope > 0.01:
            signal = "sell_front_vol"

        return {
            "status": "ok",
            "symbol": symbol,
            "spot": spot,
            "structure": structure,
            "slope_per_30d": slope,
            "front_iv": front_iv,
            "back_iv": back_iv,
            "term_points": term_points,
            "signal": signal,
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    def _get_iv_at_strike(self, chain: list[dict], strike: float) -> float:
        matches = [c for c in chain if c["strike"] == strike and c.get("iv", 0) > 0]
        if not matches:
            return 0.0
        return sum(c["iv"] for c in matches) / len(matches)

    def _get_delta_strike(
        self, options: list[dict], spot: float, target_delta: float
    ) -> float | None:
        """Find the strike closest to a target delta."""
        if not options:
            return None
        moneyness_target = 1.0 + target_delta * 0.4
        target_strike = spot * moneyness_target
        closest = min(options, key=lambda c: abs(c["strike"] - target_strike))
        return closest["strike"]

    async def _fetch_chain_with_iv(self, symbol: str, expiry: str = "") -> list[dict]:
        if not self._polygon_key:
            return []
        url = f"https://api.polygon.io/v3/snapshot/options/{symbol}"
        params: dict[str, Any] = {"limit": 250, "apiKey": self._polygon_key}
        if expiry:
            params["expiration_date"] = expiry
        try:
            resp = await self._http.get(url, params=params)
            resp.raise_for_status()
            results = resp.json().get("results", [])
            chain = []
            for r in results:
                details = r.get("details", {})
                greeks = r.get("greeks", {})
                chain.append({
                    "strike": details.get("strike_price", 0),
                    "cp": details.get("contract_type", "").lower(),
                    "expiry": details.get("expiration_date", ""),
                    "iv": r.get("implied_volatility", 0) or greeks.get("vega", 0),
                    "delta": greeks.get("delta", 0),
                    "gamma": greeks.get("gamma", 0),
                    "oi": r.get("open_interest", 0),
                })
            return chain
        except Exception as e:
            logger.warning("Options chain IV fetch failed for %s: %s", symbol, e)
            return []

    async def _fetch_expirations(self, symbol: str) -> list[str]:
        if not self._polygon_key:
            return []
        url = f"https://api.polygon.io/v3/reference/options/contracts"
        try:
            resp = await self._http.get(url, params={
                "underlying_ticker": symbol, "limit": 1000,
                "apiKey": self._polygon_key,
            })
            resp.raise_for_status()
            results = resp.json().get("results", [])
            expirations = sorted(set(r.get("expiration_date", "") for r in results if r.get("expiration_date")))
            today = datetime.now().strftime("%Y-%m-%d")
            return [e for e in expirations if e >= today][:8]
        except Exception as e:
            logger.warning("Expirations fetch failed for %s: %s", symbol, e)
            return []

    async def _fetch_spot(self, symbol: str) -> float:
        if not self._polygon_key:
            return 0.0
        try:
            resp = await self._http.get(
                f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers/{symbol}",
                params={"apiKey": self._polygon_key},
            )
            resp.raise_for_status()
            t = resp.json().get("ticker", {})
            return t.get("lastTrade", {}).get("p", 0) or t.get("day", {}).get("c", 0)
        except Exception as e:
            logger.warning("Spot price fetch failed for %s: %s", symbol, e)
            return 0.0
