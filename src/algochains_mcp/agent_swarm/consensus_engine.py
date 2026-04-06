"""Multi-agent consensus for trading decisions."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


class ConsensusEngine:
    """Multi-agent consensus for trading decisions."""

    def __init__(self) -> None:
        self._proposals: dict[str, dict] = {}
        self._history: list[dict] = []

    async def request(self, proposal: dict, agent_ids: list[str] | None = None, method: str = "majority") -> dict:
        try:
            proposal_id = uuid.uuid4().hex[:12]
            record = {
                "id": proposal_id,
                "proposal": proposal,
                "agent_ids": agent_ids or [],
                "method": method,
                "votes": [],
                "status": "pending",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._proposals[proposal_id] = record
            self._history.append(record)
            return {"status": "ok", "consensus_request": record}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def get_result(self, proposal_id: str) -> dict:
        try:
            record = self._proposals.get(proposal_id)
            if not record:
                return {"status": "error", "error": f"Proposal {proposal_id} not found"}
            return {"status": "ok", "result": record}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def get_history(self, limit: int = 20) -> dict:
        try:
            return {"status": "ok", "history": self._history[-limit:], "total": len(self._history)}
        except Exception as e:
            return {"status": "error", "error": str(e)}
