"""
MCP 2025-11-25 Resource Subscriptions + Notifications.

Clients subscribe to resource URIs and receive push updates when the
underlying data changes. Maps AlgoChains events to MCP resource URIs.

Subscribed resource URIs:
  algochains://positions/{broker}           — push on every fill
  algochains://account/{broker}             — push on equity change > 0.5%
  algochains://circuit_breaker/status       — push when any guard fires
  algochains://market/regime/{symbol}       — push on regime transition
  algochains://strategy/paper/{id}/pnl      — daily P&L push
  algochains://alerts/price/{symbol}        — push when price alert triggers
  algochains://tasks/{task_id}/progress     — push task progress updates
  algochains://evolution/cycle              — push on evolution loop completion
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ResourceSubscription:
    subscription_id: str
    resource_uri: str
    client_id: str
    created_at: float = field(default_factory=time.time)
    last_notified: float | None = None
    notification_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "subscription_id": self.subscription_id,
            "resource_uri": self.resource_uri,
            "client_id": self.client_id,
            "created_at": self.created_at,
            "last_notified": self.last_notified,
            "notification_count": self.notification_count,
        }


@dataclass
class ResourceNotification:
    resource_uri: str
    notification_type: str  # "updated" | "deleted" | "created"
    data: Any
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource_uri": self.resource_uri,
            "notification_type": self.notification_type,
            "data": self.data,
            "timestamp": self.timestamp,
        }


class SubscriptionManager:
    """
    Manages resource subscriptions and notification dispatch.

    In stdio mode: notifications are stored in a queue that agents can poll
    via get_pending_notifications().

    In Streamable HTTP mode (Phase 5): notifications are pushed over SSE.
    """

    # Standard notification types
    POSITIONS_URI = "algochains://positions/{broker}"
    ACCOUNT_URI = "algochains://account/{broker}"
    CIRCUIT_BREAKER_URI = "algochains://circuit_breaker/status"
    REGIME_URI = "algochains://market/regime/{symbol}"
    PAPER_PNL_URI = "algochains://strategy/paper/{strategy_id}/pnl"
    PRICE_ALERT_URI = "algochains://alerts/price/{symbol}"
    TASK_PROGRESS_URI = "algochains://tasks/{task_id}/progress"
    EVOLUTION_URI = "algochains://evolution/cycle"

    MAX_QUEUE = 500

    def __init__(self) -> None:
        self._subscriptions: dict[str, ResourceSubscription] = {}
        self._notification_queue: list[ResourceNotification] = []
        self._listeners: dict[str, list[Callable]] = {}

    # ── Subscription management ───────────────────────────────────────

    def subscribe(self, resource_uri: str, client_id: str = "default") -> ResourceSubscription:
        sub = ResourceSubscription(
            subscription_id=str(uuid.uuid4()),
            resource_uri=resource_uri,
            client_id=client_id,
        )
        self._subscriptions[sub.subscription_id] = sub
        return sub

    def unsubscribe(self, subscription_id: str) -> bool:
        return self._subscriptions.pop(subscription_id, None) is not None

    def get_subscription(self, subscription_id: str) -> ResourceSubscription | None:
        return self._subscriptions.get(subscription_id)

    def list_subscriptions(self, client_id: str | None = None) -> list[dict[str, Any]]:
        subs = list(self._subscriptions.values())
        if client_id:
            subs = [s for s in subs if s.client_id == client_id]
        return [s.to_dict() for s in subs]

    # ── Notification dispatch ─────────────────────────────────────────

    def notify(self, resource_uri: str, data: Any, notification_type: str = "updated") -> int:
        """Emit a notification. Returns number of subscribers notified."""
        notif = ResourceNotification(
            resource_uri=resource_uri,
            notification_type=notification_type,
            data=data,
        )
        if len(self._notification_queue) >= self.MAX_QUEUE:
            self._notification_queue.pop(0)
        self._notification_queue.append(notif)

        # Count matched subscribers
        matched = 0
        for sub in self._subscriptions.values():
            if self._uri_matches(sub.resource_uri, resource_uri):
                sub.last_notified = time.time()
                sub.notification_count += 1
                matched += 1

        # Call registered listeners (for Streamable HTTP SSE push)
        for pattern, callbacks in self._listeners.items():
            if self._uri_matches(pattern, resource_uri):
                for cb in callbacks:
                    try:
                        cb(notif)
                    except Exception:
                        pass

        return matched

    def _uri_matches(self, pattern: str, uri: str) -> bool:
        """Simple wildcard matching: {placeholder} matches any segment."""
        p_parts = pattern.split("/")
        u_parts = uri.split("/")
        if len(p_parts) != len(u_parts):
            return False
        return all(
            pp.startswith("{") and pp.endswith("}") or pp == up
            for pp, up in zip(p_parts, u_parts)
        )

    def get_pending_notifications(self, limit: int = 20, resource_uri_filter: str | None = None) -> list[dict[str, Any]]:
        """Poll-mode: return recent notifications."""
        notifs = list(reversed(self._notification_queue))
        if resource_uri_filter:
            notifs = [n for n in notifs if resource_uri_filter in n.resource_uri]
        return [n.to_dict() for n in notifs[:limit]]

    def drain_notifications(self) -> list[dict[str, Any]]:
        """Drain and return all pending notifications."""
        all_notifs = [n.to_dict() for n in self._notification_queue]
        self._notification_queue.clear()
        return all_notifs

    def add_listener(self, resource_uri_pattern: str, callback: Callable) -> None:
        """Register a push callback for Streamable HTTP SSE."""
        self._listeners.setdefault(resource_uri_pattern, []).append(callback)

    # ── Convenience notifiers ─────────────────────────────────────────

    def notify_position_update(self, broker: str, positions: list[dict]) -> None:
        self.notify(f"algochains://positions/{broker}", {"broker": broker, "positions": positions})

    def notify_account_update(self, broker: str, equity: float, change_pct: float) -> None:
        if abs(change_pct) >= 0.5:
            self.notify(f"algochains://account/{broker}", {"broker": broker, "equity": equity, "change_pct": change_pct})

    def notify_circuit_breaker(self, guard_name: str, reason: str, severity: str) -> None:
        self.notify("algochains://circuit_breaker/status", {
            "guard_name": guard_name,
            "reason": reason,
            "severity": severity,
            "fired_at": time.time(),
        })

    def notify_regime_change(self, symbol: str, old_regime: str, new_regime: str, confidence: float) -> None:
        self.notify(f"algochains://market/regime/{symbol}", {
            "symbol": symbol,
            "old_regime": old_regime,
            "new_regime": new_regime,
            "confidence": confidence,
            "changed_at": time.time(),
        })

    def notify_paper_pnl(self, strategy_id: str, pnl: float, return_pct: float, period: str) -> None:
        self.notify(f"algochains://strategy/paper/{strategy_id}/pnl", {
            "strategy_id": strategy_id,
            "pnl": pnl,
            "return_pct": return_pct,
            "period": period,
        })

    def notify_price_alert(self, symbol: str, condition: str, price: float, alert_id: str) -> None:
        self.notify(f"algochains://alerts/price/{symbol}", {
            "symbol": symbol,
            "condition": condition,
            "triggered_price": price,
            "alert_id": alert_id,
            "triggered_at": time.time(),
        })

    def notify_task_progress(self, task_id: str, pct: float, message: str) -> None:
        self.notify(f"algochains://tasks/{task_id}/progress", {
            "task_id": task_id,
            "pct": pct,
            "message": message,
        })

    def notify_evolution_cycle(self, cycle_result: dict) -> None:
        self.notify("algochains://evolution/cycle", cycle_result)

    def stats(self) -> dict[str, Any]:
        return {
            "active_subscriptions": len(self._subscriptions),
            "pending_notifications": len(self._notification_queue),
            "total_listeners": sum(len(v) for v in self._listeners.values()),
        }


_subscription_manager: SubscriptionManager | None = None


def get_subscription_manager() -> SubscriptionManager:
    global _subscription_manager
    if _subscription_manager is None:
        _subscription_manager = SubscriptionManager()
    return _subscription_manager
