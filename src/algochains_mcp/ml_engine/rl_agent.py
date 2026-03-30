"""Reinforcement learning trading agents (PPO/SAC)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


class RLAgentEngine:
    """Reinforcement learning trading agents (PPO/SAC)."""

    def __init__(self) -> None:
        self._agents: dict[str, dict] = {}

    async def create_agent(
        self,
        env_config: dict,
        algo: str = "ppo",
        reward: str = "sharpe",
        episodes: int = 1000,
    ) -> dict:
        try:
            if algo not in ("ppo", "sac"):
                return {"status": "error", "error": f"Invalid algo: {algo}. Must be 'ppo' or 'sac'"}
            if reward not in ("sharpe", "pnl", "sortino"):
                return {"status": "error", "error": f"Invalid reward: {reward}. Must be 'sharpe', 'pnl', or 'sortino'"}
            agent_id = uuid.uuid4().hex[:12]
            agent = {
                "id": agent_id,
                "env_config": env_config,
                "algo": algo,
                "reward_fn": reward,
                "episodes_target": episodes,
                "episodes_trained": 0,
                "best_reward": None,
                "metrics": None,
                "checkpoint_path": None,
                "stage": "dev",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._agents[agent_id] = agent
            return {"status": "ok", "agent": agent}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def train(
        self,
        agent_id: str,
        train_range: dict | None = None,
        episodes: int = 1000,
        checkpoint_every: int = 100,
    ) -> dict:
        try:
            agent = self._agents.get(agent_id)
            if not agent:
                return {"status": "error", "error": f"Agent {agent_id} not found"}
            agent["episodes_trained"] += episodes
            return {
                "status": "ok",
                "agent_id": agent_id,
                "episodes_trained": agent["episodes_trained"],
                "train_range": train_range,
                "checkpoint_every": checkpoint_every,
                "trained_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def evaluate(self, agent_id: str, eval_range: dict | None = None) -> dict:
        try:
            agent = self._agents.get(agent_id)
            if not agent:
                return {"status": "error", "error": f"Agent {agent_id} not found"}
            eval_metrics = {"sharpe": 0.0, "total_pnl": 0.0, "max_dd": 0.0, "win_rate": 0.0, "trades": 0}
            agent["metrics"] = eval_metrics
            return {
                "status": "ok",
                "agent_id": agent_id,
                "eval_range": eval_range,
                "metrics": eval_metrics,
                "evaluated_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def get_agent_state(self, agent_id: str) -> dict:
        try:
            agent = self._agents.get(agent_id)
            if not agent:
                return {"status": "error", "error": f"Agent {agent_id} not found"}
            return {"status": "ok", "agent": agent}
        except Exception as e:
            return {"status": "error", "error": str(e)}
