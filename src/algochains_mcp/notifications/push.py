"""
Push notification system for AlgoChains MCP Server (V6).

Supports multiple notification channels:
  - WebSocket push (for mobile companion app)
  - Slack webhook
  - Email (via Resend / SendGrid)
  - Discord webhook
  - Telegram bot
  - APNS / FCM (for native mobile push)

Events that trigger notifications:
  - Order fills (buy/sell executed)
  - Daily P&L summary
  - Drawdown alerts (configurable threshold)
  - Bot status changes (started, stopped, error)
  - Margin warnings
  - Strategy validation results
  - Marketplace subscription events
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger("algochains_mcp.notifications")


class NotificationChannel(str, Enum):
    WEBSOCKET = "websocket"
    SLACK = "slack"
    EMAIL = "email"
    DISCORD = "discord"
    TELEGRAM = "telegram"
    APNS = "apns"  # Apple Push Notification Service
    FCM = "fcm"    # Firebase Cloud Messaging


class NotificationPriority(str, Enum):
    CRITICAL = "critical"  # Drawdown breach, margin call
    HIGH = "high"          # Order fills, bot errors
    MEDIUM = "medium"      # Daily P&L, status changes
    LOW = "low"            # Info, subscription events


class NotificationEvent(str, Enum):
    ORDER_FILL = "order_fill"
    DAILY_PNL = "daily_pnl"
    DRAWDOWN_ALERT = "drawdown_alert"
    BOT_STATUS = "bot_status"
    MARGIN_WARNING = "margin_warning"
    VALIDATION_RESULT = "validation_result"
    SUBSCRIPTION_EVENT = "subscription_event"
    RISK_ALERT = "risk_alert"
    REBALANCE_NEEDED = "rebalance_needed"


@dataclass
class Notification:
    """A single notification to be dispatched."""
    event: NotificationEvent
    priority: NotificationPriority
    title: str
    body: str
    data: dict = field(default_factory=dict)
    channels: list[NotificationChannel] = field(default_factory=lambda: [NotificationChannel.WEBSOCKET])
    user_id: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "event": self.event.value,
            "priority": self.priority.value,
            "title": self.title,
            "body": self.body,
            "data": self.data,
            "channels": [c.value for c in self.channels],
            "timestamp": self.timestamp,
        }


@dataclass
class NotificationPreferences:
    """User notification preferences."""
    channels: list[NotificationChannel] = field(
        default_factory=lambda: [NotificationChannel.WEBSOCKET]
    )
    min_priority: NotificationPriority = NotificationPriority.MEDIUM
    events: list[NotificationEvent] = field(default_factory=lambda: list(NotificationEvent))
    quiet_hours: tuple[int, int] = (22, 7)  # 10pm-7am local time
    fill_notifications: bool = True
    daily_pnl_summary: bool = True
    drawdown_threshold: float = 0.05  # Alert at 5% drawdown


class NotificationDispatcher:
    """Dispatches notifications across configured channels.

    Usage:
        dispatcher = NotificationDispatcher()
        dispatcher.configure_slack(webhook_url="https://hooks.slack.com/...")
        dispatcher.configure_email(api_key="re_...", from_addr="alerts@algochains.ai")

        await dispatcher.send(Notification(
            event=NotificationEvent.ORDER_FILL,
            priority=NotificationPriority.HIGH,
            title="Order Filled: AAPL",
            body="Bought 10 AAPL @ $185.50 on Alpaca",
            data={"symbol": "AAPL", "side": "buy", "qty": 10, "price": 185.50},
        ))
    """

    def __init__(self):
        self._channels: dict[NotificationChannel, dict] = {}
        self._history: list[Notification] = []
        self._max_history = 500
        self._preferences: dict[str, NotificationPreferences] = {}

    def configure_slack(self, webhook_url: str) -> None:
        self._channels[NotificationChannel.SLACK] = {"webhook_url": webhook_url}
        logger.info("Slack notifications configured")

    def configure_email(self, api_key: str, from_addr: str = "alerts@algochains.ai") -> None:
        self._channels[NotificationChannel.EMAIL] = {"api_key": api_key, "from": from_addr}
        logger.info("Email notifications configured")

    def configure_discord(self, webhook_url: str) -> None:
        self._channels[NotificationChannel.DISCORD] = {"webhook_url": webhook_url}
        logger.info("Discord notifications configured")

    def configure_telegram(self, bot_token: str, chat_id: str) -> None:
        self._channels[NotificationChannel.TELEGRAM] = {"bot_token": bot_token, "chat_id": chat_id}
        logger.info("Telegram notifications configured")

    def configure_mobile_push(self, fcm_key: str = "", apns_cert: str = "") -> None:
        if fcm_key:
            self._channels[NotificationChannel.FCM] = {"server_key": fcm_key}
            logger.info("FCM (Android) push configured")
        if apns_cert:
            self._channels[NotificationChannel.APNS] = {"cert_path": apns_cert}
            logger.info("APNS (iOS) push configured")

    def set_preferences(self, user_id: str, prefs: NotificationPreferences) -> None:
        self._preferences[user_id] = prefs

    async def send(self, notification: Notification) -> dict:
        """Dispatch a notification to all configured channels."""
        results: dict[str, str] = {}

        # Check user preferences
        prefs = self._preferences.get(notification.user_id)
        if prefs:
            if notification.priority.value > prefs.min_priority.value:
                return {"skipped": "Below minimum priority"}
            if notification.event not in prefs.events:
                return {"skipped": "Event type not subscribed"}

        for channel in notification.channels:
            if channel not in self._channels:
                results[channel.value] = "not_configured"
                continue

            try:
                if channel == NotificationChannel.SLACK:
                    await self._send_slack(notification)
                    results[channel.value] = "sent"
                elif channel == NotificationChannel.EMAIL:
                    await self._send_email(notification)
                    results[channel.value] = "sent"
                elif channel == NotificationChannel.DISCORD:
                    await self._send_discord(notification)
                    results[channel.value] = "sent"
                elif channel == NotificationChannel.TELEGRAM:
                    await self._send_telegram(notification)
                    results[channel.value] = "sent"
                elif channel == NotificationChannel.WEBSOCKET:
                    results[channel.value] = "queued"
                elif channel in (NotificationChannel.FCM, NotificationChannel.APNS):
                    await self._send_mobile_push(notification, channel)
                    results[channel.value] = "sent"
            except Exception as e:
                logger.error("Failed to send %s notification: %s", channel.value, e)
                results[channel.value] = f"error: {e}"

        # Store in history
        self._history.append(notification)
        if len(self._history) > self._max_history:
            self._history.pop(0)

        return results

    async def _send_slack(self, n: Notification) -> None:
        import httpx
        cfg = self._channels[NotificationChannel.SLACK]
        emoji = {"critical": "🚨", "high": "🔔", "medium": "📊", "low": "ℹ️"}.get(n.priority.value, "")
        async with httpx.AsyncClient() as client:
            await client.post(cfg["webhook_url"], json={
                "text": f"{emoji} *{n.title}*\n{n.body}",
            })

    async def _send_email(self, n: Notification) -> None:
        import httpx
        cfg = self._channels[NotificationChannel.EMAIL]
        async with httpx.AsyncClient() as client:
            await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {cfg['api_key']}"},
                json={
                    "from": cfg["from"],
                    "to": n.data.get("email", ""),
                    "subject": n.title,
                    "text": n.body,
                },
            )

    async def _send_discord(self, n: Notification) -> None:
        import httpx
        cfg = self._channels[NotificationChannel.DISCORD]
        async with httpx.AsyncClient() as client:
            await client.post(cfg["webhook_url"], json={
                "content": f"**{n.title}**\n{n.body}",
            })

    async def _send_telegram(self, n: Notification) -> None:
        import httpx
        cfg = self._channels[NotificationChannel.TELEGRAM]
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage",
                json={"chat_id": cfg["chat_id"], "text": f"*{n.title}*\n{n.body}", "parse_mode": "Markdown"},
            )

    async def _send_mobile_push(self, n: Notification, channel: NotificationChannel) -> None:
        # Placeholder — real implementation would use firebase-admin or apns2 library
        logger.info("Mobile push (%s): %s", channel.value, n.title)

    def get_history(self, limit: int = 20, event: Optional[NotificationEvent] = None) -> list[dict]:
        """Get notification history, optionally filtered by event type."""
        history = self._history
        if event:
            history = [n for n in history if n.event == event]
        return [n.to_dict() for n in history[-limit:]]

    def configured_channels(self) -> list[str]:
        """List all configured notification channels."""
        return [c.value for c in self._channels]

    def stats(self) -> dict:
        """Get notification system statistics."""
        return {
            "configured_channels": self.configured_channels(),
            "total_sent": len(self._history),
            "by_event": {
                e.value: sum(1 for n in self._history if n.event == e)
                for e in NotificationEvent
                if any(n.event == e for n in self._history)
            },
            "by_priority": {
                p.value: sum(1 for n in self._history if n.priority == p)
                for p in NotificationPriority
                if any(n.priority == p for n in self._history)
            },
        }
