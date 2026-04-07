"""
AlgoChains ntfy Push Notifications — Mobile Alerts for Trading Events

Adapted from danielmiessler/Personal_AI_Infrastructure notification system.
ntfy (https://ntfy.sh) provides instant mobile push without app install.
Subscribers visit ntfy.sh/<topic> to receive live alerts.

AlgoChains ntfy Topics:
  algochains/bots          ← Bot up/down, trade events
  algochains/risk          ← Circuit breaker, daily loss limit
  algochains/marketplace   ← New subscriber, bot promoted/demoted
  algochains/ops           ← System health, deploy complete
  algochains/alpha         ← High-confidence signal detected

Environment Variables:
  NTFY_BASE_URL        — Base URL (default: https://ntfy.sh)
  NTFY_TOPIC_PREFIX    — Topic namespace (default: algochains)
  NTFY_AUTH_TOKEN      — Optional Bearer token for private topics
  NTFY_DEFAULT_PRIORITY — Default priority (default: default)

Fails gracefully if ntfy is unavailable — notifications are best-effort,
never block trading operations.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("algochains_mcp.notifications.ntfy")

_NTFY_BASE = os.getenv("NTFY_BASE_URL", "https://ntfy.sh").rstrip("/")
_TOPIC_PREFIX = os.getenv("NTFY_TOPIC_PREFIX", "algochains")
_AUTH_TOKEN = os.getenv("NTFY_AUTH_TOKEN", "").strip()

_VALID_PRIORITIES = {"max", "urgent", "high", "default", "low", "min"}

# Pre-defined topics for common AlgoChains events
TOPICS = {
    "bots": f"{_TOPIC_PREFIX}/bots",
    "risk": f"{_TOPIC_PREFIX}/risk",
    "marketplace": f"{_TOPIC_PREFIX}/marketplace",
    "ops": f"{_TOPIC_PREFIX}/ops",
    "alpha": f"{_TOPIC_PREFIX}/alpha",
}


def send_push(
    title: str,
    message: str,
    topic: str = "ops",
    priority: str = "default",
    tags: list[str] | None = None,
    click_url: str = "",
    actions: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """
    Send a push notification via ntfy.

    Args:
        title: Notification title (shown in bold on mobile)
        message: Notification body text
        topic: "bots" | "risk" | "marketplace" | "ops" | "alpha" | custom topic name
        priority: "max" | "urgent" | "high" | "default" | "low" | "min"
                  urgent/max = always-on screen; low/min = no sound
        tags: Emoji tags shown with notification (e.g. ["warning", "robot"])
              ntfy maps known tag names to emojis automatically
        click_url: URL to open when notification is tapped
        actions: Optional action buttons (list of {action, label, url} dicts)

    Priority Guide for AlgoChains:
        max/urgent: Bot crash, daily loss limit hit, position stuck
        high: New trade signal, bot reconnecting
        default: Daily P&L summary, metric update
        low: Heartbeat OK, routine status
        min: Debug/info only

    Returns status dict with success/error.
    """
    priority = priority.lower().strip()
    if priority not in _VALID_PRIORITIES:
        priority = "default"

    # Resolve topic to full path
    resolved_topic = TOPICS.get(topic, f"{_TOPIC_PREFIX}/{topic}")

    url = f"{_NTFY_BASE}/{resolved_topic}"

    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "User-Agent": "AlgoChains-MCP/22.9",
        "Title": _encode_header(title),
        "Priority": priority,
    }

    if _AUTH_TOKEN:
        headers["Authorization"] = f"Bearer {_AUTH_TOKEN}"

    if tags:
        headers["Tags"] = ",".join(tags)

    if click_url:
        headers["Click"] = click_url

    payload: dict[str, Any] = {"message": message}

    if actions:
        headers["Actions"] = _format_actions(actions)

    try:
        payload_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=payload_bytes, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp_body = resp.read().decode("utf-8")
            try:
                resp_json = json.loads(resp_body)
            except json.JSONDecodeError:
                resp_json = {"raw": resp_body[:200]}
            return {
                "success": True,
                "topic": resolved_topic,
                "title": title,
                "priority": priority,
                "ntfy_id": resp_json.get("id", ""),
                "url": url,
                "sent_at": datetime.now(timezone.utc).isoformat(),
            }
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8")[:300]
        except Exception:
            pass
        logger.warning("ntfy push failed HTTP %d for topic %s: %s", exc.code, resolved_topic, body)
        return {
            "success": False,
            "error": f"HTTP {exc.code}",
            "body": body,
            "topic": resolved_topic,
        }
    except Exception as exc:
        logger.warning("ntfy push failed for topic %s: %s", resolved_topic, exc)
        return {
            "success": False,
            "error": str(exc),
            "topic": resolved_topic,
            "note": "ntfy failure is non-fatal — trading operations continue",
        }


def send_bot_alert(
    bot: str,
    event: str,
    priority: str = "high",
    details: str = "",
) -> dict[str, Any]:
    """Convenience method: send a bot lifecycle alert."""
    tag_map = {
        "crash": ["skull", "robot"],
        "start": ["white_check_mark", "robot"],
        "stop": ["octagonal_sign", "robot"],
        "trade": ["chart_with_upwards_trend"],
        "error": ["warning", "robot"],
        "reconnect": ["electric_plug", "robot"],
    }
    event_lower = event.lower()
    tags = None
    for k, v in tag_map.items():
        if k in event_lower:
            tags = v
            break

    title = f"AlgoChains Bot Alert — {bot}"
    message = f"{bot}: {event}"
    if details:
        message += f"\n{details}"

    return send_push(
        title=title,
        message=message,
        topic="bots",
        priority=priority,
        tags=tags,
    )


def send_risk_alert(
    event: str,
    amount: float | None = None,
    priority: str = "urgent",
) -> dict[str, Any]:
    """Convenience method: send a risk/circuit-breaker alert."""
    title = "AlgoChains RISK ALERT"
    message = event
    if amount is not None:
        message += f" (${amount:,.2f})"

    return send_push(
        title=title,
        message=message,
        topic="risk",
        priority=priority,
        tags=["warning", "rotating_light"],
    )


def send_marketplace_event(event: str, priority: str = "default") -> dict[str, Any]:
    """Convenience method: marketplace event notification."""
    return send_push(
        title="AlgoChains Marketplace",
        message=event,
        topic="marketplace",
        priority=priority,
        tags=["shopping_cart"],
    )


def _encode_header(value: str) -> str:
    """Encode header value for non-ASCII characters."""
    try:
        value.encode("ascii")
        return value
    except UnicodeEncodeError:
        return urllib.parse.quote(value)


def _format_actions(actions: list[dict[str, str]]) -> str:
    """Format ntfy action buttons."""
    parts = []
    for a in actions[:3]:  # ntfy supports up to 3 actions
        label = a.get("label", "Open")
        url = a.get("url", "")
        if url:
            parts.append(f"view, {label}, {url}")
    return "; ".join(parts)
