"""
multi_account_metrics.py — Multi-Bot Account Metrics
=====================================================

Extends the single-bot metrics_parser to support multiple users
each running multiple bots via the marketplace.

For marketplace subscribers, metrics come from:
  1. The subscriber's own log path (if self-hosted)
  2. The marketplace performance DB (Supabase) for managed bots
  3. Fallback states when data is unavailable

Fallback state handling:
  - BROKER_NOT_CONNECTED: user has a subscription but hasn't connected broker
  - METRICS_PENDING:      bot is connected but not enough data yet (<48h)
  - BOT_PAUSED:           bot is deliberately paused
  - DATA_STALE:           last metric update > 6 hours ago

No synthetic metrics. If real data isn't available, return the
appropriate fallback state with clear action instructions.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import httpx

from .metrics_parser import BotMetrics, parse_bot_metrics, BOT_META

logger = logging.getLogger("algochains_mcp.live_bot_intelligence.multi_account")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
_TABLE_PERF = "algochains_bot_performance"
_TABLE_SUBS = "algochains_subscriptions"
_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
_STALE_THRESHOLD_SEC = 6 * 3600  # 6 hours


class BotDataState(str, Enum):
    LIVE = "live"                     # Real metrics available
    METRICS_PENDING = "metrics_pending"   # Bot connected, <48h data
    BROKER_NOT_CONNECTED = "broker_not_connected"
    DATA_STALE = "data_stale"          # Last update >6h ago
    BOT_PAUSED = "bot_paused"          # Deliberately paused
    SUBSCRIPTION_INACTIVE = "subscription_inactive"
    ERROR = "error"


@dataclass
class UserBotMetrics:
    """Metrics for a bot in the context of a specific user/subscription."""
    user_id: str
    subscription_id: str
    bot_id: str
    bot_name: str
    symbol: str
    state: BotDataState = BotDataState.METRICS_PENDING

    # Core metrics (None if not available)
    daily_pnl: Optional[float] = None
    weekly_pnl: Optional[float] = None
    win_rate_live: Optional[float] = None
    daily_trades: Optional[int] = None
    last_trade_at: Optional[str] = None
    is_running: bool = False
    broker: Optional[str] = None
    broker_connected: bool = False

    # Fallback context
    fallback_message: str = ""
    action_required: Optional[str] = None
    setup_url: str = "https://algochains.ai/dashboard/bots"

    # Validated backtested metrics (always show even if live metrics pending)
    sharpe_validated: Optional[float] = None
    max_dd_validated: Optional[float] = None
    win_rate_validated: Optional[float] = None
    mcpt_badge: str = ""

    as_of: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return asdict(self)


def _sb_available() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SERVICE_KEY)


def _sb_headers() -> dict:
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
    }


# ── Fallback state constructors ───────────────────────────────────────────────

def broker_not_connected_state(
    user_id: str,
    subscription_id: str,
    bot_id: str,
    bot_name: str,
    symbol: str,
    broker: Optional[str] = None,
) -> UserBotMetrics:
    """Return the canonical 'Broker Not Connected' fallback state."""
    return UserBotMetrics(
        user_id=user_id,
        subscription_id=subscription_id,
        bot_id=bot_id,
        bot_name=bot_name,
        symbol=symbol,
        state=BotDataState.BROKER_NOT_CONNECTED,
        broker=broker,
        broker_connected=False,
        is_running=False,
        fallback_message=(
            f"Connect your {broker or 'broker'} account to start receiving "
            f"{bot_name} signals."
        ),
        action_required="connect_broker",
        setup_url=f"https://algochains.ai/dashboard/connect-broker?bot={bot_id}",
    )


def metrics_pending_state(
    user_id: str,
    subscription_id: str,
    bot_id: str,
    bot_name: str,
    symbol: str,
    hours_active: float = 0.0,
) -> UserBotMetrics:
    """Return the canonical 'Metrics Pending' fallback state (bot running, not enough data)."""
    hours_remaining = max(0, 48 - hours_active)
    return UserBotMetrics(
        user_id=user_id,
        subscription_id=subscription_id,
        bot_id=bot_id,
        bot_name=bot_name,
        symbol=symbol,
        state=BotDataState.METRICS_PENDING,
        broker_connected=True,
        is_running=True,
        fallback_message=(
            f"{bot_name} is connected and running. Live performance metrics will be "
            f"available after {hours_remaining:.0f}h of trading activity."
        ),
        action_required=None,
        setup_url=f"https://algochains.ai/dashboard/bots/{bot_id}",
    )


def data_stale_state(
    user_id: str,
    subscription_id: str,
    bot_id: str,
    bot_name: str,
    symbol: str,
    last_update: Optional[str] = None,
) -> UserBotMetrics:
    """Return 'Data Stale' state when last metrics update was too long ago."""
    return UserBotMetrics(
        user_id=user_id,
        subscription_id=subscription_id,
        bot_id=bot_id,
        bot_name=bot_name,
        symbol=symbol,
        state=BotDataState.DATA_STALE,
        broker_connected=True,
        last_trade_at=last_update,
        fallback_message=(
            "Metrics haven't updated recently. The bot may have paused during "
            "low-volatility market conditions. Check the bot status page."
        ),
        action_required="check_bot_status",
        setup_url=f"https://algochains.ai/dashboard/bots/{bot_id}/health",
    )


# ── Supabase performance fetch ────────────────────────────────────────────────

async def _fetch_managed_bot_metrics(
    subscription_id: str,
    bot_id: str,
) -> Optional[dict]:
    """Fetch metrics for a managed (marketplace) bot from Supabase."""
    if not _sb_available():
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{SUPABASE_URL.rstrip('/')}/rest/v1/{_TABLE_PERF}"
                f"?subscription_id=eq.{subscription_id}&bot_id=eq.{bot_id}"
                f"&order=recorded_at.desc&limit=1",
                headers=_sb_headers(),
            )
            if resp.status_code == 200 and resp.json():
                return resp.json()[0]
    except Exception as e:
        logger.error("Supabase managed bot metrics fetch error: %s", e)
    return None


async def _fetch_subscription(user_id: str, subscription_id: str) -> Optional[dict]:
    """Fetch subscription details for a user."""
    if not _sb_available():
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{SUPABASE_URL.rstrip('/')}/rest/v1/{_TABLE_SUBS}"
                f"?user_id=eq.{user_id}&id=eq.{subscription_id}&limit=1",
                headers=_sb_headers(),
            )
            if resp.status_code == 200 and resp.json():
                return resp.json()[0]
    except Exception as e:
        logger.error("Supabase subscription fetch error: %s", e)
    return None


# ── Core multi-account API ────────────────────────────────────────────────────

async def get_user_bot_metrics(
    user_id: str,
    bot_id: str,
    subscription_id: str,
    log_path: Optional[str] = None,
) -> UserBotMetrics:
    """
    Get metrics for a specific bot in the context of a user's subscription.

    Resolution order:
      1. Owner bots (Tyler's bots) → parse live log
      2. Managed marketplace bots → fetch from Supabase performance table
      3. Self-hosted subscriber bots → parse subscriber's log path
      4. Fallback state → broker_not_connected / metrics_pending / data_stale

    Args:
        user_id:         Supabase user ID
        bot_id:          Bot identifier (mnq, cl, mes, nq, or marketplace bot UUID)
        subscription_id: Subscription record ID
        log_path:        Optional custom log file path (for self-hosted subscribers)

    Returns:
        UserBotMetrics with state indicating data quality
    """
    meta = BOT_META.get(bot_id.lower(), {})
    bot_name = meta.get("display_name", bot_id.upper())
    symbol = meta.get("symbol", bot_id.upper())

    # Check subscription status
    sub = await _fetch_subscription(user_id, subscription_id)
    if sub and sub.get("status") not in ("active", "trial", None):
        return UserBotMetrics(
            user_id=user_id,
            subscription_id=subscription_id,
            bot_id=bot_id,
            bot_name=bot_name,
            symbol=symbol,
            state=BotDataState.SUBSCRIPTION_INACTIVE,
            fallback_message="Your subscription is inactive. Renew to access live metrics.",
            action_required="renew_subscription",
            setup_url=f"https://algochains.ai/marketplace/bots/{bot_id}/subscribe",
        )

    broker_connected = sub.get("broker_connected", False) if sub else False

    if sub is None and bot_id.lower() in BOT_META:
        return broker_not_connected_state(
            user_id, subscription_id, bot_id, bot_name, symbol,
            broker=meta.get("broker"),
        )

    # Managed AlgoChains bots: parse the live log only after subscription lookup.
    if sub is not None and bot_id.lower() in BOT_META:
        try:
            raw_metrics = parse_bot_metrics(bot_id)
            # Determine state
            if not raw_metrics.is_running and raw_metrics.daily_trades == 0:
                if not broker_connected:
                    return broker_not_connected_state(
                        user_id, subscription_id, bot_id, bot_name, symbol,
                        broker=meta.get("broker"),
                    )
                return data_stale_state(user_id, subscription_id, bot_id, bot_name, symbol)

            return UserBotMetrics(
                user_id=user_id,
                subscription_id=subscription_id,
                bot_id=bot_id,
                bot_name=raw_metrics.display_name,
                symbol=raw_metrics.symbol,
                state=BotDataState.LIVE,
                daily_pnl=raw_metrics.daily_pnl,
                win_rate_live=raw_metrics.win_rate_today,
                daily_trades=raw_metrics.daily_trades,
                is_running=raw_metrics.is_running,
                broker=meta.get("broker"),
                broker_connected=True,
                sharpe_validated=raw_metrics.sharpe_validated,
                max_dd_validated=raw_metrics.max_dd_validated,
                win_rate_validated=raw_metrics.win_rate_validated,
                mcpt_badge=raw_metrics.mcpt_badge,
            )
        except Exception as e:
            logger.error("parse_bot_metrics failed for %s: %s", bot_id, e)

    # Self-hosted subscriber with custom log path
    if log_path:
        custom_path = Path(log_path)
        if custom_path.exists():
            try:
                raw_metrics = parse_bot_metrics(bot_id)
                if raw_metrics.daily_trades < 1:
                    hours_active = (time.time() - custom_path.stat().st_mtime) / 3600
                    if hours_active < 48:
                        return metrics_pending_state(
                            user_id, subscription_id, bot_id, bot_name, symbol, hours_active
                        )
                return UserBotMetrics(
                    user_id=user_id,
                    subscription_id=subscription_id,
                    bot_id=bot_id,
                    bot_name=bot_name,
                    symbol=symbol,
                    state=BotDataState.LIVE,
                    daily_pnl=raw_metrics.daily_pnl,
                    win_rate_live=raw_metrics.win_rate_today,
                    daily_trades=raw_metrics.daily_trades,
                    is_running=raw_metrics.is_running,
                    broker_connected=True,
                )
            except Exception as e:
                logger.error("Self-hosted log parse error: %s", e)

    # Managed bot: fetch from Supabase performance table
    perf = await _fetch_managed_bot_metrics(subscription_id, bot_id)
    if perf:
        last_update = perf.get("recorded_at", "")
        stale = False
        if last_update:
            try:
                last_ts = datetime.fromisoformat(last_update.replace("Z", "+00:00")).timestamp()
                stale = (time.time() - last_ts) > _STALE_THRESHOLD_SEC
            except Exception:
                stale = True

        if stale:
            return data_stale_state(
                user_id, subscription_id, bot_id, bot_name, symbol, last_update
            )

        return UserBotMetrics(
            user_id=user_id,
            subscription_id=subscription_id,
            bot_id=bot_id,
            bot_name=bot_name,
            symbol=symbol,
            state=BotDataState.LIVE,
            daily_pnl=perf.get("daily_pnl"),
            weekly_pnl=perf.get("weekly_pnl"),
            win_rate_live=perf.get("win_rate"),
            daily_trades=perf.get("trade_count"),
            last_trade_at=perf.get("last_trade_at"),
            is_running=perf.get("is_running", False),
            broker=perf.get("broker"),
            broker_connected=True,
            sharpe_validated=perf.get("sharpe_ratio"),
            max_dd_validated=perf.get("max_drawdown"),
            win_rate_validated=perf.get("win_rate_validated"),
        )

    # No data found → determine best fallback
    if not broker_connected:
        return broker_not_connected_state(
            user_id, subscription_id, bot_id, bot_name, symbol,
            broker=meta.get("broker") if meta else None,
        )
    return metrics_pending_state(user_id, subscription_id, bot_id, bot_name, symbol)


async def get_all_user_bots(user_id: str) -> dict[str, Any]:
    """
    Get metrics for all bots a user is subscribed to.

    Fetches subscription list from Supabase, then resolves metrics
    for each bot with appropriate fallback states.

    Returns:
        bots: list of UserBotMetrics dicts
        summary: aggregate stats (total, live, pending, not_connected)
    """
    bots: list[dict] = []
    subscriptions: list[dict] = []

    if _sb_available():
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    f"{SUPABASE_URL.rstrip('/')}/rest/v1/{_TABLE_SUBS}"
                    f"?user_id=eq.{user_id}&status=in.(active,trial)"
                    f"&order=created_at.desc",
                    headers=_sb_headers(),
                )
                if resp.status_code == 200:
                    subscriptions = resp.json()
        except Exception as e:
            logger.error("Supabase subscriptions fetch error: %s", e)

    if subscriptions:
        for sub in subscriptions:
            bot_id = sub.get("bot_id", "")
            sub_id = sub.get("id", "")
            log_path = sub.get("log_path")
            if bot_id:
                m = await get_user_bot_metrics(user_id, bot_id, sub_id, log_path)
                bots.append(m.to_dict())

    # Compute summary
    state_counts: dict[str, int] = {}
    for b in bots:
        s = b.get("state", "unknown")
        state_counts[s] = state_counts.get(s, 0) + 1

    return {
        "success": True,
        "user_id": user_id,
        "bots": bots,
        "total": len(bots),
        "summary": state_counts,
        "live_count": state_counts.get(BotDataState.LIVE.value, 0),
        "pending_count": state_counts.get(BotDataState.METRICS_PENDING.value, 0),
        "not_connected_count": state_counts.get(BotDataState.BROKER_NOT_CONNECTED.value, 0),
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


async def upsert_managed_bot_performance(
    subscription_id: str,
    bot_id: str,
    daily_pnl: float,
    win_rate: float,
    trade_count: int,
    is_running: bool,
    broker: str,
    sharpe_ratio: Optional[float] = None,
    max_drawdown: Optional[float] = None,
    win_rate_validated: Optional[float] = None,
    last_trade_at: Optional[str] = None,
    weekly_pnl: Optional[float] = None,
) -> dict[str, Any]:
    """
    Upsert real performance data for a managed bot subscription.
    Called by the metrics streaming daemon when a bot reports fills.
    All values must come from actual execution — no synthetic data.
    """
    if not _sb_available():
        return {"success": False, "error": "Supabase not configured"}

    record = {
        "subscription_id": subscription_id,
        "bot_id": bot_id,
        "daily_pnl": daily_pnl,
        "weekly_pnl": weekly_pnl,
        "win_rate": win_rate,
        "trade_count": trade_count,
        "is_running": is_running,
        "broker": broker,
        "sharpe_ratio": sharpe_ratio,
        "max_drawdown": max_drawdown,
        "win_rate_validated": win_rate_validated,
        "last_trade_at": last_trade_at,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{SUPABASE_URL.rstrip('/')}/rest/v1/{_TABLE_PERF}",
                headers={**_sb_headers(), "Prefer": "resolution=merge-duplicates,return=representation"},
                json=record,
            )
            if resp.status_code in (200, 201):
                return {"success": True, "recorded": True}
            return {"success": False, "error": f"Supabase upsert failed: {resp.status_code}"}
    except Exception as e:
        return {"success": False, "error": str(e)}
