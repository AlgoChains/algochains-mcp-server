"""V8: Strategy Builder SDK — AI-native declarative strategy specification, backtest, optimize, deploy."""

from .spec import StrategySpec, StrategySpecValidator
from .backtest_runner import BacktestRunner
from .optimizer import StrategyOptimizer
from .walk_forward import WalkForwardEngine
from .deployer import StrategyDeployer
from .template_manager import TemplateManager

__all__ = [
    "StrategySpec",
    "StrategySpecValidator",
    "BacktestRunner",
    "StrategyOptimizer",
    "WalkForwardEngine",
    "StrategyDeployer",
    "TemplateManager",
]
