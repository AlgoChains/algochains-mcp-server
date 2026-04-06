"""
AlgoChains Self-Improving Trading Loop — v21.0

Implements the AlphaLoop 4-stage autonomous cycle:
  SCAN → MUTATE → VALIDATE → PROMOTE

Components:
  trade_memory.py    — Episodic trade memory (vector store)
  reward_model.py    — RL reward modeling from trade outcomes
  evolution_daemon.py — Autonomous background evolution loop
  lessons_injector.py — Inject lessons into agent session context
"""
from .trade_memory import TradeMemory, TradeEpisode, get_trade_memory
from .reward_model import RewardModel, RewardScore, get_reward_model
from .evolution_daemon import EvolutionDaemon, get_evolution_daemon
from .lessons_injector import LessonsInjector, get_lessons_injector

__all__ = [
    "TradeMemory", "TradeEpisode", "get_trade_memory",
    "RewardModel", "RewardScore", "get_reward_model",
    "EvolutionDaemon", "get_evolution_daemon",
    "LessonsInjector", "get_lessons_injector",
]
