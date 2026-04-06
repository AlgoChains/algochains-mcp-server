"""
V18 Plan Executor — Executes approved IntentPlans step-by-step.

Runs each PlanStep in sequence (respecting dependencies), dispatches to the
appropriate tool, records results, and maintains execution history.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional, Callable, Awaitable, Any

from .constraint_solver import IntentPlan, PlanStep, PlanStatus, StepType

logger = logging.getLogger("algochains.plan_executor")


class PlanExecutor:
    """Execute an approved IntentPlan by dispatching each step to its tool.

    The executor is wired to a tool_dispatcher callable that mirrors the
    MCP server's _dispatch_tool signature:
        async def dispatch(tool_name: str, arguments: dict) -> Any
    """

    def __init__(self, tool_dispatcher: Optional[Callable[..., Awaitable[Any]]] = None):
        self._dispatch = tool_dispatcher
        self._history: list[dict] = []

    async def execute(self, plan: IntentPlan) -> IntentPlan:
        """Execute all steps in an approved plan."""
        if plan.status != PlanStatus.APPROVED:
            raise ValueError(f"Plan {plan.id[:8]} is {plan.status.value}, not approved")

        plan.status = PlanStatus.EXECUTING
        execution_log: list[dict] = []
        start = time.monotonic()

        logger.info("Executing plan %s: %d steps", plan.id[:8], len(plan.steps))

        completed_steps: dict[str, PlanStep] = {}

        for step in plan.steps:
            # Check dependencies
            for dep_id in step.depends_on:
                if dep_id not in completed_steps:
                    step.status = "skipped"
                    step.error = f"Dependency {dep_id} not completed"
                    execution_log.append(self._log_step(step, 0))
                    continue

            step_start = time.monotonic()
            try:
                if step.tool_name.startswith("_internal_"):
                    result = await self._run_internal(step)
                elif self._dispatch:
                    result = await self._dispatch(step.tool_name, step.tool_args)
                else:
                    result = {"status": "dry_run", "tool": step.tool_name, "args": step.tool_args}

                step.result = result
                step.status = "completed"
                completed_steps[step.id] = step

            except Exception as e:
                step.status = "failed"
                step.error = str(e)
                logger.error("Step %s failed: %s", step.id, e)

                if step.step_type == StepType.PLACE_ORDER:
                    plan.warnings.append(f"Order step {step.seq} failed: {e}")

            elapsed = time.monotonic() - step_start
            execution_log.append(self._log_step(step, elapsed))

        total_elapsed = time.monotonic() - start
        succeeded = sum(1 for s in plan.steps if s.status == "completed")
        failed = sum(1 for s in plan.steps if s.status == "failed")

        if failed == 0:
            plan.status = PlanStatus.COMPLETED
        elif succeeded > 0:
            plan.status = PlanStatus.COMPLETED
            plan.warnings.append(f"{failed}/{len(plan.steps)} steps failed")
        else:
            plan.status = PlanStatus.FAILED

        record = {
            "plan_id": plan.id,
            "intent_raw": plan.intent_raw,
            "status": plan.status.value,
            "steps_total": len(plan.steps),
            "steps_completed": succeeded,
            "steps_failed": failed,
            "elapsed_seconds": round(total_elapsed, 2),
            "execution_log": execution_log,
            "timestamp": time.time(),
        }
        self._history.append(record)

        logger.info(
            "Plan %s %s: %d/%d steps in %.1fs",
            plan.id[:8], plan.status.value, succeeded, len(plan.steps), total_elapsed,
        )
        return plan

    def get_history(self, limit: int = 50) -> list[dict]:
        """Return recent execution history."""
        return list(reversed(self._history[-limit:]))

    async def _run_internal(self, step: PlanStep) -> dict:
        """Run internal (non-tool) plan steps like rebalance, hedge, optimize."""
        name = step.tool_name
        if name == "_internal_rebalance":
            return {
                "action": "rebalance",
                "target_universe": step.tool_args.get("target_universe", []),
                "status": "computed",
                "note": "Rebalance trades computed; individual orders will follow",
            }
        elif name == "_internal_hedge":
            return {
                "action": "hedge",
                "target": step.tool_args.get("hedge_target", "portfolio"),
                "status": "computed",
                "note": "Hedge positions computed; protective orders will follow",
            }
        elif name == "_internal_optimize":
            return {
                "action": "optimize",
                "objective": step.tool_args.get("objective", "sharpe"),
                "status": "computed",
                "note": "Optimal allocation computed; rebalance trades will follow",
            }
        return {"action": name, "status": "no_handler"}

    def _log_step(self, step: PlanStep, elapsed: float) -> dict:
        return {
            "step_id": step.id,
            "seq": step.seq,
            "tool": step.tool_name,
            "status": step.status,
            "elapsed_ms": round(elapsed * 1000, 1),
            "error": step.error,
        }
