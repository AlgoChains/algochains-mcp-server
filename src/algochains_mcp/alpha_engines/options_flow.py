"""Unusual options activity detection — smart money flow, sweep detection, premium analysis.

Analyzes options trade data to detect unusual activity patterns that
indicate institutional positioning and informed trading.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx

logger = logging.getLogger("algochains_mcp.alpha_engines.options_flow")


class OptionsFlowEngine:
    """Detect unusual options activity and smart money flow."""

    def __init__(self, polygon_key: str = "") -> None:
        self._polygon_key = polygon_key
        self._http = httpx.AsyncClient(timeout=30)

    async def unusual_activity(
        self, symbol: str, min_premium: float = 50000, min_oi_ratio: float = 2.0
    ) -> dict[str, Any]:
        """Detect unusual options activity for a symbol."""
        chain = await self._fetch_options_snapshot(symbol)
        if not chain:
            return {"status": "error", "error": "No options data available"}

        unusual = []
        total_call_volume = 0
        total_put_volume = 0
        total_call_oi = 0
        total_put_oi = 0

        for contract in chain:
            cp = contract.get("contract_type", "").lower()
            volume = contract.get("day_volume", 0) or 0
            oi = contract.get("open_interest", 0) or 0
            last = contract.get("last_price", 0) or 0
            strike = contract.get("strike_price", 0)
            expiry = contract.get("expiration_date", "")

            if cp == "call":
                total_call_volume += volume
                total_call_oi += oi
            else:
                total_put_volume += volume
                total_put_oi += oi

            if volume <= 0 or oi <= 0:
                continue

            premium = volume * last * 100
            vol_oi_ratio = volume / oi if oi > 0 else 0

            is_unusual = premium >= min_premium and vol_oi_ratio >= min_oi_ratio

            if is_unusual:
                unusual.append({
                    "strike": strike,
                    "expiry": expiry,
                    "type": cp,
                    "volume": volume,
                    "open_interest": oi,
                    "vol_oi_ratio": round(vol_oi_ratio, 2),
                    "last_price": last,
                    "premium": round(premium, 0),
                    "sentiment": "bullish" if cp == "call" else "bearish",
                })

        unusual.sort(key=lambda x: x["premium"], reverse=True)

        pc_volume_ratio = round(total_put_volume / total_call_volume, 3) if total_call_volume else 0
        pc_oi_ratio = round(total_put_oi / total_call_oi, 3) if total_call_oi else 0

        bullish_flow = sum(u["premium"] for u in unusual if u["sentiment"] == "bullish")
        bearish_flow = sum(u["premium"] for u in unusual if u["sentiment"] == "bearish")
        net_sentiment = "bullish" if bullish_flow > bearish_flow * 1.3 else "bearish" if bearish_flow > bullish_flow * 1.3 else "neutral"

        return {
            "status": "ok",
            "symbol": symbol,
            "unusual_contracts": len(unusual),
            "top_unusual": unusual[:10],
            "total_call_volume": total_call_volume,
            "total_put_volume": total_put_volume,
            "put_call_volume_ratio": pc_volume_ratio,
            "put_call_oi_ratio": pc_oi_ratio,
            "bullish_premium": round(bullish_flow, 0),
            "bearish_premium": round(bearish_flow, 0),
            "net_sentiment": net_sentiment,
            "contracts_scanned": len(chain),
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    async def options_flow_scanner(
        self, symbols: list[str], min_premium: float = 100000
    ) -> dict[str, Any]:
        """Scan multiple symbols for unusual options flow."""
        results = []
        for sym in symbols[:20]:
            try:
                data = await self.unusual_activity(sym, min_premium=min_premium)
                if data.get("status") == "ok" and data.get("unusual_contracts", 0) > 0:
                    results.append({
                        "symbol": sym,
                        "unusual_count": data["unusual_contracts"],
                        "bullish_premium": data["bullish_premium"],
                        "bearish_premium": data["bearish_premium"],
                        "net_sentiment": data["net_sentiment"],
                        "pc_ratio": data["put_call_volume_ratio"],
                    })
            except Exception as e:
                logger.warning("Options flow scan failed for %s: %s", sym, e)

        results.sort(key=lambda x: x["bullish_premium"] + x["bearish_premium"], reverse=True)
        return {
            "status": "ok",
            "scanned": len(symbols),
            "with_unusual_activity": len(results),
            "min_premium": min_premium,
            "results": results,
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    async def _fetch_options_snapshot(self, symbol: str) -> list[dict]:
        if not self._polygon_key:
            return []
        url = f"https://api.polygon.io/v3/snapshot/options/{symbol}"
        try:
            resp = await self._http.get(url, params={"limit": 250, "apiKey": self._polygon_key})
            resp.raise_for_status()
            results = resp.json().get("results", [])
            parsed = []
            for r in results:
                details = r.get("details", {})
                day = r.get("day", {})
                parsed.append({
                    "strike_price": details.get("strike_price", 0),
                    "contract_type": details.get("contract_type", ""),
                    "expiration_date": details.get("expiration_date", ""),
                    "day_volume": day.get("volume", 0),
                    "open_interest": r.get("open_interest", 0),
                    "last_price": day.get("close", 0) or day.get("last_price", 0),
                })
            return parsed
        except Exception as e:
            logger.warning("Options snapshot failed for %s: %s", symbol, e)
            return []
