"""
V18 Constraint Solver — Intent + constraints → executable IntentPlan.

Takes a ParsedIntent and resolves it against real market data, compliance rules,
risk limits, and broker capabilities to produce a concrete execution plan.
"""

from __future__ import annotations

import logging
import uuid
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Any

from .intent_parser import ParsedIntent, IntentGoal

logger = logging.getLogger("algochains.constraint_solver")


class PlanStatus(str, Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepType(str, Enum):
    QUOTE = "quote"
    COMPLIANCE_CHECK = "compliance_check"
    RISK_CHECK = "risk_check"
    PLACE_ORDER = "place_order"
    CLOSE_POSITION = "close_position"
    MONITOR = "monitor"
    RESEARCH = "research"
    REBALANCE = "rebalance"
    HEDGE = "hedge"


@dataclass
class PlanStep:
    """A single executable step in an IntentPlan."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    seq: int = 0
    step_type: StepType = StepType.QUOTE
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    description: str = ""
    depends_on: list[str] = field(default_factory=list)
    status: str = "pending"
    result: Any = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "seq": self.seq,
            "step_type": self.step_type.value,
            "tool_name": self.tool_name,
            "tool_args": self.tool_args,
            "description": self.description,
            "depends_on": self.depends_on,
            "status": self.status,
            "error": self.error,
        }


@dataclass
class IntentPlan:
    """A complete execution plan generated from a ParsedIntent."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    intent_id: str = ""
    intent_raw: str = ""
    status: PlanStatus = PlanStatus.DRAFT
    steps: list[PlanStep] = field(default_factory=list)
    summary: str = ""
    estimated_cost: float = 0.0
    estimated_slippage_bps: float = 0.0
    risk_impact: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    broker: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "intent_id": self.intent_id,
            "intent_raw": self.intent_raw,
            "status": self.status.value,
            "steps": [s.to_dict() for s in self.steps],
            "step_count": len(self.steps),
            "summary": self.summary,
            "estimated_cost": self.estimated_cost,
            "estimated_slippage_bps": self.estimated_slippage_bps,
            "risk_impact": self.risk_impact,
            "broker": self.broker,
            "warnings": self.warnings,
        }


class ConstraintSolver:
    """Resolve a ParsedIntent into an executable IntentPlan.

    Uses broker registry for capability checks, compliance engine for
    pre-trade validation, and risk engine for VaR/concentration analysis.
    """

    def __init__(
        self,
        broker_registry=None,
        compliance_engine=None,
        risk_engine=None,
        quote_provider=None,
    ):
        self._brokers = broker_registry
        self._compliance = compliance_engine
        self._risk = risk_engine
        self._quotes = quote_provider
        self._plans: dict[str, IntentPlan] = {}

    async def solve(self, intent: ParsedIntent) -> IntentPlan:
        """Generate an executable plan from a parsed intent."""
        plan = IntentPlan(
            intent_id=intent.id,
            intent_raw=intent.raw_text,
            broker=intent.preferred_broker or self._select_broker(intent),
        )

        if intent.goal in (IntentGoal.BUY, IntentGoal.INCREASE_EXPOSURE):
            self._plan_buy(intent, plan)
        elif intent.goal in (IntentGoal.SELL, IntentGoal.REDUCE_EXPOSURE):
            self._plan_sell(intent, plan)
        elif intent.goal == IntentGoal.CLOSE:
            self._plan_close(intent, plan)
        elif intent.goal == IntentGoal.REBALANCE:
            self._plan_rebalance(intent, plan)
        elif intent.goal in (IntentGoal.PROTECT, IntentGoal.HEDGE):
            self._plan_hedge(intent, plan)
        elif intent.goal == IntentGoal.RESEARCH:
            self._plan_research(intent, plan)
        elif intent.goal == IntentGoal.MONITOR:
            self._plan_monitor(intent, plan)
        elif intent.goal == IntentGoal.ARBITRAGE:
            self._plan_arbitrage(intent, plan)
        elif intent.goal == IntentGoal.OPTIMIZE:
            self._plan_optimize(intent, plan)
        else:
            plan.warnings.append(f"Unsupported goal: {intent.goal.value}")
            self._plan_research(intent, plan)

        plan.summary = self._generate_summary(intent, plan)
        plan.status = PlanStatus.PENDING_APPROVAL

        self._plans[plan.id] = plan
        logger.info("Plan %s: %d steps, broker=%s", plan.id[:8], len(plan.steps), plan.broker)
        return plan

    def get_plan(self, plan_id: str) -> Optional[IntentPlan]:
        return self._plans.get(plan_id)

    def approve_plan(self, plan_id: str) -> Optional[IntentPlan]:
        plan = self._plans.get(plan_id)
        if plan and plan.status == PlanStatus.PENDING_APPROVAL:
            plan.status = PlanStatus.APPROVED
        return plan

    def cancel_plan(self, plan_id: str) -> Optional[IntentPlan]:
        plan = self._plans.get(plan_id)
        if plan and plan.status in (PlanStatus.DRAFT, PlanStatus.PENDING_APPROVAL):
            plan.status = PlanStatus.CANCELLED
        return plan

    def list_plans(self, limit: int = 20) -> list[dict]:
        plans = sorted(self._plans.values(), key=lambda p: p.created_at, reverse=True)
        return [p.to_dict() for p in plans[:limit]]

    # ── Plan builders ────────────────────────────────────────────

    def _plan_buy(self, intent: ParsedIntent, plan: IntentPlan) -> None:
        seq = 0

        if not intent.universe:
            plan.warnings.append("No specific assets identified; plan may need refinement")
            return

        per_asset = self._compute_per_asset_notional(intent)

        # Step 1: Compliance pre-check
        seq += 1
        plan.steps.append(PlanStep(
            seq=seq, step_type=StepType.COMPLIANCE_CHECK,
            tool_name="pre_trade_check",
            tool_args={"symbols": intent.universe, "side": "buy", "broker": plan.broker},
            description=f"Pre-trade compliance check for {len(intent.universe)} symbols",
        ))

        # Step 2: Get quotes for all symbols
        seq += 1
        quote_step = PlanStep(
            seq=seq, step_type=StepType.QUOTE,
            tool_name="get_quote",
            tool_args={"symbols": intent.universe, "broker": plan.broker},
            description=f"Get live quotes for {len(intent.universe)} symbols",
        )
        plan.steps.append(quote_step)

        # Step 3: Risk assessment
        seq += 1
        plan.steps.append(PlanStep(
            seq=seq, step_type=StepType.RISK_CHECK,
            tool_name="calculate_var",
            tool_args={"symbols": intent.universe, "notional": intent.notional},
            description="Assess portfolio risk impact (VaR, concentration)",
            depends_on=[quote_step.id],
        ))

        # Step 4+: Place orders for each symbol
        for symbol in intent.universe:
            seq += 1
            order_args = {
                "broker": plan.broker,
                "symbol": symbol,
                "side": "buy",
                "order_type": intent.order_type,
            }
            if per_asset:
                order_args["notional"] = round(per_asset, 2)
            elif intent.qty:
                order_args["qty"] = intent.qty / len(intent.universe)

            if intent.stop_loss_pct:
                order_args["stop_loss_pct"] = intent.stop_loss_pct
            if intent.take_profit_pct:
                order_args["take_profit_pct"] = intent.take_profit_pct

            plan.steps.append(PlanStep(
                seq=seq, step_type=StepType.PLACE_ORDER,
                tool_name="place_order",
                tool_args=order_args,
                description=f"Buy {symbol}: ${per_asset:,.0f}" if per_asset else f"Buy {symbol}",
            ))

        plan.estimated_slippage_bps = len(intent.universe) * 0.5

    def _plan_sell(self, intent: ParsedIntent, plan: IntentPlan) -> None:
        seq = 0
        for symbol in intent.universe:
            seq += 1
            plan.steps.append(PlanStep(
                seq=seq, step_type=StepType.PLACE_ORDER,
                tool_name="place_order",
                tool_args={
                    "broker": plan.broker, "symbol": symbol,
                    "side": "sell", "order_type": intent.order_type,
                    "qty": intent.qty / len(intent.universe) if intent.qty else None,
                },
                description=f"Sell {symbol}",
            ))

    def _plan_close(self, intent: ParsedIntent, plan: IntentPlan) -> None:
        seq = 0
        targets = intent.universe or ["_all"]
        for symbol in targets:
            seq += 1
            if symbol == "_all":
                plan.steps.append(PlanStep(
                    seq=seq, step_type=StepType.CLOSE_POSITION,
                    tool_name="close_all_positions",
                    tool_args={"broker": plan.broker},
                    description="Close all positions",
                ))
            else:
                plan.steps.append(PlanStep(
                    seq=seq, step_type=StepType.CLOSE_POSITION,
                    tool_name="close_position",
                    tool_args={"broker": plan.broker, "symbol": symbol},
                    description=f"Close {symbol} position",
                ))

    def _plan_rebalance(self, intent: ParsedIntent, plan: IntentPlan) -> None:
        seq = 0
        seq += 1
        plan.steps.append(PlanStep(
            seq=seq, step_type=StepType.QUOTE,
            tool_name="get_positions",
            tool_args={"broker": plan.broker},
            description="Get current portfolio positions for rebalancing",
        ))
        seq += 1
        plan.steps.append(PlanStep(
            seq=seq, step_type=StepType.REBALANCE,
            tool_name="_internal_rebalance",
            tool_args={
                "target_universe": intent.universe,
                "notional": intent.notional,
                "max_pct_per_asset": intent.max_pct_per_asset,
            },
            description="Compute rebalancing trades",
        ))

    def _plan_hedge(self, intent: ParsedIntent, plan: IntentPlan) -> None:
        seq = 0
        seq += 1
        plan.steps.append(PlanStep(
            seq=seq, step_type=StepType.QUOTE,
            tool_name="get_positions",
            tool_args={"broker": plan.broker},
            description="Get current positions to determine hedge needs",
        ))
        seq += 1
        plan.steps.append(PlanStep(
            seq=seq, step_type=StepType.HEDGE,
            tool_name="_internal_hedge",
            tool_args={
                "hedge_target": intent.universe_filter or "portfolio",
                "notional": intent.notional,
            },
            description="Calculate and execute protective hedge",
        ))

    def _plan_research(self, intent: ParsedIntent, plan: IntentPlan) -> None:
        seq = 0
        if intent.universe:
            for symbol in intent.universe[:5]:
                seq += 1
                plan.steps.append(PlanStep(
                    seq=seq, step_type=StepType.RESEARCH,
                    tool_name="get_quote",
                    tool_args={"broker": plan.broker or "alpaca", "symbol": symbol},
                    description=f"Research: get {symbol} quote",
                ))
        else:
            seq += 1
            plan.steps.append(PlanStep(
                seq=seq, step_type=StepType.RESEARCH,
                tool_name="massive_search_endpoints",
                tool_args={"query": intent.raw_text},
                description=f"Search for relevant data: {intent.raw_text[:60]}",
            ))

    def _plan_monitor(self, intent: ParsedIntent, plan: IntentPlan) -> None:
        seq = 0
        seq += 1
        plan.steps.append(PlanStep(
            seq=seq, step_type=StepType.MONITOR,
            tool_name="get_positions",
            tool_args={"broker": plan.broker or "alpaca"},
            description="Get current positions for monitoring",
        ))
        seq += 1
        plan.steps.append(PlanStep(
            seq=seq, step_type=StepType.MONITOR,
            tool_name="get_account",
            tool_args={"broker": plan.broker or "alpaca"},
            description="Get account summary",
        ))

    def _plan_arbitrage(self, intent: ParsedIntent, plan: IntentPlan) -> None:
        seq = 0
        seq += 1
        plan.steps.append(PlanStep(
            seq=seq, step_type=StepType.RESEARCH,
            tool_name="detect_arbitrage",
            tool_args={"symbols": intent.universe, "brokers": ["alpaca", "ibkr"]},
            description="Scan for cross-broker arbitrage opportunities",
        ))

    def _plan_optimize(self, intent: ParsedIntent, plan: IntentPlan) -> None:
        seq = 0
        seq += 1
        plan.steps.append(PlanStep(
            seq=seq, step_type=StepType.RESEARCH,
            tool_name="get_positions",
            tool_args={"broker": plan.broker or "alpaca"},
            description="Get current portfolio for optimization",
        ))
        seq += 1
        plan.steps.append(PlanStep(
            seq=seq, step_type=StepType.RESEARCH,
            tool_name="_internal_optimize",
            tool_args={"objective": "sharpe", "constraints": intent.raw_text},
            description="Optimize portfolio allocation",
        ))

    # ── Helpers ───────────────────────────────────────────────────

    def _select_broker(self, intent: ParsedIntent) -> str:
        """Select best broker based on intent asset class."""
        mapping = {
            "equities": "alpaca",
            "futures": "tradovate",
            "options": "alpaca",
            "forex": "oanda",
            "crypto": "alpaca",
        }
        return mapping.get(intent.asset_class, "alpaca")

    def _compute_per_asset_notional(self, intent: ParsedIntent) -> Optional[float]:
        if not intent.notional or not intent.universe:
            return None
        n = len(intent.universe)
        if intent.max_pct_per_asset:
            max_per = intent.notional * (intent.max_pct_per_asset / 100)
            equal = intent.notional / n
            return min(max_per, equal)
        return intent.notional / n

    def _generate_summary(self, intent: ParsedIntent, plan: IntentPlan) -> str:
        order_steps = [s for s in plan.steps if s.step_type == StepType.PLACE_ORDER]
        close_steps = [s for s in plan.steps if s.step_type == StepType.CLOSE_POSITION]

        parts = []
        if order_steps:
            side = order_steps[0].tool_args.get("side", "trade")
            parts.append(f"{side.upper()} {len(order_steps)} symbols on {plan.broker}")
            if intent.notional:
                parts.append(f"Total: ${intent.notional:,.0f}")
            if intent.max_pct_per_asset:
                parts.append(f"Max {intent.max_pct_per_asset}% per asset")
        elif close_steps:
            parts.append(f"Close {len(close_steps)} position(s) on {plan.broker}")
        else:
            parts.append(f"{intent.goal.value}: {len(plan.steps)} steps on {plan.broker}")

        if intent.stop_loss_pct:
            parts.append(f"Stop: {intent.stop_loss_pct}%")
        if intent.time_horizon != "now":
            parts.append(f"Timing: {intent.time_horizon}")

        return " | ".join(parts)
