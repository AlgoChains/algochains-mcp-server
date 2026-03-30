"""BacktestRunner — execute StrategySpec through the Rust backtest engine."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from typing import Any

from .spec import StrategySpec

logger = logging.getLogger("algochains_mcp.strategy_builder.backtest")


class BacktestRunner:
    """Translate a StrategySpec into Rust engine CLI args and parse results."""

    STRATEGY_MAP = {
        "rsi": "rsi",
        "bbands": "bb",
        "ema": "swing",
        "sma": "swing",
        "macd": "swing",
        "mean_reversion": "bb",
        "momentum": "rsi",
        "breakout": "scalper",
        "trend": "swing",
        "scalp": "scalper",
    }

    CONTRACT_MAP = {
        "equity": "STOCK",
        "forex": "FOREX",
        "futures": "FUTURES",
        "crypto": "CRYPTO",
    }

    def __init__(self, engine_dir: str | None = None):
        self._engine_dir = engine_dir or self._find_engine()

    def _find_engine(self) -> str:
        candidates = [
            "tick_backtest_engine_v2/target/release",
            "./target/release",
        ]
        for c in candidates:
            if shutil.which(f"{c}/rsi") or shutil.which(f"{c}/bb"):
                return c
        return "tick_backtest_engine_v2/target/release"

    def _detect_strategy_binary(self, spec: StrategySpec) -> str:
        for ind in spec.indicators:
            name = ind.get("name", "").lower()
            if name in self.STRATEGY_MAP:
                return self.STRATEGY_MAP[name]
        entry_rules = spec.entry_rules
        for direction in ("long", "short"):
            rules = entry_rules.get(direction, {})
            for cond in rules.get("conditions", []):
                indicator = cond.get("indicator", "").lower()
                if indicator in self.STRATEGY_MAP:
                    return self.STRATEGY_MAP[indicator]
        return "rsi"

    def _build_cli_args(self, spec: StrategySpec) -> list[str]:
        binary = self._detect_strategy_binary(spec)
        contract = self.CONTRACT_MAP.get(spec.asset_class, "STOCK")
        symbol = spec.symbols[0] if spec.symbols else "SPY"

        args = [
            f"{self._engine_dir}/{binary}",
            "-d", f"data/{'forex' if contract == 'FOREX' else 'stocks'}/{symbol}/{self._tf_to_filename(spec.timeframe)}.parquet",
            "-c", contract,
        ]

        for ind in spec.indicators:
            name = ind.get("name", "").lower()
            period = ind.get("period")
            if name == "rsi" and period:
                args.extend(["--rsi-period", str(period)])
                oversold = None
                overbought = None
                for direction in ("long", "short"):
                    rules = spec.entry_rules.get(direction, {})
                    for cond in rules.get("conditions", []):
                        if cond.get("indicator", "").lower() == "rsi":
                            val = cond.get("value")
                            op = cond.get("operator", "")
                            if op == "<" and val:
                                oversold = val
                            elif op == ">" and val:
                                overbought = val
                if oversold:
                    args.extend(["--oversold", str(oversold)])
                if overbought:
                    args.extend(["--overbought", str(overbought)])
            elif name == "bbands":
                if period:
                    args.extend(["--bb-period", str(period)])
                std_dev = ind.get("std_dev")
                if std_dev:
                    args.extend(["--bb-std", str(std_dev)])

        # Exit rules
        sl = spec.exit_rules.get("stop_loss", {})
        tp = spec.exit_rules.get("take_profit", {})
        if sl.get("multiplier"):
            args.extend(["--stop-atr-mult", str(sl["multiplier"])])
        if tp.get("multiplier"):
            args.extend(["--target-atr-mult", str(tp["multiplier"])])

        # Position sizing
        capital = spec.position_sizing.get("capital", 100000)
        args.extend(["--capital", str(capital)])

        return args

    def _tf_to_filename(self, timeframe: str) -> str:
        mapping = {
            "1min": "1min", "5min": "5min", "15min": "15min", "30min": "30min",
            "1h": "hour", "4h": "4h", "daily": "day", "weekly": "week",
        }
        return mapping.get(timeframe, "day")

    async def run(self, spec: StrategySpec) -> dict[str, Any]:
        args = self._build_cli_args(spec)
        logger.info("Running backtest: %s", " ".join(args))

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)

            if proc.returncode != 0:
                return {
                    "success": False,
                    "error": stderr.decode().strip() or f"Engine exited with code {proc.returncode}",
                    "spec_id": spec.id,
                    "command": " ".join(args),
                }

            try:
                results = json.loads(stdout.decode())
            except json.JSONDecodeError:
                output = stdout.decode().strip()
                results = {"raw_output": output}

            return {
                "success": True,
                "spec_id": spec.id,
                "spec_name": spec.name,
                "symbol": spec.symbols[0] if spec.symbols else "unknown",
                "timeframe": spec.timeframe,
                "results": {
                    "sharpe": results.get("sharpe", results.get("oos_sharpe", 0)),
                    "total_return": results.get("total_return", results.get("pnl", 0)),
                    "max_drawdown": results.get("max_drawdown", results.get("max_dd", 0)),
                    "total_trades": results.get("total_trades", results.get("trades", 0)),
                    "win_rate": results.get("win_rate", 0),
                    "profit_factor": results.get("profit_factor", 0),
                    "avg_trade_pnl": results.get("avg_trade_pnl", results.get("avg_pnl", 0)),
                    "annual_return": results.get("annual_return", 0),
                    "annual_volatility": results.get("annual_volatility", 0),
                },
                "engine_output": results,
            }

        except asyncio.TimeoutError:
            return {"success": False, "error": "Backtest timed out (300s limit)", "spec_id": spec.id}
        except FileNotFoundError:
            return {
                "success": False,
                "error": f"Rust engine binary not found. Compile with: cd tick_backtest_engine_v2 && cargo build --release",
                "spec_id": spec.id,
            }
        except Exception as e:
            return {"success": False, "error": str(e), "spec_id": spec.id}
