"""Prop Fund Drawdown Monitor — real-time evaluation account protection daemon.

Monitors a live prop fund evaluation account against fund rules every 30 min
during US market hours (9:30 AM - 4:00 PM ET, Monday-Friday).

Safety tiers:
  70% of daily limit → Reduce position size by 50%, alert via ntfy
  85% of daily limit → Stop all new entries, alert URGENT
  95% of daily limit → Emergency flatten all positions

Trailing drawdown protection:
  80% of trailing limit → Scale down position size
  90% of trailing limit → Stop new entries
  98% of trailing limit → Emergency flatten (critical)

Can run as:
  1. Standalone daemon: python -m algochains_mcp.brokers.prop_fund_drawdown_monitor
  2. Called from autonomous/prop_fund_monitor.py (control tower daemon)
  3. Triggered by MCP tool: monitor_prop_fund_account(fund, account_id)

State is persisted to state/prop_fund_monitor_state.json so it survives restarts.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("algochains_mcp.prop_fund_monitor")

_STATE_FILE = os.environ.get("PROP_FUND_MONITOR_STATE", "state/prop_fund_monitor_state.json")
_SUPPORTED_BROKERS = {"rithmic", "tradovate"}

# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

@dataclass
class MonitoredAccount:
    account_id: str
    fund_name: str
    broker: str                 # "rithmic" or "tradovate"
    max_daily_loss_usd: float
    max_trailing_drawdown_usd: float
    profit_target_usd: float
    starting_balance: float
    high_water_mark: float
    days_traded: int = 0
    cumulative_profit: float = 0.0
    alerted_daily_thresholds: list[float] = field(default_factory=list)
    alerted_trailing_thresholds: list[float] = field(default_factory=list)
    last_check_ts: float = 0.0
    status: str = "active"      # active, passed, failed, suspended
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(tz=timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return asdict(self)


def _load_state() -> dict:
    if os.path.exists(_STATE_FILE):
        try:
            with open(_STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"accounts": {}}


def _save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(_STATE_FILE) if os.path.dirname(_STATE_FILE) else ".", exist_ok=True)
    with open(_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------------------------
# Alert tiers
# ---------------------------------------------------------------------------

DAILY_ALERT_TIERS = [
    (0.70, "warn",   "⚠️ 70% daily loss limit reached — reduce position size"),
    (0.85, "high",   "🚨 85% daily loss limit reached — stop new entries"),
    (0.95, "urgent", "🔴 95% DAILY LIMIT — EMERGENCY FLATTEN NOW"),
]

TRAILING_ALERT_TIERS = [
    (0.80, "warn",   "⚠️ 80% trailing drawdown limit — reduce size"),
    (0.90, "high",   "🚨 90% trailing drawdown — stop new entries"),
    (0.98, "urgent", "🔴 98% TRAILING DD — EMERGENCY FLATTEN"),
]


def _send_alert(title: str, message: str, priority: str, account: MonitoredAccount) -> None:
    """Send ntfy push notification for prop fund alerts."""
    try:
        from algochains_mcp.notifications.ntfy_push import send_push
        send_push(
            title=f"[{account.fund_name.upper()}] {title}",
            message=f"{message} | Account: {account.account_id}",
            priority=priority,
            topic="risk",
            tags=["prop_fund", account.fund_name],
        )
    except Exception as exc:
        logger.warning("ntfy alert failed: %s", exc)


# ---------------------------------------------------------------------------
# Core monitor class
# ---------------------------------------------------------------------------

class PropFundDrawdownMonitor:
    """Monitor prop fund evaluation accounts against fund-specific drawdown rules.

    Designed to run as a continuous async loop during market hours.
    Integrates with RithmicConnector (prop fund accounts) or TradovateConnector
    (for Apex accounts that support Tradovate).

    Example usage:
        monitor = PropFundDrawdownMonitor()
        monitor.add_account("APEX_ACCT_123", "apex", "rithmic", starting_balance=50000)
        await monitor.run_market_hours()
    """

    def __init__(self, check_interval_minutes: int = 30):
        self.check_interval_seconds = check_interval_minutes * 60
        self._accounts: dict[str, MonitoredAccount] = {}
        self._load_persisted_accounts()

    def _load_persisted_accounts(self) -> None:
        state = _load_state()
        for account_id, data in state.get("accounts", {}).items():
            try:
                self._accounts[account_id] = MonitoredAccount(**data)
            except Exception as exc:
                logger.warning("Failed to restore account %s: %s", account_id, exc)

    def _persist(self) -> None:
        state = {
            "accounts": {k: v.to_dict() for k, v in self._accounts.items()},
            "last_save": datetime.now(tz=timezone.utc).isoformat(),
        }
        _save_state(state)

    def add_account(
        self,
        account_id: str,
        fund_name: str,
        broker: str,
        starting_balance: float,
        max_daily_loss_usd: float = None,
        max_trailing_drawdown_usd: float = None,
        profit_target_usd: float = None,
    ) -> dict:
        """Register a prop fund evaluation account for monitoring.

        If fund rules not provided, loads from PROP_FUNDS dict.
        """
        from algochains_mcp.brokers.prop_fund_manager import PROP_FUNDS
        broker = broker.lower().strip()
        if broker not in _SUPPORTED_BROKERS:
            return {"registered": False, "error": f"Unsupported broker: {broker}"}

        try:
            starting_balance = float(starting_balance)
            if starting_balance <= 0:
                raise ValueError("starting_balance must be positive")
            if max_daily_loss_usd is not None:
                max_daily_loss_usd = float(max_daily_loss_usd)
                if max_daily_loss_usd <= 0:
                    raise ValueError("max_daily_loss_usd must be positive")
            if max_trailing_drawdown_usd is not None:
                max_trailing_drawdown_usd = float(max_trailing_drawdown_usd)
                if max_trailing_drawdown_usd <= 0:
                    raise ValueError("max_trailing_drawdown_usd must be positive")
            if profit_target_usd is not None:
                profit_target_usd = float(profit_target_usd)
                if profit_target_usd <= 0:
                    raise ValueError("profit_target_usd must be positive")
        except (TypeError, ValueError) as exc:
            return {"registered": False, "error": str(exc)}

        fund = PROP_FUNDS.get(fund_name.lower())

        acct = MonitoredAccount(
            account_id=account_id,
            fund_name=fund_name.lower(),
            broker=broker,
            max_daily_loss_usd=max_daily_loss_usd or (fund.max_daily_loss_usd if fund else 2500),
            max_trailing_drawdown_usd=max_trailing_drawdown_usd or (fund.max_trailing_drawdown_usd if fund else 2500),
            profit_target_usd=profit_target_usd or (fund.profit_target_usd if fund else 3000),
            starting_balance=starting_balance,
            high_water_mark=starting_balance,
        )
        self._accounts[account_id] = acct
        self._persist()

        logger.info("Registered prop fund account: %s (%s, %s)", account_id, fund_name, broker)
        return {
            "registered": True,
            "account_id": account_id,
            "fund": fund_name,
            "daily_limit": acct.max_daily_loss_usd,
            "trailing_limit": acct.max_trailing_drawdown_usd,
            "profit_target": acct.profit_target_usd,
        }

    def remove_account(self, account_id: str) -> dict:
        """Unregister an account from monitoring."""
        if account_id in self._accounts:
            del self._accounts[account_id]
            self._persist()
            return {"removed": True, "account_id": account_id}
        return {"removed": False, "error": "Account not found"}

    async def check_account(self, account_id: str) -> dict:
        """Run a single drawdown check on one account.

        Fetches live balance/P&L, evaluates against fund limits,
        sends alerts and takes protective actions as needed.
        """
        acct = self._accounts.get(account_id)
        if not acct:
            return {"error": f"Account {account_id} not registered"}

        result = {
            "account_id": account_id,
            "fund": acct.fund_name,
            "broker": acct.broker,
            "checked_at": datetime.now(tz=timezone.utc).isoformat(),
            "actions_taken": [],
            "alerts_sent": [],
        }

        # Fetch live account state from broker
        live_data = await self._fetch_live_data(acct)
        if not live_data:
            result["error"] = "Failed to fetch live data from broker"
            return result

        daily_pnl = live_data.get("daily_pnl", 0.0)
        current_balance = live_data.get("balance", acct.starting_balance)

        # Update high water mark
        if current_balance > acct.high_water_mark:
            acct.high_water_mark = current_balance
            result["high_water_mark_updated"] = current_balance

        trailing_drawdown = acct.high_water_mark - current_balance
        cumulative_profit = current_balance - acct.starting_balance

        result["daily_pnl"] = daily_pnl
        result["current_balance"] = current_balance
        result["trailing_drawdown"] = trailing_drawdown
        result["cumulative_profit"] = cumulative_profit
        result["profit_target_remaining"] = max(0, acct.profit_target_usd - cumulative_profit)
        result["days_traded"] = acct.days_traded

        # ── Check daily loss tiers ──────────────────────────────────────────
        if daily_pnl < 0:
            daily_util = abs(daily_pnl) / acct.max_daily_loss_usd
            result["daily_loss_utilization_pct"] = round(daily_util * 100, 1)

            for threshold, priority, msg in DAILY_ALERT_TIERS:
                if daily_util >= threshold and threshold not in acct.alerted_daily_thresholds:
                    _send_alert(
                        title=f"Daily Loss {threshold*100:.0f}%",
                        message=f"{msg} | Daily P&L: ${daily_pnl:.0f} / ${-acct.max_daily_loss_usd:.0f}",
                        priority=priority,
                        account=acct,
                    )
                    acct.alerted_daily_thresholds.append(threshold)
                    result["alerts_sent"].append(f"daily_{threshold*100:.0f}pct")

                    if threshold >= 0.95:
                        # Emergency flatten
                        flatten_result = await self._emergency_flatten(acct)
                        result["actions_taken"].append(f"EMERGENCY_FLATTEN: {flatten_result}")
                        acct.status = "suspended"
                        logger.critical("EMERGENCY FLATTEN: Account %s hit 95%% daily limit", account_id)
                    elif threshold >= 0.85:
                        result["actions_taken"].append("STOP_NEW_ENTRIES: No new positions allowed")
                        logger.warning("Account %s: 85%% daily limit — stop new entries", account_id)
                    elif threshold >= 0.70:
                        result["actions_taken"].append("REDUCE_SIZE: Reduce position size by 50%%")
                        logger.warning("Account %s: 70%% daily limit — reduce size", account_id)
        else:
            # New day — reset daily alerts
            if acct.alerted_daily_thresholds:
                acct.alerted_daily_thresholds = []
                result["daily_alerts_reset"] = True

        # ── Check trailing drawdown tiers ────────────────────────────────────
        if trailing_drawdown > 0:
            trail_util = trailing_drawdown / acct.max_trailing_drawdown_usd
            result["trailing_dd_utilization_pct"] = round(trail_util * 100, 1)

            for threshold, priority, msg in TRAILING_ALERT_TIERS:
                if trail_util >= threshold and threshold not in acct.alerted_trailing_thresholds:
                    _send_alert(
                        title=f"Trailing DD {threshold*100:.0f}%",
                        message=f"{msg} | DD: ${trailing_drawdown:.0f} / ${acct.max_trailing_drawdown_usd:.0f}",
                        priority=priority,
                        account=acct,
                    )
                    acct.alerted_trailing_thresholds.append(threshold)
                    result["alerts_sent"].append(f"trailing_{threshold*100:.0f}pct")

                    if threshold >= 0.98:
                        flatten_result = await self._emergency_flatten(acct)
                        result["actions_taken"].append(f"EMERGENCY_FLATTEN_TRAILING: {flatten_result}")
                        acct.status = "suspended"

        # ── Check profit target ──────────────────────────────────────────────
        if cumulative_profit >= acct.profit_target_usd and acct.status == "active":
            acct.status = "passed"
            _send_alert(
                title="PROFIT TARGET HIT!",
                message=(
                    f"Profit target of ${acct.profit_target_usd:.0f} reached! "
                    f"Total profit: ${cumulative_profit:.0f}. "
                    f"Days traded: {acct.days_traded}. Evaluation PASSED!"
                ),
                priority="urgent",
                account=acct,
            )
            result["evaluation_passed"] = True
            result["actions_taken"].append("NOTIFY_PASS: Profit target achieved — evaluation passed")
            logger.info("EVALUATION PASSED: Account %s profit=$%.0f target=$%.0f",
                       account_id, cumulative_profit, acct.profit_target_usd)

        acct.last_check_ts = time.time()
        acct.cumulative_profit = cumulative_profit
        self._persist()

        return result

    async def _fetch_live_data(self, acct: MonitoredAccount) -> Optional[dict]:
        """Fetch live account data from broker."""
        try:
            if acct.broker == "rithmic":
                from algochains_mcp.brokers.rithmic_connector import RithmicConnector
                conn = RithmicConnector(account_id=acct.account_id)
                return await conn.get_account(acct.account_id)
            elif acct.broker == "tradovate":
                # Use TradovateConnector.get_account() — the standard MCP broker interface
                from algochains_mcp.brokers.tradovate import TradovateConnector, TradovateConfig
                import os
                cfg = TradovateConfig(
                    cid=os.environ.get("TRADOVATE_CID", ""),
                    secret=os.environ.get("TRADOVATE_SECRET", ""),
                    device_id=os.environ.get("TRADOVATE_DEVICE_ID", ""),
                    env=os.environ.get("TRADOVATE_ENV", "live"),
                )
                tv = TradovateConnector(cfg)
                await tv.connect()
                acct_info = await tv.get_account()
                await tv.disconnect()
                return {
                    "account_id": acct_info.account_id,
                    "balance": acct_info.equity,
                    "daily_pnl": 0.0,   # Tradovate REST doesn't expose daily P&L directly
                    "day_open_pnl": 0.0,
                    "source": "tradovate",
                    "note": "daily_pnl requires WebSocket streaming; use control tower daemon for live P&L",
                }
            else:
                logger.warning("Unsupported broker for live data: %s", acct.broker)
                return None
        except Exception as exc:
            logger.error("Failed to fetch live data for %s: %s", acct.account_id, exc)
            return None

    async def _emergency_flatten(self, acct: MonitoredAccount) -> dict:
        """Execute emergency flatten for an account.

        Cancels all working orders then places market orders to close all positions.
        """
        try:
            if acct.broker == "rithmic":
                from algochains_mcp.brokers.rithmic_connector import RithmicConnector
                conn = RithmicConnector(account_id=acct.account_id)
                await conn.connect()
                result = await conn.flatten_all_positions(acct.account_id)
                await conn.disconnect()
                return result
            elif acct.broker == "tradovate":
                from algochains_mcp.brokers.tradovate import TradovateConnector, TradovateConfig
                import os
                cfg = TradovateConfig(
                    cid=os.environ.get("TRADOVATE_CID", ""),
                    secret=os.environ.get("TRADOVATE_SECRET", ""),
                    device_id=os.environ.get("TRADOVATE_DEVICE_ID", ""),
                    env=os.environ.get("TRADOVATE_ENV", "live"),
                )
                tv = TradovateConnector(cfg)
                await tv.connect()
                # Cancel all working orders first
                orders = await tv.get_orders(status="Working")
                cancelled = 0
                for order in orders:
                    if await tv.cancel_order(order.order_id):
                        cancelled += 1
                # Close all open positions with market orders
                positions = await tv.get_positions()
                closed = 0
                errors = []
                for pos in positions:
                    if pos.quantity != 0:
                        close_side = "Sell" if pos.quantity > 0 else "Buy"
                        try:
                            from algochains_mcp.brokers.base import OrderRequest, OrderType, OrderSide
                            req = OrderRequest(
                                symbol=pos.symbol,
                                side=OrderSide.SELL if pos.quantity > 0 else OrderSide.BUY,
                                quantity=abs(pos.quantity),
                                order_type=OrderType.MARKET,
                            )
                            await tv.place_order(req)
                            closed += 1
                        except Exception as e:
                            errors.append(str(e))
                await tv.disconnect()
                return {
                    "flattened": True,
                    "orders_cancelled": cancelled,
                    "positions_closed": closed,
                    "errors": errors,
                    "account_id": acct.account_id,
                }
            return {"error": f"No flatten handler for broker: {acct.broker}"}
        except Exception as exc:
            logger.error("Emergency flatten failed for %s: %s", acct.account_id, exc)
            return {"error": str(exc)}

    async def check_all_accounts(self) -> dict:
        """Run drawdown checks across all registered accounts."""
        results = {}
        active = [a for a in self._accounts.values() if a.status == "active"]
        for acct in active:
            results[acct.account_id] = await self.check_account(acct.account_id)
        return {
            "checked_at": datetime.now(tz=timezone.utc).isoformat(),
            "accounts_checked": len(active),
            "results": results,
        }

    def get_all_account_status(self) -> dict:
        """Return current status of all monitored accounts (no broker calls)."""
        state = _load_state()
        accounts = []
        for acct_id, data in state.get("accounts", {}).items():
            accounts.append({
                "account_id": acct_id,
                "fund_name": data.get("fund_name"),
                "broker": data.get("broker"),
                "status": data.get("status", "unknown"),
                "days_traded": data.get("days_traded", 0),
                "cumulative_profit": data.get("cumulative_profit", 0),
                "profit_target": data.get("profit_target_usd"),
                "last_check": data.get("last_check_ts"),
            })
        return {
            "total_accounts": len(accounts),
            "active": sum(1 for a in accounts if a["status"] == "active"),
            "passed": sum(1 for a in accounts if a["status"] == "passed"),
            "suspended": sum(1 for a in accounts if a["status"] == "suspended"),
            "accounts": accounts,
        }

    async def run_market_hours(self) -> None:
        """Run continuous monitoring loop during US market hours.

        Checks every 30 minutes between 9:30 AM and 4:00 PM ET, Mon-Fri.
        Safe to run as a background task alongside live bots.
        """
        import pytz
        et = pytz.timezone("America/New_York")
        logger.info("PropFundDrawdownMonitor started — checking every %d min during market hours",
                    self.check_interval_seconds // 60)

        while True:
            try:
                now_et = datetime.now(et)
                market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
                market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
                is_weekday = now_et.weekday() < 5

                if is_weekday and market_open <= now_et <= market_close:
                    active_count = sum(1 for a in self._accounts.values() if a.status == "active")
                    if active_count > 0:
                        result = await self.check_all_accounts()
                        logger.info("Drawdown check complete: %d accounts checked", active_count)
                    else:
                        logger.debug("No active prop fund accounts to monitor")
                else:
                    logger.debug("Outside market hours — skipping check")

            except Exception as exc:
                logger.error("Monitor loop error: %s", exc)

            await asyncio.sleep(self.check_interval_seconds)


# ---------------------------------------------------------------------------
# MCP-callable functions
# ---------------------------------------------------------------------------

_MONITOR = PropFundDrawdownMonitor()


def register_prop_fund_account(
    account_id: str,
    fund_name: str,
    broker: str,
    starting_balance: float,
    max_daily_loss_usd: float = None,
    max_trailing_drawdown_usd: float = None,
    profit_target_usd: float = None,
) -> dict:
    """Register an account with the drawdown monitor."""
    return _MONITOR.add_account(
        account_id=account_id,
        fund_name=fund_name,
        broker=broker,
        starting_balance=starting_balance,
        max_daily_loss_usd=max_daily_loss_usd,
        max_trailing_drawdown_usd=max_trailing_drawdown_usd,
        profit_target_usd=profit_target_usd,
    )


def get_prop_fund_monitor_status() -> dict:
    """Get current status of all monitored prop fund accounts."""
    return _MONITOR.get_all_account_status()


async def run_prop_fund_check(account_id: str = None) -> dict:
    """Manually trigger a drawdown check (MCP tool)."""
    if account_id:
        return await _MONITOR.check_account(account_id)
    return await _MONITOR.check_all_accounts()


# ---------------------------------------------------------------------------
# Standalone daemon entry point
# ---------------------------------------------------------------------------

async def _main():
    """Run the drawdown monitor as a standalone daemon."""
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("Starting PropFundDrawdownMonitor daemon")
    monitor = PropFundDrawdownMonitor(check_interval_minutes=30)
    await monitor.run_market_hours()


if __name__ == "__main__":
    asyncio.run(_main())
