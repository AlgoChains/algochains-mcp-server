"""
AlgoChains MCP Server — Hard-Coded Trading Guardrails (V22)

SECURITY ARCHITECTURE:
    All limits in this module are Python constants defined at import time.
    They are NOT tools. The AI has no MCP tool to modify them.
    Changing limits requires a code deploy + human review.

    This module sits at the execution boundary — every order placement
    flows through TradingGuardrails.check_all() before hitting a broker.
    If check_all() raises GuardrailTripped, the order is rejected with
    a clear explanation. The broker API is never called.

CASE STUDY (SupraWall 2025):
    An infinite AI loop executed 10,000 trades in 8 seconds → $2.4M loss.
    The loop detected a rate-limit error, misinterpreted it as "incomplete",
    and retried in a tighter loop. Hard-coded velocity limits prevent this.

CIRCUIT BREAKER STATES:
    CLOSED    → Normal operation. Orders allowed.
    OPEN      → Hard limit breached. All orders blocked. Cooldown active.
    HALF_OPEN → Cooldown expired. One test call allowed. Watching.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Deque, Dict, Optional

logger = logging.getLogger("algochains_mcp.guardrails")

# ═══════════════════════════════════════════════════════════════════════════
# HARD-CODED LIMITS — NOT CONFIGURABLE VIA AI TOOL CALLS
# Source: CLAUDE.md, AlgoChains validation gates, Tradovate rate limit docs
# ═══════════════════════════════════════════════════════════════════════════

# Order velocity limits (Tradovate official: 80/min, 5000/hour)
# We use 12.5% of Tradovate's cap as our ceiling — leaves buffer for retries.
MAX_ORDERS_PER_MINUTE: int = 10
MAX_ORDERS_PER_HOUR: int = 60

# Financial loss limits (from CLAUDE.md and AlgoChains validation gates)
MAX_DAILY_LOSS_USD: float = 500.0          # Tyler's hard limit
MAX_DRAWDOWN_PCT: float = 0.15             # 15% max drawdown gate
MAX_CONSECUTIVE_LOSSES: int = 5            # halt + review after 5 straight losses

# Position size limits (per symbol, in contracts)
MAX_POSITION_SIZE_CONTRACTS: int = 5
MAX_TOTAL_OPEN_NOTIONAL_USD: float = 100_000.0

# Volatility kill switch (from live bot configs: VIX > 35 blocks all trades)
VIX_KILL_THRESHOLD: float = 35.0

# AI agent loop detection
AI_LOOP_WINDOW_SEC: int = 60              # rolling window for loop detection
AI_LOOP_MAX_IDENTICAL_CALLS: int = 5      # 5 identical calls in window → trip
AI_LOOP_MAX_CALLS_PER_MINUTE: int = 30   # total tool call rate limit

# Circuit breaker cooldown periods
CB_OPEN_COOLDOWN_SEC: int = 300          # 5 minutes before HALF_OPEN
CB_DAILY_LOSS_COOLDOWN_SEC: int = 86400  # 24 hours for daily loss limit
CB_CONSECUTIVE_LOSS_COOLDOWN_SEC: int = 3600  # 1 hour for consecutive losses

# State persistence
_STATE_PATH = Path(os.environ.get("ALGOCHAINS_STATE_DIR", "state")) / "guardrails_state.json"


# ═══════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════

class CBState(Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class GuardrailReason(Enum):
    ORDER_VELOCITY = "order_velocity"
    DAILY_LOSS = "daily_loss"
    DRAWDOWN = "drawdown"
    CONSECUTIVE_LOSSES = "consecutive_losses"
    POSITION_SIZE = "position_size"
    NOTIONAL_LIMIT = "notional_limit"
    VIX_KILL = "vix_kill"
    AI_LOOP_DETECTED = "ai_loop_detected"
    TOOL_RATE_LIMIT = "tool_rate_limit"
    MANUAL_TRIP = "manual_trip"


class GuardrailTripped(Exception):
    """Raised when a hard-coded limit is breached. Order is rejected."""

    def __init__(self, reason: GuardrailReason, message: str, cooldown_sec: int = 0):
        self.reason = reason
        self.cooldown_sec = cooldown_sec
        super().__init__(f"[GUARDRAIL:{reason.value}] {message}")


@dataclass
class CBStatus:
    state: CBState = CBState.CLOSED
    tripped_at: float = 0.0
    trip_reason: Optional[GuardrailReason] = None
    trip_message: str = ""
    cooldown_sec: int = CB_OPEN_COOLDOWN_SEC
    half_open_test_allowed: bool = False
    trip_count_today: int = 0


@dataclass
class OrderVelocityTracker:
    """Sliding-window order velocity tracking. Thread-safe."""
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _minute_window: Deque[float] = field(default_factory=lambda: deque(maxlen=MAX_ORDERS_PER_MINUTE + 1))
    _hour_window: Deque[float] = field(default_factory=lambda: deque(maxlen=MAX_ORDERS_PER_HOUR + 1))

    def record_order(self) -> None:
        now = time.monotonic()
        with self._lock:
            self._minute_window.append(now)
            self._hour_window.append(now)

    def orders_in_last_minute(self) -> int:
        now = time.monotonic()
        cutoff = now - 60.0
        with self._lock:
            return sum(1 for t in self._minute_window if t >= cutoff)

    def orders_in_last_hour(self) -> int:
        now = time.monotonic()
        cutoff = now - 3600.0
        with self._lock:
            return sum(1 for t in self._hour_window if t >= cutoff)


@dataclass
class ToolCallTracker:
    """AI loop detection via call hashing + frequency analysis."""
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _call_log: Deque[tuple[float, str]] = field(
        default_factory=lambda: deque(maxlen=AI_LOOP_MAX_CALLS_PER_MINUTE * 2)
    )
    _hash_counts: Dict[str, Deque[float]] = field(default_factory=dict)

    def _call_hash(self, tool_name: str, arguments: dict) -> str:
        payload = json.dumps({"t": tool_name, "a": arguments}, sort_keys=True)
        return hashlib.md5(payload.encode()).hexdigest()[:12]

    def record_call(self, tool_name: str, arguments: dict) -> None:
        now = time.monotonic()
        h = self._call_hash(tool_name, arguments)
        with self._lock:
            self._call_log.append((now, h))
            if h not in self._hash_counts:
                self._hash_counts[h] = deque(maxlen=AI_LOOP_MAX_IDENTICAL_CALLS + 1)
            self._hash_counts[h].append(now)

    def check_loop(self, tool_name: str, arguments: dict) -> Optional[str]:
        """
        Returns a human-readable warning if a loop is detected, else None.
        Call BEFORE recording — if None, record and proceed.
        """
        now = time.monotonic()
        h = self._call_hash(tool_name, arguments)
        window_cutoff = now - AI_LOOP_WINDOW_SEC

        with self._lock:
            # Check 1: identical call frequency in rolling window
            # We check BEFORE recording so threshold is N-1 (this call is the Nth)
            if h in self._hash_counts:
                recent_identical = sum(1 for t in self._hash_counts[h] if t >= window_cutoff)
                if recent_identical >= AI_LOOP_MAX_IDENTICAL_CALLS - 1:
                    return (
                        f"AI loop detected: tool '{tool_name}' called {recent_identical + 1} times "
                        f"with identical arguments in the last {AI_LOOP_WINDOW_SEC}s "
                        f"(limit: {AI_LOOP_MAX_IDENTICAL_CALLS}). "
                        f"This indicates an agent reasoning loop. Halting to prevent damage."
                    )

            # Check 2: total call frequency (any tool)
            recent_total = sum(1 for t, _ in self._call_log if t >= window_cutoff)
            if recent_total >= AI_LOOP_MAX_CALLS_PER_MINUTE:
                return (
                    f"Tool call rate exceeded: {recent_total} calls in {AI_LOOP_WINDOW_SEC}s "
                    f"(limit: {AI_LOOP_MAX_CALLS_PER_MINUTE}). Possible agent loop. Halting."
                )

        return None

    def call_frequency_stats(self) -> dict:
        now = time.monotonic()
        cutoff = now - AI_LOOP_WINDOW_SEC
        with self._lock:
            recent = [(t, h) for t, h in self._call_log if t >= cutoff]
            unique_hashes = len({h for _, h in recent})
            return {
                "calls_last_60s": len(recent),
                "unique_call_signatures_last_60s": unique_hashes,
                "loop_risk": "HIGH" if len(recent) > 20 else "MEDIUM" if len(recent) > 10 else "LOW",
                "max_identical_any_call": max(
                    (sum(1 for t in times if t >= cutoff) for times in self._hash_counts.values()),
                    default=0
                ),
            }


# ═══════════════════════════════════════════════════════════════════════════
# CORE GUARDRAIL ENGINE
# ═══════════════════════════════════════════════════════════════════════════

class TradingGuardrails:
    """
    Hard-coded trading safety layer. Singleton. Thread-safe.

    The AI cannot modify any limit in this class via tool calls.
    Only a code deploy can change limits. This is intentional.

    Usage:
        guardrails = get_guardrails()
        guardrails.record_tool_call("place_order", args)   # before call
        guardrails.check_all(                               # blocks if tripped
            broker="tradovate",
            symbol="MNQ",
            qty_contracts=2,
            current_daily_pnl=-150.0,
            current_drawdown_pct=0.05,
            consecutive_losses=1,
            vix=22.0,
            total_open_notional=45_000.0,
        )
        guardrails.record_order()                           # after approved
    """

    _instance: Optional["TradingGuardrails"] = None
    _lock: threading.Lock = threading.Lock()

    def __new__(cls) -> "TradingGuardrails":
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self._state_lock = threading.RLock()  # Reentrant — _trip() calls _get_cb() while holding lock
        self._cb: Dict[str, CBStatus] = {}  # per-broker circuit breaker
        self._velocity = OrderVelocityTracker()
        self._loop_detector = ToolCallTracker()
        self._session_start = time.monotonic()
        self._load_state()
        logger.info(
            "TradingGuardrails V22 online — MAX_ORDERS_PER_MINUTE=%d "
            "MAX_DAILY_LOSS_USD=%.0f VIX_KILL=%.1f",
            MAX_ORDERS_PER_MINUTE, MAX_DAILY_LOSS_USD, VIX_KILL_THRESHOLD,
        )

    # ─── State persistence ───────────────────────────────────────────────

    def _load_state(self) -> None:
        """Restore circuit breaker state from disk (survives restarts)."""
        try:
            if _STATE_PATH.exists():
                raw = json.loads(_STATE_PATH.read_text())
                for broker, data in raw.items():
                    status = CBStatus(
                        state=CBState(data.get("state", "CLOSED")),
                        tripped_at=data.get("tripped_at", 0.0),
                        trip_reason=GuardrailReason(data["trip_reason"]) if data.get("trip_reason") else None,
                        trip_message=data.get("trip_message", ""),
                        cooldown_sec=data.get("cooldown_sec", CB_OPEN_COOLDOWN_SEC),
                        trip_count_today=data.get("trip_count_today", 0),
                    )
                    self._cb[broker] = status
                logger.info("Guardrail state restored from %s", _STATE_PATH)
        except Exception as exc:
            logger.warning("Could not restore guardrail state: %s", exc)

    def _save_state(self) -> None:
        try:
            _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            payload = {}
            for broker, status in self._cb.items():
                payload[broker] = {
                    "state": status.state.value,
                    "tripped_at": status.tripped_at,
                    "trip_reason": status.trip_reason.value if status.trip_reason else None,
                    "trip_message": status.trip_message,
                    "cooldown_sec": status.cooldown_sec,
                    "trip_count_today": status.trip_count_today,
                }
            _STATE_PATH.write_text(json.dumps(payload, indent=2))
        except Exception as exc:
            logger.warning("Could not save guardrail state: %s", exc)

    # ─── Circuit breaker management ──────────────────────────────────────

    def _get_cb(self, broker: str) -> CBStatus:
        with self._state_lock:
            if broker not in self._cb:
                self._cb[broker] = CBStatus()
            return self._cb[broker]

    def _trip(self, broker: str, reason: GuardrailReason, message: str,
               cooldown_sec: int = CB_OPEN_COOLDOWN_SEC) -> None:
        with self._state_lock:
            cb = self._get_cb(broker)
            cb.state = CBState.OPEN
            cb.tripped_at = time.monotonic()
            cb.trip_reason = reason
            cb.trip_message = message
            cb.cooldown_sec = cooldown_sec
            cb.half_open_test_allowed = False
            cb.trip_count_today += 1
        self._save_state()
        logger.critical(
            "CIRCUIT BREAKER TRIPPED broker=%s reason=%s cooldown=%ds | %s",
            broker, reason.value, cooldown_sec, message,
        )

    def _check_cb_state(self, broker: str) -> None:
        """Advance state machine and raise if OPEN."""
        cb = self._get_cb(broker)
        if cb.state == CBState.CLOSED:
            return

        now = time.monotonic()
        elapsed = now - cb.tripped_at

        if cb.state == CBState.OPEN:
            if elapsed >= cb.cooldown_sec:
                with self._state_lock:
                    cb.state = CBState.HALF_OPEN
                    cb.half_open_test_allowed = True
                self._save_state()
                logger.warning("Circuit breaker HALF_OPEN for broker=%s — test call allowed", broker)
            else:
                remaining = int(cb.cooldown_sec - elapsed)
                raise GuardrailTripped(
                    cb.trip_reason or GuardrailReason.MANUAL_TRIP,
                    f"Circuit breaker OPEN for broker '{broker}' ({cb.trip_message}). "
                    f"Cooldown: {remaining}s remaining.",
                    cooldown_sec=remaining,
                )

        if cb.state == CBState.HALF_OPEN:
            if not cb.half_open_test_allowed:
                raise GuardrailTripped(
                    cb.trip_reason or GuardrailReason.MANUAL_TRIP,
                    f"Circuit breaker HALF_OPEN for broker '{broker}': "
                    "waiting for test call result.",
                )

    def record_order_success(self, broker: str) -> None:
        """Call after a successful order fill to allow HALF_OPEN → CLOSED."""
        cb = self._get_cb(broker)
        if cb.state == CBState.HALF_OPEN:
            with self._state_lock:
                cb.state = CBState.CLOSED
                cb.half_open_test_allowed = False
                cb.trip_reason = None
                cb.trip_message = ""
            self._save_state()
            logger.info("Circuit breaker CLOSED for broker=%s (recovery confirmed)", broker)

    def record_order_failure(self, broker: str, reason: str) -> None:
        """Call after a failed order to reset HALF_OPEN → OPEN with doubled cooldown."""
        cb = self._get_cb(broker)
        if cb.state == CBState.HALF_OPEN:
            self._trip(
                broker,
                cb.trip_reason or GuardrailReason.MANUAL_TRIP,
                f"Recovery test failed: {reason}",
                cooldown_sec=cb.cooldown_sec * 2,
            )

    # ─── Individual limit checks ─────────────────────────────────────────

    def check_order_velocity(self, broker: str) -> None:
        per_min = self._velocity.orders_in_last_minute()
        per_hour = self._velocity.orders_in_last_hour()

        if per_min >= MAX_ORDERS_PER_MINUTE:
            self._trip(broker, GuardrailReason.ORDER_VELOCITY,
                       f"Order velocity: {per_min}/min (limit {MAX_ORDERS_PER_MINUTE})",
                       CB_OPEN_COOLDOWN_SEC)
            raise GuardrailTripped(
                GuardrailReason.ORDER_VELOCITY,
                f"Order velocity limit hit: {per_min} orders in last minute "
                f"(hard limit: {MAX_ORDERS_PER_MINUTE}). "
                "Tradovate allows 80/min but we cap at 10 to prevent loops.",
                cooldown_sec=60,
            )

        if per_hour >= MAX_ORDERS_PER_HOUR:
            self._trip(broker, GuardrailReason.ORDER_VELOCITY,
                       f"Order velocity: {per_hour}/hour (limit {MAX_ORDERS_PER_HOUR})",
                       CB_OPEN_COOLDOWN_SEC)
            raise GuardrailTripped(
                GuardrailReason.ORDER_VELOCITY,
                f"Hourly order limit hit: {per_hour} orders in last hour "
                f"(hard limit: {MAX_ORDERS_PER_HOUR}).",
                cooldown_sec=3600 - (self._velocity.orders_in_last_hour() * 60),
            )

    def check_daily_loss(self, broker: str, current_daily_pnl: float) -> None:
        if current_daily_pnl <= -MAX_DAILY_LOSS_USD:
            self._trip(broker, GuardrailReason.DAILY_LOSS,
                       f"Daily P&L: ${current_daily_pnl:.2f} (limit -${MAX_DAILY_LOSS_USD:.0f})",
                       CB_DAILY_LOSS_COOLDOWN_SEC)
            raise GuardrailTripped(
                GuardrailReason.DAILY_LOSS,
                f"Daily loss limit reached: ${current_daily_pnl:.2f} "
                f"(hard limit: -${MAX_DAILY_LOSS_USD:.0f}). "
                "No new orders until tomorrow's open.",
                cooldown_sec=CB_DAILY_LOSS_COOLDOWN_SEC,
            )

    def check_drawdown(self, broker: str, current_drawdown_pct: float) -> None:
        if current_drawdown_pct >= MAX_DRAWDOWN_PCT:
            self._trip(broker, GuardrailReason.DRAWDOWN,
                       f"Drawdown: {current_drawdown_pct*100:.1f}% (limit {MAX_DRAWDOWN_PCT*100:.0f}%)",
                       CB_OPEN_COOLDOWN_SEC)
            raise GuardrailTripped(
                GuardrailReason.DRAWDOWN,
                f"Max drawdown breached: {current_drawdown_pct*100:.1f}% "
                f"(hard limit: {MAX_DRAWDOWN_PCT*100:.0f}%).",
                cooldown_sec=CB_OPEN_COOLDOWN_SEC,
            )

    def check_consecutive_losses(self, broker: str, consecutive_losses: int) -> None:
        if consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
            self._trip(broker, GuardrailReason.CONSECUTIVE_LOSSES,
                       f"Consecutive losses: {consecutive_losses} (limit {MAX_CONSECUTIVE_LOSSES})",
                       CB_CONSECUTIVE_LOSS_COOLDOWN_SEC)
            raise GuardrailTripped(
                GuardrailReason.CONSECUTIVE_LOSSES,
                f"{consecutive_losses} consecutive losses detected "
                f"(limit: {MAX_CONSECUTIVE_LOSSES}). "
                "Market conditions may be hostile. Cooling off for 1 hour.",
                cooldown_sec=CB_CONSECUTIVE_LOSS_COOLDOWN_SEC,
            )

    def check_position_size(self, broker: str, symbol: str, qty_contracts: float) -> None:
        if abs(qty_contracts) > MAX_POSITION_SIZE_CONTRACTS:
            raise GuardrailTripped(
                GuardrailReason.POSITION_SIZE,
                f"Position size {qty_contracts} contracts exceeds hard limit "
                f"{MAX_POSITION_SIZE_CONTRACTS} for {symbol}.",
            )

    def check_notional(self, broker: str, total_open_notional: float) -> None:
        if total_open_notional > MAX_TOTAL_OPEN_NOTIONAL_USD:
            raise GuardrailTripped(
                GuardrailReason.NOTIONAL_LIMIT,
                f"Total open notional ${total_open_notional:,.0f} exceeds hard limit "
                f"${MAX_TOTAL_OPEN_NOTIONAL_USD:,.0f}.",
            )

    def check_vix(self, broker: str, vix: float) -> None:
        if vix > VIX_KILL_THRESHOLD:
            raise GuardrailTripped(
                GuardrailReason.VIX_KILL,
                f"VIX {vix:.1f} exceeds kill threshold {VIX_KILL_THRESHOLD:.0f}. "
                "All trading halted during extreme volatility regime.",
            )

    def check_ai_loop(self, tool_name: str, arguments: dict) -> None:
        warning = self._loop_detector.check_loop(tool_name, arguments)
        if warning:
            # Trip ALL brokers when an AI loop is detected
            for broker in list(self._cb.keys()) + ["tradovate", "alpaca", "oanda"]:
                if broker not in self._cb:
                    self._cb[broker] = CBStatus()
                self._trip(broker, GuardrailReason.AI_LOOP_DETECTED, warning, CB_OPEN_COOLDOWN_SEC)
            raise GuardrailTripped(
                GuardrailReason.AI_LOOP_DETECTED,
                warning,
                cooldown_sec=CB_OPEN_COOLDOWN_SEC,
            )

    # ─── Master check — call before every order ──────────────────────────

    def check_all(
        self,
        broker: str,
        symbol: str,
        qty_contracts: float,
        current_daily_pnl: float = 0.0,
        current_drawdown_pct: float = 0.0,
        consecutive_losses: int = 0,
        vix: float = 0.0,
        total_open_notional: float = 0.0,
    ) -> None:
        """
        Master pre-order gate. Raises GuardrailTripped if ANY hard limit is breached.
        All checks are evaluated in priority order — most dangerous first.

        Args:
            broker: "tradovate" | "alpaca" | "oanda"
            symbol: Instrument symbol (MNQ, CL, EURUSD, etc.)
            qty_contracts: Absolute position size being requested
            current_daily_pnl: Today's realized P&L in USD (negative = loss)
            current_drawdown_pct: Current drawdown as decimal (0.05 = 5%)
            consecutive_losses: Count of losing trades in a row
            vix: Current VIX level (0.0 = skip check)
            total_open_notional: Total USD notional across all open positions
        """
        # 1. Circuit breaker state (fastest — blocks if already OPEN)
        self._check_cb_state(broker)

        # 2. VIX kill switch (market-wide halt)
        if vix > 0:
            self.check_vix(broker, vix)

        # 3. Financial loss limits
        if current_daily_pnl != 0:
            self.check_daily_loss(broker, current_daily_pnl)
        if current_drawdown_pct > 0:
            self.check_drawdown(broker, current_drawdown_pct)
        if consecutive_losses > 0:
            self.check_consecutive_losses(broker, consecutive_losses)

        # 4. Order velocity (anti-loop protection)
        self.check_order_velocity(broker)

        # 5. Position size / notional
        self.check_position_size(broker, symbol, qty_contracts)
        if total_open_notional > 0:
            self.check_notional(broker, total_open_notional)

        logger.debug(
            "Guardrails PASSED broker=%s symbol=%s qty=%s pnl=%.0f drawdown=%.1f%% vix=%.1f",
            broker, symbol, qty_contracts, current_daily_pnl,
            current_drawdown_pct * 100, vix,
        )

    def record_tool_call(self, tool_name: str, arguments: dict) -> None:
        """Record every MCP tool call for loop detection (even non-order tools)."""
        self.check_ai_loop(tool_name, arguments)
        self._loop_detector.record_call(tool_name, arguments)

    def record_order(self) -> None:
        """Call after check_all passes and BEFORE submitting to broker."""
        self._velocity.record_order()

    # ─── Status / diagnostics (readable by AI but not modifiable) ────────

    def get_status(self) -> dict:
        """Return current guardrail state. Read-only. Safe to expose as a tool."""
        now = time.monotonic()
        broker_statuses = {}
        with self._state_lock:
            for broker, cb in self._cb.items():
                elapsed = now - cb.tripped_at if cb.tripped_at > 0 else 0
                remaining = max(0, cb.cooldown_sec - elapsed) if cb.state == CBState.OPEN else 0
                broker_statuses[broker] = {
                    "state": cb.state.value,
                    "trip_reason": cb.trip_reason.value if cb.trip_reason else None,
                    "trip_message": cb.trip_message,
                    "cooldown_remaining_sec": int(remaining),
                    "trip_count_today": cb.trip_count_today,
                }

        orders_last_min = self._velocity.orders_in_last_minute()
        orders_last_hour = self._velocity.orders_in_last_hour()
        loop_stats = self._loop_detector.call_frequency_stats()

        return {
            "hard_coded_limits": {
                "max_orders_per_minute": MAX_ORDERS_PER_MINUTE,
                "max_orders_per_hour": MAX_ORDERS_PER_HOUR,
                "max_daily_loss_usd": MAX_DAILY_LOSS_USD,
                "max_drawdown_pct": MAX_DRAWDOWN_PCT,
                "max_consecutive_losses": MAX_CONSECUTIVE_LOSSES,
                "max_position_size_contracts": MAX_POSITION_SIZE_CONTRACTS,
                "max_total_notional_usd": MAX_TOTAL_OPEN_NOTIONAL_USD,
                "vix_kill_threshold": VIX_KILL_THRESHOLD,
                "ai_loop_identical_calls_limit": AI_LOOP_MAX_IDENTICAL_CALLS,
                "ai_tool_calls_per_minute_limit": AI_LOOP_MAX_CALLS_PER_MINUTE,
            },
            "current_velocity": {
                "orders_last_minute": orders_last_min,
                "orders_last_hour": orders_last_hour,
                "orders_per_minute_headroom": max(0, MAX_ORDERS_PER_MINUTE - orders_last_min),
                "orders_per_hour_headroom": max(0, MAX_ORDERS_PER_HOUR - orders_last_hour),
            },
            "loop_detection": loop_stats,
            "broker_circuit_breakers": broker_statuses,
            "all_clear": all(
                cb.get("state") == "CLOSED"
                for cb in broker_statuses.values()
            ),
        }

    def manual_reset(self, broker: str, reason: str = "manual override by owner") -> dict:
        """
        Emergency reset by the owner (Tyler). NOT exposed as a public tool.
        Only callable from authenticated admin endpoints.
        """
        with self._state_lock:
            if broker in self._cb:
                self._cb[broker] = CBStatus()
        self._save_state()
        logger.warning("MANUAL RESET of circuit breaker for broker=%s | reason: %s", broker, reason)
        return {"reset": True, "broker": broker, "reason": reason}


# ═══════════════════════════════════════════════════════════════════════════
# SINGLETON ACCESS
# ═══════════════════════════════════════════════════════════════════════════

_guardrails: Optional[TradingGuardrails] = None


def get_guardrails() -> TradingGuardrails:
    """Return the singleton TradingGuardrails instance."""
    global _guardrails
    if _guardrails is None:
        _guardrails = TradingGuardrails()
    return _guardrails
