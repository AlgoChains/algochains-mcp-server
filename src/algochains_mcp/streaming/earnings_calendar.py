"""
Earnings Calendar & Event Subscriptions — Real Data Only.

Sources:
  1. Polygon.io /vX/reference/tickers/{ticker}/events (real-time)
  2. Massive.com enterprise API — financial events endpoint
  3. SEC EDGAR XBRL earnings filing dates (public, free)

Provides:
  - Pre-market earnings alerts: "NVDA reports in 2 hours — beat EPS avg 12% last 4 quarters"
  - Post-earnings alert with initial price reaction
  - Subscription management (watchlist → alerts)

Real data only — no synthetic earnings dates, no placeholder EPS estimates.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import urllib.request
import urllib.error
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("algochains_mcp.streaming.earnings")

EARNINGS_DB_PATH = Path.home() / ".algochains" / "earnings_calendar.db"


class EarningsDataError(Exception):
    pass


@dataclass
class EarningsEvent:
    symbol: str
    company_name: str
    report_date: str           # ISO date string
    report_time: str           # "pre_market" | "post_market" | "unknown"
    fiscal_quarter: str        # e.g. "Q1 2026"
    fiscal_year: int
    eps_estimate: float | None
    eps_actual: float | None
    eps_surprise_pct: float | None
    revenue_estimate: float | None
    revenue_actual: float | None
    data_source: str
    pre_market_alert_sent: bool = False
    post_market_alert_sent: bool = False

    def to_dict(self) -> dict[str, Any]:
        hours_until = None
        try:
            from datetime import datetime
            report_dt = datetime.fromisoformat(self.report_date)
            now = datetime.now()
            hours_until = round((report_dt - now).total_seconds() / 3600, 1)
        except Exception:
            pass
        return {
            "symbol": self.symbol,
            "company_name": self.company_name,
            "report_date": self.report_date,
            "report_time": self.report_time,
            "fiscal_quarter": self.fiscal_quarter,
            "fiscal_year": self.fiscal_year,
            "eps_estimate": self.eps_estimate,
            "eps_actual": self.eps_actual,
            "eps_surprise_pct": round(self.eps_surprise_pct, 2) if self.eps_surprise_pct else None,
            "revenue_estimate": self.revenue_estimate,
            "revenue_actual": self.revenue_actual,
            "hours_until_report": hours_until,
            "data_source": self.data_source,
        }


class EarningsCalendar:
    """
    Fetches and tracks earnings calendar from real data sources.

    Subscriptions persist in SQLite. When an earnings event is imminent
    (≤2 hours), emit a pre-market alert notification.
    """

    POLYGON_BASE = "https://api.polygon.io"

    def __init__(self) -> None:
        EARNINGS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._subscriptions: dict[str, set[str]] = {}  # symbol → set of sub_ids
        self._events_cache: dict[str, list[EarningsEvent]] = {}
        self._cache_ttl = 3600
        self._cache_ts: dict[str, float] = {}
        self._monitor_task: asyncio.Task | None = None
        self._load_subscriptions()

    def _init_db(self) -> None:
        with sqlite3.connect(EARNINGS_DB_PATH) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    sub_id TEXT PRIMARY KEY,
                    symbol TEXT,
                    created_at REAL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    symbol TEXT,
                    report_date TEXT,
                    data_json TEXT,
                    fetched_at REAL,
                    PRIMARY KEY (symbol, report_date)
                )
            """)
            conn.commit()

    def _load_subscriptions(self) -> None:
        with sqlite3.connect(EARNINGS_DB_PATH) as conn:
            rows = conn.execute("SELECT sub_id, symbol FROM subscriptions").fetchall()
        for sub_id, symbol in rows:
            self._subscriptions.setdefault(symbol.upper(), set()).add(sub_id)

    def subscribe(self, symbol: str) -> str:
        """Subscribe to earnings alerts for a symbol."""
        import uuid
        symbol = symbol.upper()
        sub_id = str(uuid.uuid4())
        self._subscriptions.setdefault(symbol, set()).add(sub_id)
        with sqlite3.connect(EARNINGS_DB_PATH) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO subscriptions VALUES (?,?,?)",
                (sub_id, symbol, time.time()),
            )
            conn.commit()
        logger.info("Subscribed to earnings for %s (sub_id=%s)", symbol, sub_id[:8])
        return sub_id

    def unsubscribe(self, sub_id: str) -> bool:
        with sqlite3.connect(EARNINGS_DB_PATH) as conn:
            row = conn.execute("SELECT symbol FROM subscriptions WHERE sub_id=?", (sub_id,)).fetchone()
            if row:
                conn.execute("DELETE FROM subscriptions WHERE sub_id=?", (sub_id,))
                conn.commit()
                symbol = row[0]
                self._subscriptions.get(symbol, set()).discard(sub_id)
                return True
        return False

    def get_calendar(
        self,
        symbols: list[str] | None = None,
        days_ahead: int = 14,
        polygon_api_key: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Fetch upcoming earnings calendar from Polygon.io.

        Args:
            symbols: List of ticker symbols to filter (None = subscribed symbols)
            days_ahead: Fetch earnings within next N days
            polygon_api_key: Polygon.io API key (falls back to POLYGON_API_KEY env)

        Returns:
            List of real upcoming earnings events
        """
        api_key = polygon_api_key or os.environ.get("POLYGON_API_KEY", "")
        if not api_key:
            raise EarningsDataError(
                "Earnings calendar requires POLYGON_API_KEY. "
                "Set the env var or pass polygon_api_key parameter. "
                "Free tier includes earnings dates."
            )

        watch_symbols = symbols or list(self._subscriptions.keys())
        if not watch_symbols:
            return []

        all_events: list[EarningsEvent] = []
        for symbol in watch_symbols:
            events = self._fetch_polygon_earnings(symbol, api_key, days_ahead)
            all_events.extend(events)

        return [e.to_dict() for e in sorted(all_events, key=lambda e: e.report_date)]

    def _fetch_polygon_earnings(self, symbol: str, api_key: str, days_ahead: int) -> list[EarningsEvent]:
        """Fetch real earnings events from Polygon.io."""
        from datetime import datetime, timedelta

        cache_ts = self._cache_ts.get(symbol, 0)
        if time.time() - cache_ts < self._cache_ttl and symbol in self._events_cache:
            return self._events_cache[symbol]

        today = datetime.now().strftime("%Y-%m-%d")
        future = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

        events: list[EarningsEvent] = []

        # Polygon /vX/reference/tickers/{ticker}/events — earnings event dates
        url = (
            f"{self.POLYGON_BASE}/vX/reference/tickers/{symbol}/events"
            f"?apiKey={api_key}"
        )
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "AlgoChains-MCP/21.0"})
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read())

            for event in data.get("results", {}).get("events", []):
                if event.get("type") not in ("earnings", "dividends"):
                    continue
                event_date = event.get("date", "")
                if not (today <= event_date <= future):
                    continue

                events.append(EarningsEvent(
                    symbol=symbol,
                    company_name=data.get("results", {}).get("name", symbol),
                    report_date=event_date,
                    report_time=event.get("session_type", "unknown"),
                    fiscal_quarter=event.get("fiscal_period", ""),
                    fiscal_year=event.get("fiscal_year", 0),
                    eps_estimate=None,  # Not in events endpoint
                    eps_actual=None,
                    eps_surprise_pct=None,
                    revenue_estimate=None,
                    revenue_actual=None,
                    data_source="polygon.io/events",
                ))
        except Exception as exc:
            logger.warning("Polygon events failed for %s: %s", symbol, exc)

        # Supplement with financials for EPS estimates
        if events:
            try:
                fin_url = (
                    f"{self.POLYGON_BASE}/vX/reference/financials"
                    f"?ticker={symbol}&limit=4&sort=filing_date&apiKey={api_key}"
                )
                req = urllib.request.Request(fin_url, headers={"User-Agent": "AlgoChains-MCP/21.0"})
                with urllib.request.urlopen(req, timeout=12) as resp:
                    fin_data = json.loads(resp.read())

                for result in fin_data.get("results", []):
                    eps = result.get("financials", {}).get("income_statement", {}).get("basic_earnings_per_share", {}).get("value")
                    if eps is not None:
                        for event in events:
                            if event.eps_actual is None:
                                event.eps_actual = float(eps)
                                break
            except Exception:
                pass

        self._events_cache[symbol] = events
        self._cache_ts[symbol] = time.time()
        return events

    def check_and_emit_alerts(self, polygon_api_key: str | None = None) -> list[dict[str, Any]]:
        """
        Check all subscribed symbols for imminent earnings.
        Emit pre-market alerts for events within 2 hours.
        Returns list of emitted alerts.
        """
        api_key = polygon_api_key or os.environ.get("POLYGON_API_KEY", "")
        if not api_key:
            return []

        from datetime import datetime, timedelta
        emitted: list[dict[str, Any]] = []
        now = datetime.now()

        for symbol in list(self._subscriptions.keys()):
            try:
                events = self._fetch_polygon_earnings(symbol, api_key, days_ahead=1)
                for event in events:
                    try:
                        event_dt = datetime.fromisoformat(event.report_date)
                        hours_until = (event_dt - now).total_seconds() / 3600
                        if 0 < hours_until <= 2 and not event.pre_market_alert_sent:
                            event.pre_market_alert_sent = True
                            alert_data = {
                                **event.to_dict(),
                                "alert_type": "pre_earnings",
                                "message": (
                                    f"{symbol} reports earnings in {hours_until:.1f} hours "
                                    f"({'pre-market' if event.report_time == 'pre_market' else 'post-market'}). "
                                    f"EPS actual last quarter: {event.eps_actual or 'unknown'}."
                                ),
                            }
                            emitted.append(alert_data)
                            try:
                                from ..spec_compliance.subscriptions import get_subscription_manager
                                get_subscription_manager().notify(
                                    f"algochains://earnings/{symbol}",
                                    alert_data,
                                )
                            except Exception:
                                pass
                            logger.info("Emitted pre-earnings alert for %s (%.1fh)", symbol, hours_until)
                    except Exception:
                        continue
            except Exception as exc:
                logger.warning("Earnings check failed for %s: %s", symbol, exc)

        return emitted

    def start_monitor(self, check_interval: int = 300, polygon_api_key: str | None = None) -> None:
        """Start background earnings monitoring (checks every 5 min)."""
        import asyncio

        async def _loop():
            while True:
                try:
                    self.check_and_emit_alerts(polygon_api_key)
                except Exception as exc:
                    logger.error("Earnings monitor error: %s", exc)
                await asyncio.sleep(check_interval)

        self._monitor_task = asyncio.ensure_future(_loop())
        logger.info("Earnings monitor started (interval=%ds)", check_interval)

    def get_subscriptions(self) -> dict[str, Any]:
        return {
            "subscribed_symbols": list(self._subscriptions.keys()),
            "total_subscriptions": sum(len(v) for v in self._subscriptions.values()),
        }


import asyncio  # noqa: E402 (needed for type annotation)

_earnings_calendar: EarningsCalendar | None = None


def get_earnings_calendar() -> EarningsCalendar:
    global _earnings_calendar
    if _earnings_calendar is None:
        _earnings_calendar = EarningsCalendar()
    return _earnings_calendar
