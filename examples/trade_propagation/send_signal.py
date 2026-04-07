#!/usr/bin/env python3
"""
Standalone signal client for AlgoChains Django trade propagation.

See TRADE_PROPAGATION.md in this folder. Do not commit real secrets.

Required environment:
  SIGNAL_URL or ALGOCHAINS_SIGNAL_URL — full URL to the signal ingest endpoint
  SIGNAL_SECRET or ALGOCHAINS_SIGNAL_SECRET — shared HMAC secret (UTF-8 string)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
from datetime import datetime, timezone

import requests

_URL = (
    os.getenv("ALGOCHAINS_SIGNAL_URL", "").strip()
    or os.getenv("SIGNAL_URL", "").strip()
)
_SECRET_RAW = (
    os.getenv("ALGOCHAINS_SIGNAL_SECRET", "").strip()
    or os.getenv("SIGNAL_SECRET", "").strip()
)
_SECRET = _SECRET_RAW.encode("utf-8")


def signal_to_api(
    strategy_name: str,
    symbol: str,
    side: str,
    qty: float,
    confidence: float = 0.0,
    stop_loss: float = 0.0,
    take_profit: float = 0.0,
) -> tuple[int, str]:
    if not _URL or not _SECRET_RAW:
        raise RuntimeError(
            "Set SIGNAL_URL and SIGNAL_SECRET (or ALGOCHAINS_* variants) before sending."
        )
    signal = {
        "strategy_name": strategy_name,
        "symbol": symbol.replace("-", "/"),
        "side": side.upper(),
        "qty": qty,
        "confidence": confidence,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    body = json.dumps(signal, separators=(",", ":")).encode("utf-8")
    sig = hmac.new(_SECRET, body, hashlib.sha256).hexdigest()
    r = requests.post(
        _URL,
        data=body,
        headers={"Content-Type": "application/json", "X-Signature": sig},
        timeout=15,
    )
    return r.status_code, r.text


if __name__ == "__main__":
    if len(sys.argv) < 5:
        print("Usage: send_signal.py <strategy_name> <symbol> <BUY|SELL> <qty>", file=sys.stderr)
        sys.exit(2)
    code, body = signal_to_api(
        sys.argv[1],
        sys.argv[2],
        sys.argv[3],
        float(sys.argv[4]),
    )
    print(code, body)
