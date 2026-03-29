"""
Strategy validation gates — MCPT-grade quality enforcement.

This is the gatekeeper. External AI agents submit strategies here,
and only validated ones get through to the marketplace.

Gate hierarchy:
  Gate 1: Schema validation (required fields, types)
  Gate 2: Performance thresholds (Sharpe, trades, drawdown)
  Gate 3: Overfitting detection (IS/OOS ratio, suspiciously high Sharpe)
  Gate 4: MCPT statistical validation (permutation test p-value)
  Gate 5: Walk-forward consistency (if WF data provided)
  Gate 6: Paper trading graduation (30-day live paper requirement)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from ..config import GatingConfig

logger = logging.getLogger("algochains_mcp.marketplace.validator")


@dataclass
class ValidationResult:
    passed: bool
    gate_results: dict[str, dict] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    score: float = 0.0  # 0-100 composite quality score
    tier: str = "rejected"  # rejected | bronze | silver | gold | platinum

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "gate_results": self.gate_results,
            "errors": self.errors,
            "warnings": self.warnings,
            "score": self.score,
            "tier": self.tier,
        }


class StrategyValidator:
    """Multi-gate strategy validator for external AI submissions."""

    def __init__(self, config: GatingConfig):
        self.cfg = config

    def validate(self, submission: dict) -> ValidationResult:
        result = ValidationResult(passed=True)

        # Gate 1: Schema
        g1 = self._gate_schema(submission)
        result.gate_results["schema"] = g1
        if not g1["passed"]:
            result.passed = False
            result.errors.extend(g1.get("errors", []))
            return result

        # Gate 2: Performance thresholds
        g2 = self._gate_performance(submission)
        result.gate_results["performance"] = g2
        if not g2["passed"]:
            result.passed = False
            result.errors.extend(g2.get("errors", []))

        # Gate 3: Overfitting detection
        g3 = self._gate_overfitting(submission)
        result.gate_results["overfitting"] = g3
        if not g3["passed"]:
            result.passed = False
            result.errors.extend(g3.get("errors", []))

        # Gate 4: MCPT statistical validation
        g4 = self._gate_mcpt(submission)
        result.gate_results["mcpt"] = g4
        if not g4["passed"]:
            result.warnings.extend(g4.get("warnings", []))

        # Gate 5: Walk-forward consistency
        g5 = self._gate_walk_forward(submission)
        result.gate_results["walk_forward"] = g5
        if not g5["passed"] and self.cfg.require_walk_forward:
            result.passed = False
            result.errors.extend(g5.get("errors", []))

        # Gate 6: Paper trading graduation
        g6 = self._gate_paper_trading(submission)
        result.gate_results["paper_trading"] = g6
        if not g6["passed"]:
            result.warnings.append("Paper trading graduation pending")

        # Compute composite score
        result.score = self._compute_score(result)
        result.tier = self._classify_tier(result.score, result.passed)

        return result

    def _gate_schema(self, s: dict) -> dict:
        required = [
            "symbol", "strategy_type", "timeframe",
            "oos_sharpe", "oos_trades", "max_drawdown_pct",
        ]
        missing = [f for f in required if f not in s]
        if missing:
            return {"passed": False, "errors": [f"Missing fields: {missing}"]}

        if not isinstance(s.get("symbol"), str) or len(s["symbol"]) < 1:
            return {"passed": False, "errors": ["Invalid symbol"]}

        return {"passed": True}

    def _gate_performance(self, s: dict) -> dict:
        errors = []
        oos_sharpe = float(s.get("oos_sharpe", 0))
        oos_trades = int(s.get("oos_trades", 0))
        max_dd = float(s.get("max_drawdown_pct", 100))

        if oos_sharpe < self.cfg.min_oos_sharpe:
            errors.append(
                f"OOS Sharpe {oos_sharpe:.2f} < minimum {self.cfg.min_oos_sharpe}"
            )
        if oos_trades < self.cfg.min_oos_trades:
            errors.append(
                f"OOS trades {oos_trades} < minimum {self.cfg.min_oos_trades}"
            )
        if max_dd > self.cfg.max_drawdown_pct:
            errors.append(
                f"Max drawdown {max_dd:.1f}% > maximum {self.cfg.max_drawdown_pct}%"
            )

        return {"passed": len(errors) == 0, "errors": errors}

    def _gate_overfitting(self, s: dict) -> dict:
        errors = []
        warnings = []
        is_sharpe = float(s.get("is_sharpe", 0))
        oos_sharpe = float(s.get("oos_sharpe", 0))

        if is_sharpe > self.cfg.max_is_sharpe:
            errors.append(
                f"IS Sharpe {is_sharpe:.2f} suspiciously high (>{self.cfg.max_is_sharpe}) — likely overfit"
            )

        if is_sharpe > 0 and oos_sharpe > 0:
            ratio = oos_sharpe / is_sharpe
            if ratio < self.cfg.min_oos_is_ratio:
                errors.append(
                    f"OOS/IS ratio {ratio:.2f} < {self.cfg.min_oos_is_ratio} — performance decay too steep"
                )
            elif ratio < 0.7:
                warnings.append(f"OOS/IS ratio {ratio:.2f} indicates moderate overfitting")

        deflated = float(s.get("deflated_sharpe", 0))
        if deflated > 0 and deflated < 0.5:
            warnings.append(f"Deflated Sharpe Ratio {deflated:.2f} is weak after multiple testing correction")

        return {"passed": len(errors) == 0, "errors": errors, "warnings": warnings}

    def _gate_mcpt(self, s: dict) -> dict:
        mcpt = s.get("mcpt", {})
        if not mcpt:
            return {"passed": False, "warnings": ["No MCPT data provided — cannot verify statistical significance"]}

        p_value = mcpt.get("p_value")
        perms = mcpt.get("permutations", 0)

        if p_value is None:
            return {"passed": False, "warnings": ["MCPT p-value missing"]}

        errors = []
        warnings = []

        if p_value > self.cfg.mcpt_max_p_value:
            errors.append(
                f"MCPT p-value {p_value:.4f} > {self.cfg.mcpt_max_p_value} — not statistically significant"
            )

        if perms < self.cfg.mcpt_permutations:
            warnings.append(
                f"Only {perms} permutations (recommend {self.cfg.mcpt_permutations}+)"
            )

        return {"passed": len(errors) == 0, "errors": errors, "warnings": warnings}

    def _gate_walk_forward(self, s: dict) -> dict:
        wf = s.get("walk_forward", {})
        if not wf:
            if self.cfg.require_walk_forward:
                return {"passed": False, "errors": ["Walk-forward validation data required but not provided"]}
            return {"passed": True, "warnings": ["No walk-forward data — single-split only"]}

        folds = wf.get("folds", 0)
        if folds < 3:
            return {"passed": False, "errors": [f"Only {folds} WF folds (minimum 3)"]}

        return {"passed": True}

    def _gate_paper_trading(self, s: dict) -> dict:
        paper = s.get("paper_trading", {})
        if not paper:
            return {"passed": False, "warnings": ["No paper trading data yet"]}

        days = paper.get("days", 0)
        trades = paper.get("trades", 0)

        if days < self.cfg.min_paper_days:
            return {"passed": False, "warnings": [f"Paper: {days}/{self.cfg.min_paper_days} days"]}
        if trades < self.cfg.min_paper_trades:
            return {"passed": False, "warnings": [f"Paper: {trades}/{self.cfg.min_paper_trades} trades"]}

        return {"passed": True}

    def _compute_score(self, result: ValidationResult) -> float:
        score = 0.0
        weights = {
            "schema": 10,
            "performance": 30,
            "overfitting": 20,
            "mcpt": 20,
            "walk_forward": 10,
            "paper_trading": 10,
        }
        for gate, weight in weights.items():
            if result.gate_results.get(gate, {}).get("passed"):
                score += weight
        return score

    def _classify_tier(self, score: float, passed: bool) -> str:
        if not passed:
            return "rejected"
        if score >= 90:
            return "platinum"
        if score >= 70:
            return "gold"
        if score >= 50:
            return "silver"
        if score >= 30:
            return "bronze"
        return "rejected"
