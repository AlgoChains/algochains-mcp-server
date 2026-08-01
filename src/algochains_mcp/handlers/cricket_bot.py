"""Read-only cricket-bot performance handlers (Avi's external partner API).

Source repo: github.com/aviralthebuilder/cricket-bot-api. Contract:

  Base URL:  CRICKET_BOT_API_URL (e.g. http://143.198.53.64/api)
  Auth:      X-API-Key header on every request (CRICKET_BOT_API_KEY)
  Endpoints: /performance /trades /matches /signals /tournaments

These handlers are advisory observability for the marketplace "Agent Cricket007"
listing. They never read broker state, never place orders, and are never part
of any trading/risk path. Fail-closed: when env vars are missing or the API is
unreachable they return an explicit error dict — no mock data, no fallbacks.
Trades/signals carry a structured ``platform`` field (polymarket | kalshi).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Awaitable, Callable

import httpx

logger = logging.getLogger("algochains_mcp.handlers.cricket_bot")

Handler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
_VALID_PLATFORMS = ("all", "polymarket", "kalshi")
_VALID_ACTIONS = ("BUY", "SKIP")


def _config() -> tuple[str, str] | None:
    key = os.getenv("CRICKET_BOT_API_KEY", "").strip()
    url = os.getenv("CRICKET_BOT_API_URL", "").strip().rstrip("/")
    if not key or not url:
        return None
    return key, url


def _not_configured() -> dict[str, Any]:
    return {
        "error": "cricket_bot_unavailable",
        "detail": (
            "CRICKET_BOT_API_KEY / CRICKET_BOT_API_URL are not set. Add both to the "
            "gitignored .env (see docs/PRIVATE_DEPS.md in control-tower). Failing "
            "closed — no mock data."
        ),
    }


async def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = _config()
    if cfg is None:
        return _not_configured()
    key, base = cfg
    url = f"{base}/{path.lstrip('/')}"
    clean = {k: v for k, v in (params or {}).items() if v is not None}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url, headers={"X-API-Key": key}, params=clean)
    except httpx.HTTPError as exc:
        return {"error": "cricket_bot_unreachable", "detail": f"{url}: {exc}"}
    if resp.status_code in (401, 403):
        return {
            "error": "cricket_bot_auth_rejected",
            "detail": f"HTTP {resp.status_code} — key may have been rotated; sync with Avi.",
        }
    if resp.status_code != 200:
        return {"error": "cricket_bot_http_error", "detail": f"HTTP {resp.status_code} for {path}"}
    try:
        body = resp.json()
    except ValueError:
        return {"error": "cricket_bot_bad_response", "detail": f"non-JSON body for {path}"}
    if not isinstance(body, dict):
        return {"error": "cricket_bot_bad_response", "detail": f"unexpected payload shape for {path}"}
    return {"status": "ok", "endpoint": path, "authority": "agent_memory", "broker_truth": False, **body}


def _clamp_limit(value: Any, default: int = 100) -> int:
    try:
        return max(1, min(int(value), 500))
    except (TypeError, ValueError):
        return default


def _opt_innings(value: Any) -> int | None:
    try:
        inn = int(value)
    except (TypeError, ValueError):
        return None
    return inn if inn in (1, 2) else None


async def get_cricket_bot_performance(arguments: dict[str, Any]) -> dict[str, Any]:
    platform = str(arguments.get("platform") or "all").lower()
    if platform not in _VALID_PLATFORMS:
        return {"error": "bad_arguments", "detail": f"platform must be one of {_VALID_PLATFORMS}"}
    return await _get("/performance", {
        "platform": platform,
        "tournament": arguments.get("tournament"),
        "innings": _opt_innings(arguments.get("innings")),
    })


async def get_cricket_bot_trades(arguments: dict[str, Any]) -> dict[str, Any]:
    platform = str(arguments.get("platform") or "all").lower()
    if platform not in _VALID_PLATFORMS:
        return {"error": "bad_arguments", "detail": f"platform must be one of {_VALID_PLATFORMS}"}
    return await _get("/trades", {
        "platform": platform,
        "tournament": arguments.get("tournament"),
        "innings": _opt_innings(arguments.get("innings")),
        "limit": _clamp_limit(arguments.get("limit")),
        "offset": max(0, int(arguments.get("offset") or 0)),
    })


async def get_cricket_bot_matches(arguments: dict[str, Any]) -> dict[str, Any]:
    return await _get("/matches", {
        "tournament": arguments.get("tournament"),
        "innings": _opt_innings(arguments.get("innings")),
    })


async def get_cricket_bot_signals(arguments: dict[str, Any]) -> dict[str, Any]:
    action = arguments.get("action")
    if action is not None:
        action = str(action).upper()
        if action not in _VALID_ACTIONS:
            return {"error": "bad_arguments", "detail": f"action must be one of {_VALID_ACTIONS}"}
    return await _get("/signals", {
        "action": action,
        "tournament": arguments.get("tournament"),
        "limit": _clamp_limit(arguments.get("limit")),
        "offset": max(0, int(arguments.get("offset") or 0)),
    })


async def get_cricket_bot_tournaments(arguments: dict[str, Any]) -> dict[str, Any]:
    return await _get("/tournaments")


CRICKET_BOT_HANDLERS: dict[str, Handler] = {
    "get_cricket_bot_performance": get_cricket_bot_performance,
    "get_cricket_bot_trades": get_cricket_bot_trades,
    "get_cricket_bot_matches": get_cricket_bot_matches,
    "get_cricket_bot_signals": get_cricket_bot_signals,
    "get_cricket_bot_tournaments": get_cricket_bot_tournaments,
}
