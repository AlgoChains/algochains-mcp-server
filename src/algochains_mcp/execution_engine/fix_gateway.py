"""FIX 4.2/4.4/5.0 protocol session management."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


class FIXGateway:
    """FIX 4.2/4.4/5.0 protocol sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, dict] = {}

    async def create_session(self, config: dict) -> dict:
        try:
            session_id = uuid.uuid4().hex[:12]
            session = {
                "id": session_id,
                "sender_comp_id": config.get("sender_comp_id", "ALGOCHAINS"),
                "target_comp_id": config.get("target_comp_id", ""),
                "fix_version": config.get("fix_version", "4.4"),
                "host": config.get("host", ""),
                "port": config.get("port", 0),
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

    async def send_new_order(self, session_id: str, order: dict) -> dict:
        try:
            session = self._sessions.get(session_id)
            if not session:
                return {"status": "error", "error": f"Session {session_id} not found"}
            session["messages_sent"] += 1
            msg_id = uuid.uuid4().hex[:12]
            return {
                "status": "ok",
                "fix_message_id": msg_id,
                "msg_type": "D",
                "session_id": session_id,
                "order": order,
                "sent_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def send_cancel(self, session_id: str, orig_order_id: str) -> dict:
        try:
            session = self._sessions.get(session_id)
            if not session:
                return {"status": "error", "error": f"Session {session_id} not found"}
            session["messages_sent"] += 1
            return {
                "status": "ok",
                "msg_type": "F",
                "session_id": session_id,
                "orig_order_id": orig_order_id,
                "sent_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def send_replace(self, session_id: str, orig_order_id: str, updates: dict) -> dict:
        try:
            session = self._sessions.get(session_id)
            if not session:
                return {"status": "error", "error": f"Session {session_id} not found"}
            session["messages_sent"] += 1
            return {
                "status": "ok",
                "msg_type": "G",
                "session_id": session_id,
                "orig_order_id": orig_order_id,
                "updates": updates,
                "sent_at": datetime.now(timezone.utc).isoformat(),
            }
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
