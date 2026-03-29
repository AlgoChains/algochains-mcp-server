"""
AlgoChains MCP Server — middleware utilities.

Provides rate limiting, retry logic, and request/response logging
for all broker and marketplace HTTP calls.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Callable, TypeVar

from .errors import RateLimitError

logger = logging.getLogger("algochains_mcp.middleware")

F = TypeVar("F", bound=Callable[..., Any])


# ═══════════════════════════════════════════════════════════════════
# Rate limiter — token-bucket per broker
# ═══════════════════════════════════════════════════════════════════

@dataclass
class _Bucket:
    capacity: int = 60
    tokens: float = 60.0
    refill_rate: float = 1.0  # tokens per second
    last_refill: float = field(default_factory=time.monotonic)

    def consume(self) -> bool:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False

    @property
    def wait_time(self) -> float:
        if self.tokens >= 1:
            return 0.0
        return (1 - self.tokens) / self.refill_rate


class RateLimiter:
    """Per-key token-bucket rate limiter."""

    # Default rate limits per broker (requests per minute)
    DEFAULTS: dict[str, int] = {
        "alpaca": 200,
        "ibkr": 50,
        "oanda": 120,
        "traderspost": 30,
        "quantconnect": 20,
        "marketplace": 60,
    }

    def __init__(self, overrides: dict[str, int] | None = None):
        self._buckets: dict[str, _Bucket] = {}
        self._limits = {**self.DEFAULTS, **(overrides or {})}

    def _get_bucket(self, key: str) -> _Bucket:
        if key not in self._buckets:
            rpm = self._limits.get(key, 60)
            self._buckets[key] = _Bucket(
                capacity=rpm,
                tokens=float(rpm),
                refill_rate=rpm / 60.0,
            )
        return self._buckets[key]

    async def acquire(self, key: str) -> None:
        bucket = self._get_bucket(key)
        if bucket.consume():
            return
        wait = bucket.wait_time
        if wait > 10:
            raise RateLimitError(
                f"Rate limit exceeded for '{key}' (wait {wait:.1f}s)",
                retry_after=int(wait),
            )
        logger.debug("Rate limiter: waiting %.2fs for %s", wait, key)
        await asyncio.sleep(wait)
        bucket.consume()

    def reset(self, key: str | None = None) -> None:
        if key:
            self._buckets.pop(key, None)
        else:
            self._buckets.clear()


# Global rate limiter instance
_limiter = RateLimiter()


def get_rate_limiter() -> RateLimiter:
    return _limiter


# ═══════════════════════════════════════════════════════════════════
# Retry decorator — exponential backoff for transient errors
# ═══════════════════════════════════════════════════════════════════

def retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retryable: tuple[type[Exception], ...] = (),
):
    """
    Async retry with exponential backoff.

    Usage:
        @retry(max_attempts=3, retryable=(httpx.ConnectError, httpx.TimeoutException))
        async def fetch_data():
            ...
    """
    def decorator(func: F) -> F:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except retryable as e:
                    last_exc = e
                    if attempt == max_attempts:
                        break
                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    logger.warning(
                        "Retry %d/%d for %s after %.1fs: %s",
                        attempt, max_attempts, func.__name__, delay, e,
                    )
                    await asyncio.sleep(delay)
            raise last_exc  # type: ignore[misc]
        return wrapper  # type: ignore[return-value]
    return decorator


# ═══════════════════════════════════════════════════════════════════
# Request logger — structured logging for tool calls
# ═══════════════════════════════════════════════════════════════════

class ToolCallLogger:
    """Logs tool invocations with timing and outcome."""

    def __init__(self):
        self._history: list[dict] = []
        self._max_history = 500

    def log_call(
        self,
        tool: str,
        arguments: dict,
        result: Any = None,
        error: str | None = None,
        duration_ms: float = 0,
    ) -> None:
        entry = {
            "tool": tool,
            "arguments": {k: v for k, v in arguments.items() if k != "backtest_code"},
            "success": error is None,
            "duration_ms": round(duration_ms, 1),
            "timestamp": time.time(),
        }
        if error:
            entry["error"] = error
        self._history.append(entry)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        if error:
            logger.error("Tool %s failed in %.1fms: %s", tool, duration_ms, error)
        else:
            logger.info("Tool %s completed in %.1fms", tool, duration_ms)

    def recent(self, n: int = 20) -> list[dict]:
        return self._history[-n:]

    def stats(self) -> dict:
        if not self._history:
            return {"total_calls": 0}
        total = len(self._history)
        errors = sum(1 for h in self._history if not h["success"])
        durations = [h["duration_ms"] for h in self._history]
        return {
            "total_calls": total,
            "error_count": errors,
            "error_rate": round(errors / total * 100, 1),
            "avg_duration_ms": round(sum(durations) / len(durations), 1),
            "max_duration_ms": round(max(durations), 1),
        }


_tool_logger = ToolCallLogger()


def get_tool_logger() -> ToolCallLogger:
    return _tool_logger
