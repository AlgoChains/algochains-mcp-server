"""Minimal loop to verify trade propagation (see TRADE_PROPAGATION.md)."""
from __future__ import annotations

import time

from send_signal import signal_to_api

BOT_NAME = "YourBotNameHere"
SYMBOL = "BTC/USD"
QTY = 0.001


def send(side: str) -> None:
    print(f"Sending {side} {QTY} {SYMBOL} ...")
    status, response = signal_to_api(
        strategy_name=BOT_NAME,
        symbol=SYMBOL,
        side=side,
        qty=QTY,
    )
    print(f"  → status={status}  response={response}\n")


if __name__ == "__main__":
    send("BUY")
    print("Waiting 2 minutes ...")
    time.sleep(120)
    send("SELL")
    print("Waiting 1 minute ...")
    time.sleep(60)
    send("BUY")
    print("Done — check algochains.ai dashboard.")
