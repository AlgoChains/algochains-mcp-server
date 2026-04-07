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


def _resolve_url() -> str:
    return (
        os.getenv("ALGOCHAINS_SIGNAL_URL", "").strip()
        or os.getenv("SIGNAL_URL", "").strip()
    )


def _resolve_secret() -> bytes:
    raw = (
        os.getenv("ALGOCHAINS_SIGNAL_SECRET", "").strip()
        or os.getenv("SIGNAL_SECRET", "").strip()
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
