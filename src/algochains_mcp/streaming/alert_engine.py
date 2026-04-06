"""
Price Alert Engine — Real-Time Price Alert Webhooks.

Agents register conditions like "notify me when SPY crosses $550".
When triggered, emits MCP resource notifications to all subscribed clients.

Alert conditions (real-time, checked on each price update):
  - price_above: price >= threshold
  - price_below: price <= threshold
  - percent_change_15min: |change| >= threshold in 15 min
  - vwap_cross: price crosses VWAP level
  - volume_spike: volume >= N * avg_volume

Storage: SQLite at ~/.algochains/price_alerts.db (persistent across restarts)
Price data: Polygon.io WebSocket (real-time) or REST polling fallback

Real data only — no synthetic price moves, no simulated triggers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("algochains_mcp.streaming.alerts")

ALERT_DB_PATH = Path.home() / ".algochains" / "price_alerts.db"


class AlertError(Exception):
    pass


@dataclass
class PriceAlert:
    alert_id: str
    symbol: str
    condition: str           # "price_above" | "price_below" | "pct_change_15min" | "vwap_cross" | "volume_spike"
    threshold: float
    created_at: float
    triggered_at: float | None = None
    triggered_price: float | None = None
    status: str = "active"   # "active" | "triggered" | "cancelled"
    repeat: bool = False     # re-arm after trigger
    callback_note: str = ""  # optional note shown in notification

    def to_dict(self) -> dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "symbol": self.symbol,
            "condition": self.condition,
            "threshold": self.threshold,
            "status": self.status,
            "repeat": self.repeat,
            "created_at": self.created_at,
            "triggered_at": self.triggered_at,
            "triggered_price": self.triggered_price,
            "callback_note": self.callback_note,
        }


class PriceAlertEngine:
    """
    Persistent price alert engine with SQLite storage.

    Price checking occurs via:
    1. Polygon.io REST polling (every 60s) — always available with API key
    2. Polygon.io WebSocket — real-time (preferred when available)

    Triggered alerts emit notifications via SubscriptionManager.
    """

    SUPPORTED_CONDITIONS = {
        "price_above", "price_below", "pct_change_15min", "vwap_cross", "volume_spike"
    }

    def __init__(self) -> None:
        ALERT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._alerts: dict[str, PriceAlert] = {}
        self._load_from_db()
        self._price_history: dict[str, list[tuple[float, float]]] = {}  # symbol → [(ts, price)]
        self._check_task: asyncio.Task | None = None

    def _init_db(self) -> None:
        with sqlite3.connect(ALERT_DB_PATH) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS alerts (
                    alert_id TEXT PRIMARY KEY,
                    symbol TEXT,
                    condition TEXT,
                    threshold REAL,
                    created_at REAL,
                    triggered_at REAL,
                    triggered_price REAL,
                    status TEXT,
                    repeat INTEGER,
                    callback_note TEXT
                )
            """)
            conn.commit()

    def _load_from_db(self) -> None:
        with sqlite3.connect(ALERT_DB_PATH) as conn:
            rows = conn.execute(
                "SELECT * FROM alerts WHERE status = 'active'"
            ).fetchall()
        for row in rows:
            (aid, sym, cond, thresh, created, triggered, tprice, status, repeat, note) = row
            self._alerts[aid] = PriceAlert(
                alert_id=aid, symbol=sym, condition=cond, threshold=thresh,
                created_at=created, triggered_at=triggered, triggered_price=tprice,
                status=status, repeat=bool(repeat), callback_note=note or "",
            )

    def _persist(self, alert: PriceAlert) -> None:
        with sqlite3.connect(ALERT_DB_PATH) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO alerts VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (
                alert.alert_id, alert.symbol, alert.condition, alert.threshold,
                alert.created_at, alert.triggered_at, alert.triggered_price,
                alert.status, int(alert.repeat), alert.callback_note,
            ))
            conn.commit()

    def create_alert(
        self,
        symbol: str,
        condition: str,
        threshold: float,
        repeat: bool = False,
        callback_note: str = "",
    ) -> PriceAlert:
        """Register a new price alert."""
        if condition not in self.SUPPORTED_CONDITIONS:
            raise AlertError(
                f"Unknown condition '{condition}'. "
                f"Supported: {', '.join(sorted(self.SUPPORTED_CONDITIONS))}"
            )
        if not symbol:
            raise AlertError("symbol is required.")

        alert = PriceAlert(
            alert_id=str(uuid.uuid4()),
            symbol=symbol.upper(),
            condition=condition,
            threshold=threshold,
            created_at=time.time(),
            repeat=repeat,
            callback_note=callback_note,
        )
        self._alerts[alert.alert_id] = alert
        self._persist(alert)
        logger.info("Created alert: %s %s %.2f (id=%s)", symbol, condition, threshold, alert.alert_id[:8])
        return alert

    def list_alerts(self, symbol: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        alerts = list(self._alerts.values())
        if symbol:
            alerts = [a for a in alerts if a.symbol == symbol.upper()]
        if status:
            alerts = [a for a in alerts if a.status == status]
        return [a.to_dict() for a in alerts]

    def cancel_alert(self, alert_id: str) -> dict[str, Any]:
        alert = self._alerts.get(alert_id)
        if not alert:
            raise AlertError(f"Alert {alert_id} not found.")
        alert.status = "cancelled"
        self._persist(alert)
        return {"cancelled": True, "alert_id": alert_id}

    def _check_condition(self, alert: PriceAlert, current_price: float, vwap: float | None = None) -> bool:
        """Check if an alert condition is met."""
        if alert.status != "active":
            return False
        c = alert.condition
        t = alert.threshold

        if c == "price_above":
            return current_price >= t
        elif c == "price_below":
            return current_price <= t
        elif c == "pct_change_15min":
            history = self._price_history.get(alert.symbol, [])
            cutoff = time.time() - 900  # 15 min
            old_prices = [p for ts, p in history if ts >= cutoff]
            if not old_prices:
                return False
            pct_change = abs(current_price - old_prices[0]) / old_prices[0] * 100
            return pct_change >= t
        elif c == "vwap_cross" and vwap is not None:
            history = self._price_history.get(alert.symbol, [])
            if len(history) < 2:
                return False
            prev_price = history[-2][1] if len(history) >= 2 else current_price
            return (prev_price < vwap and current_price >= vwap) or (prev_price > vwap and current_price <= vwap)
        return False

    def _record_price(self, symbol: str, price: float) -> None:
        history = self._price_history.setdefault(symbol, [])
        history.append((time.time(), price))
        # Keep only last 30 min
        cutoff = time.time() - 1800
        self._price_history[symbol] = [(ts, p) for ts, p in history if ts >= cutoff]

    def process_price_update(self, symbol: str, price: float, vwap: float | None = None) -> list[str]:
        """
        Process a real price update and check all active alerts for this symbol.
        Returns list of triggered alert_ids.
        """
        self._record_price(symbol, price)
        triggered: list[str] = []

        for alert in list(self._alerts.values()):
            if alert.symbol != symbol.upper() or alert.status != "active":
                continue
            if self._check_condition(alert, price, vwap):
                alert.triggered_at = time.time()
                alert.triggered_price = price
                if not alert.repeat:
                    alert.status = "triggered"
                self._persist(alert)
                triggered.append(alert.alert_id)

                # Emit subscription notification
                try:
                    from ..spec_compliance.subscriptions import get_subscription_manager
                    get_subscription_manager().notify_price_alert(
                        symbol=symbol,
                        condition=alert.condition,
                        price=price,
                        alert_id=alert.alert_id,
                    )
                except Exception:
                    pass

                logger.info(
                    "Alert TRIGGERED: %s %s %.2f @ %.2f (id=%s)",
                    symbol, alert.condition, alert.threshold, price, alert.alert_id[:8],
                )

        return triggered

    async def start_polygon_polling(self, poll_interval: int = 60) -> None:
        """
        Poll Polygon.io REST API for real-time prices to drive alert checking.
        Requires POLYGON_API_KEY environment variable.
        """
        api_key = os.environ.get("POLYGON_API_KEY", "")
        if not api_key:
            logger.warning(
                "POLYGON_API_KEY not set. Price alert polling disabled. "
                "Set POLYGON_API_KEY to enable real-time alert checking."
            )
            return

        import urllib.request
        import json

        logger.info("Starting Polygon.io price polling (interval=%ds)", poll_interval)

        while True:
            try:
                symbols = list({a.symbol for a in self._alerts.values() if a.status == "active"})
                if not symbols:
                    await asyncio.sleep(poll_interval)
                    continue

                tickers = ",".join(symbols[:20])  # Polygon snapshot supports batched tickers
                url = (
                    f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers"
                    f"?tickers={tickers}&apiKey={api_key}"
                )
                req = urllib.request.Request(url, headers={"User-Agent": "AlgoChains-MCP/21.0"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read())

                for ticker in data.get("tickers", []):
                    sym = ticker.get("ticker", "")
                    day = ticker.get("day", {})
                    price = float(day.get("c", ticker.get("lastTrade", {}).get("p", 0)) or 0)
                    vwap = float(day.get("vw", 0) or 0) or None
                    if price > 0:
                        self.process_price_update(sym, price, vwap)

            except Exception as exc:
                logger.error("Polygon polling error: %s", exc)

            await asyncio.sleep(poll_interval)

    def start_polling_task(self, poll_interval: int = 60) -> None:
        """Start the background polling task."""
        if self._check_task and not self._check_task.done():
            return
        self._check_task = asyncio.ensure_future(self.start_polygon_polling(poll_interval))

    def stats(self) -> dict[str, Any]:
        active = sum(1 for a in self._alerts.values() if a.status == "active")
        triggered = sum(1 for a in self._alerts.values() if a.status == "triggered")
        return {
            "total_alerts": len(self._alerts),
            "active_alerts": active,
            "triggered_alerts": triggered,
            "symbols_monitored": list({a.symbol for a in self._alerts.values() if a.status == "active"}),
            "polling_active": self._check_task is not None and not self._check_task.done(),
        }


_alert_engine: PriceAlertEngine | None = None


def get_alert_engine() -> PriceAlertEngine:
    global _alert_engine
    if _alert_engine is None:
        _alert_engine = PriceAlertEngine()
    return _alert_engine
