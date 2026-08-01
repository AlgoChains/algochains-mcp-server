"""
trade_propagation.py — Django Trade Propagation Bridge
======================================================

Sends signed trade signals to the AlgoChains Django signal ingest endpoint
(Roo's architecture: bot → HTTP POST → subscribers' paper brokers).

Aligned with TRADE_PROPAGATION.md and ``send_signal.py``:
  - JSON body with strategy_name, symbol, side, qty, confidence, SL/TP, timestamp
  - HMAC-SHA256 over raw body bytes in ``X-Signature`` header

**Security / real-data policy**
  - ``SIGNAL_URL`` and ``SIGNAL_SECRET`` (or ``ALGOCHAINS_SIGNAL_URL`` /
    ``ALGOCHAINS_SIGNAL_SECRET``) must be set — **no default production secret**.
  - If unset, calls fail closed with a clear configuration error.

This module does **not** invent fills, prices, or broker responses — only relays
what the caller sends and returns the HTTP status and response body from Django.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger("algochains_mcp.trade_propagation")

_DEFAULT_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


# Legacy HTTP endpoint kept for documentation only — never used in production.
# Set ALGOCHAINS_SIGNAL_URL=http://172.232.170.168/signals/signal/ explicitly
# if you need the legacy HTTP endpoint during migration.
_ROO_LEGACY_HTTP_URL = "http://172.232.170.168/signals/signal/"  # documentation only


def _resolve_url() -> str:
    """Return signal endpoint URL — fails closed when env var is unset."""
    url = (
        os.getenv("ALGOCHAINS_SIGNAL_URL", "").strip()
        or os.getenv("SIGNAL_URL", "").strip()
    )
    if not url:
        raise RuntimeError(
            "trade_propagation: ALGOCHAINS_SIGNAL_URL (or SIGNAL_URL) is not set. "
            "Refusing to propagate signal over an unverified endpoint. "
            "Set ALGOCHAINS_SIGNAL_URL=https://... to enable signal propagation."
        )
    if url.startswith("http://"):
        logger.warning(
            "trade_propagation: ALGOCHAINS_SIGNAL_URL uses plain HTTP (%s). "
            "HMAC secret and trade signals are transmitted in cleartext. "
            "Use HTTPS to protect signal integrity.",
            url,
        )
    return url


def _resolve_secret() -> bytes:
    """Return HMAC secret bytes — fails closed when ALGOCHAINS_SIGNAL_SECRET is unset.

    The dev fallback secret was removed after the repo was briefly public (2026-06-08).
    Set ALGOCHAINS_SIGNAL_SECRET in .env to match the Django signal ingest endpoint.
    Contact Roo to confirm the current server-side secret has been rotated.
    """
    raw = (
        os.getenv("ALGOCHAINS_SIGNAL_SECRET", "").strip()
        or os.getenv("SIGNAL_SECRET", "").strip()
    )
    if not raw:
        raise RuntimeError(
            "trade_propagation: ALGOCHAINS_SIGNAL_SECRET (or SIGNAL_SECRET) is not set. "
            "Set it in .env to match the Django signal ingest endpoint HMAC secret. "
            "The previous '1234' default was removed — rotate the server-side secret too."
        )
    return raw.encode("utf-8")


def _service_client():
    try:
        from .marketplace.supabase_tools import _get_sb_client
    except Exception as exc:  # pragma: no cover
        logger.warning("supabase_tools unavailable: %s", exc)
        return None
    return _get_sb_client(use_service_role=True)


def _rows(resp: Any) -> list[dict[str, Any]]:
    return list(getattr(resp, "data", None) or [])


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _age_seconds(now: datetime, value: Any) -> float | None:
    dt = _parse_timestamp(value)
    if dt is None:
        return None
    return round(max(0.0, (now - dt).total_seconds()), 2)


async def propagate_signal(
    strategy_name: str,
    symbol: str,
    side: str,
    qty: float,
    confidence: float = 0.0,
    stop_loss: float = 0.0,
    take_profit: float = 0.0,
) -> dict[str, Any]:
    """
    POST a trade signal to the AlgoChains propagation service.

    ``strategy_name`` must match the bot name registered on algochains.ai exactly.
    """
    url = _resolve_url()
    secret = _resolve_secret()

    if not url:
        return {
            "success": False,
            "error": (
                "SIGNAL_URL or ALGOCHAINS_SIGNAL_URL is not set. "
                "Configure the Django signal endpoint before propagating."
            ),
        }
    if not secret:
        return {
            "success": False,
            "error": (
                "SIGNAL_SECRET or ALGOCHAINS_SIGNAL_SECRET is not set. "
                "Refusing to send unsigned or empty-secret requests."
            ),
        }

    # NOTE: _resolve_secret() already raises RuntimeError when the secret is
    # unset or empty — we never reach this point with an invalid secret.
    # The previous _ROO_DEFAULT_SECRET check was removed when the dev fallback
    # was removed from the module (2026-06-08 public exposure incident).

    if not strategy_name or not symbol or not side or qty <= 0:
        return {
            "success": False,
            "error": "strategy_name, symbol, side, and positive qty are required",
        }

    signal: dict[str, Any] = {
        "strategy_name": strategy_name.strip(),
        "symbol": symbol.strip().replace("-", "/"),
        "side": side.strip().upper(),
        "qty": qty,
        "confidence": confidence,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    body = json.dumps(signal, separators=(",", ":")).encode("utf-8")
    sig = hmac.new(secret, body, hashlib.sha256).hexdigest()

    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            resp = await client.post(
                url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Signature": sig,
                },
            )
        text = resp.text[:2000] if resp.text else ""
        ok = 200 <= resp.status_code < 300
        if ok:
            logger.info(
                "Signal propagated strategy=%s side=%s status=%s",
                strategy_name,
                side,
                resp.status_code,
            )
        else:
            logger.warning(
                "Signal propagate non-success strategy=%s status=%s body=%s",
                strategy_name,
                resp.status_code,
                text[:500],
            )
        return {
            "success": ok,
            "http_status": resp.status_code,
            "response_body": text,
            "strategy_name": strategy_name,
            "symbol": signal["symbol"],
            "side": signal["side"],
            "qty": qty,
        }
    except httpx.RequestError as exc:
        logger.error("Signal propagate network error: %s", exc)
        return {"success": False, "error": f"HTTP request failed: {exc}"}


def get_copy_trade_fanout_health(max_lag_seconds: float = 30.0) -> dict[str, Any]:
    """Return copy-trade fanout health without treating idle time as lag.

    The Command Center alert should page on active, unexpired signal backlog.
    Age since the last historical signal is useful telemetry, but it is not a
    stall when there are no active signals waiting to be fanned out.
    """
    sb = _service_client()
    if sb is None:
        return {
            "status": "unknown",
            "reason": "supabase_unavailable",
            "detail": "SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY are required for fanout health",
        }

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    try:
        active_signals = _rows(
            sb.table("copy_trade_signals")
            .select("id,bot,symbol,side,emitted_at,expires_at")
            .gt("expires_at", now_iso)
            .order("emitted_at", desc=False)
            .limit(100)
            .execute()
        )
        latest_signal = _rows(
            sb.table("copy_trade_signals")
            .select("emitted_at,expires_at")
            .order("emitted_at", desc=True)
            .limit(1)
            .execute()
        )
        latest_audit = _rows(
            sb.table("copy_trade_signal_audit")
            .select("occurred_at")
            .order("occurred_at", desc=True)
            .limit(1)
            .execute()
        )
        latest_paper_fill = _rows(
            sb.table("subscriber_fills")
            .select("filled_at")
            .eq("is_paper", True)
            .order("filled_at", desc=True)
            .limit(1)
            .execute()
        )
        paper_accounts = _rows(
            sb.table("subscriber_paper_accounts")
            .select("subscriber_id,updated_at")
            .order("updated_at", desc=True)
            .limit(1000)
            .execute()
        )
    except Exception as exc:
        return {"status": "unknown", "reason": "query_failed", "detail": str(exc)}

    active_lag_seconds = 0.0
    if active_signals:
        active_lag_seconds = _age_seconds(now, active_signals[0].get("emitted_at")) or 0.0

    if active_signals and active_lag_seconds > max_lag_seconds:
        status = "degraded"
        reason = "active_signal_lag_high"
    elif active_signals:
        status = "healthy"
        reason = "active_signals_within_slo"
    else:
        status = "healthy"
        reason = "idle_no_active_signals"

    latest_signal_row = latest_signal[0] if latest_signal else {}
    latest_audit_row = latest_audit[0] if latest_audit else {}
    latest_fill_row = latest_paper_fill[0] if latest_paper_fill else {}
    latest_account_row = paper_accounts[0] if paper_accounts else {}

    return {
        "status": status,
        "reason": reason,
        "max_lag_seconds": float(max_lag_seconds),
        "active_signal_count": len(active_signals),
        "active_lag_seconds": active_lag_seconds,
        "idle_since_last_signal_seconds": _age_seconds(now, latest_signal_row.get("emitted_at")),
        "latest_audit_age_seconds": _age_seconds(now, latest_audit_row.get("occurred_at")),
        "latest_paper_fill_age_seconds": _age_seconds(now, latest_fill_row.get("filled_at")),
        "latest_paper_account_update_age_seconds": _age_seconds(now, latest_account_row.get("updated_at")),
        "paper_account_count": len(paper_accounts),
        "note": (
            "active_lag_seconds is the paging signal. "
            "idle_since_last_signal_seconds is informational and may grow overnight."
        ),
    }


async def check_propagation_health(max_lag_seconds: float = 30.0) -> dict[str, Any]:
    """Check whether the Django signal propagation service is reachable.

    Returns endpoint URL, reachability status, and configuration guidance.
    """
    url = _resolve_url()
    secret = _resolve_secret()
    # _ROO_DEFAULT_URL / _ROO_DEFAULT_SECRET constants were removed after the
    # dev fallback was retired (2026-06-08). _resolve_url() / _resolve_secret()
    # already raise RuntimeError when unset, so using_defaults is always False
    # when execution reaches this point.
    using_defaults = False

    base_url = url.rsplit("/signal", 1)[0] if "/signal" in url else url
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0)) as client:
            resp = await client.get(base_url + "/", follow_redirects=True)
        reachable = resp.status_code < 500
    except httpx.RequestError:
        reachable = False

    return {
        "endpoint": url,
        "reachable": reachable,
        "using_roo_defaults": using_defaults,
        "setup": {
            "SIGNAL_URL": url,
            "SIGNAL_SECRET": "*** (set)",
        },
        "register_bot_at": "https://algochains.ai → Bots → Register New Bot",
        "paper_trading_only": True,
        "copy_trade_fanout": get_copy_trade_fanout_health(max_lag_seconds=max_lag_seconds),
        "note": "Custom endpoint configured.",
    }


async def run_dummy_signal_test(strategy_name: str, symbol: str = "BTC/USD", qty: float = 0.001) -> dict[str, Any]:
    """Run Roo's 3-signal verification sequence: BUY → (immediate) → SELL → BUY.

    NOTE: Does NOT sleep 2 minutes like the original dummy_signal_test.py.
    All 3 signals are sent immediately so the MCP tool returns fast.
    Check your algochains.ai dashboard to see all 3 trades appear.
    """
    if not strategy_name or strategy_name in ("YourBotNameHere", ""):
        return {
            "error": "Provide your exact bot name from algochains.ai",
            "usage": "test_signal_propagation({'strategy_name': 'MyBot', 'symbol': 'BTC/USD'})",
            "register_at": "https://algochains.ai → Bots → Register New Bot",
        }

    results = []
    for side in ("BUY", "SELL", "BUY"):
        result = await propagate_signal(
            strategy_name=strategy_name,
            symbol=symbol,
            side=side,
            qty=qty,
        )
        results.append({"side": side, **result})

    all_ok = all(r.get("success") for r in results)
    return {
        "test": "dummy_signal_test",
        "strategy_name": strategy_name,
        "symbol": symbol,
        "qty": qty,
        "signals_sent": len(results),
        "all_succeeded": all_ok,
        "results": results,
        "next_step": (
            "Check your algochains.ai dashboard — 3 trades should appear on your paper account."
            if all_ok
            else "Some signals failed — check endpoint config and bot registration."
        ),
    }
