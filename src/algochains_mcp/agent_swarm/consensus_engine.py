"""Multi-agent consensus for trading decisions."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


class ConsensusEngine:
    """Multi-agent consensus for trading decisions."""

    def __init__(self) -> None:
        self._votes: dict[str, list[dict]] = {}

    async def propose(self, proposal: dict, required_votes: int = 3) -> dict:
        try:
            proposal_id = uuid.uuid4().hex[:12]
            self._votes[proposal_id] = []
            return {
                "status": "ok",
                "proposal_id": proposal_id,
                "proposal": proposal,
                "required_votes": required_votes,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def vote(self, proposal_id: str, agent_id: str, decision: str, confidence: float) -> dict:
        try:
            if proposal_id not in self._votes:
                return {"status": "error", "error": f"Proposal {proposal_id} not found"}
            vote = {"agent_id": agent_id, "decision": decision, "confidence": confidence, "voted_at": datetime.now(timezone.utc).isoformat()}
            self._votes[proposal_id].append(vote)
            return {"status": "ok", "vote": vote, "total_votes": len(self._votes[proposal_id])}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def get_result(self, proposal_id: str) -> dict:
        try:
            votes = self._votes.get(proposal_id)
            if votes is None:
                return {"status": "error", "error": f"Proposal {proposal_id} not found"}
            return {"status": "ok", "proposal_id": proposal_id, "votes": votes, "total": len(votes)}
        except Exception as e:
            return {"status": "error", "error": str(e)}
