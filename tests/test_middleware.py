"""Tests for rate limiting, retry, and tool call logging middleware."""
import asyncio
import time

import pytest

from algochains_mcp.errors import RateLimitError
from algochains_mcp.middleware import RateLimiter, ToolCallLogger, retry


class TestRateLimiter:
    def test_initial_tokens_available(self):
        limiter = RateLimiter(overrides={"test": 10})
        bucket = limiter._get_bucket("test")
        assert bucket.consume() is True

    def test_exhausts_tokens(self):
        limiter = RateLimiter(overrides={"test": 2})
        bucket = limiter._get_bucket("test")
        assert bucket.consume() is True
        assert bucket.consume() is True
        assert bucket.consume() is False

    @pytest.mark.asyncio
    async def test_acquire_succeeds(self):
        limiter = RateLimiter(overrides={"fast": 100})
        await limiter.acquire("fast")  # should not raise

    @pytest.mark.asyncio
    async def test_acquire_raises_on_exhaustion(self):
        limiter = RateLimiter(overrides={"slow": 1})
        await limiter.acquire("slow")  # first call OK
        # Exhaust remaining and force long wait
        limiter._get_bucket("slow").tokens = 0
        limiter._get_bucket("slow").refill_rate = 0.01  # very slow refill
        with pytest.raises(RateLimitError):
            await limiter.acquire("slow")

    def test_reset_clears_bucket(self):
        limiter = RateLimiter(overrides={"x": 5})
        limiter._get_bucket("x")
        assert "x" in limiter._buckets
        limiter.reset("x")
        assert "x" not in limiter._buckets

    def test_reset_all(self):
        limiter = RateLimiter()
        limiter._get_bucket("a")
        limiter._get_bucket("b")
        limiter.reset()
        assert len(limiter._buckets) == 0

    def test_default_limits(self):
        limiter = RateLimiter()
        assert limiter._limits["alpaca"] == 200
        assert limiter._limits["ibkr"] == 50
        assert limiter._limits["oanda"] == 120

    def test_overrides(self):
        limiter = RateLimiter(overrides={"alpaca": 500})
        assert limiter._limits["alpaca"] == 500
        assert limiter._limits["ibkr"] == 50  # unchanged


class TestToolCallLogger:
    def test_log_success(self):
        logger = ToolCallLogger()
        logger.log_call("place_order", {"broker": "alpaca"}, duration_ms=50.0)
        assert len(logger.recent()) == 1
        assert logger.recent()[0]["success"] is True

    def test_log_error(self):
        logger = ToolCallLogger()
        logger.log_call("place_order", {"broker": "alpaca"}, error="boom", duration_ms=10.0)
        assert logger.recent()[0]["success"] is False
        assert logger.recent()[0]["error"] == "boom"

    def test_stats_empty(self):
        logger = ToolCallLogger()
        assert logger.stats() == {"total_calls": 0}

    def test_stats_with_data(self):
        logger = ToolCallLogger()
        logger.log_call("a", {}, duration_ms=10.0)
        logger.log_call("b", {}, duration_ms=20.0)
        logger.log_call("c", {}, error="fail", duration_ms=30.0)
        s = logger.stats()
        assert s["total_calls"] == 3
        assert s["error_count"] == 1
        assert s["avg_duration_ms"] == 20.0
        assert s["max_duration_ms"] == 30.0
        assert s["error_rate"] == pytest.approx(33.3, abs=0.1)

    def test_max_history_cap(self):
        logger = ToolCallLogger()
        logger._max_history = 5
        for i in range(10):
            logger.log_call(f"tool_{i}", {}, duration_ms=1.0)
        assert len(logger._history) == 5
        assert logger._history[0]["tool"] == "tool_5"

    def test_backtest_code_stripped(self):
        logger = ToolCallLogger()
        logger.log_call("submit_strategy", {"symbol": "AAPL", "backtest_code": "SECRET"}, duration_ms=1.0)
        assert "backtest_code" not in logger.recent()[0]["arguments"]
        assert logger.recent()[0]["arguments"]["symbol"] == "AAPL"


class TestRetryDecorator:
    @pytest.mark.asyncio
    async def test_succeeds_first_try(self):
        call_count = 0

        @retry(max_attempts=3, retryable=(ValueError,))
        async def good():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await good()
        assert result == "ok"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retries_on_failure(self):
        call_count = 0

        @retry(max_attempts=3, base_delay=0.01, retryable=(ValueError,))
        async def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("transient")
            return "recovered"

        result = await flaky()
        assert result == "recovered"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_raises_after_max_attempts(self):
        @retry(max_attempts=2, base_delay=0.01, retryable=(ValueError,))
        async def always_fails():
            raise ValueError("permanent")

        with pytest.raises(ValueError, match="permanent"):
            await always_fails()

    @pytest.mark.asyncio
    async def test_non_retryable_raises_immediately(self):
        call_count = 0

        @retry(max_attempts=3, base_delay=0.01, retryable=(ValueError,))
        async def wrong_error():
            nonlocal call_count
            call_count += 1
            raise TypeError("not retryable")

        with pytest.raises(TypeError):
            await wrong_error()
        assert call_count == 1
