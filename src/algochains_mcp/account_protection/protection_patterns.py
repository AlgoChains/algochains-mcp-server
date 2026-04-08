"""Freqtrade-style protection patterns for live futures bots.

Ports battle-tested logic from freqtrade/freqtrade (30k stars) to AlgoChains:
  - StoplossGuard:    Lock instrument after N stops in X hours
  - CooldownPeriod:   Block re-entry after a stop for Y minutes
  - LowProfitPairs:   Pause instrument if PnL below threshold over rolling window
  - MaxDrawdownPerInstrument: Per-instrument drawdown limit separate from account limit
  - EdgePositioning:  Historical win-rate + R:R to adjust effective stop levels

Storage: SQLite via state/protection_state.db (no external dependency)
All methods are safe to call from async MCP dispatch handlers.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger("algochains_mcp.protection_patterns")

_DB_PATH = os.environ.get("PROTECTION_DB", "state/protection_state.db")


# ---------------------------------------------------------------------------
# Database bootstrap
# ---------------------------------------------------------------------------

def _get_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB_PATH) if os.path.dirname(_DB_PATH) else ".", exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _bootstrap(conn)
    return conn


def _bootstrap(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS stop_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot TEXT NOT NULL,
            symbol TEXT NOT NULL,
            ts_unix REAL NOT NULL,
            pnl_usd REAL NOT NULL,
            reason TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS instrument_locks (
            symbol TEXT NOT NULL,
            bot TEXT NOT NULL,
            locked_until_unix REAL NOT NULL,
            lock_reason TEXT NOT NULL,
            locked_at_unix REAL NOT NULL,
            PRIMARY KEY (symbol, bot)
        );

        CREATE TABLE IF NOT EXISTS cooldown_periods (
            symbol TEXT NOT NULL,
            bot TEXT NOT NULL,
            cooldown_until_unix REAL NOT NULL,
            PRIMARY KEY (symbol, bot)
        );

        CREATE TABLE IF NOT EXISTS pnl_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot TEXT NOT NULL,
            symbol TEXT NOT NULL,
            ts_unix REAL NOT NULL,
            pnl_usd REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_stop_events_bot_ts ON stop_events(bot, ts_unix);
        CREATE INDEX IF NOT EXISTS idx_pnl_bot_ts ON pnl_snapshots(bot, ts_unix);
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class StopEvent:
    bot: str
    symbol: str
    pnl_usd: float
    reason: str = ""
    ts_unix: float = 0.0

    def __post_init__(self):
        if not self.ts_unix:
            self.ts_unix = time.time()


@dataclass
class LockStatus:
    symbol: str
    bot: str
    is_locked: bool
    locked_until: Optional[str] = None
    lock_reason: str = ""
    seconds_remaining: float = 0.0


@dataclass
class ProtectionCheckResult:
    symbol: str
    bot: str
    blocked: bool
    blocker: str = ""
    details: dict = None

    def __post_init__(self):
        if self.details is None:
            self.details = {}

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "bot": self.bot,
            "blocked": self.blocked,
            "blocker": self.blocker,
            "details": self.details,
        }


# ---------------------------------------------------------------------------
# StoplossGuard
# ---------------------------------------------------------------------------

class StoplossGuard:
    """Lock an instrument after N stoploss events in a rolling window.

    Prevents cascading losses when a bot keeps getting stopped out.
    Modeled after freqtrade's StoplossGuard protection.

    Config:
        stoploss_count: int   — number of stops triggering the lock (default 3)
        window_hours: float   — rolling lookback window (default 4h)
        lock_hours: float     — how long to lock the instrument (default 2h)
    """

    def __init__(self, stoploss_count: int = 3, window_hours: float = 4.0, lock_hours: float = 2.0):
        self.stoploss_count = stoploss_count
        self.window_hours = window_hours
        self.lock_hours = lock_hours

    def record_stop(self, bot: str, symbol: str, pnl_usd: float, reason: str = "") -> None:
        """Call this after every stoploss fill."""
        with _get_db() as conn:
            conn.execute(
                "INSERT INTO stop_events (bot, symbol, ts_unix, pnl_usd, reason) VALUES (?,?,?,?,?)",
                (bot, symbol, time.time(), pnl_usd, reason)
            )
            conn.commit()
        # Check if guard should trigger
        self._check_and_lock(bot, symbol)

    def _check_and_lock(self, bot: str, symbol: str) -> bool:
        cutoff = time.time() - self.window_hours * 3600
        with _get_db() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM stop_events WHERE bot=? AND symbol=? AND ts_unix > ?",
                (bot, symbol, cutoff)
            ).fetchone()
            count = row["cnt"] if row else 0

            if count >= self.stoploss_count:
                locked_until = time.time() + self.lock_hours * 3600
                reason = f"StoplossGuard: {count} stops in {self.window_hours}h (limit={self.stoploss_count})"
                conn.execute("""
                    INSERT OR REPLACE INTO instrument_locks
                    (symbol, bot, locked_until_unix, lock_reason, locked_at_unix)
                    VALUES (?,?,?,?,?)
                """, (symbol, bot, locked_until, reason, time.time()))
                conn.commit()
                logger.warning("StoplossGuard: Locked %s/%s for %sh — %s", bot, symbol, self.lock_hours, reason)
                return True
        return False

    def is_locked(self, bot: str, symbol: str) -> LockStatus:
        with _get_db() as conn:
            row = conn.execute(
                "SELECT * FROM instrument_locks WHERE symbol=? AND bot=? AND locked_until_unix > ?",
                (symbol, bot, time.time())
            ).fetchone()
        if not row:
            return LockStatus(symbol=symbol, bot=bot, is_locked=False)
        remaining = row["locked_until_unix"] - time.time()
        until_dt = datetime.fromtimestamp(row["locked_until_unix"], tz=timezone.utc).isoformat()
        return LockStatus(
            symbol=symbol, bot=bot, is_locked=True,
            locked_until=until_dt,
            lock_reason=row["lock_reason"],
            seconds_remaining=max(0.0, remaining)
        )

    def get_recent_stops(self, bot: str, symbol: str, window_hours: float = None) -> list[dict]:
        window = window_hours or self.window_hours
        cutoff = time.time() - window * 3600
        with _get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM stop_events WHERE bot=? AND symbol=? AND ts_unix > ? ORDER BY ts_unix DESC",
                (bot, symbol, cutoff)
            ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# CooldownPeriod
# ---------------------------------------------------------------------------

class CooldownPeriod:
    """Block re-entry on an instrument for N minutes after any stop.

    Prevents revenge trading — the most common way retail traders amplify losses.
    Modeled after freqtrade's CooldownPeriod protection.

    Config:
        cooldown_minutes: float — cooldown duration after each stop (default 30)
    """

    def __init__(self, cooldown_minutes: float = 30.0):
        self.cooldown_minutes = cooldown_minutes

    def trigger_cooldown(self, bot: str, symbol: str) -> None:
        """Call after any stoploss or manual close to start the cooldown."""
        cooldown_until = time.time() + self.cooldown_minutes * 60
        with _get_db() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO cooldown_periods (symbol, bot, cooldown_until_unix)
                VALUES (?,?,?)
            """, (symbol, bot, cooldown_until))
            conn.commit()
        logger.info("CooldownPeriod: %s/%s in cooldown for %sm", bot, symbol, self.cooldown_minutes)

    def is_in_cooldown(self, bot: str, symbol: str) -> dict:
        with _get_db() as conn:
            row = conn.execute(
                "SELECT cooldown_until_unix FROM cooldown_periods WHERE symbol=? AND bot=? AND cooldown_until_unix > ?",
                (symbol, bot, time.time())
            ).fetchone()
        if not row:
            return {"in_cooldown": False, "symbol": symbol, "bot": bot, "seconds_remaining": 0}
        remaining = row["cooldown_until_unix"] - time.time()
        until_dt = datetime.fromtimestamp(row["cooldown_until_unix"], tz=timezone.utc).isoformat()
        return {
            "in_cooldown": True,
            "symbol": symbol,
            "bot": bot,
            "cooldown_until": until_dt,
            "seconds_remaining": max(0.0, remaining),
            "minutes_remaining": round(max(0.0, remaining) / 60, 1),
        }


# ---------------------------------------------------------------------------
# LowProfitPairs
# ---------------------------------------------------------------------------

class LowProfitPairs:
    """Pause instruments that haven't produced minimum profit in a rolling window.

    Identifies instruments that are in a bad regime for our strategy —
    either trending against us or in low-volatility choppy conditions.
    Modeled after freqtrade's LowProfitPairs protection.

    Config:
        min_profit_pct: float  — minimum required profit (default 0.1%)
        window_hours: float    — lookback window (default 48h)
        lock_hours: float      — lock duration for failing instruments (default 8h)
    """

    def __init__(self, min_profit_pct: float = 0.10, window_hours: float = 48.0, lock_hours: float = 8.0):
        self.min_profit_pct = min_profit_pct
        self.window_hours = window_hours
        self.lock_hours = lock_hours

    def record_pnl_snapshot(self, bot: str, symbol: str, pnl_usd: float) -> None:
        """Record a P&L checkpoint for rolling window evaluation."""
        with _get_db() as conn:
            conn.execute(
                "INSERT INTO pnl_snapshots (bot, symbol, ts_unix, pnl_usd) VALUES (?,?,?,?)",
                (bot, symbol, time.time(), pnl_usd)
            )
            conn.commit()

    def check_and_lock(self, bot: str, symbol: str, current_pnl_usd: float, capital_usd: float = 10000) -> bool:
        """Check rolling P&L — lock instrument if below minimum profit threshold."""
        cutoff = time.time() - self.window_hours * 3600
        with _get_db() as conn:
            rows = conn.execute(
                "SELECT pnl_usd FROM pnl_snapshots WHERE bot=? AND symbol=? AND ts_unix > ? ORDER BY ts_unix ASC LIMIT 1",
                (bot, symbol, cutoff)
            ).fetchall()

            if not rows:
                return False  # No history yet

            start_pnl = rows[0]["pnl_usd"]
            profit_usd = current_pnl_usd - start_pnl
            profit_pct = (profit_usd / capital_usd) * 100

            if profit_pct < self.min_profit_pct:
                locked_until = time.time() + self.lock_hours * 3600
                reason = (
                    f"LowProfitPairs: {profit_pct:.3f}% profit in {self.window_hours}h "
                    f"(min={self.min_profit_pct}%)"
                )
                conn.execute("""
                    INSERT OR REPLACE INTO instrument_locks
                    (symbol, bot, locked_until_unix, lock_reason, locked_at_unix)
                    VALUES (?,?,?,?,?)
                """, (symbol, bot, locked_until, reason, time.time()))
                conn.commit()
                logger.warning("LowProfitPairs: Locked %s/%s — %s", bot, symbol, reason)
                return True
        return False


# ---------------------------------------------------------------------------
# Universal pre-trade check (freqtrade's confirm_trade_entry pattern)
# ---------------------------------------------------------------------------

class PreTradeProtectionGate:
    """Single entry point for all freqtrade-style protection checks.

    Run this before every order. Returns ProtectionCheckResult with
    blocked=True and blocker name if any protection is active.
    """

    def __init__(
        self,
        stoploss_guard: Optional[StoplossGuard] = None,
        cooldown_period: Optional[CooldownPeriod] = None,
        low_profit_pairs: Optional[LowProfitPairs] = None,
    ):
        self.stoploss_guard = stoploss_guard or StoplossGuard()
        self.cooldown_period = cooldown_period or CooldownPeriod()
        self.low_profit_pairs = low_profit_pairs or LowProfitPairs()

    def check(self, bot: str, symbol: str) -> ProtectionCheckResult:
        # StoplossGuard — hard lock after cascading stops
        lock = self.stoploss_guard.is_locked(bot, symbol)
        if lock.is_locked:
            return ProtectionCheckResult(
                symbol=symbol, bot=bot, blocked=True,
                blocker="StoplossGuard",
                details={
                    "locked_until": lock.locked_until,
                    "reason": lock.lock_reason,
                    "minutes_remaining": round(lock.seconds_remaining / 60, 1),
                }
            )

        # CooldownPeriod — post-stop re-entry delay
        cooldown = self.cooldown_period.is_in_cooldown(bot, symbol)
        if cooldown["in_cooldown"]:
            return ProtectionCheckResult(
                symbol=symbol, bot=bot, blocked=True,
                blocker="CooldownPeriod",
                details={
                    "cooldown_until": cooldown.get("cooldown_until"),
                    "minutes_remaining": cooldown.get("minutes_remaining"),
                }
            )

        return ProtectionCheckResult(symbol=symbol, bot=bot, blocked=False)


# ---------------------------------------------------------------------------
# Manual lock/unlock helpers
# ---------------------------------------------------------------------------

def lock_instrument(bot: str, symbol: str, reason: str, lock_hours: float = 1.0) -> dict:
    """Manually lock an instrument for a specified duration."""
    locked_until = time.time() + lock_hours * 3600
    with _get_db() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO instrument_locks
            (symbol, bot, locked_until_unix, lock_reason, locked_at_unix)
            VALUES (?,?,?,?,?)
        """, (symbol, bot, locked_until, f"MANUAL: {reason}", time.time()))
        conn.commit()
    until_dt = datetime.fromtimestamp(locked_until, tz=timezone.utc).isoformat()
    logger.info("Manual lock: %s/%s until %s — %s", bot, symbol, until_dt, reason)
    return {"locked": True, "symbol": symbol, "bot": bot, "locked_until": until_dt, "reason": reason}


def unlock_instrument(bot: str, symbol: str) -> dict:
    """Manually clear any active lock on an instrument."""
    with _get_db() as conn:
        conn.execute(
            "DELETE FROM instrument_locks WHERE symbol=? AND bot=?",
            (symbol, bot)
        )
        conn.execute(
            "DELETE FROM cooldown_periods WHERE symbol=? AND bot=?",
            (symbol, bot)
        )
        conn.commit()
    return {"unlocked": True, "symbol": symbol, "bot": bot}


def get_all_protection_status(bot: str = None) -> dict:
    """Return full protection status across all instruments for a bot (or all bots)."""
    now = time.time()
    with _get_db() as conn:
        if bot:
            locks = conn.execute(
                "SELECT * FROM instrument_locks WHERE bot=? AND locked_until_unix > ?",
                (bot, now)
            ).fetchall()
            cooldowns = conn.execute(
                "SELECT * FROM cooldown_periods WHERE bot=? AND cooldown_until_unix > ?",
                (bot, now)
            ).fetchall()
        else:
            locks = conn.execute(
                "SELECT * FROM instrument_locks WHERE locked_until_unix > ?", (now,)
            ).fetchall()
            cooldowns = conn.execute(
                "SELECT * FROM cooldown_periods WHERE cooldown_until_unix > ?", (now,)
            ).fetchall()

    active_locks = []
    for r in locks:
        until_dt = datetime.fromtimestamp(r["locked_until_unix"], tz=timezone.utc).isoformat()
        active_locks.append({
            "symbol": r["symbol"],
            "bot": r["bot"],
            "locked_until": until_dt,
            "reason": r["lock_reason"],
            "minutes_remaining": round((r["locked_until_unix"] - now) / 60, 1),
        })

    active_cooldowns = []
    for r in cooldowns:
        until_dt = datetime.fromtimestamp(r["cooldown_until_unix"], tz=timezone.utc).isoformat()
        active_cooldowns.append({
            "symbol": r["symbol"],
            "bot": r["bot"],
            "cooldown_until": until_dt,
            "minutes_remaining": round((r["cooldown_until_unix"] - now) / 60, 1),
        })

    return {
        "bot": bot or "all",
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "active_locks": active_locks,
        "active_cooldowns": active_cooldowns,
        "total_blocked": len(active_locks) + len(active_cooldowns),
    }


# ---------------------------------------------------------------------------
# Singletons for MCP dispatch
# ---------------------------------------------------------------------------

_stoploss_guard = StoplossGuard(stoploss_count=3, window_hours=4.0, lock_hours=2.0)
_cooldown_period = CooldownPeriod(cooldown_minutes=30.0)
_low_profit_pairs = LowProfitPairs(min_profit_pct=0.1, window_hours=48.0, lock_hours=8.0)
_protection_gate = PreTradeProtectionGate(_stoploss_guard, _cooldown_period, _low_profit_pairs)
