"""Gamma Exposure (GEX) engine — dealer positioning, pin risk, vol trigger levels.

Computes net gamma exposure from options chain data to identify
key price levels where dealer hedging amplifies or dampens moves.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger("algochains_mcp.alpha_engines.gex")


class GEXEngine:
    """Gamma exposure analysis from options chain data via Polygon."""

    def __init__(self, polygon_key: str = "") -> None:
        self._polygon_key = polygon_key
        self._http = httpx.AsyncClient(timeout=30)

    async def compute_gex(self, symbol: str, expiry: str = "") -> dict[str, Any]:
        """Compute net gamma exposure across strikes for a symbol."""
        chain = await self._fetch_options_chain(symbol, expiry)
        if not chain:
            return {"status": "error", "error": "No options chain data available"}

        spot = await self._fetch_spot(symbol)
        if not spot:
            return {"status": "error", "error": "Could not fetch spot price"}

        gex_by_strike: dict[float, float] = {}
        total_call_gamma = 0.0
        total_put_gamma = 0.0
        total_call_oi = 0
        total_put_oi = 0

        for contract in chain:
            strike = contract.get("strike_price", 0)
            gamma = contract.get("greeks", {}).get("gamma", 0) or 0
            oi = contract.get("open_interest", 0) or 0
            cp = contract.get("contract_type", "").lower()

            if strike <= 0 or gamma <= 0:
                continue

            gex_value = gamma * oi * 100 * spot

            if cp == "call":
                gex_by_strike[strike] = gex_by_strike.get(strike, 0) + gex_value
                total_call_gamma += gex_value
                total_call_oi += oi
            elif cp == "put":
                gex_by_strike[strike] = gex_by_strike.get(strike, 0) - gex_value
                total_put_gamma += gex_value
                total_put_oi += oi

        net_gex = total_call_gamma - total_put_gamma

        sorted_strikes = sorted(gex_by_strike.items(), key=lambda x: abs(x[1]), reverse=True)
        top_levels = [
            {"strike": s, "gex": round(g, 0), "type": "support" if g > 0 else "resistance"}
            for s, g in sorted_strikes[:10]
        ]

        zero_gamma = self._find_zero_gamma(gex_by_strike, spot)

        flip_point = zero_gamma if zero_gamma else spot
        regime = "positive_gamma" if net_gex > 0 else "negative_gamma"
        volatility_bias = "compressed" if regime == "positive_gamma" else "expanded"

        put_call_oi_ratio = round(total_put_oi / total_call_oi, 3) if total_call_oi else 0

        return {
            "status": "ok",
            "symbol": symbol,
            "spot_price": spot,
            "net_gex": round(net_gex, 0),
            "call_gamma_notional": round(total_call_gamma, 0),
            "put_gamma_notional": round(total_put_gamma, 0),
            "gamma_regime": regime,
            "volatility_bias": volatility_bias,
            "zero_gamma_level": round(zero_gamma, 2) if zero_gamma else None,
            "gamma_flip_point": round(flip_point, 2),
            "put_call_oi_ratio": put_call_oi_ratio,
            "key_levels": top_levels,
            "pin_risk_strikes": self._find_pin_strikes(gex_by_strike, spot),
            "total_contracts_analyzed": len(chain),
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    async def gex_scanner(
        self, symbols: list[str]
    ) -> dict[str, Any]:
        """Scan multiple symbols for gamma exposure signals."""
        results = []
        for sym in symbols[:15]:
            try:
                data = await self.compute_gex(sym)
                if data.get("status") == "ok":
                    results.append({
                        "symbol": sym,
                        "net_gex": data["net_gex"],
                        "regime": data["gamma_regime"],
                        "vol_bias": data["volatility_bias"],
                        "zero_gamma": data.get("zero_gamma_level"),
                        "pc_ratio": data["put_call_oi_ratio"],
                    })
            except Exception as e:
                logger.warning("GEX scan failed for %s: %s", sym, e)

        results.sort(key=lambda x: abs(x["net_gex"]), reverse=True)
        return {
            "status": "ok",
            "scanned": len(symbols),
            "results": results,
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    def _find_zero_gamma(self, gex_map: dict[float, float], spot: float) -> float | None:
        """Find the strike where net GEX crosses zero (gamma flip point)."""
        strikes = sorted(gex_map.keys())
        for i in range(len(strikes) - 1):
            g1 = gex_map[strikes[i]]
            g2 = gex_map[strikes[i + 1]]
            if g1 * g2 < 0:
                ratio = abs(g1) / (abs(g1) + abs(g2))
                return strikes[i] + ratio * (strikes[i + 1] - strikes[i])
        return None

    def _find_pin_strikes(self, gex_map: dict[float, float], spot: float) -> list[float]:
        """Find strikes with highest positive GEX near spot (pin magnets)."""
        near = {
            s: g for s, g in gex_map.items()
            if abs(s - spot) / spot < 0.05 and g > 0
        }
        return sorted(near, key=lambda s: near[s], reverse=True)[:3]

    async def _fetch_options_chain(self, symbol: str, expiry: str = "") -> list[dict]:
        if not self._polygon_key:
            return []
        url = f"https://api.polygon.io/v3/snapshot/options/{symbol}"
        params: dict[str, Any] = {
            "limit": 250,
            "apiKey": self._polygon_key,
        }
        if expiry:
            params["expiration_date"] = expiry
        try:
            resp = await self._http.get(url, params=params)
            resp.raise_for_status()
            results = resp.json().get("results", [])
            return [r.get("details", {}) | r.get("greeks", {}) | {"open_interest": r.get("open_interest", 0), "greeks": r.get("greeks", {})} for r in results]
        except Exception as e:
            logger.warning("Options chain fetch failed for %s: %s", symbol, e)
            return []

    async def _fetch_spot(self, symbol: str) -> float:
        if not self._polygon_key:
            return 0.0
        url = f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers/{symbol}"
        try:
            resp = await self._http.get(url, params={"apiKey": self._polygon_key})
            resp.raise_for_status()
            ticker = resp.json().get("ticker", {})
            return ticker.get("lastTrade", {}).get("p", 0) or ticker.get("day", {}).get("c", 0)
        except Exception as e:
            logger.warning("Spot price fetch failed for %s: %s", symbol, e)
            return 0.0
