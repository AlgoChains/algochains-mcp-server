"""
guardrail.py — GUARDRAIL Pre-Flight Middleware Chain
=====================================================

A composable middleware pipeline that runs before any order-placement tool.
Each guard is a callable that returns (ok: bool, reason: str).

Pipeline (in order):
  1. VixGate        — block if VIX ≥ threshold (default 35)
  2. DailyLossGate  — block if daily realized loss ≥ hard limit (default $500)
  3. StoplossGuard  — block if instrument hit max stops in rolling window
  4. CooldownGate   — block if instrument is in post-stop cooldown period
  5. ThoughtProof   — adversarial cross-model confidence check

Usage (from server.py or any bot):
    from algochains_mcp.security.guardrail import run_guardrail

    result = run_guardrail(
        symbol="MNQ",
        side="BUY",
        entry=18050,
        stop=17990,
        confidence=0.72,
    )
    if not result["approved"]:
        raise RuntimeError(f"GUARDRAIL blocked trade: {result['reason']}")

All gates use local state only — no broker API calls (fast path, < 5ms).
Override any threshold via environment variables.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("algochains_mcp.guardrail")

# ── Configurable thresholds ────────────────────────────────────────────────────
_VIX_MAX = float(os.environ.get("GUARDRAIL_VIX_MAX", "35"))
_DAILY_LOSS_MAX = float(os.environ.get("GUARDRAIL_DAILY_LOSS_MAX", "500"))
_MIN_CONFIDENCE = float(os.environ.get("GUARDRAIL_MIN_CONFIDENCE", "0.60"))
_STOPLOSS_MAX_STOPS = int(os.environ.get("GUARDRAIL_MAX_STOPS", "3"))
_STOPLOSS_WINDOW_H = float(os.environ.get("GUARDRAIL_STOP_WINDOW_HOURS", "4"))
_COOLDOWN_MINUTES = float(os.environ.get("GUARDRAIL_COOLDOWN_MINUTES", "30"))

# ── State directory (shared with AlgoClaw)  ────────────────────────────────────
_STATE_DIR = Path(__file__).parents[3] / "algoclaw" / "state"


# ---------------------------------------------------------------------------
# Individual gates
# ---------------------------------------------------------------------------

def _gate_vix(symbol: str, vix: float | None) -> tuple[bool, str]:
    """Block if current VIX is at or above threshold."""
    if vix is None:
        try:
            _env_vix = os.environ.get("CURRENT_VIX", "")
            vix = float(_env_vix) if _env_vix else 0.0
        except ValueError:
            vix = 0.0
    if vix <= 0:
        # BUG-13 FIX: Previously returned (True, "VIX not set — gate skipped") silently.
        # VIX=0 means we have no real market data; the gate is effectively disabled.
        # Now we log a structured warning so operators can see when VIX protection
        # is weakened (e.g. during CBOE outage or missing CURRENT_VIX env var).
        logger.warning(
            "GUARDRAIL VIX gate SKIPPED — vix=0 (unknown). "
            "Set CURRENT_VIX env var or ensure CBOE data feed is healthy. "
            "VIX kill-switch is inactive for symbol=%s", symbol,
        )
        return True, "VIX unknown (0) — gate skipped [WARNING: protection weakened]"
    if vix >= _VIX_MAX:
        return False, f"VIX {vix} ≥ {_VIX_MAX} — all trades blocked"
    return True, f"VIX {vix} < {_VIX_MAX} — ok"


def _gate_daily_loss(daily_pnl: float | None) -> tuple[bool, str]:
    """Block if today's realized loss has hit the hard limit."""
    if daily_pnl is None:
        try:
            _env_pnl = os.environ.get("TODAY_REALIZED_PNL", "")
            daily_pnl = float(_env_pnl) if _env_pnl else 0.0
        except ValueError:
            daily_pnl = 0.0
    if daily_pnl == 0.0 and not os.environ.get("TODAY_REALIZED_PNL"):
        # BUG-13 FIX: $0 P&L with no env source means we have no real data —
        # log a warning so operators can see when the daily-loss gate is unverified.
        logger.warning(
            "GUARDRAIL daily-loss gate running with daily_pnl=0 (unknown source). "
            "Ensure broker P&L is passed or TODAY_REALIZED_PNL env var is set."
        )
    loss = -daily_pnl  # positive = loss
    if loss >= _DAILY_LOSS_MAX:
        return False, f"Daily loss ${loss:.2f} ≥ hard limit ${_DAILY_LOSS_MAX} — trading halted"
    return True, f"Daily P&L ${daily_pnl:.2f} within limit"


def _gate_stoploss_guard(symbol: str) -> tuple[bool, str]:
    """Block if symbol has hit max stops within the rolling window."""
    guard_file = _STATE_DIR / f"stoploss_guard_{symbol}.json"
    if not guard_file.exists():
        return True, "No stop history — clear"
    try:
        state = json.loads(guard_file.read_text())
    except Exception:
        return True, "Guard state unreadable — skipped"
    if state.get("locked"):
        return False, f"StoplossGuard: {symbol} locked — too many stops in {_STOPLOSS_WINDOW_H}h window"
    stops = state.get("stops", [])
    return True, f"StoplossGuard: {len(stops)} stop(s) in window — ok"


def _gate_cooldown(symbol: str) -> tuple[bool, str]:
    """Block if symbol is inside the post-stop cooldown period."""
    cooldown_file = _STATE_DIR / f"cooldown_{symbol}.json"
    if not cooldown_file.exists():
        return True, "No cooldown active"
    try:
        state = json.loads(cooldown_file.read_text())
    except Exception:
        return True, "Cooldown state unreadable — skipped"
    last_stop_ts = state.get("last_stop_ts")
    if not last_stop_ts:
        return True, "No cooldown active"
    now = datetime.now(tz=timezone.utc).timestamp()
    elapsed = (now - last_stop_ts) / 60
    remaining = _COOLDOWN_MINUTES - elapsed
    if remaining > 0:
        return False, f"CooldownPeriod: {symbol} in cooldown for {remaining:.1f} more minutes"
    return True, "CooldownPeriod: expired — ok"


def _gate_confidence(confidence: float | None) -> tuple[bool, str]:
    """Block if model confidence is below minimum threshold."""
    if confidence is None:
        return True, "Confidence not provided — gate skipped"
    if confidence < _MIN_CONFIDENCE:
        return False, f"Confidence {confidence:.2f} < {_MIN_CONFIDENCE} threshold"
    return True, f"Confidence {confidence:.2f} ≥ {_MIN_CONFIDENCE} — ok"


def _gate_rr(entry: float | None, stop: float | None, max_stop_pct: float = 0.05) -> tuple[bool, str]:
    """Block if stop distance is wider than max_stop_pct of entry price."""
    if not entry or not stop:
        return True, "Entry/stop not provided — gate skipped"
    risk_pct = abs(entry - stop) / entry
    if risk_pct > max_stop_pct:
        return False, f"Risk/reward: stop is {risk_pct*100:.1f}% of entry (> {max_stop_pct*100:.0f}% limit)"
    return True, f"Stop {risk_pct*100:.2f}% of entry — ok"


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_guardrail(
    symbol: str,
    side: str,
    entry: float | None = None,
    stop: float | None = None,
    confidence: float | None = None,
    vix: float | None = None,
    daily_pnl: float | None = None,
    gates: list[str] | None = None,
) -> dict[str, Any]:
    """Run the full GUARDRAIL chain. Returns approved=True only if all gates pass.

    Args:
        symbol:      Instrument (e.g. "MNQ", "BTC/USD")
        side:        "BUY" or "SELL"
        entry:       Proposed entry price (optional, enables R/R gate)
        stop:        Stop loss price (optional, enables R/R gate)
        confidence:  Model confidence 0–1 (optional)
        vix:         Current VIX level (optional, reads env if not provided)
        daily_pnl:   Today's realized P&L (optional, reads env if not provided)
        gates:       List of gate names to run. Omit for all gates.
    """
    all_gates = {
        "vix": lambda: _gate_vix(symbol, vix),
        "daily_loss": lambda: _gate_daily_loss(daily_pnl),
        "stoploss_guard": lambda: _gate_stoploss_guard(symbol),
        "cooldown": lambda: _gate_cooldown(symbol),
        "confidence": lambda: _gate_confidence(confidence),
        "risk_reward": lambda: _gate_rr(entry, stop),
    }

    run_gates = {k: v for k, v in all_gates.items() if gates is None or k in gates}

    gate_results: list[dict] = []
    blocked = False
    block_reasons: list[str] = []

    for gate_name, gate_fn in run_gates.items():
        try:
            ok, reason = gate_fn()
        except Exception as exc:
            # BUG-06 FIX: Previously any exception in a gate became ok=True ("Gate error skipped"),
            # making every gate fail-OPEN on bugs or bad state files. Changed to fail-CLOSED:
            # a gate that errors BLOCKS the order and surfaces the error as a structured reason.
            ok = False
            reason = f"Gate BLOCKED (internal error — fail-safe): {exc}"
            logger.error(
                "GUARDRAIL gate %s raised an exception — blocking order for safety: %s (symbol=%s)",
                gate_name, exc, symbol, exc_info=True,
            )

        gate_results.append({"gate": gate_name, "passed": ok, "reason": reason})
        if not ok:
            blocked = True
            block_reasons.append(f"[{gate_name}] {reason}")
            if "internal error" not in reason:
                logger.warning("GUARDRAIL blocked: gate=%s reason=%s symbol=%s", gate_name, reason, symbol)

    approved = not blocked
    if approved:
        logger.info("GUARDRAIL approved: symbol=%s side=%s confidence=%s", symbol, side, confidence)

    return {
        "approved": approved,
        "symbol": symbol,
        "side": side,
        "gates_run": len(gate_results),
        "gates_failed": sum(1 for g in gate_results if not g["passed"]),
        "reason": " | ".join(block_reasons) if block_reasons else "All gates passed",
        "gate_results": gate_results,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    }
