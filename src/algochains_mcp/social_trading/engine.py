"""SocialTradingEngine — leader/follower copy-trading with proportional scaling."""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger("algochains_mcp.social_trading")


class SocialTradingEngine:
    """Full social/copy-trading engine with leader registration, follower management, and scaling."""

    def __init__(self):
        self._leaders: dict[str, dict[str, Any]] = {}
        self._copy_relationships: dict[str, dict[str, Any]] = {}
        self._signals: list[dict[str, Any]] = []
        self._copy_positions: dict[str, list[dict[str, Any]]] = {}

    # ── Leader Management ────────────────────────────────────────

    async def become_leader(
        self,
        user_id: str,
        handle: str,
        track_record: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if user_id in self._leaders:
            return {"success": False, "error": f"User {user_id} is already a registered leader."}

        record = track_record or {}
        days = record.get("track_record_days", 0)
        trades = record.get("total_trades", 0)
        sharpe = record.get("sharpe", 0)
        max_dd = record.get("max_drawdown", 1.0)

        errors = []
        if days < 90:
            errors.append(f"Minimum 90-day track record required (have {days} days)")
        if trades < 50:
            errors.append(f"Minimum 50 trades required (have {trades})")
        if sharpe < 1.0:
            errors.append(f"Minimum Sharpe 1.0 required (have {sharpe:.2f})")
        if max_dd > 0.30:
            errors.append(f"Max drawdown must be ≤30% (have {max_dd:.1%})")

        if errors:
            return {
                "success": False,
                "error": "Leader registration requirements not met.",
                "requirements_failed": errors,
                "requirements": {
                    "min_track_record_days": 90,
                    "min_trades": 50,
                    "min_sharpe": 1.0,
                    "max_drawdown": 0.30,
                },
            }

        leader = {
            "user_id": user_id,
            "handle": handle,
            "verified": False,
            "ranking_score": 0.0,
            "sharpe_12m": sharpe,
            "sortino_12m": record.get("sortino", sharpe * 1.1),
            "max_drawdown_12m": max_dd,
            "consistency_pct": record.get("consistency", 0.6),
            "total_followers": 0,
            "total_aum": 0.0,
            "total_trades": trades,
            "registered_at": datetime.utcnow().isoformat(),
        }
        leader["ranking_score"] = self._compute_ranking(leader)
        self._leaders[user_id] = leader

        return {
            "success": True,
            "leader": leader,
            "next_steps": "Complete identity verification (KYC) to appear on the public leaderboard.",
        }

    async def get_leader_stats(self, leader_id: str) -> dict[str, Any]:
        leader = self._leaders.get(leader_id)
        if not leader:
            return {"success": False, "error": f"Leader '{leader_id}' not found."}

        followers = [
            r for r in self._copy_relationships.values()
            if r["leader_id"] == leader_id and r["status"] == "active"
        ]

        return {
            "success": True,
            "leader": leader,
            "active_followers": len(followers),
            "recent_signals": [
                s for s in self._signals[-20:]
                if s.get("leader_id") == leader_id
            ],
        }

    # ── Follower Management ──────────────────────────────────────

    async def follow_leader(
        self,
        follower_id: str,
        leader_id: str,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if leader_id not in self._leaders:
            return {"success": False, "error": f"Leader '{leader_id}' not found."}

        rel_key = f"{follower_id}_{leader_id}"
        if rel_key in self._copy_relationships:
            existing = self._copy_relationships[rel_key]
            if existing["status"] == "active":
                return {"success": False, "error": "Already following this leader."}

        copy_config = {
            "scaling_mode": "risk_parity",
            "scale_factor": 1.0,
            "max_risk_per_trade": 0.02,
            "max_drawdown_halt": 0.10,
            "allowed_assets": [],
            "excluded_symbols": [],
            "copy_stops": True,
            "slippage_tolerance": 0.005,
        }
        if config:
            copy_config.update(config)

        relationship = {
            "id": f"copy_{uuid.uuid4().hex[:12]}",
            "follower_id": follower_id,
            "leader_id": leader_id,
            "config": copy_config,
            "status": "active",
            "total_pnl": 0.0,
            "trades_copied": 0,
            "created_at": datetime.utcnow().isoformat(),
        }

        self._copy_relationships[rel_key] = relationship
        self._leaders[leader_id]["total_followers"] += 1

        return {
            "success": True,
            "relationship": relationship,
            "leader": {
                "handle": self._leaders[leader_id]["handle"],
                "ranking_score": self._leaders[leader_id]["ranking_score"],
                "sharpe_12m": self._leaders[leader_id]["sharpe_12m"],
            },
        }

    async def unfollow_leader(
        self,
        follower_id: str,
        leader_id: str,
        close_positions: bool = False,
    ) -> dict[str, Any]:
        rel_key = f"{follower_id}_{leader_id}"
        rel = self._copy_relationships.get(rel_key)
        if not rel or rel["status"] != "active":
            return {"success": False, "error": "No active copy relationship found."}

        rel["status"] = "stopped"
        rel["stopped_at"] = datetime.utcnow().isoformat()

        if leader_id in self._leaders:
            self._leaders[leader_id]["total_followers"] = max(
                0, self._leaders[leader_id]["total_followers"] - 1
            )

        positions_closed = 0
        if close_positions:
            key = f"{follower_id}_{leader_id}"
            if key in self._copy_positions:
                positions_closed = len(self._copy_positions[key])
                del self._copy_positions[key]

        return {
            "success": True,
            "relationship": rel,
            "positions_closed": positions_closed,
        }

    async def get_copy_status(self, follower_id: str) -> dict[str, Any]:
        relationships = [
            r for r in self._copy_relationships.values()
            if r["follower_id"] == follower_id
        ]

        active = [r for r in relationships if r["status"] == "active"]
        total_pnl = sum(r["total_pnl"] for r in relationships)

        return {
            "success": True,
            "follower_id": follower_id,
            "active_copies": len(active),
            "total_relationships": len(relationships),
            "total_pnl": round(total_pnl, 2),
            "relationships": relationships,
        }

    async def set_copy_parameters(
        self,
        follower_id: str,
        leader_id: str,
        config_updates: dict[str, Any],
    ) -> dict[str, Any]:
        rel_key = f"{follower_id}_{leader_id}"
        rel = self._copy_relationships.get(rel_key)
        if not rel or rel["status"] != "active":
            return {"success": False, "error": "No active copy relationship found."}

        valid_keys = {
            "scaling_mode", "scale_factor", "max_risk_per_trade",
            "max_drawdown_halt", "allowed_assets", "excluded_symbols",
            "copy_stops", "slippage_tolerance",
        }
        invalid = set(config_updates.keys()) - valid_keys
        if invalid:
            return {"success": False, "error": f"Invalid config keys: {invalid}"}

        rel["config"].update(config_updates)
        rel["updated_at"] = datetime.utcnow().isoformat()

        return {"success": True, "relationship": rel}

    # ── Ranking ──────────────────────────────────────────────────

    def _compute_ranking(self, leader: dict[str, Any]) -> float:
        import math
        sharpe = leader.get("sharpe_12m", 0)
        sortino = leader.get("sortino_12m", 0)
        max_dd = leader.get("max_drawdown_12m", 1.0)
        consistency = leader.get("consistency_pct", 0)
        trades = leader.get("total_trades", 0)
        aum = leader.get("total_aum", 0)

        trade_score = min(1.0, math.log1p(trades) / math.log1p(500))
        aum_score = min(1.0, math.log1p(aum) / math.log1p(1_000_000))

        score = (
            0.30 * max(0, sharpe)
            + 0.20 * max(0, sortino)
            + 0.15 * (1 - min(1.0, max_dd))
            + 0.15 * consistency
            + 0.10 * trade_score
            + 0.10 * aum_score
        )
        return round(score, 4)
