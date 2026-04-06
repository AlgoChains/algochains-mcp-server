"""FIX 4.2/4.4/5.0 protocol session management."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


class FIXGateway:
    """FIX 4.2/4.4/5.0 protocol sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, dict] = {}

    async def connect(self, venue: str, sender_comp_id: str, target_comp_id: str, config: dict | None = None) -> dict:
        try:
            session_id = uuid.uuid4().hex[:12]
            cfg = config or {}
            session = {
                "id": session_id,
                "venue": venue,
                "sender_comp_id": sender_comp_id,
                "target_comp_id": target_comp_id,
                "fix_version": cfg.get("fix_version", "4.4"),
                "host": cfg.get("host", ""),
                "port": cfg.get("port", 0),
                "status": "connected",
                "messages_sent": 0,
                "messages_received": 0,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "last_heartbeat": datetime.now(timezone.utc).isoformat(),
            }
            self._sessions[session_id] = session
            return {"status": "ok", "session": session}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def disconnect(self, session_id: str) -> dict:
        try:
            session = self._sessions.get(session_id)
            if not session:
                return {"status": "error", "error": f"Session {session_id} not found"}
            session["status"] = "disconnected"
            return {"status": "ok", "session_id": session_id, "disconnected": True}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def get_session_status(self, session_id: str) -> dict:
        try:
            session = self._sessions.get(session_id)
            if not session:
                return {"status": "error", "error": f"Session {session_id} not found"}
            return {"status": "ok", "session": session}
        except Exception as e:
            return {"status": "error", "error": str(e)}
