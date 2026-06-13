"""
AlgoChains MCP HTTP Transport — Phase 2

Provides a FastAPI-based HTTP/SSE transport layer so remote AI agents
(Claude API, OpenAI Agents SDK, Cursor remote mode, etc.) can connect
to the MCP server over HTTP instead of stdio.

Usage:
    # Start the HTTP server (in addition to or instead of stdio):
    algochains-mcp-http --host 0.0.0.0 --port 8080

    # Or programmatically:
    from algochains_mcp.http_transport import create_http_app
    app = create_http_app()
    uvicorn.run(app, host="0.0.0.0", port=8080)

MCP 2025-11-05 Streamable HTTP spec:
    POST /mcp          — JSON-RPC request (immediate response or SSE stream)
    GET  /mcp          — SSE stream for server-initiated messages
    DELETE /mcp        — Close a session

Authentication:
    Bearer token via Authorization header.
    Token is validated against ALGOCHAINS_HTTP_TRANSPORT_SECRET env var.

CORS:
    Configurable via ALGOCHAINS_HTTP_CORS_ORIGINS env var (comma-separated).
    Defaults to allow all origins in development, restricted in production.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
import uuid
from typing import Any, AsyncGenerator

logger = logging.getLogger("algochains_mcp.http_transport")

# Session store — maps session_id -> asyncio.Queue for server-push messages
_sessions: dict[str, asyncio.Queue] = {}
_session_last_active: dict[str, float] = {}
SESSION_TTL_SECONDS = 3600  # 1 hour idle timeout


def _get_transport_secret() -> str | None:
    return os.environ.get("ALGOCHAINS_HTTP_TRANSPORT_SECRET")


def _get_cors_origins() -> list[str]:
    raw = os.environ.get("ALGOCHAINS_HTTP_CORS_ORIGINS", "*")
    if raw == "*":
        return ["*"]
    return [o.strip() for o in raw.split(",") if o.strip()]


def _verify_bearer_token(authorization: str | None) -> bool:
    """Verify Authorization: Bearer <token> header."""
    secret = _get_transport_secret()
    if not secret:
        return True  # No secret configured — open access (dev mode)
    if not authorization or not authorization.startswith("Bearer "):
        return False
    token = authorization[len("Bearer "):]
    return secrets.compare_digest(token, secret)


# ─── OAuth 2.1 Protected Resource Metadata (RFC 9728) ─────────────────────────
# Foundation only: this server is an OAuth *protected resource*, not an
# authorization server. /authorize, /token, and PKCE are delegated to an
# external IdP (Supabase Auth / WorkOS). We only expose the resource-metadata
# discovery document and the WWW-Authenticate challenge header so MCP clients
# (e.g. Claude.ai) can discover the authorization server. HTTPS is assumed at
# the proxy.

def _mcp_resource() -> str:
    return os.environ.get("ALGOCHAINS_MCP_RESOURCE", "https://mcp.algochains.ai")


def _oauth_issuer() -> str:
    return os.environ.get("ALGOCHAINS_OAUTH_ISSUER", "https://auth.algochains.ai")


def protected_resource_metadata() -> dict:
    """RFC 9728 OAuth 2.0 Protected Resource Metadata document."""
    return {
        "resource": _mcp_resource(),
        "authorization_servers": [_oauth_issuer()],
        "scopes_supported": ["mcp:read", "mcp:tools"],
        "bearer_methods_supported": ["header"],
        "resource_name": "AlgoChains MCP Server",
        "resource_documentation": "https://algochains.ai/docs",
    }


def oauth_challenge_header() -> dict:
    """WWW-Authenticate challenge pointing at the resource-metadata document.

    Attach to 401 responses on the MCP endpoint so clients can discover the
    authorization server per RFC 9728 §5.1.
    """
    resource = _mcp_resource()
    return {
        "WWW-Authenticate": (
            f'Bearer resource_metadata="{resource}/.well-known/oauth-protected-resource"'
        )
    }


async def _cleanup_stale_sessions() -> None:
    """Periodically remove idle sessions."""
    while True:
        await asyncio.sleep(300)  # run every 5 min
        now = time.time()
        stale = [
            sid for sid, last in _session_last_active.items()
            if now - last > SESSION_TTL_SECONDS
        ]
        for sid in stale:
            _sessions.pop(sid, None)
            _session_last_active.pop(sid, None)
            logger.info("Cleaned up stale session %s", sid)


def create_http_app(mcp_server: Any | None = None) -> Any:
    """Create a FastAPI app that wraps the MCP server with HTTP/SSE transport.

    Args:
        mcp_server: Optional pre-built MCP server. If None, imports the
                    algochains_mcp.server.app singleton.

    Returns:
        A FastAPI application instance ready to serve with uvicorn.
    """
    try:
        from fastapi import FastAPI, Request, Response, HTTPException, Depends
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import StreamingResponse, JSONResponse
    except ImportError:
        raise ImportError(
            "FastAPI and uvicorn are required for HTTP transport. "
            "Install with: pip install 'algochains-mcp[http]'"
        )

    if mcp_server is None:
        from algochains_mcp.server import app as mcp_server  # type: ignore

    http_app = FastAPI(
        title="AlgoChains MCP Server",
        description="HTTP/SSE transport for the AlgoChains MCP trading platform",
        version="20.0.0",
        docs_url="/docs",
        redoc_url=None,
    )

    # CORS
    origins = _get_cors_origins()
    http_app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Mcp-Session-Id"],
        expose_headers=["Mcp-Session-Id"],
    )

    # Start background cleanup — store ref to prevent silent GC drop
    _bg_tasks: list[asyncio.Task] = []

    @http_app.on_event("startup")
    async def _on_startup() -> None:
        t = asyncio.create_task(_cleanup_stale_sessions(), name="cleanup_stale_sessions")
        t.add_done_callback(
            lambda task: logger.warning("Cleanup task failed: %s", task.exception())
            if not task.cancelled() and task.exception() else None
        )
        _bg_tasks.append(t)
        logger.info("AlgoChains MCP HTTP transport started")

    def _auth(request: Request) -> None:
        authz = request.headers.get("Authorization")

        # Path 1 — OAuth 2.1 access token (MCP spec 2025-06-18). When an external
        # IdP is configured, validate the JWT (signature/aud/iss/exp/scope) and
        # bind tenant context for the request. Set once, from the token claim —
        # never from caller input (OWASP API1:2023 BOLA).
        try:
            from .auth.oauth_resource import oauth_enabled, validate_oauth_token
            if oauth_enabled():
                if not authz or not authz.startswith("Bearer "):
                    raise HTTPException(
                        status_code=401,
                        detail="Unauthorized",
                        headers=oauth_challenge_header(),
                    )
                principal = validate_oauth_token(authz[len("Bearer "):])
                if principal is None:
                    raise HTTPException(
                        status_code=401,
                        detail="Unauthorized",
                        headers=oauth_challenge_header(),
                    )
                try:
                    from .multi_tenant.isolation import set_tenant
                    set_tenant(principal.tenant_id)
                except Exception:
                    pass
                request.state.oauth_subject = principal.subject
                request.state.tenant_id = principal.tenant_id
                return
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("OAuth validation setup failed closed: %s", exc)
            raise HTTPException(
                status_code=401,
                detail="Unauthorized",
                headers=oauth_challenge_header(),
            )

        # Path 2 — static transport secret (existing behavior / dev mode).
        if not _verify_bearer_token(authz):
            # RFC 9728 §5.1: include the resource_metadata discovery pointer so
            # MCP clients (Claude.ai) can find the authorization server.
            raise HTTPException(
                status_code=401,
                detail="Unauthorized",
                headers=oauth_challenge_header(),
            )

    def _health_payload() -> dict:
        return {
            "status": "ok",
            "server": "algochains-mcp",
            "version": "20.0.0",
            "transport": "http+sse",
            "active_sessions": len(_sessions),
        }

    @http_app.get("/.well-known/oauth-protected-resource")
    async def oauth_protected_resource() -> dict:
        """RFC 9728 discovery document — unauthenticated.

        Lets MCP clients discover which authorization server protects this
        resource. The AS itself (/authorize, /token, PKCE) is an external IdP.
        """
        return protected_resource_metadata()

    @http_app.get("/health")
    async def health() -> dict:
        return _health_payload()

    @http_app.get("/status")
    async def status() -> dict:
        """Legacy watchdog-compatible alias for /health."""
        return _health_payload()

    @http_app.post("/mcp")
    async def handle_post(request: Request, _: None = Depends(_auth)) -> Response:
        """Handle JSON-RPC requests. Returns JSON for simple responses,
        SSE stream for streaming/subscription requests."""
        session_id = request.headers.get("Mcp-Session-Id")
        if not session_id:
            session_id = str(uuid.uuid4())

        # Ensure session queue exists
        if session_id not in _sessions:
            _sessions[session_id] = asyncio.Queue(maxsize=1000)
        _session_last_active[session_id] = time.time()

        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": None},
                status_code=400,
            )

        # Detect if client wants SSE streaming
        accept = request.headers.get("Accept", "")
        wants_sse = "text/event-stream" in accept

        if wants_sse:
            async def _sse_generator() -> AsyncGenerator[bytes, None]:
                try:
                    result = await _dispatch_jsonrpc(mcp_server, body, session_id)
                    data = json.dumps(result, default=str)
                    yield f"data: {data}\n\n".encode()
                    # Flush any queued server-push messages
                    q = _sessions.get(session_id)
                    if q:
                        while not q.empty():
                            msg = await q.get()
                            yield f"data: {json.dumps(msg, default=str)}\n\n".encode()
                except Exception as exc:
                    err = {"jsonrpc": "2.0", "error": {"code": -32603, "message": str(exc)}, "id": body.get("id")}
                    yield f"data: {json.dumps(err)}\n\n".encode()

            return StreamingResponse(
                _sse_generator(),
                media_type="text/event-stream",
                headers={
                    "Mcp-Session-Id": session_id,
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )
        else:
            result = await _dispatch_jsonrpc(mcp_server, body, session_id)
            return JSONResponse(result, headers={"Mcp-Session-Id": session_id})

    @http_app.get("/mcp")
    async def handle_get_sse(request: Request, _: None = Depends(_auth)) -> StreamingResponse:
        """Open a persistent SSE stream for server-initiated messages."""
        session_id = request.headers.get("Mcp-Session-Id")
        if not session_id or session_id not in _sessions:
            session_id = str(uuid.uuid4())
            _sessions[session_id] = asyncio.Queue(maxsize=1000)

        _session_last_active[session_id] = time.time()

        async def _event_stream() -> AsyncGenerator[bytes, None]:
            # Send session established event
            yield f"event: session\ndata: {json.dumps({'session_id': session_id})}\n\n".encode()
            q = _sessions[session_id]
            try:
                while True:
                    try:
                        msg = await asyncio.wait_for(q.get(), timeout=30.0)
                        _session_last_active[session_id] = time.time()
                        yield f"data: {json.dumps(msg, default=str)}\n\n".encode()
                    except asyncio.TimeoutError:
                        # Send keepalive comment
                        yield b": keepalive\n\n"
                    # Check if client disconnected
                    if await request.is_disconnected():
                        break
            finally:
                _sessions.pop(session_id, None)
                _session_last_active.pop(session_id, None)

        return StreamingResponse(
            _event_stream(),
            media_type="text/event-stream",
            headers={
                "Mcp-Session-Id": session_id,
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @http_app.delete("/mcp")
    async def handle_delete(request: Request, _: None = Depends(_auth)) -> JSONResponse:
        """Close a session."""
        session_id = request.headers.get("Mcp-Session-Id")
        if session_id:
            _sessions.pop(session_id, None)
            _session_last_active.pop(session_id, None)
        return JSONResponse({"status": "closed"})

    return http_app


async def _dispatch_jsonrpc(mcp_server: Any, body: dict, session_id: str) -> dict:
    """Route a JSON-RPC 2.0 request to the MCP server's handlers."""
    method = body.get("method", "")
    params = body.get("params", {})
    req_id = body.get("id")

    try:
        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"listChanged": False, "subscribe": False},
                    "prompts": {"listChanged": False},
                    "logging": {},
                },
                "serverInfo": {
                    "name": "algochains-mcp-server",
                    "version": "20.0.0",
                },
                "instructions": mcp_server.instructions if hasattr(mcp_server, "instructions") else "",
            }
        elif method == "tools/list":
            # Delegate to the server's list_tools handler
            cursor = params.get("cursor")
            tools_list = await mcp_server._mcp_server.list_tools()
            result = {
                "tools": [
                    t.model_dump() if hasattr(t, "model_dump") else vars(t)
                    for t in (tools_list.tools if hasattr(tools_list, "tools") else tools_list)
                ]
            }
        elif method == "tools/call":
            from algochains_mcp.server import call_tool
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})
            content = await call_tool(tool_name, tool_args)
            result = {
                "content": [
                    c.model_dump() if hasattr(c, "model_dump") else vars(c)
                    for c in content
                ],
                "isError": False,
            }
        elif method == "resources/list":
            result = {"resources": [], "nextCursor": None}
        elif method == "prompts/list":
            result = {"prompts": [], "nextCursor": None}
        elif method == "ping":
            result = {}
        else:
            return {
                "jsonrpc": "2.0",
                "error": {"code": -32601, "message": f"Method not found: {method}"},
                "id": req_id,
            }

        return {"jsonrpc": "2.0", "result": result, "id": req_id}

    except Exception as exc:
        logger.exception("JSON-RPC dispatch error for method %s", method)
        return {
            "jsonrpc": "2.0",
            "error": {"code": -32603, "message": f"Internal error: {exc}"},
            "id": req_id,
        }


def run_http_server(host: str = "0.0.0.0", port: int = 8080) -> None:
    """Entry point for algochains-mcp-http CLI command."""
    try:
        import uvicorn
    except ImportError:
        raise SystemExit(
            "uvicorn is required for HTTP transport. "
            "Install with: pip install 'algochains-mcp[http]'"
        )

    http_app = create_http_app()
    secret = _get_transport_secret()
    if secret:
        logger.info("HTTP transport: Bearer token authentication ENABLED")
    else:
        logger.warning(
            "HTTP transport: No ALGOCHAINS_HTTP_TRANSPORT_SECRET set — "
            "open access (set this in production!)"
        )
    logger.info("Starting AlgoChains MCP HTTP server on http://%s:%d", host, port)
    logger.info("MCP endpoint: http://%s:%d/mcp", host, port)
    logger.info("Health check: http://%s:%d/health", host, port)
    uvicorn.run(http_app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="AlgoChains MCP HTTP Server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    run_http_server(host=args.host, port=args.port)
