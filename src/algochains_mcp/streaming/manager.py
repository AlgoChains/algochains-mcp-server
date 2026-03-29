"""
WebSocket streaming manager for AlgoChains MCP Server (V4).

Provides real-time streaming of:
  - Live P&L updates across all connected brokers
  - Order fill notifications
  - Position change events
  - Market data ticks (quotes, trades)
  - Risk alerts (drawdown breaches, exposure limits)

Architecture:
  StreamManager owns one asyncio.Queue per subscription topic.
  Broker connectors push events into the queue; MCP resource endpoints
  drain the queue and return the latest snapshot.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger("algochains_mcp.streaming")


class StreamTopic(str, Enum):
    """Topics that clients can subscribe to."""
    PNL = "pnl"                     # Real-time P&L per broker
    FILLS = "fills"                 # Order fill notifications
    POSITIONS = "positions"         # Position change events
    QUOTES = "quotes"              # Live quote ticks
    TRADES = "trades"              # Market trade ticks
    RISK_ALERTS = "risk_alerts"    # Drawdown / exposure alerts
    ORDER_UPDATES = "order_updates" # Order status changes
    HEARTBEAT = "heartbeat"        # Connection health


@dataclass
class StreamEvent:
    """A single streaming event."""
    topic: StreamTopic
    broker: str
    timestamp: float = field(default_factory=time.time)
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "topic": self.topic.value,
            "broker": self.broker,
            "timestamp": self.timestamp,
            "data": self.data,
        }


@dataclass
class Subscription:
    """A client subscription to a stream topic."""
    topic: StreamTopic
    symbols: list[str] = field(default_factory=list)
    brokers: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)


class StreamManager:
    """Manages real-time data streams across all connected brokers.

    Usage:
        manager = StreamManager()
        await manager.start()

        # Push events from broker connectors
        manager.push(StreamEvent(topic=StreamTopic.FILLS, broker="alpaca",
                                 data={"symbol": "AAPL", "side": "buy", "qty": 10, "price": 185.50}))

        # Get latest snapshot for a topic
        events = manager.get_latest(StreamTopic.PNL, limit=10)
    """

    def __init__(self, max_buffer: int = 1000):
        self._queues: dict[StreamTopic, asyncio.Queue] = {}
        self._buffers: dict[StreamTopic, list[StreamEvent]] = {}
        self._callbacks: dict[StreamTopic, list[Callable]] = {}
        self._max_buffer = max_buffer
        self._running = False
        self._pnl_snapshot: dict[str, dict] = {}  # broker -> latest P&L
        self._position_snapshot: dict[str, list] = {}  # broker -> positions

        for topic in StreamTopic:
            self._queues[topic] = asyncio.Queue(maxsize=max_buffer)
            self._buffers[topic] = []
            self._callbacks[topic] = []

    async def start(self) -> None:
        """Start the stream processing loop."""
        self._running = True
        logger.info("StreamManager started")

    async def stop(self) -> None:
        """Stop the stream processing loop."""
        self._running = False
        logger.info("StreamManager stopped")

    def push(self, event: StreamEvent) -> None:
        """Push an event into the stream (non-blocking)."""
        buf = self._buffers.get(event.topic, [])
        buf.append(event)
        if len(buf) > self._max_buffer:
            buf.pop(0)

        # Update snapshots for fast access
        if event.topic == StreamTopic.PNL:
            self._pnl_snapshot[event.broker] = event.data
        elif event.topic == StreamTopic.POSITIONS:
            self._position_snapshot[event.broker] = event.data.get("positions", [])

        # Fire registered callbacks
        for cb in self._callbacks.get(event.topic, []):
            try:
                cb(event)
            except Exception as e:
                logger.warning("Callback error on %s: %s", event.topic, e)

    def get_latest(self, topic: StreamTopic, limit: int = 20) -> list[dict]:
        """Get the latest N events for a topic."""
        buf = self._buffers.get(topic, [])
        return [e.to_dict() for e in buf[-limit:]]

    def get_pnl_snapshot(self) -> dict[str, dict]:
        """Get the latest P&L snapshot across all brokers."""
        return dict(self._pnl_snapshot)

    def get_position_snapshot(self) -> dict[str, list]:
        """Get the latest position snapshot across all brokers."""
        return dict(self._position_snapshot)

    def on(self, topic: StreamTopic, callback: Callable) -> None:
        """Register a callback for a stream topic."""
        self._callbacks[topic].append(callback)

    def subscribe(self, sub: Subscription) -> str:
        """Register a subscription (returns subscription ID)."""
        sub_id = f"sub_{sub.topic.value}_{int(sub.created_at * 1000)}"
        logger.info("New subscription: %s", sub_id)
        return sub_id

    def stats(self) -> dict:
        """Get streaming statistics."""
        return {
            "running": self._running,
            "topics": {
                t.value: {
                    "buffered_events": len(self._buffers.get(t, [])),
                    "callbacks": len(self._callbacks.get(t, [])),
                }
                for t in StreamTopic
            },
            "pnl_brokers": list(self._pnl_snapshot.keys()),
            "position_brokers": list(self._position_snapshot.keys()),
        }
