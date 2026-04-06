"""
Lessons Learned Injector — inject relevant trade lessons into agent sessions.

At agent session start, auto-appends the top-N lessons for the current
market regime to SERVER_INSTRUCTIONS, giving each agent session awareness
of what has worked and failed in similar conditions.

Example injected context:
  REGIME=volatile: In the last 3 volatile regimes, momentum strategies
  underperformed 73% of the time. Reduce position size by 30%.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .trade_memory import get_trade_memory
from .reward_model import get_reward_model


@dataclass
class SessionContext:
    session_id: str
    regime: str
    symbol: str | None
    injected_lessons: list[str]
    top_strategies: list[str]
    injected_at: float = field(default_factory=time.time)

    def to_injection_text(self) -> str:
        lines = [
            f"\n\n--- AlgoChains Episodic Memory Context ---",
            f"Current regime: {self.regime.upper()}",
        ]
        if self.injected_lessons:
            lines.append(f"\nLessons from {self.regime} regime trades:")
            for i, lesson in enumerate(self.injected_lessons, 1):
                lines.append(f"  {i}. {lesson}")
        if self.top_strategies:
            lines.append(f"\nTop-performing strategies in this regime: {', '.join(self.top_strategies)}")
        lines.append("--- End Memory Context ---\n")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "regime": self.regime,
            "symbol": self.symbol,
            "lessons_count": len(self.injected_lessons),
            "injected_lessons": self.injected_lessons,
            "top_strategies": self.top_strategies,
            "injected_at": self.injected_at,
        }


class LessonsInjector:
    """
    Injects regime-specific lessons from episodic trade memory into
    agent sessions.

    Usage:
        injector = get_lessons_injector()
        ctx = injector.build_session_context(regime="volatile", symbol="SPY")
        extra_instructions = ctx.to_injection_text()
        # Prepend to SERVER_INSTRUCTIONS or include in prompt
    """

    MAX_LESSONS = 5
    MAX_STRATEGIES = 3

    def build_session_context(
        self,
        regime: str,
        symbol: str | None = None,
        session_id: str | None = None,
    ) -> SessionContext:
        """Build the context to inject at session start."""
        import uuid as _uuid
        session_id = session_id or str(_uuid.uuid4())

        mem = get_trade_memory()
        rm = get_reward_model()

        # Get regime-specific lessons
        lessons = mem.get_lessons(regime=regime, symbol=symbol, limit=self.MAX_LESSONS)

        # Get top strategies for this regime
        try:
            rankings = rm.get_strategy_rankings()
            # Filter by performance in this regime (uses trade memory indirectly)
            top_strategies = [r["strategy_id"] for r in rankings[:self.MAX_STRATEGIES]]
        except Exception:
            top_strategies = []

        return SessionContext(
            session_id=session_id,
            regime=regime,
            symbol=symbol,
            injected_lessons=lessons,
            top_strategies=top_strategies,
        )

    def get_regime_summary(self, regime: str) -> dict[str, Any]:
        """Summarize historical performance in a given regime."""
        mem = get_trade_memory()
        perf = mem.performance_by_regime()
        regime_data = perf.get(regime, {})
        lessons = mem.get_lessons(regime=regime, limit=10)
        return {
            "regime": regime,
            "performance": regime_data,
            "top_lessons": lessons,
            "recommendation": self._generate_recommendation(regime, regime_data),
        }

    def _generate_recommendation(self, regime: str, perf: dict) -> str:
        if not perf:
            return f"No historical data for {regime} regime yet."
        win_rate = perf.get("win_rate", 0)
        avg_pnl = perf.get("avg_pnl", 0)
        if win_rate >= 60 and avg_pnl > 0:
            return f"Historically strong in {regime} regime. Maintain normal position sizing."
        elif win_rate < 45:
            return f"Historically weak in {regime} regime. Reduce position size by 30-50%."
        else:
            return f"Mixed performance in {regime} regime. Apply conservative position sizing."

    def inject_into_instructions(self, base_instructions: str, regime: str, symbol: str | None = None) -> str:
        """Append lessons context to existing server instructions."""
        ctx = self.build_session_context(regime=regime, symbol=symbol)
        return base_instructions + ctx.to_injection_text()

    def list_all_lessons(self) -> dict[str, list[str]]:
        """All lessons organized by regime."""
        mem = get_trade_memory()
        regimes = ["bull", "bear", "neutral", "volatile", "ranging"]
        return {
            regime: mem.get_lessons(regime=regime, limit=10)
            for regime in regimes
        }

    def add_lesson_from_trade(self, episode_id: str, lesson: str) -> dict[str, Any]:
        """Add a lesson to a specific trade episode."""
        mem = get_trade_memory()
        ok = mem.add_lesson(episode_id=episode_id, lesson=lesson)
        return {
            "success": ok,
            "episode_id": episode_id,
            "lesson": lesson,
        }


_lessons_injector: LessonsInjector | None = None


def get_lessons_injector() -> LessonsInjector:
    global _lessons_injector
    if _lessons_injector is None:
        _lessons_injector = LessonsInjector()
    return _lessons_injector
