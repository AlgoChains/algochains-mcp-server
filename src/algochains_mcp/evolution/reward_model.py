"""
RL Reward Modeling — compute rewards from trade outcomes and rank strategies.

Reward function:
  R = α·risk_adj_return + β·regime_alignment + γ·consistency - δ·drawdown_penalty

Where:
  risk_adj_return  = pnl_pct / max(volatility, 0.01)  — Sharpe-like per trade
  regime_alignment = 1 if trade direction matches regime, else -0.5
  consistency      = rolling win rate over last 30 trades
  drawdown_penalty = max consecutive losses * 0.1

Strategies accumulate reward over a rolling 30-trade window.
Auto-promotion: strategy_reward > PROMOTE_THRESHOLD → TIER1 candidate.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .trade_memory import TradeEpisode, get_trade_memory  # noqa: F401


@dataclass
class RewardScore:
    strategy_id: str
    reward: float
    components: dict[str, float]
    trade_count: int
    win_rate: float
    avg_pnl: float
    rank: int = 0
    promote_candidate: bool = False
    computed_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "reward": round(self.reward, 4),
            "components": {k: round(v, 4) for k, v in self.components.items()},
            "trade_count": self.trade_count,
            "win_rate": round(self.win_rate, 1),
            "avg_pnl": round(self.avg_pnl, 2),
            "rank": self.rank,
            "promote_candidate": self.promote_candidate,
            "computed_at": self.computed_at,
        }


class RewardModel:
    """
    RL reward modeling for trading strategy evaluation.

    Reads from TradeMemory, computes per-strategy reward scores,
    and maintains a ranking for auto-promotion decisions.
    """

    PROMOTE_THRESHOLD = 0.65    # reward score to become TIER1 candidate
    DEMOTE_THRESHOLD = 0.20     # reward score to flag for replacement
    ROLLING_WINDOW = 30         # trades per evaluation window
    REGIME_BONUS = 0.15         # bonus for trading with the regime
    REGIME_PENALTY = 0.10       # penalty for trading against regime

    # Weights
    ALPHA = 0.40   # risk-adjusted return weight
    BETA = 0.25    # regime alignment weight
    GAMMA = 0.25   # consistency weight
    DELTA = 0.10   # drawdown penalty weight

    def __init__(self) -> None:
        self._scores: dict[str, RewardScore] = {}

    def _risk_adj_return(self, episodes: list[TradeEpisode]) -> float:
        """Compute mean risk-adjusted return (Sharpe-like)."""
        if not episodes:
            return 0.0
        returns = [ep.pnl_pct for ep in episodes]
        mean_r = sum(returns) / len(returns)
        if len(returns) < 2:
            return max(0.0, mean_r / 100.0)
        variance = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
        std = math.sqrt(variance) if variance > 0 else 1.0
        sharpe = mean_r / std if std > 0 else 0.0
        return max(-1.0, min(1.0, sharpe / 3.0))  # normalize to [-1, 1]

    def _regime_alignment(self, episodes: list[TradeEpisode]) -> float:
        """Score for trading in alignment with the detected regime."""
        if not episodes:
            return 0.0
        aligned = sum(
            1 for ep in episodes
            if (ep.side == "long" and ep.market_regime in ("bull",))
            or (ep.side == "short" and ep.market_regime in ("bear",))
            or ep.market_regime in ("neutral",)  # neutral → no penalty
        )
        return aligned / len(episodes)

    def _consistency(self, episodes: list[TradeEpisode]) -> float:
        """Rolling win rate normalized to [0, 1]."""
        if not episodes:
            return 0.0
        wins = sum(1 for ep in episodes if ep.pnl > 0)
        return wins / len(episodes)

    def _drawdown_penalty(self, episodes: list[TradeEpisode]) -> float:
        """Penalty for consecutive losing streaks."""
        if not episodes:
            return 0.0
        sorted_eps = sorted(episodes, key=lambda e: e.timestamp)
        max_consec = 0
        current = 0
        for ep in sorted_eps:
            if ep.pnl < 0:
                current += 1
                max_consec = max(max_consec, current)
            else:
                current = 0
        return min(1.0, max_consec * 0.1)

    def compute_strategy_reward(self, strategy_id: str, window: int | None = None) -> RewardScore:
        """Compute reward score for a strategy from its trade history."""
        window = window or self.ROLLING_WINDOW
        mem = get_trade_memory()

        with sqlite3.connect(mem.DB_PATH) as conn:
            rows = conn.execute(
                "SELECT * FROM episodes WHERE strategy_id = ? ORDER BY timestamp DESC LIMIT ?",
                (strategy_id, window)
            ).fetchall()

        if not rows:
            return RewardScore(
                strategy_id=strategy_id,
                reward=0.0,
                components={"risk_adj_return": 0, "regime_alignment": 0, "consistency": 0, "drawdown_penalty": 0},
                trade_count=0,
                win_rate=0.0,
                avg_pnl=0.0,
            )

        from .trade_memory import TradeEpisode
        episodes = [TradeEpisode.from_row(r) for r in rows]

        rar = self._risk_adj_return(episodes)
        regime = self._regime_alignment(episodes)
        consist = self._consistency(episodes)
        dd = self._drawdown_penalty(episodes)

        reward = (
            self.ALPHA * rar
            + self.BETA * regime
            + self.GAMMA * consist
            - self.DELTA * dd
        )
        reward = max(0.0, min(1.0, reward))

        wins = sum(1 for ep in episodes if ep.pnl > 0)
        avg_pnl = sum(ep.pnl for ep in episodes) / len(episodes)

        score = RewardScore(
            strategy_id=strategy_id,
            reward=round(reward, 4),
            components={
                "risk_adj_return": round(rar, 4),
                "regime_alignment": round(regime, 4),
                "consistency": round(consist, 4),
                "drawdown_penalty": round(dd, 4),
            },
            trade_count=len(episodes),
            win_rate=round(wins / len(episodes) * 100, 1),
            avg_pnl=round(avg_pnl, 2),
            promote_candidate=reward >= self.PROMOTE_THRESHOLD,
        )
        self._scores[strategy_id] = score
        return score

    def get_strategy_rankings(self, recompute: bool = False) -> list[dict[str, Any]]:
        """Return ranked list of all strategies by reward score."""
        mem = get_trade_memory()
        with sqlite3.connect(mem.DB_PATH) as conn:
            strategy_ids = [r[0] for r in conn.execute(
                "SELECT DISTINCT strategy_id FROM episodes WHERE strategy_id != ''"
            ).fetchall()]

        if recompute or not self._scores:
            for sid in strategy_ids:
                self.compute_strategy_reward(sid)

        ranked = sorted(self._scores.values(), key=lambda s: s.reward, reverse=True)
        for i, score in enumerate(ranked):
            score.rank = i + 1

        return [s.to_dict() for s in ranked]

    def get_promote_candidates(self) -> list[str]:
        """Strategy IDs that meet the promotion threshold."""
        return [sid for sid, score in self._scores.items() if score.promote_candidate]

    def get_demote_candidates(self) -> list[str]:
        """Strategy IDs that fall below the demotion threshold."""
        return [sid for sid, score in self._scores.items() if score.reward < self.DEMOTE_THRESHOLD]

    def compute_portfolio_reward(self) -> dict[str, Any]:
        """Aggregate reward across all strategies."""
        rankings = self.get_strategy_rankings()
        if not rankings:
            return {"portfolio_reward": 0.0, "strategy_count": 0, "promote_candidates": [], "demote_candidates": []}
        avg_reward = sum(r["reward"] for r in rankings) / len(rankings)
        return {
            "portfolio_reward": round(avg_reward, 4),
            "strategy_count": len(rankings),
            "top_strategy": rankings[0]["strategy_id"] if rankings else None,
            "promote_candidates": self.get_promote_candidates(),
            "demote_candidates": self.get_demote_candidates(),
            "rankings": rankings[:10],
        }


_reward_model: RewardModel | None = None


def get_reward_model() -> RewardModel:
    global _reward_model
    if _reward_model is None:
        _reward_model = RewardModel()
    return _reward_model
