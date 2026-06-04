"""
developer_rate_limiter.py — Per-key rate limiting for the MCP HTTP bridge.

Enforces per-developer-key request limits using an in-process sliding window
counter. Designed to be fast (< 1 µs on the hot path) and correct for the
single-process bridge deployment model.

Limits (all configurable via env):
  ALGOCHAINS_DEV_RPM        — requests per minute (default 60)
  ALGOCHAINS_DEV_RPH        — requests per hour (default 1000)
  ALGOCHAINS_DEV_MAX_BODY_KB — max POST body size in kilobytes (default 256 KB)
  ALGOCHAINS_DEV_BURST       — burst allowance above RPM in a 10s window (default 15)

Architecture notes:
  - Keyed by SHA-256 key hash (same as Supabase lookup) — never stores plaintext.
  - Uses a sliding window (deque of timestamps) per key+window combination.
  - Thread-safe via per-key lock.
  - Entries are GC'd on each check to prevent unbounded memory.
  - For multi-instance deployments, replace with a Redis token-bucket
    implementation (algochains-mcp-server issue #RATE-2).
"""
from __future__ import annotations

import collections
import logging
import os
import threading
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (ValueError, TypeError):
        return default


RPM_LIMIT = _int_env("ALGOCHAINS_DEV_RPM", 60)
RPH_LIMIT = _int_env("ALGOCHAINS_DEV_RPH", 1000)
BURST_LIMIT = _int_env("ALGOCHAINS_DEV_BURST", 15)
MAX_BODY_KB = _int_env("ALGOCHAINS_DEV_MAX_BODY_KB", 256)
MAX_BODY_BYTES = MAX_BODY_KB * 1024


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    remaining_rpm: int
    remaining_rph: int
    retry_after_ms: int
    reason: str = ""

    def as_error_dict(self) -> dict:
        return {
            "error": "rate_limit_exceeded",
            "reason": self.reason,
            "retry_after_ms": self.retry_after_ms,
            "limits": {
                "rpm": RPM_LIMIT,
                "rph": RPH_LIMIT,
                "burst_per_10s": BURST_LIMIT,
            },
        }


class _SlidingWindow:
    """Thread-safe sliding window counter for a single (key_hash, window_size) pair."""

    __slots__ = ("_lock", "_window_sec", "_deque")

    def __init__(self, window_sec: float) -> None:
        self._lock = threading.Lock()
        self._window_sec = window_sec
        self._deque: collections.deque[float] = collections.deque()

    def add_and_count(self, now: float) -> int:
        """Record a hit and return the current count within the window."""
        cutoff = now - self._window_sec
        with self._lock:
            # Prune expired entries
            while self._deque and self._deque[0] < cutoff:
                self._deque.popleft()
            self._deque.append(now)
            return len(self._deque)

    def peek_count(self, now: float) -> int:
        cutoff = now - self._window_sec
        with self._lock:
            while self._deque and self._deque[0] < cutoff:
                self._deque.popleft()
            return len(self._deque)

    def next_available(self, now: float) -> float:
        """Return the monotonic time at which one more request would be allowed."""
        cutoff = now - self._window_sec
        with self._lock:
            while self._deque and self._deque[0] < cutoff:
                self._deque.popleft()
            if len(self._deque) == 0:
                return now
            # The oldest timestamp will fall out of the window at:
            return self._deque[0] + self._window_sec


# Registry: key_hash → dict[window_label → _SlidingWindow]
_REGISTRY: dict[str, dict[str, _SlidingWindow]] = {}
_REGISTRY_LOCK = threading.Lock()


def _get_windows(key_hash: str) -> dict[str, _SlidingWindow]:
    with _REGISTRY_LOCK:
        if key_hash not in _REGISTRY:
            _REGISTRY[key_hash] = {
                "minute": _SlidingWindow(60.0),
                "hour": _SlidingWindow(3600.0),
                "burst": _SlidingWindow(10.0),
            }
        return _REGISTRY[key_hash]


def check_rate_limit(key_hash: str) -> RateLimitResult:
    """
    Check and record one request for the given key hash.

    Returns RateLimitResult with allowed=False if any limit is breached.
    The hit is recorded unconditionally — callers should check `allowed`
    before proceeding with the request.
    """
    now = time.monotonic()
    windows = _get_windows(key_hash)

    burst_count = windows["burst"].add_and_count(now)
    rpm_count = windows["minute"].add_and_count(now)
    rph_count = windows["hour"].add_and_count(now)

    if burst_count > BURST_LIMIT:
        retry_ms = int((windows["burst"].next_available(now) - now) * 1000) + 100
        log.warning("rate_limit: burst exceeded for key_hash=%s...%s count=%d", key_hash[:8], key_hash[-4:], burst_count)
        return RateLimitResult(
            allowed=False,
            remaining_rpm=max(0, RPM_LIMIT - rpm_count),
            remaining_rph=max(0, RPH_LIMIT - rph_count),
            retry_after_ms=max(retry_ms, 1000),
            reason=f"burst_limit_exceeded (burst_per_10s={BURST_LIMIT})",
        )

    if rpm_count > RPM_LIMIT:
        retry_ms = int((windows["minute"].next_available(now) - now) * 1000) + 100
        log.warning("rate_limit: RPM exceeded for key_hash=%s...%s count=%d", key_hash[:8], key_hash[-4:], rpm_count)
        return RateLimitResult(
            allowed=False,
            remaining_rpm=0,
            remaining_rph=max(0, RPH_LIMIT - rph_count),
            retry_after_ms=max(retry_ms, 1000),
            reason=f"rpm_limit_exceeded (limit={RPM_LIMIT})",
        )

    if rph_count > RPH_LIMIT:
        retry_ms = int((windows["hour"].next_available(now) - now) * 1000) + 100
        log.warning("rate_limit: RPH exceeded for key_hash=%s...%s count=%d", key_hash[:8], key_hash[-4:], rph_count)
        return RateLimitResult(
            allowed=False,
            remaining_rpm=max(0, RPM_LIMIT - rpm_count),
            remaining_rph=0,
            retry_after_ms=max(retry_ms, 60_000),
            reason=f"rph_limit_exceeded (limit={RPH_LIMIT})",
        )

    return RateLimitResult(
        allowed=True,
        remaining_rpm=max(0, RPM_LIMIT - rpm_count),
        remaining_rph=max(0, RPH_LIMIT - rph_count),
        retry_after_ms=0,
    )


def gc_stale_entries(max_entries: int = 50_000) -> int:
    """
    Remove entries for keys that haven't been used recently.

    Called periodically to bound memory. Returns number of entries pruned.
    """
    now = time.monotonic()
    staleness_threshold = 7200.0  # 2 hours
    pruned = 0
    with _REGISTRY_LOCK:
        stale_keys = []
        for key_hash, windows in _REGISTRY.items():
            hour_window = windows.get("hour")
            if hour_window and hour_window.peek_count(now) == 0:
                stale_keys.append(key_hash)
        for k in stale_keys:
            del _REGISTRY[k]
            pruned += 1
        # Hard limit: if still too many entries, drop oldest (not LRU, just truncate)
        if len(_REGISTRY) > max_entries:
            to_drop = list(_REGISTRY.keys())[: len(_REGISTRY) - max_entries]
            for k in to_drop:
                del _REGISTRY[k]
                pruned += 1
    return pruned
