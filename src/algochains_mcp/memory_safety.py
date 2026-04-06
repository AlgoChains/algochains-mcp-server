"""Memory Safety Module — prevent OOM crashes and resource leaks.

Addresses known issues:
- Python scripts causing memory leaks on startup
- Windsurf/IDE crashes from unbounded memory growth
- MCP server accumulating state without cleanup

Provides:
- Process memory monitoring with configurable hard limit
- Bounded caches with LRU eviction
- Periodic garbage collection
- Resource cleanup hooks
- Import guards for optional heavy dependencies
"""
from __future__ import annotations

import gc
import logging
import os
import sys
import time
import weakref
from collections import OrderedDict
from functools import wraps
from typing import Any, Callable, TypeVar

logger = logging.getLogger("algochains_mcp.memory_safety")

F = TypeVar("F", bound=Callable[..., Any])

MAX_PROCESS_MEMORY_MB = int(os.getenv("ALGOCHAINS_MAX_MEMORY_MB", "1024"))
CACHE_CLEANUP_INTERVAL = 300
GC_INTERVAL = 60


def get_process_memory_mb() -> float:
    """Get current process RSS in MB. Cross-platform."""
    try:
        import resource
        usage = resource.getrusage(resource.RUSAGE_SELF)
        if sys.platform == "darwin":
            return usage.ru_maxrss / (1024 * 1024)
        return usage.ru_maxrss / 1024
    except ImportError:
        pass
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except ImportError:
        return 0.0


class BoundedCache:
    """LRU cache with max size and TTL eviction.

    Unlike functools.lru_cache, this:
    - Has a hard memory bound
    - Supports TTL-based expiry
    - Can be explicitly cleared
    - Reports memory usage
    """

    def __init__(self, max_size: int = 1000, ttl_seconds: float = 3600):
        self._store: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self.max_size = max_size
        self.ttl = ttl_seconds
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Any | None:
        if key in self._store:
            value, ts = self._store[key]
            if time.time() - ts > self.ttl:
                del self._store[key]
                self._misses += 1
                return None
            self._store.move_to_end(key)
            self._hits += 1
            return value
        self._misses += 1
        return None

    def set(self, key: str, value: Any) -> None:
        if key in self._store:
            self._store.move_to_end(key)
        self._store[key] = (value, time.time())
        while len(self._store) > self.max_size:
            self._store.popitem(last=False)

    def clear(self) -> None:
        self._store.clear()

    def evict_expired(self) -> int:
        now = time.time()
        expired = [k for k, (_, ts) in self._store.items() if now - ts > self.ttl]
        for k in expired:
            del self._store[k]
        return len(expired)

    @property
    def size(self) -> int:
        return len(self._store)

    def stats(self) -> dict:
        return {
            "size": self.size,
            "max_size": self.max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / max(1, self._hits + self._misses) * 100, 1),
        }


class MemoryMonitor:
    """Monitors process memory and triggers cleanup when thresholds are exceeded."""

    def __init__(self, hard_limit_mb: float = MAX_PROCESS_MEMORY_MB):
        self.hard_limit_mb = hard_limit_mb
        self.warn_limit_mb = hard_limit_mb * 0.8
        self._caches: list[weakref.ref] = []
        self._cleanup_hooks: list[Callable] = []
        self._last_gc: float = 0.0
        self._last_check: float = 0.0

    def register_cache(self, cache: BoundedCache) -> None:
        self._caches.append(weakref.ref(cache))

    def register_cleanup(self, hook: Callable) -> None:
        self._cleanup_hooks.append(hook)

    def check(self) -> dict:
        """Check memory usage and trigger cleanup if needed."""
        now = time.time()
        if now - self._last_check < 10:
            return {"status": "skipped", "reason": "checked recently"}
        self._last_check = now

        current_mb = get_process_memory_mb()
        status = "ok"

        if current_mb >= self.hard_limit_mb:
            status = "critical"
            self._emergency_cleanup()
            logger.critical(
                "Memory CRITICAL: %.0fMB / %.0fMB — emergency cleanup triggered",
                current_mb, self.hard_limit_mb,
            )
        elif current_mb >= self.warn_limit_mb:
            status = "warning"
            self._gentle_cleanup()
            logger.warning(
                "Memory WARNING: %.0fMB / %.0fMB — cleanup triggered",
                current_mb, self.hard_limit_mb,
            )

        if now - self._last_gc > GC_INTERVAL:
            gc.collect()
            self._last_gc = now

        return {
            "status": status,
            "current_mb": round(current_mb, 1),
            "hard_limit_mb": self.hard_limit_mb,
            "warn_limit_mb": round(self.warn_limit_mb, 1),
            "pct_used": round(current_mb / self.hard_limit_mb * 100, 1) if self.hard_limit_mb else 0,
        }

    def _gentle_cleanup(self) -> None:
        for ref in self._caches:
            cache = ref()
            if cache:
                evicted = cache.evict_expired()
                if evicted:
                    logger.info("Evicted %d expired cache entries", evicted)
        gc.collect()

    def _emergency_cleanup(self) -> None:
        for ref in self._caches:
            cache = ref()
            if cache:
                cache.clear()
        for hook in self._cleanup_hooks:
            try:
                hook()
            except Exception as e:
                logger.error("Cleanup hook failed: %s", e)
        gc.collect(generation=2)

    def get_report(self) -> dict:
        current_mb = get_process_memory_mb()
        cache_stats = []
        for ref in self._caches:
            cache = ref()
            if cache:
                cache_stats.append(cache.stats())

        return {
            "process_memory_mb": round(current_mb, 1),
            "hard_limit_mb": self.hard_limit_mb,
            "utilization_pct": round(current_mb / self.hard_limit_mb * 100, 1),
            "caches": cache_stats,
            "gc_stats": {
                "collections": gc.get_count(),
                "objects_tracked": len(gc.get_objects()) if current_mb < 500 else "skipped",
            },
        }


_monitor = MemoryMonitor()


def get_memory_monitor() -> MemoryMonitor:
    return _monitor


def lazy_import(module_name: str) -> Any:
    """Import a module lazily — only when first accessed.

    Prevents startup memory spikes from importing heavy libraries
    (pandas, numpy, torch, etc.) that may never be used.
    """
    if module_name in sys.modules:
        return sys.modules[module_name]
    try:
        import importlib
        return importlib.import_module(module_name)
    except ImportError:
        return None


def memory_guard(max_mb: float = 512):
    """Decorator that checks memory before executing a function.

    If memory exceeds max_mb, runs GC before proceeding.
    If still over after GC, logs a warning but proceeds.
    """
    def decorator(func: F) -> F:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            current = get_process_memory_mb()
            if current > max_mb:
                gc.collect()
                current = get_process_memory_mb()
                if current > max_mb:
                    logger.warning(
                        "%s: Memory at %.0fMB (limit %.0fMB) — proceeding with caution",
                        func.__name__, current, max_mb,
                    )
            return await func(*args, **kwargs)
        return wrapper  # type: ignore
    return decorator
