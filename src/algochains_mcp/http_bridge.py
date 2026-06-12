"""
http_bridge.py — AlgoChains MCP HTTP Bridge
=============================================
Exposes the AlgoChains MCP Server as a simple HTTP API for algochains.ai
and for the Command Center on port 3333.

Three auth modes are supported on every protected endpoint:
  1. No key configured (BRIDGE_API_KEY unset) → public tools only.
  2. Owner key (BRIDGE_API_KEY) + matching `user_email == OWNER_EMAIL`
     → full owner tool set.
  3. Subscriber key (`sub_live_…`) → resolved against
     `subscriber_api_keys` in Supabase. The bridge then exposes only the
     SUBSCRIBER_TOOLS surface, scoped to that subscriber's data.

Run standalone with:
    uvicorn algochains_mcp.http_bridge:app
or via the convenience entry point at the bottom of this file.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path as _PathGlobal
from typing import Any

# FastAPI imports at module level so inner functions can resolve Request type
try:
    from fastapi import FastAPI, HTTPException, Header, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import StreamingResponse
    _FASTAPI_AVAILABLE = True
except ImportError:
    _FASTAPI_AVAILABLE = False

from .subscriber_auth import (
    ResolvedSubscriber,
    is_subscriber_key,
    resolve_subscriber_key,
)
from .subscriber_tools import (
    SUBSCRIBER_TOOL_SCOPES,
    SUBSCRIBER_TOOLS,
    call_subscriber_tool,
)
from .developer_auth import (
    ResolvedDeveloper,
    is_developer_key,
    resolve_developer_key,
)
from .developer_tools import (
    DEVELOPER_TOOLS,
    DEVELOPER_TOOL_SCOPES,
    check_developer_tool_access,
)
from .tool_policy import (
    evaluate_bridge_tool,
    visible_tools_for_bridge,
)
from .otel_tracing import redacted_argument_hash, trace_span

log = logging.getLogger(__name__)

# Single source of truth: read version from pyproject.toml at startup.
# Prevents version drift between FastAPI /docs and package metadata
# (hidden-killers v8 Phase J fix — previously hardcoded "22.0.0").
def _read_project_version() -> str:
    # Prefer pyproject.toml (source of truth when running from dev checkout).
    # Fall back to importlib.metadata (installed wheel), then hardcoded constant.
    try:
        import tomllib  # Python 3.11+
        _toml_path = __file__
        for _ in range(6):
            _toml_path = os.path.dirname(_toml_path)
            _candidate = os.path.join(_toml_path, "pyproject.toml")
            if os.path.exists(_candidate):
                with open(_candidate, "rb") as _f:
                    _data = tomllib.load(_f)
                return _data["project"]["version"]
    except Exception:
        pass
    try:
        from importlib.metadata import version as _pkg_version
        return _pkg_version("algochains-mcp-server")
    except Exception:
        pass
    return "22.5.0"  # fallback (only used when pyproject + installed metadata are both unreadable)

_SERVER_VERSION = _read_project_version()

# ─── Tool whitelist (what the site is allowed to call) ───────────────────────

PUBLIC_TOOLS = {
    "get_marketplace_listings",
    "browse_strategy_marketplace",
    "detect_market_regime",
    "get_macro_signals",
    "onyx_search",
    "onyx_ask",
    "discover_tools",
    "get_strategy_academic_citations",
    "get_bot_card_data",
    "list_bot_research_attachments",
    "get_vix_term_structure",
    "get_earnings_catalyst",
    "get_latency_profile",
}

OWNER_TOOLS = {
    "get_live_bot_metrics",
    "get_all_bot_metrics",
    "get_bot_health",
    "get_quant_regime_state",
    "get_system_heartbeat",
    "get_account",
    "get_positions",
    "get_orders",
    "place_order",
    "cancel_order",
    "close_position",
    "portfolio_summary",
    "run_marketplace_autopilot",
    "get_onyx_status",
    "run_onyx_ingest",
    "get_protection_config",
    "submit_to_marketplace",
    "get_circuit_breaker_status",
    "get_onboarding_status",
    # Numerai tournament tools — owner-only (tournament credentials required).
    # Read tools: anyone with owner key. Upload: gated by NUMERAI_ALLOW_LIVE=1 in handler.
    "numerai_status",
    "numerai_round_info",
    "numerai_download_dataset",
    "numerai_train_baseline",
    "numerai_validate_metrics",
    "numerai_dry_run_submit",
    "numerai_get_model_scores",
    # HK-17: upload is TIER_ORDER_EXEC — restricted tier. Confirm gate in handler.
    "numerai_upload_predictions",
}


async def handle_mcp_request(
    tool_name: str,
    arguments: dict,
    *,
    is_owner: bool = False,
    subscriber: ResolvedSubscriber | None = None,
    developer: ResolvedDeveloper | None = None,
    caller_scope: str | None = None,
) -> dict:
    """
    Route an MCP tool call from the HTTP bridge.

    Resolution order:
      1. Subscriber-scoped tools (when `subscriber` is provided).
      2. Developer-scoped tools (when `developer` is provided).
      3. Owner tools (require `is_owner`).
      4. Public tools.

    Returns a dict that will be serialised to JSON.
    """
    # ── 1. Subscriber surface ─────────────────────────────────────────────
    if subscriber is not None:
        if tool_name not in SUBSCRIBER_TOOLS:
            return {
                "error": "Tool not available to subscribers",
                "tool": tool_name,
                "available_tools": sorted(SUBSCRIBER_TOOLS),
            }
        required_scope = SUBSCRIBER_TOOL_SCOPES.get(tool_name)
        if required_scope and required_scope not in subscriber.scopes:
            return {
                "error": "Missing scope on this API key",
                "tool": tool_name,
                "required_scope": required_scope,
            }
        return call_subscriber_tool(tool_name, subscriber.subscriber_id, arguments)

    # ── 2. Developer surface ──────────────────────────────────────────────
    if developer is not None:
        allowed, denial_reason = check_developer_tool_access(tool_name, developer.scopes)
        if not allowed:
            return {
                "error": "Tool not available for developer tier",
                "tool": tool_name,
                "reason": denial_reason,
                "available_tools": sorted(DEVELOPER_TOOLS),
            }
        # Developer keys execute via the standard server.call_tool path — the
        # tool allowlist already guarantees tier 0/1 only. Tracing is preserved.
        try:
            from algochains_mcp import server as _server
            with trace_span(
                "mcp.tool.call",
                {
                    "tool.name": tool_name,
                    "mcp.server": "algochains",
                    "mcp.transport": "http_bridge",
                    "algochains.auth_type": "developer",
                    "algochains.developer_env": developer.env,
                    "algochains.arguments_hash": redacted_argument_hash(arguments),
                },
            ) as span:
                result = await _server.call_tool(tool_name, arguments)
                if span is not None:
                    span.set_attribute("algochains.tool.success", True)
            if result and hasattr(result[0], "text"):
                try:
                    return json.loads(result[0].text)
                except json.JSONDecodeError:
                    return {"result": result[0].text}
            return {"result": str(result)}
        except Exception as e:
            log.error("MCP bridge (developer) tool call failed: %s — %s", tool_name, e)
            return {"error": str(e), "tool": tool_name}

    # ── 3/4. Owner / public surfaces ─────────────────────────────────────
    # Centralized policy keeps bridge auth, caller scopes, danger tiers, and
    # approval vocabulary in sync with stdio dynamic dispatch.
    decision = evaluate_bridge_tool(
        tool_name,
        arguments,
        is_owner=is_owner,
        caller_scope=caller_scope,
        public_tools=PUBLIC_TOOLS,
        owner_tools=OWNER_TOOLS,
    )
    if not decision.allow:
        payload = decision.as_error()
        if tool_name not in PUBLIC_TOOLS and tool_name not in OWNER_TOOLS:
            payload["available_tools"] = sorted(PUBLIC_TOOLS)
        if decision.required_scope:
            payload["caller_scope"] = caller_scope
        return payload

    try:
        from algochains_mcp import server as _server

        with trace_span(
            "mcp.tool.call",
            {
                "tool.name": tool_name,
                "mcp.server": "algochains",
                "mcp.transport": "http_bridge",
                "algochains.danger_tier": decision.danger_tier,
                "algochains.danger_label": decision.danger_label,
                "algochains.tier_source": decision.tier_source,
                "algochains.arguments_hash": redacted_argument_hash(arguments),
                "algochains.caller_scope": caller_scope or "legacy_owner",
            },
        ) as span:
            result = await _server.call_tool(tool_name, arguments)
            if span is not None:
                span.set_attribute("algochains.tool.success", True)

        if result and hasattr(result[0], "text"):
            try:
                return json.loads(result[0].text)
            except json.JSONDecodeError:
                return {"result": result[0].text}
        return {"result": str(result)}

    except Exception as e:
        log.error(f"MCP bridge tool call failed: {tool_name} — {e}")
        return {"error": str(e), "tool": tool_name}


def create_fastapi_app():
    """Create FastAPI app for standalone HTTP bridge. Install: pip install fastapi uvicorn"""
    try:
        from pydantic import BaseModel
    except ImportError:
        raise ImportError("Install fastapi and uvicorn: pip install fastapi uvicorn")
    if not _FASTAPI_AVAILABLE:
        raise ImportError("Install fastapi and uvicorn: pip install fastapi uvicorn")

    app_http = FastAPI(
        title="AlgoChains MCP HTTP Bridge",
        description="REST bridge to AlgoChains MCP Server for algochains.ai",
        version=_SERVER_VERSION,
    )

    app_http.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "https://algochains.ai",
            "https://www.algochains.ai",
            "https://cc.algochains.io",
            "http://localhost:3000",
            "http://localhost:3333",
            "http://localhost:5173",
        ],
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    # Request-ID + structured access logging middleware (Phase J observability)
    # Adds X-Request-Id response header and emits one structured log line per request.
    try:
        from starlette.middleware.base import BaseHTTPMiddleware

        class _RequestIdMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next):
                req_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())[:8]
                t0 = time.monotonic()
                response = await call_next(request)
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                response.headers["X-Request-Id"] = req_id
                log.info(
                    "bridge_request",
                    extra={
                        "req_id": req_id,
                        "method": request.method,
                        "path": request.url.path,
                        "status": response.status_code,
                        "elapsed_ms": elapsed_ms,
                    },
                )
                return response

        app_http.add_middleware(_RequestIdMiddleware)
    except Exception as _mw_err:
        log.warning("Request-ID middleware unavailable: %s", _mw_err)

    BRIDGE_API_KEY = os.getenv("ALGOCHAINS_BRIDGE_API_KEY", "")
    OWNER_EMAIL = os.getenv("OWNER_EMAIL", "owner@algochains.ai")
    # K-8 fix: dev-mode escape hatch — set ALGOCHAINS_BRIDGE_DEV_MODE=true to
    # allow unauthenticated public-tool access on localhost during development.
    # In production (default) an empty key means the bridge refuses all requests.
    _DEV_MODE = os.getenv("ALGOCHAINS_BRIDGE_DEV_MODE", "").lower() in ("1", "true", "yes")

    if not BRIDGE_API_KEY:
        if _DEV_MODE:
            log.warning(
                "⚠️  BRIDGE_API_KEY not set — running in dev mode (public tools only, "
                "no owner access). Set ALGOCHAINS_BRIDGE_DEV_MODE=false in production."
            )
        else:
            log.error(
                "FATAL: ALGOCHAINS_BRIDGE_API_KEY is not set and "
                "ALGOCHAINS_BRIDGE_DEV_MODE is not enabled. "
                "The HTTP bridge will reject all requests until the key is configured."
            )

    def _resolve_auth(
        x_api_key: str | None,
        authorization: str | None,
        user_email: str | None = None,
        caller_scope: str | None = None,
    ) -> tuple[bool, bool, ResolvedSubscriber | None, ResolvedDeveloper | None, str | None]:
        """
        Returns (key_valid, is_owner, subscriber, developer, caller_scope).

        Resolution order (first match wins):
          1. Subscriber key (sub_live_… / sub_test_…) → subscriber-scoped surface
          2. Developer key (ac_live_… / ac_test_…)   → developer-scoped surface
          3. Owner key (BRIDGE_API_KEY match)         → full owner surface
          4. No key + dev mode                        → public tools only
          5. No key + production                      → 401

        NOTE: is_owner is derived exclusively from the API key match, NOT from
        the user_email body field. user_email is audit-only attribution.
        """
        provided_key = x_api_key or (authorization.replace("Bearer ", "") if authorization else "")

        # 1. Subscriber path — resolves before owner check so a sub key is never
        #    treated as an invalid owner key.
        if is_subscriber_key(provided_key):
            sub = resolve_subscriber_key(provided_key)
            if sub:
                return True, False, sub, None, caller_scope
            return False, False, None, None, caller_scope

        # 2. Developer path — ac_live_* / ac_test_* keys.
        if is_developer_key(provided_key):
            dev = resolve_developer_key(provided_key)
            if dev:
                return True, False, None, dev, caller_scope
            # Key looks like a developer key but didn't resolve → fail closed.
            return False, False, None, None, caller_scope

        # 3. Owner path.
        if BRIDGE_API_KEY:
            key_valid = provided_key == BRIDGE_API_KEY
            is_owner = key_valid
            return key_valid, is_owner, None, None, caller_scope

        # 4. No bridge key configured.
        if _DEV_MODE:
            return True, False, None, None, caller_scope

        # 5. Production with no key → fail closed.
        return False, False, None, None, caller_scope

    class McpRequest(BaseModel):
        tool: str
        arguments: dict = {}
        user_email: str | None = None

    @app_http.get("/health")
    async def health():
        """
        Bridge health — includes version, auth mode, and server import check.
        Phase J observability: richer /health for incident triage.
        """
        auth_mode = "owner" if BRIDGE_API_KEY else ("dev_open" if _DEV_MODE else "no_key_locked")
        server_ok = True
        try:
            from . import server as _srv
            tool_count = len(getattr(_srv, "TOOLS", []))
        except Exception as _srv_err:
            server_ok = False
            tool_count = -1
        return {
            "status": "ok",
            "server": f"AlgoChains MCP Bridge v{_SERVER_VERSION}",
            "version": _SERVER_VERSION,
            "auth_mode": auth_mode,
            "server_import_ok": server_ok,
            "tool_count": tool_count,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @app_http.get("/tools")
    async def list_available_tools(
        x_api_key: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        include_danger_tiers: bool = True,
        x_algochains_caller_scope: str | None = Header(default=None),
    ):
        """
        List available tools with danger tier classification.

        danger_tier:
          0 READ_ONLY    — No side effects. Safe for any user.
          1 WRITE_LOCAL  — Internal writes only. No money, no broker.
          2 ORDER_EXEC   — Executes real broker orders.
          3 DESTRUCTIVE  — Irreversible bulk/high-impact actions.
        """
        from algochains_mcp.tool_danger_tiers import get_scope_max_tier, get_tool_danger_info

        key_valid, is_owner, subscriber, developer, caller_scope = _resolve_auth(
            x_api_key,
            authorization,
            caller_scope=x_algochains_caller_scope,
        )

        tier_legend = {
            "0": "READ_ONLY — no side effects, safe for any agent",
            "1": "WRITE_LOCAL — internal state writes only, no broker",
            "2": "ORDER_EXEC — executes real broker orders",
            "3": "DESTRUCTIVE — irreversible bulk actions",
        }

        # Subscribers see ONLY the subscriber tool surface.
        if subscriber is not None:
            visible_tools = sorted(SUBSCRIBER_TOOLS)
            if include_danger_tiers:
                return {
                    "tools": [get_tool_danger_info(t) for t in visible_tools],
                    "subscriber_tool_count": len(SUBSCRIBER_TOOLS),
                    "scopes": list(subscriber.scopes),
                    "tier_legend": tier_legend,
                }
            return {"subscriber_tools": visible_tools, "scopes": list(subscriber.scopes)}

        # Developers see ONLY the developer-tier tool surface.
        if developer is not None:
            visible_tools = sorted(DEVELOPER_TOOLS)
            if include_danger_tiers:
                return {
                    "tools": [get_tool_danger_info(t) for t in visible_tools],
                    "developer_tool_count": len(DEVELOPER_TOOLS),
                    "scopes": list(developer.scopes),
                    "env": developer.env,
                    "tier_legend": tier_legend,
                }
            return {"developer_tools": visible_tools, "scopes": list(developer.scopes), "env": developer.env}

        # Owner / public surfaces.
        visible_tools = visible_tools_for_bridge(
            public_tools=PUBLIC_TOOLS,
            owner_tools=OWNER_TOOLS,
            is_owner=is_owner,
            caller_scope=caller_scope,
        )
        if include_danger_tiers:
            tools_with_tiers = [get_tool_danger_info(t) for t in sorted(set(visible_tools))]
            if is_owner:
                return {
                    "tools": tools_with_tiers,
                    "public_tool_count": len(PUBLIC_TOOLS),
                    "owner_tool_count": len(OWNER_TOOLS),
                    "caller_scope": caller_scope,
                    "caller_scope_max_tier": get_scope_max_tier(caller_scope),
                    "tier_legend": tier_legend,
                }
            return {
                "tools": tools_with_tiers,
                "public_tool_count": len(PUBLIC_TOOLS),
                "tier_legend": tier_legend,
            }

        if is_owner:
            return {"public_tools": sorted(PUBLIC_TOOLS), "owner_tools": sorted(OWNER_TOOLS)}
        return {"public_tools": sorted(PUBLIC_TOOLS)}

    @app_http.post("/api/mcp")
    async def mcp_endpoint(
        request: Request,
        x_api_key: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        x_algochains_caller_scope: str | None = Header(default=None),
    ):
        from .developer_rate_limiter import (
            MAX_BODY_BYTES,
            check_rate_limit,
        )

        # ── Body size guard (H-F7: prevent flooding with large payloads) ──
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > MAX_BODY_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Request body exceeds {MAX_BODY_BYTES // 1024} KB limit",
            )

        try:
            raw_body = await request.body()
        except Exception:
            raise HTTPException(status_code=400, detail="Could not read request body")

        if len(raw_body) > MAX_BODY_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Request body exceeds {MAX_BODY_BYTES // 1024} KB limit",
            )

        try:
            import json as _json
            body = _json.loads(raw_body)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")

        tool = body.get("tool")
        arguments = body.get("arguments", {})
        user_email = body.get("user_email")
        if not tool:
            raise HTTPException(status_code=400, detail="Missing 'tool' field")

        provided_key = x_api_key or (
            (authorization or "").replace("Bearer ", "") or None
        )
        key_valid, is_owner, subscriber, developer, caller_scope = _resolve_auth(
            x_api_key,
            authorization,
            user_email,
            x_algochains_caller_scope,
        )
        if not key_valid:
            raise HTTPException(status_code=401, detail="Invalid API key")

        # ── Per-key rate limiting (developer and subscriber keys) ──────────
        if developer is not None and provided_key:
            from .developer_auth import hash_developer_key
            rate_result = check_rate_limit(hash_developer_key(provided_key))
            if not rate_result.allowed:
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    status_code=429,
                    content=rate_result.as_error_dict(),
                    headers={
                        "Retry-After": str(max(1, rate_result.retry_after_ms // 1000)),
                        "X-RateLimit-Remaining-RPM": str(rate_result.remaining_rpm),
                        "X-RateLimit-Remaining-RPH": str(rate_result.remaining_rph),
                    },
                )

        result = await handle_mcp_request(
            tool,
            arguments,
            is_owner=is_owner,
            subscriber=subscriber,
            developer=developer,
            caller_scope=caller_scope,
        )
        return result

    # ── SSE: copy-trade signal stream for subscriber daemons ────────────
    @app_http.get("/api/signals/stream")
    async def signals_stream(
        request: Request,
        x_api_key: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        bots: str | None = None,
        poll_interval: float = 1.5,
    ):
        """
        Server-Sent Events stream of fresh copy_trade_signals scoped to the
        caller's subscriber assignments.

        Daemons should prefer this over polling get_signal_stream because:
          - new entries are pushed within `poll_interval` seconds
          - the connection auto-prunes already-delivered signal IDs
          - on disconnect the daemon will simply reconnect and re-sync via
            get_signal_stream

        Auth: subscriber API key only.
        """
        key_valid, _is_owner, subscriber, _developer, _caller_scope = _resolve_auth(x_api_key, authorization)
        if not key_valid or subscriber is None:
            raise HTTPException(status_code=401, detail="Subscriber API key required")
        bot_filter = [b.strip().upper() for b in bots.split(",")] if bots else None
        interval = max(0.5, min(float(poll_interval), 10.0))

        async def event_gen():
            seen: set[str] = set()
            yield f"event: ready\ndata: {json.dumps({'subscriber_id': subscriber.subscriber_id})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                payload = await asyncio.to_thread(
                    call_subscriber_tool,
                    "get_signal_stream",
                    subscriber.subscriber_id,
                    {"bots": bot_filter, "limit": 50},
                )
                signals = payload.get("signals") or []
                fresh = [s for s in signals if s.get("id") and s["id"] not in seen]
                for sig in fresh:
                    seen.add(sig["id"])
                    yield f"event: signal\ndata: {json.dumps(sig, default=str)}\n\n"
                # Bound the seen set to avoid unbounded memory on long sessions
                if len(seen) > 4096:
                    seen = set(list(seen)[-2048:])
                yield f"event: heartbeat\ndata: {datetime.now(timezone.utc).isoformat()}\n\n"
                await asyncio.sleep(interval)

        return StreamingResponse(event_gen(), media_type="text/event-stream")

    # SECURITY FIX (V22 audit): All GET convenience endpoints now require API key.
    # Previously these called handle_mcp_request(is_owner=True) with no auth check —
    # any unauthenticated client could scrape live bot metrics and heartbeat status.

    @app_http.get("/api/bots")
    async def get_all_bots(
        x_api_key: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        user_email: str | None = None,
        x_algochains_caller_scope: str | None = Header(default=None),
    ):
        """Convenience endpoint: get all 4 bot metrics. Owner key required."""
        key_valid, is_owner, subscriber, developer, caller_scope = _resolve_auth(
            x_api_key,
            authorization,
            user_email,
            x_algochains_caller_scope,
        )
        if not key_valid or subscriber is not None:
            raise HTTPException(status_code=401, detail="Owner API key required")
        return await handle_mcp_request("get_all_bot_metrics", {}, is_owner=is_owner, caller_scope=caller_scope)

    @app_http.get("/api/bots/{bot_id}")
    async def get_bot(
        bot_id: str,
        x_api_key: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        user_email: str | None = None,
        x_algochains_caller_scope: str | None = Header(default=None),
    ):
        """Convenience endpoint: get metrics for a specific bot. Owner key required."""
        if bot_id not in {"mnq", "cl", "mes", "nq"}:
            raise HTTPException(status_code=400, detail="bot_id must be mnq | cl | mes | nq")
        key_valid, is_owner, subscriber, developer, caller_scope = _resolve_auth(
            x_api_key,
            authorization,
            user_email,
            x_algochains_caller_scope,
        )
        if not key_valid or subscriber is not None:
            raise HTTPException(status_code=401, detail="Owner API key required")
        return await handle_mcp_request(
            "get_live_bot_metrics",
            {"bot_id": bot_id},
            is_owner=is_owner,
            caller_scope=caller_scope,
        )

    @app_http.get("/api/bots/{bot_id}/card")
    async def get_bot_card(
        bot_id: str,
        x_api_key: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        user_email: str | None = None,
        x_algochains_caller_scope: str | None = Header(default=None),
    ):
        """Get full bot card data. Public card data is unauthenticated; attachments require owner."""
        if bot_id not in {"mnq", "cl", "mes", "nq"}:
            raise HTTPException(status_code=400, detail="bot_id must be mnq | cl | mes | nq")
        _key_valid, is_owner, _subscriber, caller_scope = _resolve_auth(
            x_api_key,
            authorization,
            user_email,
            x_algochains_caller_scope,
        )
        card = await handle_mcp_request("get_bot_card_data", {"bot_id": bot_id}, is_owner=False)
        if is_owner:
            attachments = await handle_mcp_request(
                "list_bot_research_attachments",
                {"bot_id": bot_id},
                is_owner=True,
                caller_scope=caller_scope,
            )
            card["research_attachments"] = attachments
        return card

    @app_http.get("/api/bots/{bot_id}/citations")
    async def get_citations(bot_id: str):
        """Get academic citations for a bot. Public endpoint — no auth required."""
        if bot_id not in {"mnq", "cl", "mes", "nq"}:
            raise HTTPException(status_code=400, detail="bot_id must be mnq | cl | mes | nq")
        return await handle_mcp_request("get_strategy_academic_citations", {"bot_id": bot_id}, is_owner=False)

    @app_http.get("/api/heartbeat")
    async def system_heartbeat(
        x_api_key: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        x_algochains_caller_scope: str | None = Header(default=None),
    ):
        """Get system heartbeat. Requires owner API key — reveals infrastructure topology."""
        key_valid, is_owner, subscriber, developer, caller_scope = _resolve_auth(
            x_api_key,
            authorization,
            caller_scope=x_algochains_caller_scope,
        )
        if not key_valid or subscriber is not None or not is_owner:
            raise HTTPException(status_code=401, detail="Owner API key required")
        return await handle_mcp_request("get_system_heartbeat", {}, is_owner=True, caller_scope=caller_scope)

    @app_http.get("/api/guardrails")
    async def guardrail_status(
        x_api_key: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        x_algochains_caller_scope: str | None = Header(default=None),
    ):
        """Get circuit breaker and guardrail status. Requires owner API key."""
        key_valid, is_owner, subscriber, developer, caller_scope = _resolve_auth(
            x_api_key,
            authorization,
            caller_scope=x_algochains_caller_scope,
        )
        if not key_valid or subscriber is not None or not is_owner:
            raise HTTPException(status_code=401, detail="Owner API key required")
        return await handle_mcp_request("get_circuit_breaker_status", {}, is_owner=True, caller_scope=caller_scope)

    def _health_rows(sb: Any, table: str, columns: str, *, limit: int = 5000) -> tuple[list[dict[str, Any]], str | None]:
        """Best-effort Supabase table read for Command Center health aliases."""
        try:
            resp = sb.table(table).select(columns).limit(limit).execute()
            return list(getattr(resp, "data", None) or []), None
        except Exception as exc:
            log.warning("Command Center subscriber health read failed for %s: %s", table, exc)
            return [], str(exc)

    def _money(value: Any) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    def _paper_account_rollup(rows: list[dict[str, Any]]) -> dict[str, float | int]:
        realized = sum(_money(row.get("realized_pnl_usd")) for row in rows)
        balance_delta = 0.0
        balance_rows = 0
        current_balance = 0.0
        starting_balance = 0.0
        fills_count = 0
        for row in rows:
            current_raw = row.get("current_balance_usd")
            starting_raw = row.get("starting_balance_usd")
            if current_raw is not None:
                current_balance += _money(current_raw)
            if starting_raw is not None:
                starting_balance += _money(starting_raw)
            if current_raw is not None and starting_raw is not None:
                balance_delta += _money(current_raw) - _money(starting_raw)
                balance_rows += 1
            fills_count += int(_money(row.get("fills_count")))

        # Some early paper-account rows only tracked balances. Use that delta
        # when explicit realized PnL has not been populated yet.
        paper_pnl = balance_delta if abs(realized) < 0.005 and balance_rows else realized
        return {
            "account_count": len(rows),
            "paper_pnl_usd": round(paper_pnl, 2),
            "paper_realized_pnl_usd": round(realized, 2),
            "paper_balance_delta_usd": round(balance_delta, 2),
            "paper_current_balance_usd": round(current_balance, 2),
            "paper_starting_balance_usd": round(starting_balance, 2),
            "fills_count": fills_count,
        }

    def _lag_seconds(rows: list[dict[str, Any]], now: datetime) -> float | None:
        latest: datetime | None = None
        for row in rows:
            raw = row.get("last_seen")
            if not raw:
                continue
            try:
                seen = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except ValueError:
                continue
            if seen.tzinfo is None:
                seen = seen.replace(tzinfo=timezone.utc)
            seen = seen.astimezone(timezone.utc)
            if latest is None or seen > latest:
                latest = seen
        if latest is None:
            return None
        return round(max(0.0, (now - latest).total_seconds()), 2)

    @app_http.get("/api/subscribers")
    async def subscriber_health(
        x_api_key: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        user_email: str | None = None,
        x_algochains_caller_scope: str | None = Header(default=None),
    ):
        """Owner-only Command Center view of subscriber and copy-trade paper state."""
        key_valid, is_owner, subscriber, _developer, caller_scope = _resolve_auth(
            x_api_key,
            authorization,
            user_email,
            x_algochains_caller_scope,
        )
        if not key_valid or subscriber is not None or not is_owner:
            raise HTTPException(status_code=401, detail="Owner API key required")

        try:
            from .marketplace.supabase_tools import _get_sb_client
        except Exception as exc:  # pragma: no cover - import path safety
            return {
                "status": "degraded",
                "error": f"supabase_tools unavailable: {exc}",
                "source": "supabase_unavailable",
            }

        sb = _get_sb_client(use_service_role=True)
        if sb is None:
            return {
                "status": "degraded",
                "error": "Supabase service_role not configured",
                "source": "supabase_unavailable",
            }

        now = datetime.now(timezone.utc)
        subscriptions, sub_err = await asyncio.to_thread(
            _health_rows,
            sb,
            "marketplace_botsubscription",
            "id,status,created_at,subscriber_email,requester_slack_id",
        )
        assignments, assign_err = await asyncio.to_thread(
            _health_rows,
            sb,
            "subscriber_bot_assignments",
            "subscriber_id,bot,paused,updated_at",
        )
        paper_accounts, paper_err = await asyncio.to_thread(
            _health_rows,
            sb,
            "subscriber_paper_accounts",
            "subscriber_id,starting_balance_usd,current_balance_usd,realized_pnl_usd,"
            "fills_count,last_reset_at,updated_at",
        )
        heartbeats, hb_err = await asyncio.to_thread(
            _health_rows,
            sb,
            "subscriber_heartbeats",
            "subscriber_id,last_seen,pnl_today_usd,fills_today,tradovate_linked,daemon_version",
        )

        errors = {
            table: err
            for table, err in {
                "marketplace_botsubscription": sub_err,
                "subscriber_bot_assignments": assign_err,
                "subscriber_paper_accounts": paper_err,
                "subscriber_heartbeats": hb_err,
            }.items()
            if err
        }

        active_subscriptions = [
            row for row in subscriptions if str(row.get("status") or "").lower() == "active"
        ]
        active_assignments = [row for row in assignments if not row.get("paused")]
        active_assignment_subscribers = {
            str(row.get("subscriber_id"))
            for row in active_assignments
            if row.get("subscriber_id")
        }
        subscription_subscribers = {
            str(row.get("subscriber_email") or row.get("requester_slack_id"))
            for row in active_subscriptions
            if row.get("subscriber_email") or row.get("requester_slack_id")
        }
        paper = _paper_account_rollup(paper_accounts)
        lag_seconds = _lag_seconds(heartbeats, now)
        live_pnl_usd = sum(_money(row.get("pnl_today_usd")) for row in heartbeats)
        heartbeat_recent = lag_seconds is not None and lag_seconds <= 120
        subscriber_count = max(
            len(active_subscriptions),
            len(active_assignment_subscribers),
            len(subscription_subscribers),
            len(paper_accounts),
        )
        status = "ok" if not errors else "degraded"

        copy_trade = {
            "schema_ok": not bool(errors),
            "executor_ok": heartbeat_recent if heartbeats else None,
            "subs": subscriber_count,
            "subscriber_count": subscriber_count,
            "active_subscriptions": len(active_subscriptions),
            "active_assignments": len(active_assignments),
            "paper_pnl_usd": paper["paper_pnl_usd"],
            "paper_pnl": paper["paper_pnl_usd"],
            "paper_pnl_rollup_usd": paper["paper_pnl_usd"],
            "live_pnl_usd": round(live_pnl_usd, 2),
            "lag_seconds": lag_seconds,
        }

        return {
            "status": status,
            "source": "supabase",
            "as_of": now.isoformat(),
            "subscriber_count": subscriber_count,
            "subscribers": subscriber_count,
            "subscriptions_count": len(active_subscriptions),
            "assignments_count": len(active_assignments),
            "paper_accounts_count": paper["account_count"],
            "heartbeat_count": len(heartbeats),
            "paper_pnl_usd": paper["paper_pnl_usd"],
            "paper_pnl": paper["paper_pnl_usd"],
            "paper_pnl_rollup_usd": paper["paper_pnl_usd"],
            "paper_realized_pnl_usd": paper["paper_realized_pnl_usd"],
            "paper_balance_delta_usd": paper["paper_balance_delta_usd"],
            "paper_current_balance_usd": paper["paper_current_balance_usd"],
            "paper_starting_balance_usd": paper["paper_starting_balance_usd"],
            "fills_count": paper["fills_count"],
            "live_pnl_usd": round(live_pnl_usd, 2),
            "lag_seconds": lag_seconds,
            "copy_trade": copy_trade,
            "tables": {
                "marketplace_botsubscription": len(subscriptions),
                "subscriber_bot_assignments": len(assignments),
                "subscriber_paper_accounts": len(paper_accounts),
                "subscriber_heartbeats": len(heartbeats),
            },
            "errors": errors,
            "caller_scope": caller_scope,
        }

    # ── /v1/agent/* — multi-agent status API (owner + subscriber access) ───────
    # These endpoints are purpose-built for external OpenClaw skill agents that
    # need live AlgoChains system state. They read state files directly (no MCP
    # tool call overhead) so latency is <150ms from disk.
    # Auth: owner BRIDGE_API_KEY or any valid subscriber key (sub_live_…).
    # Subscribers receive a sanitised view — no raw P&L, no account numbers.

    _CT = os.environ.get("ALGOCHAINS_CONTROL_TOWER", os.environ.get("ALGOCHAINS_CONTROL_TOWER_PATH", ""))
    if not _CT:
        # resolve relative to this file's location
        _CT = str(_PathGlobal(__file__).resolve().parents[4] / "algochains-control-tower")

    def _ct_path(*parts: str) -> _PathGlobal:
        return _PathGlobal(_CT, *parts)

    def _read_json_state(rel_path: str) -> dict:
        """Read a JSON state file; return {} on any failure."""
        try:
            p = _ct_path(rel_path)
            if p.exists():
                return json.loads(p.read_text())
        except Exception:
            pass
        return {}

    def _tail_log(rel_path: str, lines: int = 80) -> list[str]:
        """Return last N non-empty lines from a log file."""
        try:
            p = _ct_path(rel_path)
            if not p.exists():
                return []
            with p.open() as fh:
                all_lines = fh.readlines()
            return [l.rstrip() for l in all_lines[-lines:] if l.strip()]
        except Exception:
            return []

    def _bot_process_alive(pattern: str) -> bool:
        """Check if a bot process matching the pattern is running (pgrep)."""
        import subprocess
        try:
            result = subprocess.run(
                ["pgrep", "-f", pattern],
                capture_output=True,
                timeout=3,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _build_status_snapshot(include_sensitive: bool) -> dict:
        """Assemble the /v1/agent/status payload from state files."""
        sig = _read_json_state("state/signal_health.json")
        sentinel = _read_json_state("state/e2e_execution_sentinel.json")
        guardian = _read_json_state("state/bracket_guardian_state.json")
        session = _read_json_state("state/session_summary.json")
        mnq_stats = _read_json_state("state/mnq_session_stats.json")
        incident_dedup = _read_json_state("state/incident_dedup.json")

        # Recent incidents — last 5 fingerprints with timestamps
        recent_incidents = sorted(
            [{"fingerprint": k, "last_ts": v} for k, v in incident_dedup.items()],
            key=lambda x: x["last_ts"],
            reverse=True,
        )[:5]

        bot_procs = {
            "mnq": _bot_process_alive("FUTURES_SCALPER_UPGRADED"),
            "cl": _bot_process_alive("CL_FUTURES_SCALPER"),
            "mes": _bot_process_alive("mes_swing_live"),
            "nq": _bot_process_alive("nq_swing_live"),
        }

        # E2E sentinel summary
        sentinel_class = sentinel.get("classification") or {}
        sentinel_summary = {
            "outcome": sentinel_class.get("outcome"),
            "severity": sentinel_class.get("severity"),
            "reason": sentinel_class.get("reason"),
            "description": sentinel_class.get("description"),
            "ts": sentinel.get("last_check"),
        }

        # Signal health summary per bot
        signal_summaries: dict = {}
        for bot_key, bot_data in (sig.items() if isinstance(sig, dict) else {}.items()):
            if not isinstance(bot_data, dict):
                continue
            signal_summaries[bot_key] = {
                "last_signal_ts": bot_data.get("last_signal_time"),
                "last_outcome": bot_data.get("last_trade_result"),
                "confidence": bot_data.get("last_confidence"),
                "regime": bot_data.get("last_regime"),
            }

        # Guardian summary
        guardian_summary = {
            "positions_count": guardian.get("positions_count", 0),
            "working_orders_count": guardian.get("working_orders_count", 0),
            "unknown_flat_orders": len(guardian.get("unknown_flat_orders") or []),
            "last_check": guardian.get("last_check"),
        }

        payload: dict = {
            "server": f"AlgoChains v{_SERVER_VERSION}",
            "ts": datetime.now(timezone.utc).isoformat(),
            "bots_alive": bot_procs,
            "all_bots_running": all(bot_procs.values()),
            "sentinel": sentinel_summary,
            "guardian": guardian_summary,
            "signal_health": signal_summaries,
            "recent_incidents": recent_incidents,
        }

        if include_sensitive:
            payload["session"] = {
                "total_trades": session.get("total_trades"),
                "wins": session.get("wins"),
                "losses": session.get("losses"),
                "session_pnl": session.get("session_pnl"),
            }
            payload["mnq_advisory"] = {
                "advisory_total": mnq_stats.get("advisory_total_count"),
                "advisory_fallback": mnq_stats.get("advisory_fallback_count"),
                "advisory_timeout": mnq_stats.get("advisory_timeout_count"),
            }

        return payload

    @app_http.get("/v1/agent/status")
    async def agent_status(
        x_api_key: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ):
        """
        Live AlgoChains system status snapshot for external OpenClaw skill agents.

        Returns: bot process state, E2E sentinel, bracket guardian, signal health
        per bot, and recent incident fingerprints.

        Auth: owner BRIDGE_API_KEY (full view) or subscriber key (sanitised view).
        Latency: <150ms — reads from state files on disk.
        """
        key_valid, is_owner, subscriber, _ = _resolve_auth(x_api_key, authorization)
        if not key_valid:
            raise HTTPException(status_code=401, detail="Valid API key required (owner or subscriber)")
        snapshot = await asyncio.to_thread(_build_status_snapshot, is_owner)
        snapshot["access_level"] = "owner" if is_owner else "subscriber"
        return snapshot

    @app_http.get("/v1/agent/signals")
    async def agent_signals(
        x_api_key: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        limit: int = 20,
    ):
        """
        Recent signal health entries across all bots.

        Auth: owner BRIDGE_API_KEY or subscriber key.
        """
        key_valid, is_owner, subscriber, _ = _resolve_auth(x_api_key, authorization)
        if not key_valid:
            raise HTTPException(status_code=401, detail="Valid API key required")
        limit = max(1, min(int(limit), 100))

        def _get_signals():
            sig = _read_json_state("state/signal_health.json")
            entries = []
            for bot_key, bot_data in (sig.items() if isinstance(sig, dict) else {}.items()):
                if not isinstance(bot_data, dict):
                    continue
                entry = {
                    "bot": bot_key,
                    "last_signal_ts": bot_data.get("last_signal_time"),
                    "last_outcome": bot_data.get("last_trade_result"),
                    "confidence": bot_data.get("last_confidence"),
                    "regime": bot_data.get("last_regime"),
                    "advisory_path": bot_data.get("advisory_path"),
                }
                if is_owner:
                    entry["kronos_shadow"] = bot_data.get("kronos_shadow")
                    entry["validator_summary"] = bot_data.get("validator_summary")
                entries.append(entry)
            return {
                "signals": entries[:limit],
                "ts": datetime.now(timezone.utc).isoformat(),
            }

        return await asyncio.to_thread(_get_signals)

    @app_http.get("/v1/agent/incidents")
    async def agent_incidents(
        x_api_key: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        hours: int = 24,
    ):
        """
        Recent incident fingerprints from the cross-component dedup store.
        Useful for agents to understand system health trends and avoid double-triaging.

        Auth: owner BRIDGE_API_KEY or subscriber key.
        """
        key_valid, is_owner, subscriber, _ = _resolve_auth(x_api_key, authorization)
        if not key_valid:
            raise HTTPException(status_code=401, detail="Valid API key required")
        hours = max(1, min(int(hours), 168))

        def _get_incidents():
            dedup = _read_json_state("state/incident_dedup.json")
            cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
            incidents = [
                {"fingerprint": k, "last_fired_ts": v, "age_sec": int(datetime.now(timezone.utc).timestamp() - float(v))}
                for k, v in dedup.items()
                if float(v) >= cutoff
            ]
            incidents.sort(key=lambda x: x["last_fired_ts"], reverse=True)
            return {
                "incidents": incidents,
                "count": len(incidents),
                "window_hours": hours,
                "ts": datetime.now(timezone.utc).isoformat(),
            }

        return await asyncio.to_thread(_get_incidents)

    @app_http.get("/v1/agent/stream")
    async def agent_stream(
        request: Request,
        x_api_key: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        poll_interval: float = 2.0,
    ):
        """
        Server-Sent Events stream of live AlgoChains system events.

        Emits events:
          status_snapshot  — full /v1/agent/status payload every poll_interval seconds
          log_line         — classified log lines from all 4 bots (entry/fill/exit/error)
          heartbeat        — keep-alive every poll_interval seconds

        Auth: owner BRIDGE_API_KEY or subscriber key.
        Reconnect: standard SSE retry — client reconnects automatically on disconnect.
        """
        key_valid, is_owner, subscriber, _ = _resolve_auth(x_api_key, authorization)
        if not key_valid:
            raise HTTPException(status_code=401, detail="Valid API key required")
        interval = max(1.0, min(float(poll_interval), 30.0))

        LOG_PATHS = [
            ("mnq", "logs/futures_bot_live.log"),
            ("cl", "logs/cl_futures_live.log"),
            ("mes", "logs/mes_swing_live.log"),
            ("nq", "logs/nq_swing_live.log"),
        ]
        LOG_KEYWORDS = ("SIGNAL", "FILL", "EXIT", "ERROR", "Exception", "Traceback",
                        "BRACKET", "SENTINEL", "guardian", "P0", "P1", "P2")

        def _classify_line(line: str) -> str | None:
            l = line.lower()
            if any(k in line for k in ("FILL", "filled")):
                return "fill"
            if any(k in line for k in ("SIGNAL", "signal_fired", "confidence")):
                return "signal"
            if any(k in line for k in ("EXIT", "exit_reason", "closed")):
                return "exit"
            if any(k in line for k in ("ERROR", "Exception", "Traceback", "BRACKET FAILED")):
                return "error"
            if any(k in line for k in ("BRACKET", "stop_order", "target_order")):
                return "bracket"
            return None

        # Track last-seen file offset per log
        _last_pos: dict[str, int] = {}

        async def event_gen():
            yield f"event: ready\ndata: {json.dumps({'access_level': 'owner' if is_owner else 'subscriber', 'ts': datetime.now(timezone.utc).isoformat()})}\n\n"

            while True:
                if await request.is_disconnected():
                    break

                # Emit log lines that appeared since last poll
                def _poll_logs():
                    new_events = []
                    for bot, rel_path in LOG_PATHS:
                        p = _ct_path(rel_path)
                        if not p.exists():
                            continue
                        try:
                            size = p.stat().st_size
                            last = _last_pos.get(rel_path, size)
                            if size > last:
                                with p.open() as fh:
                                    fh.seek(last)
                                    new_text = fh.read(min(size - last, 32768))
                                _last_pos[rel_path] = last + len(new_text.encode())
                                for raw_line in new_text.splitlines():
                                    line = raw_line.strip()
                                    if not line:
                                        continue
                                    if not any(kw in line for kw in LOG_KEYWORDS):
                                        continue
                                    event_type = _classify_line(line)
                                    if event_type:
                                        new_events.append({"bot": bot, "type": event_type, "line": line[-400:]})
                            else:
                                _last_pos[rel_path] = size
                        except Exception:
                            pass
                    return new_events

                new_log_events = await asyncio.to_thread(_poll_logs)
                for ev in new_log_events[:20]:
                    yield f"event: log_line\ndata: {json.dumps(ev, default=str)}\n\n"

                # Emit a status snapshot every cycle
                snapshot = await asyncio.to_thread(_build_status_snapshot, is_owner)
                yield f"event: status_snapshot\ndata: {json.dumps(snapshot, default=str)}\n\n"

                yield f"event: heartbeat\ndata: {datetime.now(timezone.utc).isoformat()}\n\n"
                await asyncio.sleep(interval)

        return StreamingResponse(
            event_gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    return app_http


# Standalone entry point
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("MCP_BRIDGE_PORT", "8090"))
    log.info(f"Starting AlgoChains MCP HTTP Bridge on :{port}")
    app = create_fastapi_app()
    # Default to localhost only. Set ALGOCHAINS_BRIDGE_HOST=0.0.0.0 intentionally for LAN access.
    host = os.getenv("ALGOCHAINS_BRIDGE_HOST", "127.0.0.1")
    uvicorn.run(app, host=host, port=port, log_level="info")
