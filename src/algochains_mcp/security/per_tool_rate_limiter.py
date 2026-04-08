"""Per-tool rate limiting for destructive MCP tools.

Addresses SAFE-MCP T067 (Rate Limit Bypass) — without this, a compromised agent
or runaway loop calling place_order 100x could blow through our $500/day limit
before the daily circuit breaker triggers.

Uses token bucket algorithm (same as middleware.py's broker rate limiter)
but scoped per-tool rather than per-broker.

No external dependencies — pure stdlib threading.
"""
from __future__ import annotations

import logging
import time
from threading import Lock
from typing import NamedTuple

logger = logging.getLogger("algochains_mcp.security.per_tool_rate_limiter")


class ToolRateLimit(NamedTuple):
    calls: int          # Max calls allowed
    window_seconds: int # In this rolling window
    description: str = ""


# Default rate limits for destructive tools
# These are conservative starting points — adjust based on real trading patterns
TOOL_RATE_LIMITS: dict[str, ToolRateLimit] = {
    # Order execution — CRITICAL
    "place_order":              ToolRateLimit(5, 60,   "Max 5 orders/min"),
    "place_kalshi_order":       ToolRateLimit(3, 60,   "Max 3 Kalshi orders/min"),
    "place_polymarket_bet":     ToolRateLimit(3, 60,   "Max 3 Polymarket bets/min"),
    "place_prop_fund_order":    ToolRateLimit(3, 60,   "Max 3 prop fund orders/min"),

    # Position management
    "cancel_all_orders":        ToolRateLimit(2, 300,  "Max 2 cancel-all/5min"),
    "cancel_order":             ToolRateLimit(10, 60,  "Max 10 cancels/min"),
    "flatten_all_positions":    ToolRateLimit(1, 3600, "Max 1 flatten/hour"),
    "set_instrument_lock":      ToolRateLimit(5, 300,  "Max 5 locks/5min"),

    # Config changes
    "update_algochains_telos":  ToolRateLimit(10, 300, "Max 10 TELOS updates/5min"),
    "set_daily_loss_limit":     ToolRateLimit(2, 3600, "Max 2 limit changes/hour"),

    # Notifications (prevent spam)
    "send_ntfy_notification":   ToolRateLimit(20, 60,  "Max 20 notifications/min"),
}


class ToolRateLimiter:
    """Token bucket rate limiter keyed by (tool_name, client_id).

    thread-safe for the MCP server's async dispatch loop.
    """

    def __init__(self, limits: dict[str, ToolRateLimit] = None):
        self._limits = limits or TOOL_RATE_LIMITS
        self._buckets: dict[str, list[float]] = {}  # key -> [call_timestamps]
        self._lock = Lock()

    def check(self, tool_name: str, client_id: str = "default") -> dict:
        """Check if a tool call is allowed.

        Returns:
            dict with allowed (bool), remaining (int), reset_in (float seconds)
        """
        limit = self._limits.get(tool_name)
        if not limit:
            return {"allowed": True, "remaining": -1, "limited": False}

        bucket_key = f"{tool_name}:{client_id}"
        now = time.monotonic()
        window_start = now - limit.window_seconds

        with self._lock:
            calls = self._buckets.get(bucket_key, [])
            # Remove expired calls
            calls = [t for t in calls if t > window_start]

            if len(calls) >= limit.calls:
                oldest = calls[0]
                reset_in = oldest + limit.window_seconds - now
                logger.warning(
                    "Rate limit hit: tool=%s client=%s calls=%d limit=%d window=%ds",
                    tool_name, client_id, len(calls), limit.calls, limit.window_seconds
                )
                self._buckets[bucket_key] = calls
                return {
                    "allowed": False,
                    "limited": True,
                    "tool": tool_name,
                    "calls_in_window": len(calls),
                    "limit": limit.calls,
                    "window_seconds": limit.window_seconds,
                    "reset_in_seconds": round(reset_in, 1),
                    "description": limit.description,
                }

            calls.append(now)
            self._buckets[bucket_key] = calls
            remaining = limit.calls - len(calls)

            return {
                "allowed": True,
                "limited": False,
                "tool": tool_name,
                "calls_in_window": len(calls),
                "remaining": remaining,
                "limit": limit.calls,
                "window_seconds": limit.window_seconds,
            }

    def get_status(self, tool_name: str = None, client_id: str = "default") -> dict:
        """Get current rate limit status for all tools or a specific tool."""
        now = time.monotonic()
        result = {}

        with self._lock:
            for key, calls in self._buckets.items():
                t_name, c_id = key.rsplit(":", 1)
                if tool_name and t_name != tool_name:
                    continue
                if c_id != client_id and client_id != "all":
                    continue

                limit = self._limits.get(t_name)
                if not limit:
                    continue

                window_start = now - limit.window_seconds
                active_calls = [t for t in calls if t > window_start]
                result[t_name] = {
                    "calls_in_window": len(active_calls),
                    "limit": limit.calls,
                    "remaining": max(0, limit.calls - len(active_calls)),
                    "window_seconds": limit.window_seconds,
                    "description": limit.description,
                }

        return result

    def reset(self, tool_name: str = None, client_id: str = "default") -> dict:
        """Reset rate limit counters (for testing or manual override by owner)."""
        with self._lock:
            if tool_name:
                bucket_key = f"{tool_name}:{client_id}"
                self._buckets.pop(bucket_key, None)
                return {"reset": [tool_name]}
            else:
                keys_to_remove = [k for k in self._buckets if k.endswith(f":{client_id}")]
                for k in keys_to_remove:
                    del self._buckets[k]
                return {"reset": [k.split(":")[0] for k in keys_to_remove]}


# Singleton for MCP server dispatch
_LIMITER = ToolRateLimiter()


def check_rate_limit(tool_name: str, client_id: str = "default") -> dict:
    """Convenience wrapper for MCP dispatch — call before executing any rate-limited tool."""
    return _LIMITER.check(tool_name, client_id)


def get_rate_limit_status(tool_name: str = None, client_id: str = "default") -> dict:
    return _LIMITER.get_status(tool_name, client_id)


def reset_rate_limit(tool_name: str = None, client_id: str = "default") -> dict:
    return _LIMITER.reset(tool_name, client_id)
