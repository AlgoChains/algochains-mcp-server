"""Strategy Submission Pipeline — submit validated algos to the marketplace.

End-to-end flow:
1. Builder creates strategy + runs backtest
2. Pipeline validates through 7 gates
3. If approved, stages for marketplace listing
4. HMAC-signed signal propagation configured
5. Paper trading period (30 days)
6. Graduation to live marketplace listing

All submissions are metadata-only — source code is NEVER uploaded.
Signal payloads contain: direction, symbol, qty, entry, stop, target.
No strategy logic, parameters, or "why" is ever exposed.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("algochains_mcp.builder_sdk")


@dataclass
class StrategySubmission:
    """Metadata-only strategy submission (no source code)."""
    symbol: str
    strategy_type: str
    timeframe: str
    oos_sharpe: float
    oos_trades: int
    max_drawdown_pct: float

    is_sharpe: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    deflated_sharpe: float = 0.0

    mcpt_p_value: float = 1.0
    mcpt_permutations: int = 0
    wf_folds: int = 0
    wf_avg_oos_sharpe: float = 0.0
    wf_worst_fold: float = 0.0

    parameters: dict = field(default_factory=dict)
    description: str = ""
    submitter_id: str = ""
    asset_class: str = "stock"
    price_monthly: float = 29.99
    verification_artifact: dict = field(default_factory=dict)

    def validate(self) -> list[str]:
        errors = []
        if not self.symbol:
            errors.append("symbol required")
        if not self.strategy_type:
            errors.append("strategy_type required")
        if self.oos_sharpe <= 0:
            errors.append("oos_sharpe must be positive")
        if self.oos_trades < 1:
            errors.append("oos_trades must be at least 1")
        if self.max_drawdown_pct < 0 or self.max_drawdown_pct > 100:
            errors.append("max_drawdown_pct must be 0-100")
        return errors

    def to_listing_payload(self) -> dict:
        """Convert to marketplace listing format."""
        return {
            "strategy_title": f"{self.symbol} {self.strategy_type.replace('_', ' ').title()}",
            "symbol": self.symbol,
            "strategy_type": self.strategy_type,
            "timeframe": self.timeframe,
            "asset_class": self.asset_class,
            "oos_sharpe": self.oos_sharpe,
            "max_drawdown": self.max_drawdown_pct,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "total_trades": self.oos_trades,
            "price_monthly": self.price_monthly,
            "description": self.description,
            "mcpt_metadata": {
                "p_value": self.mcpt_p_value,
                "permutations": self.mcpt_permutations,
                "deflated_sharpe": self.deflated_sharpe,
                "walk_forward_folds": self.wf_folds,
            },
            "strategy_file": None,
            "artifact_verification": {
                "artifact_id": self.verification_artifact.get("artifact_id", ""),
                "sha256": self.verification_artifact.get("sha256", ""),
            },
        }


@dataclass
class SubmissionResult:
    """Result of strategy submission attempt."""
    submission_id: str = ""
    status: str = ""
    tier: str = ""
    score: float = 0.0
    passed: bool = False
    gate_results: dict = field(default_factory=dict)
    feedback: list[str] = field(default_factory=list)
    next_steps: str = ""
    dry_run: bool = True
    staged: bool = False

    def to_dict(self) -> dict:
        return self.__dict__


class SubmissionPipeline:
    """End-to-end strategy submission pipeline.

    Validates through marketplace gates and stages for listing.
    """

    def __init__(
        self,
        api_key: str | None = None,
        django_url: str | None = None,
        signal_secret: str | None = None,
    ):
        self.api_key = api_key or os.getenv("LISTING_API_KEY", "")
        self.django_url = django_url or os.getenv(
            "ALGOCHAINS_DJANGO_URL", "https://algochains.ai"
        )
        self.signal_secret = (signal_secret or os.getenv("SIGNAL_SECRET", "")).encode()

    async def submit(self, submission: StrategySubmission) -> SubmissionResult:
        """Submit a strategy for marketplace validation.

        Returns validation result with tier classification.
        """
        errors = submission.validate()
        if errors:
            return SubmissionResult(
                status="rejected",
                feedback=errors,
                next_steps="Fix validation errors and resubmit.",
            )

        submission_id = hashlib.sha256(
            f"{submission.symbol}:{submission.strategy_type}:{time.time()}".encode()
        ).hexdigest()[:16]

        result = self._validate_gates(submission)
        result.submission_id = f"sub_{submission_id}"

        if result.passed:
            key_ok = bool((self.api_key or "").strip())
            if not key_ok:
                # Tier-1 callers may validate submitted metrics, but without a
                # listing credential this must remain a side-effect-free dry run.
                result.status = "validated_dry_run"
                result.dry_run = True
                result.staged = False
                result.feedback.append(
                    "Validation passed in dry-run mode; no marketplace listing was staged."
                )
                result.next_steps = (
                    "An owner must provide LISTING_API_KEY plus a verified local "
                    "artifact before marketplace staging."
                )
            else:
                artifact_ok, artifact_error = self._verify_artifact(
                    submission.verification_artifact
                )
                if not artifact_ok:
                    result.passed = False
                    result.status = "staging_blocked"
                    result.dry_run = True
                    result.staged = False
                    result.feedback.append(artifact_error)
                    result.next_steps = (
                        "Provide a validation artifact under "
                        "ALGOCHAINS_VERIFIED_ARTIFACT_DIR with its exact SHA-256."
                    )
                elif await self._stage_listing(submission, result):
                    result.status = "staged"
                    result.dry_run = False
                    result.staged = True
                else:
                    result.passed = False
                    result.status = "staging_failed"
                    result.dry_run = False
                    result.staged = False
                    result.next_steps = (
                        "Resolve the listing API error, then resubmit the verified artifact."
                    )

        return result

    @staticmethod
    def _verify_artifact(artifact: dict) -> tuple[bool, str]:
        """Verify a staged listing against a local, allowlisted artifact."""
        if not isinstance(artifact, dict):
            return False, "Verified artifact is required before marketplace staging."

        configured_root = (os.getenv("ALGOCHAINS_VERIFIED_ARTIFACT_DIR") or "").strip()
        if not configured_root:
            return False, (
                "ALGOCHAINS_VERIFIED_ARTIFACT_DIR is not configured; "
                "marketplace staging fails closed."
            )

        raw_path = str(artifact.get("path") or "").strip()
        expected_sha = str(artifact.get("sha256") or "").strip().lower()
        if not raw_path or len(expected_sha) != 64:
            return False, "Artifact path and a 64-character SHA-256 are required."
        if any(ch not in "0123456789abcdef" for ch in expected_sha):
            return False, "Artifact SHA-256 must be lowercase hexadecimal."

        root = Path(configured_root).expanduser().resolve()
        path = Path(raw_path).expanduser().resolve()
        if not path.is_relative_to(root):
            return False, "Artifact path is outside ALGOCHAINS_VERIFIED_ARTIFACT_DIR."
        if not path.is_file() or path.stat().st_size <= 0:
            return False, "Verified artifact file is missing or empty."

        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        if not hmac.compare_digest(digest.hexdigest(), expected_sha):
            return False, "Artifact SHA-256 does not match the local file."

        return True, ""

    def _validate_gates(self, sub: StrategySubmission) -> SubmissionResult:
        """Run through 7 validation gates."""
        gates = {}
        feedback = []
        total_score = 0.0

        gates["schema"] = {"passed": True, "score": 100}
        total_score += 10

        perf_score = 0
        if sub.oos_sharpe >= 2.5:
            perf_score += 40
        elif sub.oos_sharpe >= 1.5:
            perf_score += 25
        elif sub.oos_sharpe >= 1.0:
            perf_score += 15
        else:
            feedback.append(f"OOS Sharpe {sub.oos_sharpe} below minimum 1.0")

        if sub.oos_trades >= 50:
            perf_score += 30
        else:
            feedback.append(f"Only {sub.oos_trades} trades (minimum 50)")

        if sub.max_drawdown_pct <= 25:
            perf_score += 30
        elif sub.max_drawdown_pct <= 40:
            perf_score += 15

        gates["performance"] = {
            "passed": perf_score >= 40,
            "score": perf_score,
        }
        total_score += perf_score * 0.3

        overfit_score = 100
        if sub.is_sharpe > 0:
            ratio = sub.oos_sharpe / sub.is_sharpe
            if ratio < 0.5:
                overfit_score = 25
                feedback.append(f"OOS/IS ratio {ratio:.2f} suggests overfitting")
            elif ratio < 0.7:
                overfit_score = 60
        if sub.is_sharpe > 8.0:
            overfit_score = 0
            feedback.append("IS Sharpe > 8.0 — suspiciously high, likely curve-fit")

        gates["overfitting"] = {"passed": overfit_score >= 50, "score": overfit_score}
        total_score += overfit_score * 0.2

        mcpt_score = 0
        if sub.mcpt_p_value <= 0.01:
            mcpt_score = 100
        elif sub.mcpt_p_value <= 0.05:
            mcpt_score = 75
        elif sub.mcpt_p_value < 1.0:
            mcpt_score = 25
            feedback.append(f"MCPT p-value {sub.mcpt_p_value} — not significant (need < 0.05)")

        gates["mcpt"] = {"passed": mcpt_score >= 50, "score": mcpt_score}
        total_score += mcpt_score * 0.2

        wf_score = 0
        if sub.wf_folds >= 5:
            wf_score = 80
        elif sub.wf_folds >= 3:
            wf_score = 50
        else:
            wf_score = 20
            if sub.wf_folds > 0:
                feedback.append(f"Only {sub.wf_folds} WF folds (recommend 5+)")

        if sub.wf_worst_fold >= 1.0:
            wf_score += 20
        elif sub.wf_worst_fold >= 0.5:
            wf_score += 10

        gates["walk_forward"] = {"passed": wf_score >= 40, "score": min(100, wf_score)}
        total_score += min(100, wf_score) * 0.1

        gates["paper_trading"] = {
            "passed": False,
            "score": 0,
            "status": "queued",
            "note": "30-day paper trading required before live listing",
        }

        gates["decay_monitor"] = {
            "passed": True,
            "score": 100,
            "note": "Active after listing",
        }

        passed = all(
            g.get("passed", False) for name, g in gates.items()
            if name not in ("paper_trading", "decay_monitor")
        )

        if total_score >= 85:
            tier = "platinum"
        elif total_score >= 70:
            tier = "gold"
        elif total_score >= 55:
            tier = "silver"
        elif total_score >= 40:
            tier = "bronze"
        else:
            tier = "rejected"
            passed = False

        if passed:
            feedback.append(f"Strategy approved as {tier.upper()} tier (score: {total_score:.0f})")
            next_steps = (
                "Strategy queued for 30-day paper trading. "
                "You will be notified when paper trading completes. "
                "Estimated listing date: ~30 days from now."
            )
        else:
            next_steps = (
                "Strategy did not pass all gates. "
                "Review feedback above and resubmit with improvements."
            )

        return SubmissionResult(
            status="approved" if passed else "rejected",
            tier=tier,
            score=round(total_score, 1),
            passed=passed,
            gate_results=gates,
            feedback=feedback,
            next_steps=next_steps,
        )

    async def _stage_listing(
        self, sub: StrategySubmission, result: SubmissionResult
    ) -> bool:
        """Stage a validated strategy for marketplace listing."""
        payload = sub.to_listing_payload()
        payload["tier"] = result.tier
        payload["validation_score"] = result.score
        payload["submission_id"] = result.submission_id

        try:
            import httpx
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{self.django_url}/api/v1/listings/create/",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                )
                if resp.status_code in (200, 201):
                    result.feedback.append("Listing staged on marketplace")
                    return True
                elif resp.status_code == 404:
                    result.feedback.append(
                        "Listing API not available (404); nothing was staged."
                    )
                else:
                    result.feedback.append(
                        f"Listing staging returned {resp.status_code}; nothing was staged."
                    )
        except Exception as e:
            logger.warning("Could not stage listing: %s", e)
            result.feedback.append("Listing API unavailable; nothing was staged.")
        return False

    def create_signal_signature(self, signal_data: dict) -> str:
        """Create HMAC-SHA256 signature for marketplace signal propagation."""
        if not self.signal_secret:
            return ""
        payload = json.dumps(signal_data, sort_keys=True).encode()
        return hmac.new(self.signal_secret, payload, hashlib.sha256).hexdigest()

    def get_submission_guide(self) -> dict:
        """Return step-by-step guide for strategy submission."""
        return {
            "steps": [
                {
                    "step": 1,
                    "title": "Build Your Strategy",
                    "description": "Use Backtrader, custom Python, or any framework. "
                                   "AlgoChains never sees your source code.",
                },
                {
                    "step": 2,
                    "title": "Run Backtest",
                    "description": "Use data_warehouse.query() for data, then run_backtest(). "
                                   "Ensure OOS Sharpe >= 1.0, trades >= 50, max DD <= 40%.",
                },
                {
                    "step": 3,
                    "title": "Run MCPT Validation",
                    "description": "1000+ permutations, p-value < 0.05. "
                                   "Use validate_strategy tool or local Rust engine.",
                },
                {
                    "step": 4,
                    "title": "Walk-Forward Test",
                    "description": "5+ folds, ensure temporal stability across all folds.",
                },
                {
                    "step": 5,
                    "title": "Submit via MCP",
                    "description": "Call submit_to_marketplace tool with your metrics. "
                                   "Strategy passes through 7-gate validation pipeline.",
                },
                {
                    "step": 6,
                    "title": "Paper Trading (30 days)",
                    "description": "Strategy runs on paper account. Must maintain "
                                   "Sharpe >= 0.5 and complete >= 50 trades.",
                },
                {
                    "step": 7,
                    "title": "Live on Marketplace",
                    "description": "Subscribers can auto-deploy your strategy. "
                                   "You earn 70% of subscription revenue.",
                },
            ],
            "ip_protection": {
                "source_code": "NEVER uploaded or exposed",
                "parameters": "Summary only visible to Builder tier",
                "signals": "Direction, symbol, qty, entry, stop, target only",
                "reverse_engineering": "Prohibited by Terms of Service",
            },
            "pricing_guide": {
                "platinum": "$50-100/mo (Sharpe 2.5+, trades 200+)",
                "gold": "$30-50/mo (Sharpe 2.0+, trades 100+)",
                "silver": "$15-30/mo (Sharpe 1.5+, trades 50+)",
                "bronze": "$5-15/mo (Sharpe 1.0+, trades 50+)",
            },
            "revenue_split": "70% creator / 30% AlgoChains",
        }
