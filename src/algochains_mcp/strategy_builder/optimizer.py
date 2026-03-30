"""StrategyOptimizer — Optuna-based parameter search over StrategySpec parameters."""

from __future__ import annotations

import copy
import logging
from typing import Any

from .spec import StrategySpec

logger = logging.getLogger("algochains_mcp.strategy_builder.optimizer")


class StrategyOptimizer:
    """Run parameter optimization on a StrategySpec using Optuna."""

    def __init__(self, backtest_runner=None):
        self._runner = backtest_runner

    async def optimize(
        self,
        spec: StrategySpec,
        n_trials: int = 50,
        metric: str = "sharpe",
        search_space: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            import optuna
            optuna.logging.set_verbosity(optuna.logging.WARNING)
        except ImportError:
            return {
                "success": False,
                "error": "Optuna not installed. Run: pip install optuna",
                "spec_id": spec.id,
            }

        if not self._runner:
            from .backtest_runner import BacktestRunner
            self._runner = BacktestRunner()

        # Build search space from spec indicators + exit rules
        if search_space is None:
            search_space = self._default_search_space(spec)

        if not search_space:
            return {
                "success": False,
                "error": "No optimizable parameters found in the strategy spec.",
                "spec_id": spec.id,
            }

        results_log: list[dict[str, Any]] = []

        def objective(trial: optuna.Trial) -> float:
            trial_spec = copy.deepcopy(spec)

            # Sample parameters
            for param_path, config in search_space.items():
                if config["type"] == "int":
                    val = trial.suggest_int(param_path, config["low"], config["high"])
                elif config["type"] == "float":
                    val = trial.suggest_float(param_path, config["low"], config["high"], step=config.get("step"))
                else:
                    val = trial.suggest_categorical(param_path, config["choices"])

                self._set_param(trial_spec, param_path, val)

            # Run backtest synchronously via event loop
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    result = pool.submit(
                        asyncio.run, self._runner.run(trial_spec)
                    ).result()
            else:
                result = asyncio.run(self._runner.run(trial_spec))

            if not result.get("success"):
                return float("-inf")

            metrics = result.get("results", {})
            value = metrics.get(metric, 0)
            results_log.append({
                "trial": trial.number,
                "params": trial.params,
                metric: value,
                "trades": metrics.get("total_trades", 0),
            })
            return value

        study = optuna.create_study(direction="maximize", study_name=f"opt_{spec.id}")

        try:
            study.optimize(objective, n_trials=n_trials, timeout=600)
        except Exception as e:
            logger.error("Optimization failed: %s", e)
            return {"success": False, "error": str(e), "spec_id": spec.id}

        # Top N results
        top_trials = sorted(study.trials, key=lambda t: t.value if t.value else float("-inf"), reverse=True)[:10]

        return {
            "success": True,
            "spec_id": spec.id,
            "spec_name": spec.name,
            "metric": metric,
            "n_trials": n_trials,
            "best_value": study.best_value,
            "best_params": study.best_params,
            "top_10": [
                {
                    "trial": t.number,
                    "value": t.value,
                    "params": t.params,
                }
                for t in top_trials
            ],
            "search_space": {k: {kk: vv for kk, vv in v.items()} for k, v in search_space.items()},
        }

    def _default_search_space(self, spec: StrategySpec) -> dict[str, Any]:
        space: dict[str, Any] = {}

        for i, ind in enumerate(spec.indicators):
            name = ind.get("name", "").lower()
            period = ind.get("period", 14)

            if name == "rsi":
                space[f"indicators.{i}.period"] = {"type": "int", "low": 5, "high": 30}
            elif name == "bbands":
                space[f"indicators.{i}.period"] = {"type": "int", "low": 10, "high": 40}
                space[f"indicators.{i}.std_dev"] = {"type": "float", "low": 1.0, "high": 3.5, "step": 0.25}
            elif name in ("ema", "sma"):
                space[f"indicators.{i}.period"] = {"type": "int", "low": 5, "high": 200}
            elif name == "atr":
                space[f"indicators.{i}.period"] = {"type": "int", "low": 7, "high": 21}

        # Entry thresholds
        for direction in ("long", "short"):
            rules = spec.entry_rules.get(direction, {})
            for j, cond in enumerate(rules.get("conditions", [])):
                if "value" in cond and isinstance(cond["value"], (int, float)):
                    indicator = cond.get("indicator", "").lower()
                    val = cond["value"]
                    if indicator == "rsi":
                        if val < 50:
                            space[f"entry_rules.{direction}.conditions.{j}.value"] = {"type": "int", "low": 15, "high": 45}
                        else:
                            space[f"entry_rules.{direction}.conditions.{j}.value"] = {"type": "int", "low": 55, "high": 85}

        # Exit rules
        for exit_key in ("stop_loss", "take_profit", "trailing_stop"):
            rule = spec.exit_rules.get(exit_key, {})
            mult = rule.get("multiplier")
            if mult:
                space[f"exit_rules.{exit_key}.multiplier"] = {
                    "type": "float", "low": 0.5, "high": 5.0, "step": 0.25,
                }

        return space

    def _set_param(self, spec: StrategySpec, path: str, value: Any) -> None:
        parts = path.split(".")
        obj: Any = spec

        for i, part in enumerate(parts[:-1]):
            if isinstance(obj, StrategySpec):
                obj = getattr(obj, part, None)
            elif isinstance(obj, dict):
                obj = obj.get(part, {})
            elif isinstance(obj, list):
                try:
                    obj = obj[int(part)]
                except (IndexError, ValueError):
                    return

        last = parts[-1]
        if isinstance(obj, dict):
            obj[last] = value
        elif isinstance(obj, list):
            try:
                obj[int(last)] = value
            except (IndexError, ValueError):
                pass
