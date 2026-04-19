"""
Kalshi WebSocket Client — AlgoChains v1.0

Implements the Kalshi trade-api WebSocket v2 spec:
  wss://api.elections.kalshi.com/trade-api/ws/v2

Auth: RSA-PSS signed login command (same signing scheme as REST).
Subscriptions: orderbook_delta, ticker, trade, fill

Usage:
    asyncio.run(stream_kalshi_live(["KXFED-27APR-T4.25"], on_event=my_handler))

Docs: https://docs.kalshi.com/websockets/websocket-connection
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger("algochains_mcp.order_flow.kalshi_websocket")

WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"


def _build_ws_auth_headers() -> dict[str, str]:
    """Build RSA-PSS signed headers for WebSocket upgrade request."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    access_key = os.getenv("KALSHI_ACCESS_KEY", "").strip()
    key_path = os.getenv("KALSHI_PRIVATE_KEY_PATH", "").strip()

    if not access_key or not key_path:
        raise RuntimeError("KALSHI_ACCESS_KEY and KALSHI_PRIVATE_KEY_PATH must be set")

    raw = Path(key_path).expanduser().read_bytes()
    private_key = serialization.load_pem_private_key(raw, password=None)

    ts = str(int(time.time() * 1000))
    msg = f"{ts}GET/trade-api/ws/v2".encode("utf-8")
    sig = private_key.sign(
        msg,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    signature = base64.b64encode(sig).decode("ascii")

    return {
        "KALSHI-ACCESS-KEY": access_key,
        "KALSHI-ACCESS-TIMESTAMP": ts,
        "KALSHI-ACCESS-SIGNATURE": signature,
    }


class KalshiWSClient:
    """
    Async WebSocket client for Kalshi live data feeds.

    Handles:
      - Authenticated connection via RSA-PSS signed headers
      - Channel subscriptions (orderbook_delta, ticker, trade, fill)
      - Heartbeat (ping every 10s)
      - Reconnection with exponential backoff
      - Event routing to registered callbacks
    """

    def __init__(
        self,
        tickers: list[str],
        channels: list[str] | None = None,
        on_event: Optional[Callable[[dict[str, Any]], None]] = None,
        on_orderbook: Optional[Callable[[dict[str, Any]], None]] = None,
        on_trade: Optional[Callable[[dict[str, Any]], None]] = None,
        on_fill: Optional[Callable[[dict[str, Any]], None]] = None,
    ):
        self.tickers = tickers
        self.channels = channels or ["orderbook_delta", "ticker", "trade"]
        self.on_event = on_event
        self.on_orderbook = on_orderbook
        self.on_trade = on_trade
        self.on_fill = on_fill
        self._msg_id = 0
        self._orderbooks: dict[str, dict[str, Any]] = {}
        self._running = False

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    def _apply_orderbook_delta(self, ticker: str, delta: dict[str, Any]) -> dict[str, Any]:
        """
        Apply an orderbook_delta message to the local snapshot.
        Kalshi sends: {"type": "orderbook_delta", "msg": {"market_ticker": ..., "yes": [...], "no": [...]}}
        Each side entry: [price_cents, size_delta]  (size_delta can be negative = remove)
        """
        if ticker not in self._orderbooks:
            self._orderbooks[ticker] = {"yes": {}, "no": {}, "seq": 0}
        ob = self._orderbooks[ticker]

        for side in ("yes", "no"):
            for price_cents, size_delta in delta.get(side, []):
                price = float(price_cents)
                existing = ob[side].get(price, 0.0)
                new_size = existing + float(size_delta)
                if new_size <= 0:
                    ob[side].pop(price, None)
                else:
                    ob[side][price] = new_size

        ob["seq"] = delta.get("seq", ob["seq"]) + 1
        ob["updated_at"] = datetime.now(timezone.utc).isoformat()
        return ob

    def get_best_bid_ask(self, ticker: str) -> dict[str, Any]:
        """Return current best bid/ask from local orderbook snapshot."""
        ob = self._orderbooks.get(ticker, {})
        yes_prices = sorted(ob.get("yes", {}).keys(), reverse=True)
        no_prices = sorted(ob.get("no", {}).keys())  # lower NO price = higher implied YES ask

        best_bid = yes_prices[0] / 100 if yes_prices else None
        best_ask = (1.0 - no_prices[0] / 100) if no_prices else None

        return {
            "ticker": ticker,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread": round(best_ask - best_bid, 4) if (best_bid and best_ask) else None,
        }

    async def _handle_message(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        msg_type = msg.get("type", "")
        data = msg.get("msg", msg)

        if msg_type in ("orderbook_snapshot", "orderbook_delta"):
            ticker = data.get("market_ticker", "")
            ob = self._apply_orderbook_delta(ticker, data)
            if self.on_orderbook:
                self.on_orderbook({"ticker": ticker, "orderbook": ob, "type": msg_type})

        elif msg_type == "trade":
            if self.on_trade:
                self.on_trade(data)

        elif msg_type == "fill":
            if self.on_fill:
                self.on_fill(data)

        elif msg_type == "ticker":
            pass  # Price ticker updates — available if needed

        elif msg_type == "subscribed":
            logger.info("Subscribed to Kalshi channels: %s", data)

        elif msg_type == "error":
            logger.error("Kalshi WS error: %s", data)

        if self.on_event:
            self.on_event(msg)

    async def run(self, duration_seconds: Optional[float] = None) -> None:
        """
        Connect and stream until duration_seconds elapses or task is cancelled.
        Automatically reconnects with backoff on disconnect.
        """
        try:
            import websockets
        except ImportError:
            raise RuntimeError("websockets package required: pip install websockets")

        backoff = 1.0
        self._running = True
        start = time.monotonic()

        while self._running:
            if duration_seconds and (time.monotonic() - start) >= duration_seconds:
                break
            try:
                auth_headers = _build_ws_auth_headers()
                async with websockets.connect(
                    WS_URL,
                    additional_headers=auth_headers,
                    ping_interval=10,
                    ping_timeout=20,
                ) as ws:
                    logger.info("Kalshi WS connected")
                    backoff = 1.0

                    # Subscribe to channels for all tickers
                    sub_msg = {
                        "id": self._next_id(),
                        "cmd": "subscribe",
                        "params": {
                            "channels": self.channels,
                            "market_tickers": self.tickers,
                        },
                    }
                    await ws.send(json.dumps(sub_msg))

                    # Also subscribe to fills if callback registered
                    if self.on_fill:
                        fill_msg = {
                            "id": self._next_id(),
                            "cmd": "subscribe",
                            "params": {"channels": ["fill"]},
                        }
                        await ws.send(json.dumps(fill_msg))

                    async for raw in ws:
                        await self._handle_message(raw)
                        if duration_seconds and (time.monotonic() - start) >= duration_seconds:
                            self._running = False
                            break

            except Exception as exc:
                logger.warning("Kalshi WS disconnected: %s — reconnecting in %.1fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    def stop(self) -> None:
        self._running = False


async def stream_kalshi_live(
    tickers: list[str],
    duration_seconds: float = 30.0,
    channels: list[str] | None = None,
    on_event: Optional[Callable] = None,
) -> list[dict[str, Any]]:
    """
    Convenience function: stream Kalshi live data for duration_seconds, collect events.
    Returns list of event dicts (orderbook snapshots + trades).
    Suitable for MCP tool use (bounded duration).
    """
    events: list[dict[str, Any]] = []

    def _collect(msg: dict[str, Any]) -> None:
        events.append(msg)

    client = KalshiWSClient(
        tickers=tickers,
        channels=channels or ["orderbook_delta", "ticker", "trade"],
        on_event=_collect,
    )
    try:
        await asyncio.wait_for(client.run(duration_seconds=duration_seconds), timeout=duration_seconds + 5)
    except asyncio.TimeoutError:
        pass

    return events


def get_live_orderbook_snapshot(ticker: str, duration_seconds: float = 5.0) -> dict[str, Any]:
    """
    Synchronous wrapper: connect to Kalshi WS, collect orderbook updates for
    `duration_seconds`, then return the best bid/ask snapshot.

    Used as an MCP tool that gives a live orderbook in one call.
    """
    client = KalshiWSClient(tickers=[ticker], channels=["orderbook_delta", "orderbook_snapshot"])

    async def _run() -> dict[str, Any]:
        await client.run(duration_seconds=duration_seconds)
        return client.get_best_bid_ask(ticker)

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, _run())
                return future.result(timeout=duration_seconds + 10)
        else:
            return loop.run_until_complete(_run())
    except Exception as exc:
        return {"ticker": ticker, "error": str(exc), "best_bid": None, "best_ask": None}
