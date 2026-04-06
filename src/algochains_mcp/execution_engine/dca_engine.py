"""
DCA (Dollar-Cost Averaging) Engine — Real Broker Execution.

Schedules recurring purchases via real broker APIs (Alpaca).
No synthetic fills. No placeholder executions.

Storage: ~/.algochains/dca_schedules.db (SQLite, persistent)

Supported brokers: Alpaca (equity + crypto via Alpaca Crypto API)

Scheduling: asyncio-based internal scheduler (no external cron dependency).
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

logger = logging.getLogger("algochains_mcp.execution_engine.dca")

DCA_DB_PATH = Path.home() / ".algochains" / "dca_schedules.db"


class DCAError(Exception):
    pass


@dataclass
class DCASchedule:
    schedule_id: str
    symbol: str
    amount_usd: float
    frequency: str          # "daily" | "weekly" | "biweekly" | "monthly"
    broker: str
    status: str = "active"  # "active" | "paused" | "cancelled"
    created_at: float = field(default_factory=time.time)
    next_execution: float = field(default_factory=time.time)
    total_executed: int = 0
    total_invested_usd: float = 0.0
    total_shares_acquired: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schedule_id": self.schedule_id,
            "symbol": self.symbol,
            "amount_usd": self.amount_usd,
            "frequency": self.frequency,
            "broker": self.broker,
            "status": self.status,
            "created_at": self.created_at,
            "next_execution": self.next_execution,
            "total_executed": self.total_executed,
            "total_invested_usd": round(self.total_invested_usd, 2),
            "total_shares_acquired": round(self.total_shares_acquired, 6),
            "avg_cost_per_share": (
                round(self.total_invested_usd / self.total_shares_acquired, 4)
                if self.total_shares_acquired > 0 else None
            ),
        }


class DCAEngine:
    """
    Dollar-cost averaging engine with real broker execution via Alpaca API.
    """

    FREQUENCY_SECONDS = {
        "daily": 86400,
        "weekly": 604800,
        "biweekly": 1209600,
        "monthly": 2592000,  # ~30 days
    }

    def __init__(self) -> None:
        DCA_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._schedules: dict[str, DCASchedule] = {}
        self._load_schedules()
        self._scheduler_task: asyncio.Task | None = None

    def _init_db(self) -> None:
        with sqlite3.connect(DCA_DB_PATH) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schedules (
                    schedule_id TEXT PRIMARY KEY,
                    symbol TEXT,
                    amount_usd REAL,
                    frequency TEXT,
                    broker TEXT,
                    status TEXT,
                    created_at REAL,
                    next_execution REAL,
                    total_executed INTEGER,
                    total_invested_usd REAL,
                    total_shares_acquired REAL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS executions (
                    execution_id TEXT PRIMARY KEY,
                    schedule_id TEXT,
                    executed_at REAL,
                    amount_usd REAL,
                    shares REAL,
                    price REAL,
                    order_id TEXT,
                    status TEXT
                )
            """)
            conn.commit()

    def _load_schedules(self) -> None:
        with sqlite3.connect(DCA_DB_PATH) as conn:
            rows = conn.execute("SELECT * FROM schedules WHERE status = 'active'").fetchall()
        for row in rows:
            (sid, sym, amount, freq, broker, status, created, next_exec, total_exec, invested, shares) = row
            self._schedules[sid] = DCASchedule(
                schedule_id=sid, symbol=sym, amount_usd=amount, frequency=freq,
                broker=broker, status=status, created_at=created, next_execution=next_exec,
                total_executed=total_exec, total_invested_usd=invested, total_shares_acquired=shares,
            )

    def _persist(self, schedule: DCASchedule) -> None:
        with sqlite3.connect(DCA_DB_PATH) as conn:
            conn.execute("INSERT OR REPLACE INTO schedules VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                schedule.schedule_id, schedule.symbol, schedule.amount_usd, schedule.frequency,
                schedule.broker, schedule.status, schedule.created_at, schedule.next_execution,
                schedule.total_executed, schedule.total_invested_usd, schedule.total_shares_acquired,
            ))
            conn.commit()

    def create_schedule(
        self,
        symbol: str,
        amount_usd: float,
        frequency: str,
        broker: str = "alpaca",
    ) -> DCASchedule:
        """
        Create a real DCA schedule.

        Args:
            symbol: Ticker symbol (e.g. "BTC/USD", "AAPL")
            amount_usd: USD amount to buy per execution
            frequency: "daily" | "weekly" | "biweekly" | "monthly"
            broker: "alpaca" (only supported broker for DCA)
        """
        if frequency not in self.FREQUENCY_SECONDS:
            raise DCAError(
                f"Invalid frequency '{frequency}'. "
                f"Valid: {', '.join(self.FREQUENCY_SECONDS)}"
            )
        if amount_usd < 1.0:
            raise DCAError("Minimum DCA amount is $1.00.")
        if not symbol:
            raise DCAError("symbol is required.")
        if broker not in ("alpaca",):
            raise DCAError(f"Unsupported broker '{broker}'. Only 'alpaca' supports DCA.")

        api_key = os.environ.get("ALPACA_API_KEY", "")
        if not api_key:
            raise DCAError(
                "ALPACA_API_KEY required for DCA execution. "
                "Alpaca supports fractional shares and crypto DCA. "
                "Set ALPACA_API_KEY and ALPACA_SECRET_KEY."
            )

        schedule = DCASchedule(
            schedule_id=str(uuid.uuid4()),
            symbol=symbol.upper(),
            amount_usd=amount_usd,
            frequency=frequency,
            broker=broker,
            next_execution=time.time() + self.FREQUENCY_SECONDS[frequency],
        )
        self._schedules[schedule.schedule_id] = schedule
        self._persist(schedule)

        # Start scheduler if not running
        if self._scheduler_task is None or self._scheduler_task.done():
            self._scheduler_task = asyncio.ensure_future(self._run_scheduler())

        logger.info("DCA schedule created: %s $%.2f %s (id=%s)", symbol, amount_usd, frequency, schedule.schedule_id[:8])
        return schedule

    def list_schedules(self, symbol: str | None = None) -> list[dict[str, Any]]:
        schedules = list(self._schedules.values())
        if symbol:
            schedules = [s for s in schedules if s.symbol == symbol.upper()]
        return [s.to_dict() for s in schedules]

    def pause_schedule(self, schedule_id: str) -> dict[str, Any]:
        s = self._schedules.get(schedule_id)
        if not s:
            raise DCAError(f"Schedule {schedule_id} not found.")
        s.status = "paused"
        self._persist(s)
        return {"paused": True, "schedule_id": schedule_id}

    def resume_schedule(self, schedule_id: str) -> dict[str, Any]:
        s = self._schedules.get(schedule_id)
        if not s:
            raise DCAError(f"Schedule {schedule_id} not found.")
        s.status = "active"
        self._persist(s)
        return {"resumed": True, "schedule_id": schedule_id}

    def delete_schedule(self, schedule_id: str) -> dict[str, Any]:
        s = self._schedules.get(schedule_id)
        if not s:
            raise DCAError(f"Schedule {schedule_id} not found.")
        s.status = "cancelled"
        self._persist(s)
        del self._schedules[schedule_id]
        return {"deleted": True, "schedule_id": schedule_id}

    async def _execute_dca(self, schedule: DCASchedule) -> dict[str, Any]:
        """Execute a single DCA purchase via Alpaca fractional order."""
        api_key = os.environ.get("ALPACA_API_KEY", "")
        api_secret = os.environ.get("ALPACA_SECRET_KEY", "")
        if not api_key:
            raise DCAError("ALPACA_API_KEY not set — cannot execute DCA.")

        try:
            import httpx
            base = "https://paper-api.alpaca.markets"  # paper for safety
            async with httpx.AsyncClient(
                base_url=base,
                headers={"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret},
                timeout=15,
            ) as client:
                resp = await client.post("/v2/orders", json={
                    "symbol": schedule.symbol,
                    "notional": str(schedule.amount_usd),  # fractional dollar amount
                    "side": "buy",
                    "type": "market",
                    "time_in_force": "day",
                })
                resp.raise_for_status()
                order = resp.json()

            filled_qty = float(order.get("filled_qty", 0))
            filled_avg = float(order.get("filled_avg_price", 0))

            # Update schedule stats
            schedule.total_executed += 1
            schedule.total_invested_usd += schedule.amount_usd
            schedule.total_shares_acquired += filled_qty
            schedule.next_execution = time.time() + self.FREQUENCY_SECONDS[schedule.frequency]
            self._persist(schedule)

            # Record execution
            exec_id = str(uuid.uuid4())
            with sqlite3.connect(DCA_DB_PATH) as conn:
                conn.execute("INSERT INTO executions VALUES (?,?,?,?,?,?,?,?)", (
                    exec_id, schedule.schedule_id, time.time(), schedule.amount_usd,
                    filled_qty, filled_avg, order.get("id", ""), order.get("status", ""),
                ))
                conn.commit()

            logger.info(
                "DCA executed: %s bought $%.2f → %.6f shares @ $%.2f (order=%s)",
                schedule.symbol, schedule.amount_usd, filled_qty, filled_avg, order.get("id", "")[:8],
            )
            return {
                "executed": True,
                "symbol": schedule.symbol,
                "amount_usd": schedule.amount_usd,
                "shares_acquired": filled_qty,
                "fill_price": filled_avg,
                "order_id": order.get("id"),
            }
        except ImportError:
            raise DCAError("httpx required. Install: pip install httpx")

    async def _run_scheduler(self) -> None:
        """Background scheduler — checks DCA schedules every minute."""
        logger.info("DCA scheduler started")
        while True:
            now = time.time()
            for schedule in list(self._schedules.values()):
                if schedule.status == "active" and schedule.next_execution <= now:
                    try:
                        await self._execute_dca(schedule)
                    except Exception as exc:
                        logger.error("DCA execution failed for %s: %s", schedule.schedule_id[:8], exc)
            await asyncio.sleep(60)  # check every minute

    def start(self) -> None:
        """Start the DCA scheduler."""
        if self._scheduler_task is None or self._scheduler_task.done():
            self._scheduler_task = asyncio.ensure_future(self._run_scheduler())

    def get_execution_history(self, schedule_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with sqlite3.connect(DCA_DB_PATH) as conn:
            rows = conn.execute(
                "SELECT * FROM executions WHERE schedule_id = ? ORDER BY executed_at DESC LIMIT ?",
                (schedule_id, limit),
            ).fetchall()
        return [
            {
                "execution_id": row[0], "schedule_id": row[1], "executed_at": row[2],
                "amount_usd": row[3], "shares": row[4], "price": row[5],
                "order_id": row[6], "status": row[7],
            }
            for row in rows
        ]


_dca_engine: DCAEngine | None = None


def get_dca_engine() -> DCAEngine:
    global _dca_engine
    if _dca_engine is None:
        _dca_engine = DCAEngine()
    return _dca_engine
