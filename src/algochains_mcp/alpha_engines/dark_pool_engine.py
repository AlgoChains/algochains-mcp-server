"""Dark pool print detection — block trades, hidden liquidity, institutional footprint.

Analyzes trade data to detect dark pool prints (off-exchange trades),
large block trades, and institutional accumulation/distribution patterns.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger("algochains_mcp.alpha_engines.dark_pool")


class DarkPoolEngine:
    """Detect dark pool prints and institutional block trades via Polygon trade data."""

    DARK_POOL_EXCHANGES = {"TRF", "FINRA", "ADF"}
    BLOCK_THRESHOLD_MULTIPLIER = 5.0

    def __init__(self, polygon_key: str = "") -> None:
        self._polygon_key = polygon_key
        self._http = httpx.AsyncClient(timeout=30)

    async def detect_dark_prints(
        self, symbol: str, date: str = "", min_size: int = 10000
    ) -> dict[str, Any]:
        """Detect dark pool prints — large off-exchange trades."""
        if not date:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        trades = await self._fetch_trades(symbol, date)
        if not trades:
            return {"status": "error", "error": "No trade data available"}

        total_volume = sum(t.get("s", 0) for t in trades)
        total_trades = len(trades)

        dark_prints = []
        dark_volume = 0
        lit_volume = 0

        for t in trades:
            size = t.get("s", 0)
            conditions = t.get("c", [])
            exchange = t.get("x", 0)

            is_dark = (
                any(c in (12, 15, 16, 38) for c in conditions)
                or size >= min_size
            )

            if is_dark and size >= min_size:
                dark_prints.append({
                    "timestamp": t.get("t", 0),
                    "price": t.get("p", 0),
                    "size": size,
                    "notional": round(t.get("p", 0) * size, 2),
                    "conditions": conditions,
                    "exchange_id": exchange,
                })
                dark_volume += size
            elif is_dark:
                dark_volume += size
            else:
                lit_volume += size

        dark_prints.sort(key=lambda x: x["size"], reverse=True)

        dark_pct = round(dark_volume / total_volume * 100, 2) if total_volume else 0
        avg_dark_size = round(dark_volume / len(dark_prints), 0) if dark_prints else 0

        buy_prints = [p for p in dark_prints if self._classify_aggressor(p) == "buy"]
        sell_prints = [p for p in dark_prints if self._classify_aggressor(p) == "sell"]

        signal = "neutral"
        if len(buy_prints) > len(sell_prints) * 1.5:
            signal = "institutional_accumulation"
        elif len(sell_prints) > len(buy_prints) * 1.5:
            signal = "institutional_distribution"
        if dark_pct > 50:
            signal = f"high_dark_activity_{signal}"

        return {
            "status": "ok",
            "symbol": symbol,
            "date": date,
            "total_volume": total_volume,
            "total_trades": total_trades,
            "dark_volume": dark_volume,
            "lit_volume": lit_volume,
            "dark_pct": dark_pct,
            "large_dark_prints": len(dark_prints),
            "avg_dark_print_size": avg_dark_size,
            "top_prints": dark_prints[:10],
            "buy_prints": len(buy_prints),
            "sell_prints": len(sell_prints),
            "signal": signal,
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    async def block_trade_scanner(
        self, symbols: list[str], min_notional: float = 500_000
    ) -> dict[str, Any]:
        """Scan multiple symbols for large block trades."""
        results = []
        for symbol in symbols[:20]:
            try:
                data = await self.detect_dark_prints(symbol, min_size=1000)
                if data.get("status") == "ok" and data.get("large_dark_prints", 0) > 0:
                    top = data.get("top_prints", [])
                    big_blocks = [
                        p for p in top if p.get("notional", 0) >= min_notional
                    ]
                    if big_blocks:
                        results.append({
                            "symbol": symbol,
                            "block_count": len(big_blocks),
                            "total_notional": sum(b["notional"] for b in big_blocks),
                            "signal": data["signal"],
                            "dark_pct": data["dark_pct"],
                            "largest_print": big_blocks[0] if big_blocks else None,
                        })
            except Exception as e:
                logger.warning("Block scan failed for %s: %s", symbol, e)

        results.sort(key=lambda x: x["total_notional"], reverse=True)
        return {
            "status": "ok",
            "scanned": len(symbols),
            "with_blocks": len(results),
            "results": results,
            "min_notional": min_notional,
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    def _classify_aggressor(self, print_data: dict) -> str:
        """Heuristic: classify dark print as buy or sell based on conditions."""
        conditions = print_data.get("conditions", [])
        if 14 in conditions or 41 in conditions:
            return "buy"
        if 15 in conditions or 40 in conditions:
            return "sell"
        return "unknown"

    async def _fetch_trades(self, symbol: str, date: str) -> list[dict]:
        """Fetch trades from Polygon."""
        if not self._polygon_key:
            return []
        url = f"https://api.polygon.io/v3/trades/{symbol}"
        try:
            resp = await self._http.get(
                url,
                params={
                    "timestamp.gte": f"{date}T00:00:00Z",
                    "timestamp.lte": f"{date}T23:59:59Z",
                    "limit": 50000,
                    "sort": "timestamp",
                    "apiKey": self._polygon_key,
                },
            )
            resp.raise_for_status()
            return resp.json().get("results", [])
        except Exception as e:
            logger.warning("Polygon trades fetch failed for %s: %s", symbol, e)
            return []
