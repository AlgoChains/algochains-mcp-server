"""Tests for Memory Safety module."""
from algochains_mcp.memory_safety import (
    BoundedCache,
    MemoryMonitor,
    get_memory_monitor,
    get_process_memory_mb,
)


class TestBoundedCache:
    def test_basic_get_set(self):
        cache = BoundedCache(max_size=10)
        cache.set("key1", "value1")
        assert cache.get("key1") == "value1"

    def test_miss_returns_none(self):
        cache = BoundedCache()
        assert cache.get("nonexistent") is None

    def test_max_size_eviction(self):
        cache = BoundedCache(max_size=3)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)
        cache.set("d", 4)
        assert cache.size == 3
        assert cache.get("a") is None
        assert cache.get("d") == 4

    def test_lru_ordering(self):
        cache = BoundedCache(max_size=3)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)
        cache.get("a")
        cache.set("d", 4)
        assert cache.get("a") == 1
        assert cache.get("b") is None

    def test_clear(self):
        cache = BoundedCache()
        cache.set("key", "val")
        cache.clear()
        assert cache.size == 0
        assert cache.get("key") is None

    def test_stats(self):
        cache = BoundedCache()
        cache.set("a", 1)
        cache.get("a")
        cache.get("b")
        stats = cache.stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["size"] == 1


class TestMemoryMonitor:
    def test_get_process_memory(self):
        mb = get_process_memory_mb()
        assert mb >= 0

    def test_monitor_check(self):
        mon = MemoryMonitor(hard_limit_mb=4096)
        result = mon.check()
        assert result["status"] in ("ok", "warning", "critical", "skipped")

    def test_register_cache(self):
        mon = MemoryMonitor()
        cache = BoundedCache(max_size=10)
        mon.register_cache(cache)
        report = mon.get_report()
        assert len(report["caches"]) == 1

    def test_global_monitor(self):
        mon = get_memory_monitor()
        assert isinstance(mon, MemoryMonitor)
