"""WalkForwardEngine — K-fold walk-forward validation for StrategySpec."""

from __future__ import annotations

import copy
import logging
from datetime import datetime, timedelta
from typing import Any

from .spec import StrategySpec

logger = logging.getLogger("algochains_mcp.strategy_builder.walk_forward")


class WalkForwardEngine:
    """Execute K-fold walk-forward validation on a StrategySpec."""

    def __init__(self, backtest_runner=None):
        self._runner = backtest_runner

    async def run(
        self,
        spec: StrategySpec,
        n_folds: int = 5,
        train_pct: float = 0.70,
    ) -> dict[str, Any]:
        if not self._runner:
            from .backtest_runner import BacktestRunner
            self._runner = BacktestRunner()

        if not spec.train_start or not spec.test_end:
            return {
                "success": False,
                "error": "StrategySpec must have train_start and test_end dates for walk-forward.",
                "spec_id": spec.id,
            }

        try:
            start = datetime.fromisoformat(spec.train_start)
            end = datetime.fromisoformat(spec.test_end or spec.train_end)
        except ValueError as e:
            return {"success": False, "error": f"Invalid date format: {e}", "spec_id": spec.id}

        total_days = (end - start).days
        if total_days < 90:
            return {
                "success": False,
                "error": f"Date range too short ({total_days} days). Need at least 90 days for walk-forward.",
                "spec_id": spec.id,
            }

        fold_size = total_days // n_folds
        train_days = int(fold_size * train_pct)
        test_days = fold_size - train_days

        fold_results: list[dict[str, Any]] = []

        for fold in range(n_folds):
            fold_start = start + timedelta(days=fold * fold_size)
            fold_train_end = fold_start + timedelta(days=train_days)
            fold_test_start = fold_train_end + timedelta(days=1)
            fold_test_end = fold_start + timedelta(days=fold_size)

            if fold_test_end > end:
                fold_test_end = end

            # Create fold-specific spec
            fold_spec = copy.deepcopy(spec)
            fold_spec.train_start = fold_start.strftime("%Y-%m-%d")
            fold_spec.train_end = fold_train_end.strftime("%Y-%m-%d")
            fold_spec.test_start = fold_test_start.strftime("%Y-%m-%d")
            fold_spec.test_end = fold_test_end.strftime("%Y-%m-%d")
            fold_spec.id = f"{spec.id}_fold{fold}"

            logger.info(
                "Walk-forward fold %d/%d: train=%s→%s, test=%s→%s",
                fold + 1, n_folds,
                fold_spec.train_start, fold_spec.train_end,
                fold_spec.test_start, fold_spec.test_end,
            )

            result = await self._runner.run(fold_spec)

            fold_results.append({
                "fold": fold + 1,
                "train_period": f"{fold_spec.train_start} → {fold_spec.train_end}",
                "test_period": f"{fold_spec.test_start} → {fold_spec.test_end}",
                "success": result.get("success", False),
                "oos_sharpe": result.get("results", {}).get("sharpe", 0) if result.get("success") else None,
                "oos_trades": result.get("results", {}).get("total_trades", 0) if result.get("success") else None,
                "oos_return": result.get("results", {}).get("total_return", 0) if result.get("success") else None,
                "max_drawdown": result.get("results", {}).get("max_drawdown", 0) if result.get("success") else None,
                "error": result.get("error") if not result.get("success") else None,
            })

        # Aggregate metrics
        successful_folds = [f for f in fold_results if f["success"] and f["oos_sharpe"] is not None]
        n_successful = len(successful_folds)

        if n_successful == 0:
            return {
                "success": False,
                "error": "All folds failed. Check data availability and engine configuration.",
                "spec_id": spec.id,
                "folds": fold_results,
            }

        oos_sharpes = [f["oos_sharpe"] for f in successful_folds]
        avg_oos_sharpe = sum(oos_sharpes) / n_successful
        min_oos_sharpe = min(oos_sharpes)
        max_oos_sharpe = max(oos_sharpes)

        # Consistency: % of folds with positive Sharpe
        positive_folds = sum(1 for s in oos_sharpes if s > 0)
        consistency = positive_folds / n_successful

        # Walk-forward efficiency: avg OOS Sharpe / max OOS Sharpe
        wfe = avg_oos_sharpe / max_oos_sharpe if max_oos_sharpe > 0 else 0

        # Stability: 1 - (std / mean) of OOS Sharpes
        if n_successful > 1:
            mean_s = avg_oos_sharpe
            std_s = (sum((s - mean_s) ** 2 for s in oos_sharpes) / (n_successful - 1)) ** 0.5
            stability = max(0, 1 - (std_s / abs(mean_s))) if mean_s != 0 else 0
        else:
            stability = 1.0

        return {
            "success": True,
            "spec_id": spec.id,
            "spec_name": spec.name,
            "n_folds": n_folds,
            "successful_folds": n_successful,
            "failed_folds": n_folds - n_successful,
            "summary": {
                "avg_oos_sharpe": round(avg_oos_sharpe, 4),
                "min_oos_sharpe": round(min_oos_sharpe, 4),
                "max_oos_sharpe": round(max_oos_sharpe, 4),
                "consistency": round(consistency, 4),
                "walk_forward_efficiency": round(wfe, 4),
                "stability": round(stability, 4),
            },
            "assessment": self._assess(avg_oos_sharpe, consistency, wfe, stability),
            "folds": fold_results,
        }

    def _assess(
        self, avg_sharpe: float, consistency: float, wfe: float, stability: float
    ) -> dict[str, Any]:
        score = 0
        notes = []

        if avg_sharpe >= 2.0:
            score += 30
            notes.append("Excellent average OOS Sharpe (≥2.0)")
        elif avg_sharpe >= 1.0:
            score += 20
            notes.append("Good average OOS Sharpe (≥1.0)")
        elif avg_sharpe >= 0.5:
            score += 10
            notes.append("Marginal average OOS Sharpe (0.5–1.0)")
        else:
            notes.append("Poor average OOS Sharpe (<0.5)")

        if consistency >= 0.8:
            score += 25
            notes.append("High consistency (≥80% profitable folds)")
        elif consistency >= 0.6:
            score += 15
            notes.append("Moderate consistency (60-80% profitable folds)")
        else:
            notes.append("Low consistency (<60% profitable folds)")

        if wfe >= 0.7:
            score += 25
            notes.append("Strong walk-forward efficiency (≥0.70)")
        elif wfe >= 0.5:
            score += 15
            notes.append("Acceptable walk-forward efficiency (0.50-0.70)")
        else:
            notes.append("Weak walk-forward efficiency (<0.50) — possible overfitting")

        if stability >= 0.7:
            score += 20
            notes.append("Stable across folds (low variance)")
        elif stability >= 0.4:
            score += 10
            notes.append("Moderate stability across folds")
        else:
            notes.append("Unstable across folds (high variance)")

        if score >= 80:
            grade = "A"
            verdict = "Strategy shows strong robustness. Ready for deployment."
        elif score >= 60:
            grade = "B"
            verdict = "Strategy shows good robustness. Consider paper trading before live."
        elif score >= 40:
            grade = "C"
            verdict = "Strategy shows moderate robustness. Further optimization recommended."
        else:
            grade = "D"
            verdict = "Strategy shows poor robustness. Significant rework needed."

        return {
            "score": score,
            "max_score": 100,
            "grade": grade,
            "verdict": verdict,
            "notes": notes,
        }
