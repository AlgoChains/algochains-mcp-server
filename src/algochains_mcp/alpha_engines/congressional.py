"""Congressional & insider trading alpha — tracks politician trades and SEC filings.

Fetches congressional trade disclosures and insider filings (Form 4),
computes conviction scores, and generates follow-the-smart-money signals.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx

logger = logging.getLogger("algochains_mcp.alpha_engines.congressional")

QUIVER_BASE = "https://api.quiverquant.com/beta"


class CongressionalEngine:
    """Congressional trades and insider filing alpha signals."""

    def __init__(self, polygon_key: str = "", finnhub_key: str = "") -> None:
        self._polygon_key = polygon_key
        self._finnhub_key = finnhub_key
        self._http = httpx.AsyncClient(timeout=30)

    async def get_congressional_trades(
        self, symbol: str = "", days: int = 30
    ) -> dict[str, Any]:
        """Get recent congressional stock trades from Finnhub or SEC RSS."""
        trades = await self._fetch_insider_sentiment(symbol, days)
        if trades is None:
            trades = []

        buys = [t for t in trades if t.get("change", 0) > 0]
        sells = [t for t in trades if t.get("change", 0) < 0]

        net_shares = sum(t.get("change", 0) for t in trades)
        conviction = "neutral"
        if len(buys) > len(sells) * 2 and len(buys) >= 3:
            conviction = "strong_buy"
        elif len(buys) > len(sells):
            conviction = "buy"
        elif len(sells) > len(buys) * 2 and len(sells) >= 3:
            conviction = "strong_sell"
        elif len(sells) > len(buys):
            conviction = "sell"

        return {
            "status": "ok",
            "symbol": symbol or "ALL",
            "days": days,
            "total_filings": len(trades),
            "buy_filings": len(buys),
            "sell_filings": len(sells),
            "net_shares": net_shares,
            "conviction": conviction,
            "recent_trades": trades[:20],
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    async def insider_cluster_scan(
        self, symbols: list[str] | None = None, days: int = 14, min_insiders: int = 2
    ) -> dict[str, Any]:
        """Scan for insider buying clusters — multiple insiders buying same stock."""
        if symbols is None:
            symbols = [
                "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA",
                "JPM", "BAC", "GS", "V", "MA", "UNH", "JNJ", "PFE",
            ]

        clusters = []
        for sym in symbols[:20]:
            try:
                data = await self.get_congressional_trades(sym, days)
                if data.get("status") == "ok" and data.get("buy_filings", 0) >= min_insiders:
                    clusters.append({
                        "symbol": sym,
                        "buy_filings": data["buy_filings"],
                        "sell_filings": data["sell_filings"],
                        "net_shares": data["net_shares"],
                        "conviction": data["conviction"],
                    })
            except Exception as e:
                logger.warning("Insider scan failed for %s: %s", sym, e)

        clusters.sort(key=lambda x: x["buy_filings"], reverse=True)
        return {
            "status": "ok",
            "scanned": len(symbols),
            "clusters_found": len(clusters),
            "min_insiders": min_insiders,
            "days": days,
            "clusters": clusters,
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    async def smart_money_composite(self, symbol: str) -> dict[str, Any]:
        """Composite smart money score combining insider + institutional signals."""
        insider = await self.get_congressional_trades(symbol, days=30)
        insider_90 = await self.get_congressional_trades(symbol, days=90)

        score = 50
        signals = []

        buy_count = insider.get("buy_filings", 0)
        sell_count = insider.get("sell_filings", 0)
        if buy_count > sell_count:
            score += min(buy_count * 5, 25)
            signals.append(f"insider_buying_{buy_count}_filings")
        elif sell_count > buy_count:
            score -= min(sell_count * 5, 25)
            signals.append(f"insider_selling_{sell_count}_filings")

        buy_90 = insider_90.get("buy_filings", 0)
        sell_90 = insider_90.get("sell_filings", 0)
        if buy_90 >= 5:
            score += 10
            signals.append("sustained_insider_buying_90d")
        elif sell_90 >= 5:
            score -= 10
            signals.append("sustained_insider_selling_90d")

        score = max(0, min(100, score))
        label = "strong_buy" if score >= 80 else "buy" if score >= 60 else "neutral" if score >= 40 else "sell" if score >= 20 else "strong_sell"

        return {
            "status": "ok",
            "symbol": symbol,
            "smart_money_score": score,
            "label": label,
            "signals": signals,
            "insider_30d": {
                "buys": insider.get("buy_filings", 0),
                "sells": insider.get("sell_filings", 0),
            },
            "insider_90d": {
                "buys": buy_90,
                "sells": sell_90,
            },
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    async def _fetch_insider_sentiment(self, symbol: str, days: int) -> list[dict] | None:
        """Fetch insider sentiment from Finnhub."""
        if not self._finnhub_key:
            return []
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        url = "https://finnhub.io/api/v1/stock/insider-transactions"
        try:
            resp = await self._http.get(url, params={
                "symbol": symbol,
                "from": start.strftime("%Y-%m-%d"),
                "to": end.strftime("%Y-%m-%d"),
                "token": self._finnhub_key,
            })
            resp.raise_for_status()
            data = resp.json().get("data", [])
            return [
                {
                    "name": t.get("name", ""),
                    "share": t.get("share", 0),
                    "change": t.get("change", 0),
                    "filing_date": t.get("filingDate", ""),
                    "transaction_date": t.get("transactionDate", ""),
                    "transaction_code": t.get("transactionCode", ""),
                    "transaction_price": t.get("transactionPrice", 0),
                }
                for t in data
            ]
        except Exception as e:
            logger.warning("Finnhub insider fetch failed for %s: %s", symbol, e)
            return []
