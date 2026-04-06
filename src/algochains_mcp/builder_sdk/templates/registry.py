"""Strategy Template Registry — list and instantiate built-in Backtrader templates."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class TemplateInfo:
    name: str
    module: str
    class_name: str
    description: str
    best_for: str
    params: dict[str, Any]
    benchmark_sharpe: float
    benchmark_win_rate: float
    benchmark_max_dd: float


_TEMPLATES: list[TemplateInfo] = [
    TemplateInfo(
        name="SMACrossover",
        module="algochains_mcp.builder_sdk.templates.sma_crossover",
        class_name="SMACrossover",
        description="Dual SMA crossover with ATR-based trailing stop and drawdown circuit breaker.",
        best_for="Equities (SPY, QQQ, large-caps), daily bars, trending markets",
        params={"fast": 20, "slow": 50, "risk_pct": 0.02, "atr_period": 14, "atr_stop_mult": 2.0},
        benchmark_sharpe=1.4,
        benchmark_win_rate=55.0,
        benchmark_max_dd=18.0,
    ),
    TemplateInfo(
        name="RSIMeanReversion",
        module="algochains_mcp.builder_sdk.templates.rsi_mean_reversion",
        class_name="RSIMeanReversion",
        description="RSI oversold/overbought with Bollinger Band confirmation and R-multiple exits.",
        best_for="Liquid equities, daily/4h bars, mean-reverting regimes",
        params={"rsi_period": 14, "oversold": 30, "overbought": 70, "bb_period": 20, "take_profit_r": 2.0},
        benchmark_sharpe=2.2,
        benchmark_win_rate=48.0,
        benchmark_max_dd=14.0,
    ),
    TemplateInfo(
        name="BollingerBreakout",
        module="algochains_mcp.builder_sdk.templates.bollinger_breakout",
        class_name="BollingerBreakout",
        description="BB squeeze breakout with volume confirmation and trailing stop.",
        best_for="Futures (MNQ, NQ, ES, CL), crypto, 1h/4h bars, high-volatility",
        params={"bb_period": 20, "squeeze_threshold": 0.035, "take_profit_r": 3.0},
        benchmark_sharpe=1.8,
        benchmark_win_rate=42.0,
        benchmark_max_dd=16.0,
    ),
    TemplateInfo(
        name="EMAPullback",
        module="algochains_mcp.builder_sdk.templates.ema_pullback",
        class_name="EMAPullback",
        description="EMA trend filter + RSI pullback entry, modeled after MES/NQ swing bots.",
        best_for="Equity index futures (MES, NQ, ES), daily/4h bars",
        params={"trend_period": 200, "fast_period": 20, "slow_period": 50, "rsi_period": 14},
        benchmark_sharpe=1.9,
        benchmark_win_rate=57.0,
        benchmark_max_dd=12.0,
    ),
]


def list_templates() -> list[dict]:
    """Return all available strategy templates as dicts."""
    return [
        {
            "name": t.name,
            "description": t.description,
            "best_for": t.best_for,
            "default_params": t.params,
            "benchmark": {
                "sharpe": t.benchmark_sharpe,
                "win_rate_pct": t.benchmark_win_rate,
                "max_dd_pct": t.benchmark_max_dd,
            },
        }
        for t in _TEMPLATES
    ]


def get_template_class(name: str) -> Any:
    """Import and return a strategy class by template name.

    Raises:
        ValueError: If the template name is not found.
        ImportError: If backtrader is not installed.
    """
    try:
        import importlib
        info = next((t for t in _TEMPLATES if t.name == name), None)
        if info is None:
            available = [t.name for t in _TEMPLATES]
            raise ValueError(f"Template '{name}' not found. Available: {available}")
        mod = importlib.import_module(info.module)
        return getattr(mod, info.class_name)
    except ImportError as exc:
        if "backtrader" in str(exc):
            raise ImportError(
                "backtrader is required for strategy templates. "
                "Install with: pip install 'algochains-mcp[backtrader]'"
            ) from exc
        raise


def get_template_info(name: str) -> TemplateInfo:
    """Return template metadata for a given template name."""
    info = next((t for t in _TEMPLATES if t.name == name), None)
    if info is None:
        raise ValueError(f"Template '{name}' not found.")
    return info
