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
from datetime import datetime, timezone
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

log = logging.getLogger(__name__)

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
}


async def handle_mcp_request(
    tool_name: str,
    arguments: dict,
    *,
    is_owner: bool = False,
    subscriber: ResolvedSubscriber | None = None,
) -> dict:
    """
    Route an MCP tool call from the HTTP bridge.

    Resolution order:
      1. Subscriber-scoped tools (when `subscriber` is provided).
      2. Owner tools (require `is_owner`).
      3. Public tools.

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

    # ── 2/3. Owner / public surfaces ─────────────────────────────────────
    if tool_name in OWNER_TOOLS and not is_owner:
        return {"error": "Unauthorized — this tool requires owner access", "tool": tool_name}
    if tool_name not in PUBLIC_TOOLS and tool_name not in OWNER_TOOLS:
        return {
            "error": f"Tool '{tool_name}' not available via HTTP bridge",
            "available_tools": sorted(PUBLIC_TOOLS),
        }

    # K-1 fix: enforce danger tiers at dispatch — not just as metadata.
    # TIER_ORDER_EXEC (2) and TIER_DESTRUCTIVE (3) require owner access AND
    # an explicit confirm=true argument to prevent accidental or automated calls.
    if is_owner:
        try:
            from algochains_mcp.tool_danger_tiers import get_danger_tier, TIER_ORDER_EXEC, TIER_DESTRUCTIVE
            tool_tier = get_danger_tier(tool_name)
            if tool_tier >= TIER_ORDER_EXEC:
                if not arguments.get("confirm"):
                    tier_label = "ORDER_EXEC" if tool_tier == TIER_ORDER_EXEC else "DESTRUCTIVE"
                    return {
                        "error": (
                            f"Tool '{tool_name}' has danger tier {tier_label} ({tool_tier}). "
                            "Pass confirm=true in arguments to execute."
                        ),
                        "tool": tool_name,
                        "danger_tier": tool_tier,
                        "required_arg": "confirm=true",
                    }
        except Exception as _tier_err:
            log.warning("Danger tier check failed for %s: %s", tool_name, _tier_err)

    try:
        from algochains_mcp import server as _server

        result = await _server.call_tool(tool_name, arguments)

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
        description="REST bridge to AlgoChains MCP Server v22 for algochains.ai",
        version="22.0.0",
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

    BRIDGE_API_KEY = os.getenv("ALGOCHAINS_BRIDGE_API_KEY", "")
    OWNER_EMAIL = os.getenv("OWNER_EMAIL", "tyler@algochains.ai")
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
    ) -> tuple[bool, bool, ResolvedSubscriber | None]:
        """
        Returns (key_valid, is_owner, subscriber).

        Legitimate outcomes:
          • Owner key matches BRIDGE_API_KEY → (True, True, None)
          • Subscriber key (sub_live_…) resolves in Supabase
                → (True, False, ResolvedSubscriber)
          • No BRIDGE_API_KEY AND dev mode active
                → (True, False, None) — public tools only, no owner privilege ever

        K-8 fix: when BRIDGE_API_KEY is empty and dev mode is off, all requests
        return (False, False, None) — the endpoint raises 401.

        NOTE: is_owner is now derived exclusively from the API key match, NOT from
        the user_email body field. user_email is accepted only for audit-log
        attribution and never confers owner privilege.
        """
        provided_key = x_api_key or (authorization.replace("Bearer ", "") if authorization else "")

        # Subscriber path takes precedence so a subscriber key is never treated
        # as an invalid owner key.
        if is_subscriber_key(provided_key):
            sub = resolve_subscriber_key(provided_key)
            if sub:
                return True, False, sub
            return False, False, None

        if BRIDGE_API_KEY:
            key_valid = provided_key == BRIDGE_API_KEY
            # Owner privilege is derived from key match only — user_email is
            # accepted for audit attribution but NEVER grants owner access.
            is_owner = key_valid
            return key_valid, is_owner, None

        # No bridge key configured.
        if _DEV_MODE:
            # Dev: accept anything for public tools, owner is never True.
            return True, False, None

        # K-8 fix: production with no key — fail closed.
        return False, False, None

    class McpRequest(BaseModel):
        tool: str
        arguments: dict = {}
        user_email: str | None = None

    @app_http.get("/health")
    async def health():
        return {"status": "ok", "server": "AlgoChains MCP Bridge v22"}

    @app_http.get("/tools")
    async def list_available_tools(
        x_api_key: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        include_danger_tiers: bool = True,
    ):
        """
        List available tools with danger tier classification.

        danger_tier:
          0 READ_ONLY    — No side effects. Safe for any user.
          1 WRITE_LOCAL  — Internal writes only. No money, no broker.
          2 ORDER_EXEC   — Executes real broker orders.
          3 DESTRUCTIVE  — Irreversible bulk/high-impact actions.
        """
        from algochains_mcp.tool_danger_tiers import get_tool_danger_info

        key_valid, is_owner, subscriber = _resolve_auth(x_api_key, authorization)

        # Subscribers see ONLY the subscriber tool surface — never owner / public marketplace.
        if subscriber is not None:
            visible_tools = sorted(SUBSCRIBER_TOOLS)
        else:
            visible_tools = sorted(PUBLIC_TOOLS) + (sorted(OWNER_TOOLS) if is_owner else [])

        if include_danger_tiers:
            tools_with_tiers = [get_tool_danger_info(t) for t in sorted(set(visible_tools))]
            tier_legend = {
                "0": "READ_ONLY — no side effects, safe for any agent",
                "1": "WRITE_LOCAL — internal state writes only, no broker",
                "2": "ORDER_EXEC — executes real broker orders",
                "3": "DESTRUCTIVE — irreversible bulk actions",
            }
            if subscriber is not None:
                return {
                    "tools": tools_with_tiers,
                    "subscriber_tool_count": len(SUBSCRIBER_TOOLS),
                    "scopes": list(subscriber.scopes),
                    "tier_legend": tier_legend,
                }
            if is_owner:
                return {
                    "tools": tools_with_tiers,
                    "public_tool_count": len(PUBLIC_TOOLS),
                    "owner_tool_count": len(OWNER_TOOLS),
                    "tier_legend": tier_legend,
                }
            return {
                "tools": tools_with_tiers,
                "public_tool_count": len(PUBLIC_TOOLS),
                "tier_legend": tier_legend,
            }

        if subscriber is not None:
            return {"subscriber_tools": sorted(SUBSCRIBER_TOOLS), "scopes": list(subscriber.scopes)}
        if is_owner:
            return {"public_tools": sorted(PUBLIC_TOOLS), "owner_tools": sorted(OWNER_TOOLS)}
        return {"public_tools": sorted(PUBLIC_TOOLS)}

    @app_http.post("/api/mcp")
    async def mcp_endpoint(
        request: Request,
        x_api_key: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ):
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")
        tool = body.get("tool")
        arguments = body.get("arguments", {})
        user_email = body.get("user_email")
        if not tool:
            raise HTTPException(status_code=400, detail="Missing 'tool' field")
        key_valid, is_owner, subscriber = _resolve_auth(x_api_key, authorization, user_email)
        if not key_valid:
            raise HTTPException(status_code=401, detail="Invalid API key")
        result = await handle_mcp_request(
            tool,
            arguments,
            is_owner=is_owner,
            subscriber=subscriber,
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
        key_valid, _is_owner, subscriber = _resolve_auth(x_api_key, authorization)
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
    ):
        """Convenience endpoint: get all 4 bot metrics. Owner key required."""
        key_valid, is_owner, subscriber = _resolve_auth(x_api_key, authorization, user_email)
        if not key_valid or subscriber is not None:
            raise HTTPException(status_code=401, detail="Owner API key required")
        return await handle_mcp_request("get_all_bot_metrics", {}, is_owner=is_owner)

    @app_http.get("/api/bots/{bot_id}")
    async def get_bot(
        bot_id: str,
        x_api_key: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        user_email: str | None = None,
    ):
        """Convenience endpoint: get metrics for a specific bot. Owner key required."""
        if bot_id not in {"mnq", "cl", "mes", "nq"}:
            raise HTTPException(status_code=400, detail="bot_id must be mnq | cl | mes | nq")
        key_valid, is_owner, subscriber = _resolve_auth(x_api_key, authorization, user_email)
        if not key_valid or subscriber is not None:
            raise HTTPException(status_code=401, detail="Owner API key required")
        return await handle_mcp_request("get_live_bot_metrics", {"bot_id": bot_id}, is_owner=is_owner)

    @app_http.get("/api/bots/{bot_id}/card")
    async def get_bot_card(
        bot_id: str,
        x_api_key: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        user_email: str | None = None,
    ):
        """Get full bot card data. Public card data is unauthenticated; attachments require owner."""
        if bot_id not in {"mnq", "cl", "mes", "nq"}:
            raise HTTPException(status_code=400, detail="bot_id must be mnq | cl | mes | nq")
        _key_valid, is_owner, _subscriber = _resolve_auth(x_api_key, authorization, user_email)
        card = await handle_mcp_request("get_bot_card_data", {"bot_id": bot_id}, is_owner=False)
        if is_owner:
            attachments = await handle_mcp_request("list_bot_research_attachments", {"bot_id": bot_id}, is_owner=True)
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
    ):
        """Get system heartbeat. Requires owner API key — reveals infrastructure topology."""
        key_valid, is_owner, subscriber = _resolve_auth(x_api_key, authorization)
        if not key_valid or subscriber is not None or not is_owner:
            raise HTTPException(status_code=401, detail="Owner API key required")
        return await handle_mcp_request("get_system_heartbeat", {}, is_owner=True)

    @app_http.get("/api/guardrails")
    async def guardrail_status(
        x_api_key: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ):
        """Get circuit breaker and guardrail status. Requires owner API key."""
        key_valid, is_owner, subscriber = _resolve_auth(x_api_key, authorization)
        if not key_valid or subscriber is not None or not is_owner:
            raise HTTPException(status_code=401, detail="Owner API key required")
        return await handle_mcp_request("get_circuit_breaker_status", {}, is_owner=True)

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
