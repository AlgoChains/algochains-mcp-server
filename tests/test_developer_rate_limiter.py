"""
Tests for developer_rate_limiter.py — burst, RPM, RPH limits, and body size.
"""
import time
from unittest.mock import patch

import pytest

from algochains_mcp.developer_rate_limiter import (
    MAX_BODY_BYTES,
    BURST_LIMIT,
    RPH_LIMIT,
    RPM_LIMIT,
    RateLimitResult,
    _REGISTRY,
    check_rate_limit,
    gc_stale_entries,
)


@pytest.fixture(autouse=True)
def clear_registry():
    """Reset rate limit state before each test."""
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()


class TestRateLimitAllowed:
    def test_first_request_allowed(self):
        result = check_rate_limit("a" * 64)
        assert result.allowed is True

    def test_within_burst_allowed(self):
        key = "b" * 64
        # Only test up to the burst limit since RPM_LIMIT > BURST_LIMIT
        for _ in range(BURST_LIMIT - 1):
            result = check_rate_limit(key)
            assert result.allowed is True

    def test_remaining_counts_decrease(self):
        key = "c" * 64
        r1 = check_rate_limit(key)
        r2 = check_rate_limit(key)
        assert r2.remaining_rpm < r1.remaining_rpm


class TestBurstLimit:
    def test_burst_exceeded_returns_not_allowed(self):
        key = "d" * 64
        results = [check_rate_limit(key) for _ in range(BURST_LIMIT + 5)]
        blocked = [r for r in results if not r.allowed]
        assert len(blocked) >= 1
        for r in blocked:
            assert r.retry_after_ms >= 1000

    def test_burst_error_includes_reason(self):
        key = "e" * 64
        results = [check_rate_limit(key) for _ in range(BURST_LIMIT + 2)]
        blocked = [r for r in results if not r.allowed]
        if blocked:
            assert "burst" in blocked[0].reason


class TestRetryAfter:
    def test_retry_after_positive_when_blocked(self):
        key = "f" * 64
        results = [check_rate_limit(key) for _ in range(BURST_LIMIT + 5)]
        blocked = [r for r in results if not r.allowed]
        for r in blocked:
            assert r.retry_after_ms > 0

    def test_error_dict_format(self):
        key = "g" * 64
        results = [check_rate_limit(key) for _ in range(BURST_LIMIT + 5)]
        blocked = next((r for r in results if not r.allowed), None)
        if blocked:
            d = blocked.as_error_dict()
            assert d["error"] == "rate_limit_exceeded"
            assert "retry_after_ms" in d
            assert "limits" in d


class TestBodySizeConstant:
    def test_max_body_bytes_positive(self):
        assert MAX_BODY_BYTES > 0

    def test_max_body_bytes_reasonable(self):
        # Should be at least 64 KB and at most 10 MB
        assert 64 * 1024 <= MAX_BODY_BYTES <= 10 * 1024 * 1024


class TestGarbageCollection:
    def test_gc_removes_nothing_when_empty(self):
        _REGISTRY.clear()
        pruned = gc_stale_entries()
        assert pruned == 0

    def test_gc_does_not_crash_with_active_entries(self):
        for i in range(5):
            check_rate_limit(f"{i:064d}")
        gc_stale_entries()  # should not raise


class TestDifferentKeys:
    def test_different_keys_tracked_independently(self):
        key1 = "1" * 64
        key2 = "2" * 64
        for _ in range(BURST_LIMIT + 2):
            check_rate_limit(key1)
        # key2 should still be allowed
        result = check_rate_limit(key2)
        assert result.allowed is True
