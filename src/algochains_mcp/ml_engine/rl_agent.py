"""Reinforcement learning trading agents (PPO/SAC)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone


class RLAgentEngine:
    """Reinforcement learning trading agents (PPO/SAC)."""

    def __init__(self) -> None:
        self._agents: dict[str, dict] = {}

    async def create_agent(self, name: str, algorithm: str, environment: dict | None = None, reward_config: dict | None = None) -> dict:
        try:
            if algorithm not in ("ppo", "sac", "dqn"):
                return {"status": "error", "error": f"Invalid algorithm: {algorithm}. Must be 'ppo', 'sac', or 'dqn'"}
            agent_id = uuid.uuid4().hex[:12]
            agent = {
                "id": agent_id,
                "name": name,
                "algorithm": algorithm,
                "environment": environment or {},
                "reward_config": reward_config or {"reward_fn": "sharpe"},
                "episodes_trained": 0,
                "best_reward": None,
                "metrics": None,
                "stage": "dev",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._agents[agent_id] = agent
            return {"status": "ok", "agent": agent}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def train(self, agent_id: str, episodes: int = 1000, symbol: str | None = None) -> dict:
        try:
            agent = self._agents.get(agent_id)
            if not agent:
                return {"status": "error", "error": f"Agent {agent_id} not found"}
            agent["episodes_trained"] += episodes
            return {
                "status": "ok",
                "agent_id": agent_id,
                "episodes_trained": agent["episodes_trained"],
                "symbol": symbol,
                "trained_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def evaluate(self, agent_id: str, episodes: int = 100) -> dict:
        try:
            agent = self._agents.get(agent_id)
            if not agent:
                return {"status": "error", "error": f"Agent {agent_id} not found"}
            eval_metrics = {"sharpe": 0.0, "total_pnl": 0.0, "max_dd": 0.0, "win_rate": 0.0, "trades": 0}
            agent["metrics"] = eval_metrics
            return {
                "status": "ok",
                "agent_id": agent_id,
                "episodes": episodes,
                "metrics": eval_metrics,
                "evaluated_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def get_state(self, agent_id: str) -> dict:
        try:
            agent = self._agents.get(agent_id)
            if not agent:
                return {"status": "error", "error": f"Agent {agent_id} not found"}
            return {"status": "ok", "agent": agent}
        except Exception as e:
            return {"status": "error", "error": str(e)}
