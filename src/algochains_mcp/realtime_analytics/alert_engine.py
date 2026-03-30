"""Real-time alert engine with rule-based triggers."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


class AlertEngine:
    """Real-time alert engine with rule-based triggers."""

    def __init__(self) -> None:
        self._rules: dict[str, dict] = {}
        self._alerts: list[dict] = []

    async def create_rule(self, name: str, condition: dict, actions: list[dict], severity: str = "medium") -> dict:
        try:
            if severity not in ("low", "medium", "high", "critical"):
                return {"status": "error", "error": f"Invalid severity: {severity}"}
            rule_id = uuid.uuid4().hex[:12]
            rule = {
                "id": rule_id,
                "name": name,
                "condition": condition,
                "actions": actions,
                "severity": severity,
                "enabled": True,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._rules[rule_id] = rule
            return {"status": "ok", "rule": rule}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def list_rules(self) -> dict:
        try:
            return {"status": "ok", "rules": list(self._rules.values()), "count": len(self._rules)}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def get_active_alerts(self, severity: str | None = None) -> dict:
        try:
            alerts = self._alerts
            if severity:
                alerts = [a for a in alerts if a.get("severity") == severity]
            return {"status": "ok", "alerts": alerts, "count": len(alerts)}
        except Exception as e:
            return {"status": "error", "error": str(e)}
