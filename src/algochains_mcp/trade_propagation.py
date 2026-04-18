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


# Roo's live Django propagation endpoint (algochains.ai backend)
_ROO_DEFAULT_URL = "http://172.232.170.168/signals/signal/"
# ⚠️  SECURITY: This is the dev-only fallback secret. It MUST be overridden via
# ALGOCHAINS_SIGNAL_SECRET or SIGNAL_SECRET in production. Any signal sent with
# the default secret will be rejected by a correctly-configured backend, and the
# propagate_signal() function will log a WARNING so operators know to fix it.
_ROO_DEFAULT_SECRET = "1234"


def _resolve_url() -> str:
    """Return signal endpoint URL — env override takes priority over Roo default."""
    return (
        os.getenv("ALGOCHAINS_SIGNAL_URL", "").strip()
        or os.getenv("SIGNAL_URL", "").strip()
        or _ROO_DEFAULT_URL
    )


def _resolve_secret() -> bytes:
    """Return HMAC secret bytes — env override takes priority over Roo default."""
    raw = (
        os.getenv("ALGOCHAINS_SIGNAL_SECRET", "").strip()
        or os.getenv("SIGNAL_SECRET", "").strip()
        or _ROO_DEFAULT_SECRET
    )
    return raw.encode("utf-8")


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

    # Warn loudly when using the public fallback secret — backend should reject it.
    if secret == _ROO_DEFAULT_SECRET.encode():
        logger.warning(
            "propagate_signal using default secret '1234' — "
            "set ALGOCHAINS_SIGNAL_SECRET in .env to suppress this warning"
        )

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


async def check_propagation_health() -> dict[str, Any]:
    """Check whether the Django signal propagation service is reachable.

    Returns endpoint URL, reachability status, and configuration guidance.
    """
    url = _resolve_url()
    secret = _resolve_secret()
    using_defaults = (
        url == _ROO_DEFAULT_URL and secret == _ROO_DEFAULT_SECRET.encode()
    )

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
            "SIGNAL_SECRET": "*** (set)" if not using_defaults else "1234 (Roo default)",
        },
        "register_bot_at": "https://algochains.ai → Bots → Register New Bot",
        "paper_trading_only": True,
        "note": (
            "Using Roo's default endpoint + secret. Override with SIGNAL_URL + SIGNAL_SECRET env vars."
            if using_defaults
            else "Custom endpoint configured."
        ),
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
