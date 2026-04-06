"""
V18: Intent-Based Trading + Autonomous Intelligence Engine

Stop commanding. Start intending.

Before: "Place a market order to buy 100 AAPL on Alpaca"
After:  "Get me $10K AI exposure, max 2% per stock"

Components:
- IntentParser: LLM-powered natural language → structured intent
- ConstraintSolver: Intent + constraints → executable plan
- PlanExecutor: Plan → multi-step execution with approval gate
- ShadowPortfolioEngine: Forward-test strategies without capital risk
- StrategyEvolutionEngine: Genetic crossover of top Strategy DNA
- ArbitrageDetector: Cross-broker price/spread arbitrage
- PredictiveStatePrefetch: Predict needed state, prefetch in parallel
- RegimeDetector: Auto-detect market regime for strategy selection
"""

from .intent_parser import IntentParser, ParsedIntent
from .constraint_solver import ConstraintSolver, IntentPlan, PlanStep
from .plan_executor import PlanExecutor
from .shadow_portfolio import ShadowPortfolioEngine
from .strategy_evolution import StrategyEvolutionEngine
from .arbitrage_detector import ArbitrageDetector
from .predictive_prefetch import PredictiveStatePrefetch
from .regime_detector import RegimeDetector

__all__ = [
    "IntentParser",
    "ParsedIntent",
    "ConstraintSolver",
    "IntentPlan",
    "PlanStep",
    "PlanExecutor",
    "ShadowPortfolioEngine",
    "StrategyEvolutionEngine",
    "ArbitrageDetector",
    "PredictiveStatePrefetch",
    "RegimeDetector",
]
