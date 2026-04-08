"""
http_bridge.py — AlgoChains MCP HTTP Bridge
=============================================
Exposes the AlgoChains MCP Server as a simple HTTP API for algochains.ai.
Translates POST /api/mcp { tool, arguments } → MCP tool call → JSON response.

This is the bridge between algochains.ai (Next.js/Vercel) and the MCP server.
Can run as:
  - A standalone FastAPI server:  uvicorn algochains_mcp.http_bridge:app
  - A Vercel serverless function (see api/mcp.py in algochains.ai repo)
  - A Cloudflare Worker proxy

Authentication:
  - Requires API_KEY header matching ALGOCHAINS_BRIDGE_API_KEY env var
  - Owner-only tools (trading, bot metrics) validate against OWNER_EMAIL env var
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

# FastAPI imports at module level so inner functions can resolve Request type
try:
    from fastapi import FastAPI, HTTPException, Header, Request
    from fastapi.middleware.cors import CORSMiddleware
    _FASTAPI_AVAILABLE = True
except ImportError:
    _FASTAPI_AVAILABLE = False

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
    "get_portfolio_summary",
    "run_marketplace_autopilot",
    "get_onyx_status",
    "run_onyx_ingest",
    "get_protection_config",
    "submit_to_marketplace",
}


async def handle_mcp_request(tool_name: str, arguments: dict, is_owner: bool = False) -> dict:
    """
    Route an MCP tool call from the HTTP bridge.
    Returns dict that will be serialized to JSON.
    """
    # Authorization check
    if tool_name in OWNER_TOOLS and not is_owner:
        return {"error": "Unauthorized — this tool requires owner access", "tool": tool_name}
    if tool_name not in PUBLIC_TOOLS and tool_name not in OWNER_TOOLS:
        return {"error": f"Tool '{tool_name}' not available via HTTP bridge", "available_tools": sorted(PUBLIC_TOOLS)}

    # Import and call the server's tool handler
    try:
        from algochains_mcp import server as _server
        import asyncio

        # Call the tool handler directly
        result = await _server.call_tool(tool_name, arguments)

        # Extract text content
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
            "http://localhost:3000",
            "http://localhost:5173",
        ],
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    BRIDGE_API_KEY = os.getenv("ALGOCHAINS_BRIDGE_API_KEY", "")
    OWNER_EMAIL = os.getenv("OWNER_EMAIL", "tyler@algochains.ai")

    def _resolve_auth(
        x_api_key: str | None,
        authorization: str | None,
        user_email: str | None = None,
    ) -> tuple[bool, bool]:
        """
        Returns (key_valid, is_owner).
        key_valid: True if BRIDGE_API_KEY not set OR key matches.
        is_owner: True only when key_valid AND user_email matches OWNER_EMAIL.
        """
        provided_key = x_api_key or (authorization.replace("Bearer ", "") if authorization else "")
        key_valid = (not BRIDGE_API_KEY) or (provided_key == BRIDGE_API_KEY)
        is_owner = key_valid and (user_email == OWNER_EMAIL)
        return key_valid, is_owner

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

        key_valid, is_owner = _resolve_auth(x_api_key, authorization)
        visible_tools = sorted(PUBLIC_TOOLS) + (sorted(OWNER_TOOLS) if is_owner else [])

        if include_danger_tiers:
            tools_with_tiers = [get_tool_danger_info(t) for t in sorted(set(visible_tools))]
            if is_owner:
                return {
                    "tools": tools_with_tiers,
                    "public_tool_count": len(PUBLIC_TOOLS),
                    "owner_tool_count": len(OWNER_TOOLS),
                    "tier_legend": {
                        "0": "READ_ONLY — no side effects, safe for any agent",
                        "1": "WRITE_LOCAL — internal state writes only, no broker",
                        "2": "ORDER_EXEC — executes real broker orders",
                        "3": "DESTRUCTIVE — irreversible bulk actions",
                    },
                }
            return {
                "tools": tools_with_tiers,
                "public_tool_count": len(PUBLIC_TOOLS),
                "tier_legend": {
                    "0": "READ_ONLY — no side effects, safe for any agent",
                    "1": "WRITE_LOCAL — internal state writes only, no broker",
                    "2": "ORDER_EXEC — executes real broker orders",
                    "3": "DESTRUCTIVE — irreversible bulk actions",
                },
            }

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
        key_valid, is_owner = _resolve_auth(x_api_key, authorization, user_email)
        if not key_valid:
            raise HTTPException(status_code=401, detail="Invalid API key")
        result = await handle_mcp_request(tool, arguments, is_owner=is_owner)
        return result

    # SECURITY FIX (V22 audit): All GET convenience endpoints now require API key.
    # Previously these called handle_mcp_request(is_owner=True) with no auth check —
    # any unauthenticated client could scrape live bot metrics and heartbeat status.

    @app_http.get("/api/bots")
    async def get_all_bots(
        x_api_key: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        user_email: str | None = None,
    ):
        """Convenience endpoint: get all 4 bot metrics. Requires API key."""
        key_valid, is_owner = _resolve_auth(x_api_key, authorization, user_email)
        if not key_valid:
            raise HTTPException(status_code=401, detail="Invalid API key")
        return await handle_mcp_request("get_all_bot_metrics", {}, is_owner=is_owner)

    @app_http.get("/api/bots/{bot_id}")
    async def get_bot(
        bot_id: str,
        x_api_key: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        user_email: str | None = None,
    ):
        """Convenience endpoint: get metrics for a specific bot. Requires API key."""
        if bot_id not in {"mnq", "cl", "mes", "nq"}:
            raise HTTPException(status_code=400, detail="bot_id must be mnq | cl | mes | nq")
        key_valid, is_owner = _resolve_auth(x_api_key, authorization, user_email)
        if not key_valid:
            raise HTTPException(status_code=401, detail="Invalid API key")
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
        key_valid, is_owner = _resolve_auth(x_api_key, authorization, user_email)
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
        """Get system heartbeat. Requires API key — reveals infrastructure topology."""
        key_valid, _ = _resolve_auth(x_api_key, authorization)
        if not key_valid:
            raise HTTPException(status_code=401, detail="Invalid API key")
        return await handle_mcp_request("get_system_heartbeat", {}, is_owner=True)

    @app_http.get("/api/guardrails")
    async def guardrail_status(
        x_api_key: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ):
        """Get circuit breaker and guardrail status. Requires API key."""
        key_valid, _ = _resolve_auth(x_api_key, authorization)
        if not key_valid:
            raise HTTPException(status_code=401, detail="Invalid API key")
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
