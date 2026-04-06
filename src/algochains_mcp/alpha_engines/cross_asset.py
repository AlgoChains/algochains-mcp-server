"""Cross-asset correlation, pair trading, and relative value analysis.

Computes rolling correlations, z-score spreads for pair trades,
and cross-asset momentum/mean-reversion signals.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx

logger = logging.getLogger("algochains_mcp.alpha_engines.cross_asset")


class CrossAssetEngine:
    """Cross-asset correlation and pair trading signals."""

    ASSET_PAIRS = {
        "equity_bond": ("SPY", "TLT"),
        "equity_gold": ("SPY", "GLD"),
        "equity_oil": ("SPY", "USO"),
        "equity_dollar": ("SPY", "UUP"),
        "tech_value": ("QQQ", "IWD"),
        "growth_value": ("IWF", "IWD"),
        "us_intl": ("SPY", "EFA"),
        "equity_vol": ("SPY", "VXX"),
    }

    def __init__(self, polygon_key: str = "") -> None:
        self._polygon_key = polygon_key
        self._http = httpx.AsyncClient(timeout=30)

    async def correlation_matrix(
        self, symbols: list[str], lookback_days: int = 60
    ) -> dict[str, Any]:
        """Compute rolling correlation matrix for a basket of assets."""
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=lookback_days + 10)
        start_str = start.strftime("%Y-%m-%d")
        end_str = end.strftime("%Y-%m-%d")

        returns_map: dict[str, list[float]] = {}
        for sym in symbols[:10]:
            bars = await self._fetch_daily(sym, start_str, end_str)
            if len(bars) >= 2:
                closes = [b["c"] for b in bars]
                rets = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]
                returns_map[sym] = rets[-lookback_days:]

        if len(returns_map) < 2:
            return {"status": "error", "error": "Need at least 2 symbols with data"}

        min_len = min(len(r) for r in returns_map.values())
        for sym in returns_map:
            returns_map[sym] = returns_map[sym][-min_len:]

        syms = list(returns_map.keys())
        matrix: dict[str, dict[str, float]] = {}
        for i, s1 in enumerate(syms):
            matrix[s1] = {}
            for j, s2 in enumerate(syms):
                corr = self._pearson(returns_map[s1], returns_map[s2])
                matrix[s1][s2] = round(corr, 4)

        high_corr = []
        low_corr = []
        for i, s1 in enumerate(syms):
            for j, s2 in enumerate(syms):
                if i < j:
                    c = matrix[s1][s2]
                    if c > 0.8:
                        high_corr.append({"pair": f"{s1}/{s2}", "corr": c})
                    elif c < -0.3:
                        low_corr.append({"pair": f"{s1}/{s2}", "corr": c})

        return {
            "status": "ok",
            "symbols": syms,
            "lookback_days": lookback_days,
            "observations": min_len,
            "matrix": matrix,
            "high_correlation_pairs": sorted(high_corr, key=lambda x: x["corr"], reverse=True),
            "negative_correlation_pairs": sorted(low_corr, key=lambda x: x["corr"]),
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    async def pair_trade_signal(
        self,
        symbol_a: str,
        symbol_b: str,
        lookback_days: int = 60,
        z_entry: float = 2.0,
        z_exit: float = 0.5,
    ) -> dict[str, Any]:
        """Compute pair trade z-score signal for two symbols."""
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=lookback_days + 10)
        start_str = start.strftime("%Y-%m-%d")
        end_str = end.strftime("%Y-%m-%d")

        bars_a = await self._fetch_daily(symbol_a, start_str, end_str)
        bars_b = await self._fetch_daily(symbol_b, start_str, end_str)

        if not bars_a or not bars_b:
            return {"status": "error", "error": "Insufficient price data"}

        min_len = min(len(bars_a), len(bars_b))
        prices_a = [b["c"] for b in bars_a[-min_len:]]
        prices_b = [b["c"] for b in bars_b[-min_len:]]

        ratios = [a / b if b != 0 else 0 for a, b in zip(prices_a, prices_b)]
        if not ratios:
            return {"status": "error", "error": "Could not compute ratio"}

        mean_ratio = sum(ratios) / len(ratios)
        std_ratio = math.sqrt(sum((r - mean_ratio) ** 2 for r in ratios) / len(ratios))

        current_ratio = ratios[-1]
        z_score = (current_ratio - mean_ratio) / std_ratio if std_ratio > 0 else 0

        signal = "neutral"
        if z_score > z_entry:
            signal = f"short_{symbol_a}_long_{symbol_b}"
        elif z_score < -z_entry:
            signal = f"long_{symbol_a}_short_{symbol_b}"
        elif abs(z_score) < z_exit:
            signal = "close_position"

        half_life = self._compute_half_life(ratios)
        correlation = self._pearson(
            [(prices_a[i] - prices_a[i - 1]) / prices_a[i - 1] for i in range(1, len(prices_a))],
            [(prices_b[i] - prices_b[i - 1]) / prices_b[i - 1] for i in range(1, len(prices_b))],
        )

        return {
            "status": "ok",
            "pair": f"{symbol_a}/{symbol_b}",
            "current_ratio": round(current_ratio, 6),
            "mean_ratio": round(mean_ratio, 6),
            "std_ratio": round(std_ratio, 6),
            "z_score": round(z_score, 4),
            "signal": signal,
            "z_entry_threshold": z_entry,
            "z_exit_threshold": z_exit,
            "correlation": round(correlation, 4),
            "half_life_days": half_life,
            "lookback_days": lookback_days,
            "observations": min_len,
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    async def relative_strength(
        self, symbol: str, benchmark: str = "SPY", lookback_days: int = 20
    ) -> dict[str, Any]:
        """Compute relative strength of symbol vs benchmark."""
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=lookback_days + 10)
        start_str = start.strftime("%Y-%m-%d")
        end_str = end.strftime("%Y-%m-%d")

        bars_s = await self._fetch_daily(symbol, start_str, end_str)
        bars_b = await self._fetch_daily(benchmark, start_str, end_str)

        if not bars_s or not bars_b:
            return {"status": "error", "error": "Insufficient data"}

        min_len = min(len(bars_s), len(bars_b))
        prices_s = [b["c"] for b in bars_s[-min_len:]]
        prices_b = [b["c"] for b in bars_b[-min_len:]]

        sym_return = (prices_s[-1] - prices_s[0]) / prices_s[0] * 100
        bench_return = (prices_b[-1] - prices_b[0]) / prices_b[0] * 100
        alpha = sym_return - bench_return

        rs_line = [s / b if b else 0 for s, b in zip(prices_s, prices_b)]
        rs_trend = "rising" if len(rs_line) > 1 and rs_line[-1] > rs_line[0] else "falling"

        return {
            "status": "ok",
            "symbol": symbol,
            "benchmark": benchmark,
            "lookback_days": lookback_days,
            "symbol_return_pct": round(sym_return, 4),
            "benchmark_return_pct": round(bench_return, 4),
            "alpha_pct": round(alpha, 4),
            "rs_trend": rs_trend,
            "rs_current": round(rs_line[-1], 6) if rs_line else 0,
            "signal": "outperforming" if alpha > 1 else ("underperforming" if alpha < -1 else "inline"),
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    def _pearson(self, x: list[float], y: list[float]) -> float:
        n = min(len(x), len(y))
        if n < 2:
            return 0.0
        x, y = x[:n], y[:n]
        mx = sum(x) / n
        my = sum(y) / n
        cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
        sx = math.sqrt(sum((xi - mx) ** 2 for xi in x))
        sy = math.sqrt(sum((yi - my) ** 2 for yi in y))
        return cov / (sx * sy) if sx * sy > 0 else 0.0

    def _compute_half_life(self, series: list[float]) -> int | None:
        """Ornstein-Uhlenbeck half-life estimation."""
        if len(series) < 10:
            return None
        diffs = [series[i] - series[i - 1] for i in range(1, len(series))]
        lagged = series[:-1]
        n = len(diffs)
        mean_d = sum(diffs) / n
        mean_l = sum(lagged) / n
        cov = sum((d - mean_d) * (l - mean_l) for d, l in zip(diffs, lagged))
        var = sum((l - mean_l) ** 2 for l in lagged)
        theta = cov / var if var > 0 else 0
        if theta >= 0:
            return None
        hl = -math.log(2) / theta
        return max(1, round(hl))

    async def _fetch_daily(self, symbol: str, start: str, end: str) -> list[dict]:
        if not self._polygon_key:
            return []
        url = f"https://api.polygon.io/v2/aggs/ticker/{symbol}/range/1/day/{start}/{end}"
        try:
            resp = await self._http.get(
                url, params={"adjusted": "true", "sort": "asc", "apiKey": self._polygon_key}
            )
            resp.raise_for_status()
            return resp.json().get("results", [])
        except Exception as e:
            logger.warning("Daily bars fetch failed for %s: %s", symbol, e)
            return []
