"""
AlgoChains MCP Server — Streamable HTTP + SSE Transport (V22)

Implements the MCP 2025-03-26 Streamable HTTP transport spec alongside
a market data push layer that eliminates polling.

ARCHITECTURE:
    stdio (current):  Agent ──stdio──→ MCP Server  [one client, no streaming]
    SSE (V22):        Agent ──HTTP──→ SSE Bridge  [multi-client, push streams]

    The SSE bridge exposes:
      POST /mcp          → Standard MCP JSON-RPC tool calls
      GET  /mcp          → SSE channel for server → agent push notifications
      GET  /stream/quotes → Market data price stream
      GET  /stream/fills  → Order fill notifications
      GET  /stream/bots   → Live bot metrics (30s cadence)
      GET  /stream/alerts → Guardrail / circuit breaker events
      GET  /health        → Liveness probe

SECURITY:
    - Origin header whitelist (prevents DNS rebinding per MCP spec)
    - API key required for all stream endpoints
    - Rate limiter: 10 SSE connections max per client IP
    - Sessions expire after 4 hours of inactivity

LATENCY NOTE:
    SSE push eliminates quote polling overhead (~120ms/poll saved).
    Quotes pushed at sub-second cadence vs. agent polling every 5-30s.
    Still NOT suitable for HFT (LLM processing adds 100-2000ms latency).
    Best suited for: swing trading signals, portfolio monitoring, risk alerts.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Dict, List, Optional, Set

import anyio
import uvicorn
from mcp.server.lowlevel import Server
from mcp.server.streamable_http import (
    EventCallback,
    EventId,
    EventMessage,
    EventStore,
    StreamId,
    StreamableHTTPServerTransport,
    TransportSecuritySettings,
)
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

logger = logging.getLogger("algochains_mcp.sse_server")

# ═══════════════════════════════════════════════════════════════════════════
# SECURITY CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

ALLOWED_ORIGINS: list[str] = [
    "https://algochains.ai",
    "https://app.algochains.ai",
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:8080",
    "app://.",         # Claude Desktop
    "vscode-webview://.",  # Cursor / VS Code webview
]

SSE_API_KEY: str = os.environ.get("ALGOCHAINS_SSE_KEY", "")
MAX_SSE_CONNECTIONS_PER_IP: int = 10
SESSION_TTL_SEC: int = 4 * 3600  # 4 hours

SSE_HOST: str = os.environ.get("ALGOCHAINS_SSE_HOST", "127.0.0.1")
SSE_PORT: int = int(os.environ.get("ALGOCHAINS_SSE_PORT", "8765"))


# ═══════════════════════════════════════════════════════════════════════════
# IN-MEMORY EVENT STORE (MCP resumability support)
# ═══════════════════════════════════════════════════════════════════════════

class InMemoryEventStore(EventStore):
    """
    In-memory event store for MCP SSE stream resumability.
    Holds the last 1000 events per stream. Events expire after 1 hour.
    On restart, the store is empty — clients reconnecting must re-subscribe.
    """

    MAX_EVENTS_PER_STREAM: int = 1000
    EVENT_TTL_SEC: int = 3600

    def __init__(self) -> None:
        self._events: Dict[StreamId, list[tuple[float, EventId, EventMessage | None]]] = defaultdict(list)
        self._counter: int = 0
        self._lock = asyncio.Lock()

    async def store_event(self, stream_id: StreamId, message: EventMessage | None) -> EventId:
        async with self._lock:
            self._counter += 1
            event_id = EventId(str(self._counter))
            now = time.monotonic()
            self._events[stream_id].append((now, event_id, message))

            # Trim expired and overflow entries
            cutoff = now - self.EVENT_TTL_SEC
            self._events[stream_id] = [
                (ts, eid, msg)
                for ts, eid, msg in self._events[stream_id]
                if ts >= cutoff
            ][-self.MAX_EVENTS_PER_STREAM:]

            return event_id

    async def replay_events_after(
        self,
        last_event_id: EventId,
        send_callback: EventCallback,
    ) -> StreamId | None:
        async with self._lock:
            for stream_id, events in self._events.items():
                ids = [eid for _, eid, _ in events]
                if last_event_id not in ids:
                    continue
                idx = ids.index(last_event_id)
                for _, eid, msg in events[idx + 1:]:
                    if msg is not None:
                        await send_callback(EventMessage(message=msg.message, event_id=eid))
                return stream_id
        return None


# ═══════════════════════════════════════════════════════════════════════════
# MARKET DATA PUSH STREAMS
# ═══════════════════════════════════════════════════════════════════════════

class PushStreamManager:
    """
    Manages SSE push streams for market data, fills, bot metrics, and alerts.
    Subscribers receive JSON-encoded events as Server-Sent Events.

    Connection lifecycle:
        1. Client opens GET /stream/{channel}
        2. Server adds queue to channel subscriber set
        3. Data sources call broadcast() to fan-out to all queues
        4. Client disconnect triggers queue cleanup
    """

    def __init__(self) -> None:
        self._channels: Dict[str, Set[asyncio.Queue]] = defaultdict(set)
        self._ip_counts: Dict[str, int] = defaultdict(int)
        self._lock = asyncio.Lock()

    async def subscribe(self, channel: str, client_ip: str) -> AsyncGenerator[str, None]:
        """
        Yield SSE-formatted strings for the given channel.
        Handles slow clients by dropping frames (maxsize=50 prevents backpressure).
        """
        async with self._lock:
            if self._ip_counts[client_ip] >= MAX_SSE_CONNECTIONS_PER_IP:
                raise PermissionError(
                    f"Max SSE connections per IP exceeded ({MAX_SSE_CONNECTIONS_PER_IP})"
                )
            queue: asyncio.Queue = asyncio.Queue(maxsize=50)
            self._channels[channel].add(queue)
            self._ip_counts[client_ip] += 1

        try:
            yield f"data: {json.dumps({'type': 'connected', 'channel': channel, 'ts': time.time()})}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    # Heartbeat to keep connection alive through proxies
                    yield ": heartbeat\n\n"
        finally:
            async with self._lock:
                self._channels[channel].discard(queue)
                self._ip_counts[client_ip] = max(0, self._ip_counts[client_ip] - 1)

    async def broadcast(self, channel: str, event: dict) -> None:
        """Fan-out event to all subscribers of a channel. Drops slow clients."""
        async with self._lock:
            subscribers = list(self._channels.get(channel, set()))

        dropped = 0
        for queue in subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                dropped += 1

        if dropped:
            logger.debug("Dropped %d slow SSE clients on channel '%s'", dropped, channel)

    def subscriber_counts(self) -> dict:
        return {ch: len(subs) for ch, subs in self._channels.items() if subs}


_push_manager = PushStreamManager()


def get_push_manager() -> PushStreamManager:
    return _push_manager


# ═══════════════════════════════════════════════════════════════════════════
# BACKGROUND FEED TASKS
# ═══════════════════════════════════════════════════════════════════════════

async def _bot_metrics_feed() -> None:
    """
    Push live bot metrics every 30 seconds to the 'bots' channel.
    Reads from real log files — no mock data.
    Falls back gracefully if log files are missing.
    Initial 5s delay lets server startup complete before first disk read.
    """
    await asyncio.sleep(5)  # let startup finish before first blocking read
    while True:
        try:
            from algochains_mcp.live_bot_intelligence.metrics_parser import parse_all_bots
            loop = asyncio.get_event_loop()
            metrics = await loop.run_in_executor(None, parse_all_bots)
            await _push_manager.broadcast("bots", {
                "type": "bot_metrics",
                "ts": time.time(),
                "bots": metrics,
            })
        except ImportError:
            logger.debug("live_bot_intelligence not available for SSE feed")
        except Exception as exc:
            logger.warning("Bot metrics SSE feed error: %s", exc)
        await asyncio.sleep(30)


async def _guardrail_alerts_feed() -> None:
    """
    Push circuit breaker state changes to the 'alerts' channel.
    Polls the guardrail singleton every 5 seconds for state changes.
    Initial 3s delay lets server startup complete before first lock acquisition.
    """
    await asyncio.sleep(3)  # let startup finish before first guardrail load
    last_status: dict = {}
    while True:
        try:
            from algochains_mcp.trading_guardrails import get_guardrails
            guardrails = get_guardrails()
            status = guardrails.get_status()
            cbs = status.get("broker_circuit_breakers", {})

            for broker, cb_state in cbs.items():
                prev = last_status.get(broker, {})
                if cb_state != prev:
                    await _push_manager.broadcast("alerts", {
                        "type": "circuit_breaker_change",
                        "ts": time.time(),
                        "broker": broker,
                        "state": cb_state.get("state"),
                        "reason": cb_state.get("trip_reason"),
                        "message": cb_state.get("trip_message"),
                    })
                    last_status[broker] = dict(cb_state)

        except Exception as exc:
            logger.warning("Guardrail alert feed error: %s", exc)

        await asyncio.sleep(5)


# ═══════════════════════════════════════════════════════════════════════════
# HTTP REQUEST HANDLERS
# ═══════════════════════════════════════════════════════════════════════════

def _validate_origin(request: Request) -> bool:
    """Validate Origin header per MCP 2025-03-26 spec (DNS rebinding prevention)."""
    origin = request.headers.get("origin", "")
    if not origin:
        # Non-browser clients (curl, Python) may omit Origin — allow
        return True
    return any(origin.startswith(allowed) for allowed in ALLOWED_ORIGINS)


def _validate_api_key(request: Request) -> bool:
    if not SSE_API_KEY:
        return True  # No key configured = open (dev mode)
    provided = (
        request.headers.get("x-api-key")
        or request.query_params.get("api_key")
        or ""
    )
    return provided == SSE_API_KEY


async def _stream_handler(request: Request, channel: str) -> Response:
    """SSE endpoint handler for push data channels."""
    if not _validate_origin(request):
        return JSONResponse({"error": "Origin not allowed"}, status_code=403)
    if not _validate_api_key(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    client_ip = request.client.host if request.client else "unknown"

    from sse_starlette.sse import EventSourceResponse

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            async for chunk in _push_manager.subscribe(channel, client_ip):
                yield chunk
        except PermissionError as exc:
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"

    return EventSourceResponse(event_generator())


async def quotes_stream(request: Request) -> Response:
    """GET /stream/quotes — real-time price feed."""
    return await _stream_handler(request, "quotes")


async def fills_stream(request: Request) -> Response:
    """GET /stream/fills — order fill notifications."""
    return await _stream_handler(request, "fills")


async def bots_stream(request: Request) -> Response:
    """GET /stream/bots — live bot metrics (30s cadence)."""
    return await _stream_handler(request, "bots")


async def alerts_stream(request: Request) -> Response:
    """GET /stream/alerts — guardrail / circuit breaker events."""
    return await _stream_handler(request, "alerts")


async def health_endpoint(request: Request) -> Response:
    """GET /health — liveness probe."""
    streams = _push_manager.subscriber_counts()
    return JSONResponse({
        "status": "ok",
        "transport": "streamable-http-sse",
        "mcp_spec_version": "2025-03-26",
        "sse_streams": streams,
        "ts": time.time(),
    })


# ═══════════════════════════════════════════════════════════════════════════
# MCP STREAMABLE HTTP ENDPOINT
# ═══════════════════════════════════════════════════════════════════════════

_sessions: Dict[str, StreamableHTTPServerTransport] = {}
_session_timestamps: Dict[str, float] = {}
_session_tasks: Dict[str, "asyncio.Task[None]"] = {}  # prevent GC of session tasks
_event_store = InMemoryEventStore()
_mcp_server: Optional[Server] = None


def set_mcp_server(server: Server) -> None:
    """Register the MCP Server instance to handle tool calls over HTTP."""
    global _mcp_server
    _mcp_server = server


async def mcp_endpoint(request: Request) -> Response:
    """
    Unified MCP endpoint — handles both POST (tool calls) and GET (SSE stream).
    Implements MCP 2025-03-26 Streamable HTTP transport spec.
    """
    if not _validate_origin(request):
        return Response("Origin not allowed", status_code=403)
    if not _validate_api_key(request):
        return Response("Unauthorized", status_code=401)

    if _mcp_server is None:
        return JSONResponse({"error": "MCP server not initialized"}, status_code=503)

    session_id = request.headers.get("mcp-session-id")

    if request.method == "POST":
        if session_id and session_id in _sessions:
            transport = _sessions[session_id]
        else:
            # New session
            new_session_id = str(uuid.uuid4())
            security = TransportSecuritySettings(
                allow_credentials=False,
            )
            transport = StreamableHTTPServerTransport(
                mcp_session_id=new_session_id,
                is_json_response_enabled=False,
                event_store=_event_store,
                security_settings=security,
            )
            _sessions[new_session_id] = transport
            _session_timestamps[new_session_id] = time.monotonic()

            # Launch the MCP server on this transport.
            # Store the task so it isn't silently garbage-collected.
            async def _run_session(_sid: str = new_session_id, _t: StreamableHTTPServerTransport = transport) -> None:
                try:
                    async with _mcp_server.run_mcp_async(_t):
                        pass
                except Exception as _exc:
                    logger.warning("MCP session %s ended with error: %s", _sid, _exc)
                finally:
                    _sessions.pop(_sid, None)
                    _session_timestamps.pop(_sid, None)
                    _session_tasks.pop(_sid, None)

            task = asyncio.create_task(_run_session(), name=f"mcp-session-{new_session_id[:8]}")
            _session_tasks[new_session_id] = task

        _session_timestamps[session_id or list(_sessions.keys())[-1]] = time.monotonic()
        return await transport.handle_request(request.scope, request.receive, request._send)

    if request.method == "GET":
        if not session_id or session_id not in _sessions:
            return Response("Session not found", status_code=404)
        transport = _sessions[session_id]
        _session_timestamps[session_id] = time.monotonic()
        return await transport.handle_request(request.scope, request.receive, request._send)

    if request.method == "DELETE":
        if session_id and session_id in _sessions:
            transport = _sessions.pop(session_id, None)
            _session_timestamps.pop(session_id, None)
            if transport:
                await transport.terminate()
        return Response(status_code=204)

    return Response("Method not allowed", status_code=405)


async def _session_reaper() -> None:
    """Periodic cleanup of expired sessions (every 10 min)."""
    while True:
        await asyncio.sleep(600)
        now = time.monotonic()
        expired = [
            sid for sid, ts in _session_timestamps.items()
            if now - ts > SESSION_TTL_SEC
        ]
        for sid in expired:
            transport = _sessions.pop(sid, None)
            _session_timestamps.pop(sid, None)
            task = _session_tasks.pop(sid, None)
            if transport:
                try:
                    await transport.terminate()
                except Exception:
                    pass
            if task and not task.done():
                task.cancel()
        if expired:
            logger.info("Reaped %d expired MCP SSE sessions", len(expired))


# ═══════════════════════════════════════════════════════════════════════════
# STARLETTE APP FACTORY
# ═══════════════════════════════════════════════════════════════════════════

class _SSELifespan:
    """
    Class-based async context manager for Starlette 1.0 lifespan.
    Avoids Python 3.14 × asynccontextmanager incompatibility.
    """

    def __init__(self, app: Any) -> None:
        self._app = app
        self._tasks: list[asyncio.Task] = []

    async def __aenter__(self) -> None:
        self._tasks = [
            asyncio.create_task(_bot_metrics_feed(), name="bot_metrics_feed"),
            asyncio.create_task(_guardrail_alerts_feed(), name="guardrail_alerts_feed"),
            asyncio.create_task(_session_reaper(), name="session_reaper"),
        ]
        logger.info(
            "AlgoChains SSE bridge listening on %s:%d (MCP 2025-03-26 transport)",
            SSE_HOST, SSE_PORT,
        )

    async def __aexit__(self, *_: Any) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)


def build_app(mcp_server: Optional[Server] = None) -> Starlette:
    """
    Build the Starlette ASGI application for the SSE bridge.

    Args:
        mcp_server: Optional MCP Server instance. If provided, /mcp endpoint
                    handles full MCP JSON-RPC tool calls. If None, only push
                    stream endpoints are active.
    """
    if mcp_server is not None:
        set_mcp_server(mcp_server)

    routes = [
        Route("/mcp", endpoint=mcp_endpoint, methods=["GET", "POST", "DELETE"]),
        Route("/stream/quotes", endpoint=quotes_stream),
        Route("/stream/fills", endpoint=fills_stream),
        Route("/stream/bots", endpoint=bots_stream),
        Route("/stream/alerts", endpoint=alerts_stream),
        Route("/health", endpoint=health_endpoint),
    ]

    middleware = [
        Middleware(
            CORSMiddleware,
            allow_origins=ALLOWED_ORIGINS,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type", "Authorization", "X-Api-Key",
                           "Mcp-Session-Id", "Last-Event-Id"],
            expose_headers=["Mcp-Session-Id"],
            allow_credentials=False,
        )
    ]

    return Starlette(
        routes=routes,
        middleware=middleware,
        lifespan=_SSELifespan,
    )


# ═══════════════════════════════════════════════════════════════════════════
# STANDALONE RUNNER
# ═══════════════════════════════════════════════════════════════════════════

def run_sse_server(mcp_server: Optional[Server] = None) -> None:
    """
    Launch the SSE bridge as a standalone process.
    Typically invoked alongside the stdio MCP server for multi-mode operation.

    Example:
        python -m algochains_mcp.sse_server

    Environment variables:
        ALGOCHAINS_SSE_HOST  Default: 127.0.0.1
        ALGOCHAINS_SSE_PORT  Default: 8765
        ALGOCHAINS_SSE_KEY   API key for auth (empty = no auth in dev)
    """
    app = build_app(mcp_server)
    uvicorn.run(
        app,
        host=SSE_HOST,
        port=SSE_PORT,
        log_level="info",
        access_log=False,  # Reduce noise in production
    )


if __name__ == "__main__":
    run_sse_server()
