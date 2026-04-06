"""Real-time alert engine with rule-based triggers."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


class AlertEngine:
    """Real-time alert engine with rule-based triggers."""

    def __init__(self) -> None:
        self._alerts: dict[str, dict] = {}
        self._history: list[dict] = []

    async def create_alert(self, name: str, condition: dict, actions: list[dict] | None = None, channels: list[str] | None = None) -> dict:
        try:
            alert_id = uuid.uuid4().hex[:12]
            alert = {
                "id": alert_id,
                "name": name,
                "condition": condition,
                "actions": actions or [],
                "channels": channels or ["slack"],
                "enabled": True,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._alerts[alert_id] = alert
            return {"status": "ok", "alert": alert}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def list_alerts(self, active_only: bool = True) -> dict:
        try:
            alerts = list(self._alerts.values())
            if active_only:
                alerts = [a for a in alerts if a.get("enabled", True)]
            return {"status": "ok", "alerts": alerts, "count": len(alerts)}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def delete_alert(self, alert_id: str) -> dict:
        try:
            alert = self._alerts.pop(alert_id, None)
            if not alert:
                return {"status": "error", "error": f"Alert {alert_id} not found"}
            return {"status": "ok", "deleted": alert_id}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def get_history(self, alert_id: str | None = None, limit: int = 50) -> dict:
        try:
            history = self._history
            if alert_id:
                history = [h for h in history if h.get("alert_id") == alert_id]
            return {"status": "ok", "history": history[-limit:], "total": len(history)}
        except Exception as e:
            return {"status": "error", "error": str(e)}
