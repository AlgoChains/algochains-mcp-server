"""
Kalshi Slack Notifier — AlgoChains v1.0

Sends real-time Kalshi trading alerts to #kalshi-bot-changelog Slack channel.
Based on yllvar/Kalshi-Quant-TeleBot notification patterns,
adapted to use the existing OpenClaw Slack infrastructure.

Events notified:
  - Edge found (Safe Compounder or AI Ensemble)
  - Trade executed
  - Daily P&L summary
  - Circuit breaker triggered (max loss hit)
  - Category score updated (blocked/unblocked)

Posting strategy (in order):
  1. Direct Slack bot token via SLACK_BOT_TOKEN env var
  2. Fallback: control-tower slack_utils.post_to_slack (same token, resolves channel names)
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

logger = logging.getLogger("algochains_mcp.order_flow.kalshi_slack_notifier")

SLACK_CHANNEL = "#kalshi-bot-changelog"
KALSHI_EMOJI  = ":chart_with_upwards_trend:"

# Path to the control-tower repo so we can import slack_utils as a fallback.
# Use the shared resolver so ALGOCHAINS_CONTROL_TOWER env is honored (dual-node).
# The previous `Path(__file__).parents[4] / "algochains-control-tower"` only
# worked when both repos were checked out as siblings — broke on custom layouts.
from algochains_mcp.paths import default_control_tower as _default_ct  # noqa: E402

_CONTROL_TOWER = _default_ct()


def _get_slack_token() -> str:
    return os.getenv("SLACK_BOT_TOKEN", "")


def _post_via_slack_utils_fallback(text: str) -> bool:
    """
    Fallback: import control-tower slack_utils and call post_to_slack.
    Used when the direct httpx call has no token or fails.
    """
    try:
        if str(_CONTROL_TOWER) not in sys.path:
            sys.path.insert(0, str(_CONTROL_TOWER))
        from slack_utils import post_to_slack  # type: ignore[import]
        return post_to_slack("kalshi-bot-changelog", text)
    except Exception as exc:
        logger.debug("slack_utils fallback failed: %s", exc)
        return False


def _post_slack_message(blocks: list[dict], text: str = "") -> dict[str, Any]:
    """Post a formatted message to #kalshi-bot-changelog."""
    token = _get_slack_token()
    if not token:
        # Try fallback before giving up
        ok = _post_via_slack_utils_fallback(text or "Kalshi alert")
        if ok:
            return {"ok": True, "method": "slack_utils_fallback"}
        logger.warning("SLACK_BOT_TOKEN not set and fallback failed — notifications disabled")
        return {"ok": False, "error": "no_token"}

    payload = {
        "channel": SLACK_CHANNEL,
        "text": text or "Kalshi alert",
        "blocks": blocks,
    }

    try:
        resp = httpx.post(
            "https://slack.com/api/chat.postMessage",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=10,
        )
        return resp.json()
    except Exception as exc:
        logger.error("Slack post failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def notify_edge_found(
    ticker: str,
    title: str,
    category: str,
    action: str,
    edge: float,
    yes_bid: float,
    no_ask: float,
    suggested_contracts: int,
    position_usd: float,
    source: str = "safe_compounder",
) -> dict[str, Any]:
    """Notify #kalshi-bot-changelog that an edge opportunity was found."""
    action_icon = ":green_circle:" if action in ("buy_no", "buy_yes") else ":yellow_circle:"
    side = "NO" if action == "buy_no" else "YES"
    price = no_ask if action == "buy_no" else yes_bid

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{KALSHI_EMOJI} Kalshi Edge Found — {ticker}",
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Question:*\n{title[:100]}"},
                {"type": "mrkdwn", "text": f"*Category:* `{category}`"},
                {"type": "mrkdwn", "text": f"*Action:* {action_icon} Buy *{side}* at `{price:.2f}`"},
                {"type": "mrkdwn", "text": f"*Edge:* `{edge:+.1%}`"},
                {"type": "mrkdwn", "text": f"*Contracts:* `{suggested_contracts}`"},
                {"type": "mrkdwn", "text": f"*Capital at Risk:* `${position_usd:.2f}`"},
                {"type": "mrkdwn", "text": f"*Source:* `{source}`"},
                {"type": "mrkdwn", "text": f"*Found at:* `{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}`"},
            ],
        },
        {"type": "divider"},
    ]

    return _post_slack_message(blocks, text=f"Kalshi edge: {ticker} — {action} {side} at {price:.2f}")


def notify_trade_executed(
    ticker: str,
    title: str,
    side: str,
    contracts: int,
    price_cents: int,
    order_id: str,
    status: str = "filled",
    source: str = "safe_compounder",
) -> dict[str, Any]:
    """Notify #kalshi-bot-changelog that a Kalshi order was placed."""
    icon = ":white_check_mark:" if status == "filled" else ":warning:"

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"{icon} *Kalshi Trade Executed*\n"
                    f"Ticker: `{ticker}` | Side: *{side.upper()}* | "
                    f"Contracts: `{contracts}` @ `{price_cents/100:.2f}` "
                    f"| OrderID: `{order_id[:16]}...`\n"
                    f"_{title[:80]}_"
                ),
            },
        }
    ]

    return _post_slack_message(blocks)


def notify_daily_pnl(
    balance_usd: float,
    day_pnl_usd: float,
    total_trades_today: int,
    win_rate_today: float,
    bankroll_start_usd: float = 250.0,
) -> dict[str, Any]:
    """Send daily P&L summary to #kalshi-bot-changelog."""
    pct_gain_vs_start = (balance_usd - bankroll_start_usd) / bankroll_start_usd * 100
    day_icon = ":green_circle:" if day_pnl_usd >= 0 else ":red_circle:"

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{KALSHI_EMOJI} Kalshi Daily P&L Report",
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Balance:* `${balance_usd:.2f}`"},
                {"type": "mrkdwn", "text": f"*Day P&L:* {day_icon} `${day_pnl_usd:+.2f}`"},
                {"type": "mrkdwn", "text": f"*Total Return:* `{pct_gain_vs_start:+.1f}%` vs $250 start"},
                {"type": "mrkdwn", "text": f"*Trades Today:* `{total_trades_today}`"},
                {"type": "mrkdwn", "text": f"*Win Rate Today:* `{win_rate_today:.1%}`"},
                {"type": "mrkdwn", "text": f"*Date:* `{datetime.now(timezone.utc).strftime('%Y-%m-%d')}`"},
            ],
        },
    ]

    return _post_slack_message(blocks, text=f"Kalshi Daily P&L: ${day_pnl_usd:+.2f} | Balance: ${balance_usd:.2f}")


def notify_circuit_breaker(
    trigger_reason: str,
    balance_usd: float,
    loss_pct: float,
) -> dict[str, Any]:
    """Urgent alert: circuit breaker triggered, trading halted."""
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": ":rotating_light: Kalshi Circuit Breaker Triggered",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Reason:* {trigger_reason}\n"
                    f"*Balance:* `${balance_usd:.2f}`\n"
                    f"*Loss:* `{loss_pct:.1%}` (limit exceeded)\n"
                    f"*Action:* All trading HALTED until manual review\n"
                    f"*Time:* `{datetime.now(timezone.utc).isoformat()}`"
                ),
            },
        },
    ]

    return _post_slack_message(blocks, text=f":rotating_light: Kalshi Circuit Breaker: {trigger_reason}")


def notify_scan_summary(
    opportunities_found: int,
    actionable: int,
    top_ticker: Optional[str],
    top_edge: Optional[float],
    categories_scanned: int,
    markets_scanned: int,
) -> dict[str, Any]:
    """Post scan summary after each pipeline run. Suppressed when no actionable edges."""
    if actionable == 0:
        # Suppress no-edge noise — only post when a signal actually exists
        logger.debug("notify_scan_summary: no actionable edges — skipping Slack post")
        return {"ok": True, "suppressed": True}

    text = f"Kalshi scan: {actionable} edges found — top: {top_ticker} ({top_edge:+.1%})"

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"{KALSHI_EMOJI} *Kalshi Scan Complete*\n"
                    f"Markets: `{markets_scanned}` | Categories: `{categories_scanned}` | "
                    f"Edges: `{opportunities_found}` | Actionable: `{actionable}`\n"
                    f"Top: `{top_ticker}` edge `{top_edge:+.1%}`"
                ),
            },
        }
    ]

    return _post_slack_message(blocks, text=text)
