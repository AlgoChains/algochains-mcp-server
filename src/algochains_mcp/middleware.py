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

from .errors import InputValidationError, RateLimitError

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

    # Default rate limits per broker / category (requests per minute)
    DEFAULTS: dict[str, int] = {
        "alpaca": 200,
        "ibkr": 50,
        "oanda": 120,
        "traderspost": 30,
        "quantconnect": 20,
        "marketplace": 60,
        "v10_ml": 30,
        "v11_execution": 20,
        "v12_analytics": 60,
        "v13_alt_data": 30,
        "v14_agent_swarm": 20,
        "v15_defi": 30,
        "v16_cloud": 30,
        "v17_physical_events": 60,
        # Avi's cricket-bot partner box is small — keep our footprint polite.
        "cricket_bot": 30,
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
# Tool-name → rate-limit category mapping for V10-V16
# ═══════════════════════════════════════════════════════════════════

TOOL_RATE_LIMIT_CATEGORY: dict[str, str] = {
    # V10: ML/AI
    "create_feature_set": "v10_ml", "compute_features": "v10_ml",
    "list_feature_sets": "v10_ml", "get_feature_importance": "v10_ml",
    "train_model": "v10_ml", "evaluate_model": "v10_ml",
    "predict": "v10_ml", "explain_prediction": "v10_ml",
    "register_model": "v10_ml", "promote_model": "v10_ml",
    "list_models": "v10_ml", "compare_models": "v10_ml",
    "archive_model": "v10_ml", "create_rl_agent": "v10_ml",
    "train_rl_agent": "v10_ml", "evaluate_rl_agent": "v10_ml",
    "get_rl_agent_state": "v10_ml", "dispatch_gpu_task": "v10_ml",
    "gpu_status": "v10_ml", "generate_strategy_spec": "v10_ml",
    # V11: Execution
    "validate_order": "v11_execution", "submit_order": "v11_execution",
    "get_order_status": "v11_execution", "smart_route_order": "v11_execution",
    "get_venue_analytics": "v11_execution", "start_algo": "v11_execution",
    "stop_algo": "v11_execution", "get_algo_status": "v11_execution",
    "fix_connect": "v11_execution", "fix_disconnect": "v11_execution",
    "fix_session_status": "v11_execution", "tca_analyze": "v11_execution",
    "tca_report": "v11_execution", "tca_implementation_shortfall": "v11_execution",
    "register_venue": "v11_execution", "list_venues": "v11_execution",
    "get_venue_status": "v11_execution", "set_venue_priority": "v11_execution",
    # V12: Real-Time Analytics
    "start_pnl_stream": "v12_analytics", "get_pnl_snapshot": "v12_analytics",
    "get_pnl_history": "v12_analytics", "analyze_order_flow": "v12_analytics",
    "get_order_flow_heatmap": "v12_analytics", "get_volume_profile": "v12_analytics",
    "analyze_microstructure": "v12_analytics", "get_toxicity": "v12_analytics",
    "detect_regime": "v12_analytics", "get_regime_history": "v12_analytics",
    "get_transition_matrix": "v12_analytics", "create_alert": "v12_analytics",
    "list_alerts": "v12_analytics", "delete_alert": "v12_analytics",
    "get_alert_history": "v12_analytics",
    # V13: Alt Data
    "analyze_sentiment": "v13_alt_data", "get_sentiment_history": "v13_alt_data",
    "get_sentiment_signal": "v13_alt_data", "analyze_satellite": "v13_alt_data",
    "get_satellite_timeseries": "v13_alt_data", "scrape_web": "v13_alt_data",
    "list_scrape_jobs": "v13_alt_data", "get_scrape_results": "v13_alt_data",
    "analyze_sec_filing": "v13_alt_data", "get_insider_trades": "v13_alt_data",
    "get_institutional_holdings": "v13_alt_data", "analyze_social_media": "v13_alt_data",
    "get_social_momentum": "v13_alt_data", "get_social_feed": "v13_alt_data",
    "browse_alt_data": "v13_alt_data", "subscribe_alt_data": "v13_alt_data",
    "get_alt_data_catalog": "v13_alt_data",
    # V14: Agent Swarm
    "create_agent": "v14_agent_swarm", "list_agents": "v14_agent_swarm",
    "get_agent_status": "v14_agent_swarm", "send_agent_message": "v14_agent_swarm",
    "create_swarm": "v14_agent_swarm", "get_swarm_status": "v14_agent_swarm",
    "assign_swarm_task": "v14_agent_swarm", "create_consensus": "v14_agent_swarm",
    "get_consensus_result": "v14_agent_swarm", "create_workflow": "v14_agent_swarm",
    "execute_workflow": "v14_agent_swarm", "get_workflow_status": "v14_agent_swarm",
    "get_agent_memory": "v14_agent_swarm", "store_agent_memory": "v14_agent_swarm",
    "search_agent_memory": "v14_agent_swarm",
    # V15: DeFi
    "get_defi_positions": "v15_defi", "execute_defi_swap": "v15_defi",
    "add_liquidity": "v15_defi", "remove_liquidity": "v15_defi",
    "get_yield_opportunities": "v15_defi", "start_yield_strategy": "v15_defi",
    "get_yield_performance": "v15_defi", "get_gas_estimate": "v15_defi",
    "get_gas_history": "v15_defi", "optimize_gas": "v15_defi",
    "scan_mev": "v15_defi", "protect_from_mev": "v15_defi",
    "get_cross_chain_routes": "v15_defi", "execute_cross_chain": "v15_defi",
    "get_bridge_status": "v15_defi",
    # V16: Cloud SaaS
    "create_tenant": "v16_cloud", "get_tenant_dashboard": "v16_cloud",
    "get_sub_account_status": "v16_cloud", "set_sub_account_permissions": "v16_cloud",
    # V17: Physical-world event intelligence
    "get_physical_event_sources": "v17_physical_events",
    "map_physical_event_assets": "v17_physical_events",
    "score_physical_event_alpha": "v17_physical_events",
    "get_sonia_air_heartbeat": "v17_physical_events",
    # Cricket bot (Avi's external partner API)
    "get_cricket_bot_performance": "cricket_bot",
    "get_cricket_bot_trades": "cricket_bot",
    "get_cricket_bot_matches": "cricket_bot",
    "get_cricket_bot_signals": "cricket_bot",
    "get_cricket_bot_tournaments": "cricket_bot",
}


def get_tool_category(tool_name: str) -> str | None:
    """Return the rate-limit category for a tool, or None if uncategorized."""
    return TOOL_RATE_LIMIT_CATEGORY.get(tool_name)


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
        _REDACT_KEYS = {
            "backtest_code", "secret", "api_key", "api_secret", "password",
            "token", "access_token", "refresh_token", "private_key",
            "secret_key", "signing_secret", "hmac_secret",
        }
        safe_args = {
            k: ("***REDACTED***" if k.lower() in _REDACT_KEYS else v)
            for k, v in arguments.items()
        }
        entry = {
            "tool": tool,
            "arguments": safe_args,
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


# ═══════════════════════════════════════════════════════════════════
# Input validation — sanitize and validate tool arguments
# ═══════════════════════════════════════════════════════════════════

MAX_STRING_LENGTH = 10_000
MAX_LIST_LENGTH = 1_000


def validate_arguments(tool_name: str, arguments: dict) -> dict:
    """Sanitize and validate tool arguments. Returns cleaned arguments.

    - Truncates oversized strings to MAX_STRING_LENGTH
    - Truncates oversized lists to MAX_LIST_LENGTH
    - Strips leading/trailing whitespace from string values
    - Strips unsigned internal auth-context keys (stdio/dynamic spoof guard)
    - Raises InputValidationError for missing required fields
    """
    from .security.internal_auth_context import strip_untrusted_internal_auth

    arguments = strip_untrusted_internal_auth(dict(arguments or {}))
    cleaned: dict[str, Any] = {}
    for key, value in arguments.items():
        if isinstance(value, str):
            value = value.strip()
            if len(value) > MAX_STRING_LENGTH:
                logger.warning("Tool %s arg '%s' truncated from %d to %d chars", tool_name, key, len(value), MAX_STRING_LENGTH)
                value = value[:MAX_STRING_LENGTH]
        elif isinstance(value, list) and len(value) > MAX_LIST_LENGTH:
            logger.warning("Tool %s arg '%s' truncated from %d to %d items", tool_name, key, len(value), MAX_LIST_LENGTH)
            value = value[:MAX_LIST_LENGTH]
        cleaned[key] = value
    return cleaned


def require_fields(tool_name: str, arguments: dict, *fields: str) -> None:
    """Raise InputValidationError if any required field is missing."""
    for f in fields:
        if f not in arguments or arguments[f] is None:
            raise InputValidationError(
                f"Tool '{tool_name}' requires argument '{f}'",
                tool=tool_name,
                field=f,
            )


# ═══════════════════════════════════════════════════════════════════
# Per-tool execution timeouts — prevent hanging tools from blocking
# ═══════════════════════════════════════════════════════════════════

TOOL_TIMEOUT_SECONDS: dict[str, float] = {
    "v10_ml": 120.0,           # ML training can be slow
    "v11_execution": 30.0,     # Order execution must be fast
    "v12_analytics": 60.0,     # Analytics queries
    "v13_alt_data": 90.0,      # Web scraping, API calls
    "v14_agent_swarm": 120.0,  # Agent orchestration
    "v15_defi": 60.0,          # On-chain calls
    "v16_cloud": 30.0,         # SaaS operations
}

DEFAULT_TOOL_TIMEOUT = 60.0


def get_tool_timeout(tool_name: str) -> float:
    """Return the execution timeout in seconds for a tool."""
    cat = get_tool_category(tool_name)
    if cat:
        return TOOL_TIMEOUT_SECONDS.get(cat, DEFAULT_TOOL_TIMEOUT)
    return DEFAULT_TOOL_TIMEOUT


# ═══════════════════════════════════════════════════════════════════
# Concurrency semaphores — bound parallel tool executions
# ═══════════════════════════════════════════════════════════════════

CONCURRENCY_LIMITS: dict[str, int] = {
    "v10_ml": 3,           # GPU-heavy, limit concurrency
    "v11_execution": 5,    # Order execution
    "v12_analytics": 10,   # Analytics can run in parallel
    "v13_alt_data": 5,     # External API calls
    "v14_agent_swarm": 3,  # Agent swarms are heavy
    "v15_defi": 5,         # On-chain ops
    "v16_cloud": 10,       # SaaS admin
}

DEFAULT_CONCURRENCY = 10

_semaphores: dict[str, asyncio.Semaphore] = {}


def get_tool_semaphore(tool_name: str) -> asyncio.Semaphore | None:
    """Return a concurrency semaphore for the tool's category, or None."""
    cat = get_tool_category(tool_name)
    if not cat:
        return None
    if cat not in _semaphores:
        limit = CONCURRENCY_LIMITS.get(cat, DEFAULT_CONCURRENCY)
        _semaphores[cat] = asyncio.Semaphore(limit)
    return _semaphores[cat]


# ═══════════════════════════════════════════════════════════════════
# Circuit breaker — stop hammering a failing engine
# ═══════════════════════════════════════════════════════════════════

@dataclass
class _CircuitState:
    failures: int = 0
    last_failure: float = 0.0
    open_until: float = 0.0

CIRCUIT_FAILURE_THRESHOLD = 5
CIRCUIT_COOLDOWN_SECONDS = 60.0

_circuits: dict[str, _CircuitState] = {}


class CircuitOpenError(Exception):
    """Raised when a circuit breaker is open (engine failing repeatedly)."""
    def __init__(self, category: str, retry_after: float):
        self.category = category
        self.retry_after = retry_after
        super().__init__(f"Circuit open for '{category}', retry in {retry_after:.0f}s")


def check_circuit(tool_name: str) -> None:
    """Raise CircuitOpenError if the tool's category circuit is open."""
    cat = get_tool_category(tool_name)
    if not cat:
        return
    state = _circuits.get(cat)
    if state and state.open_until > time.monotonic():
        retry_after = state.open_until - time.monotonic()
        raise CircuitOpenError(cat, retry_after)


def record_success(tool_name: str) -> None:
    """Reset the circuit breaker on success."""
    cat = get_tool_category(tool_name)
    if cat and cat in _circuits:
        _circuits[cat].failures = 0
        _circuits[cat].open_until = 0.0


def record_failure(tool_name: str) -> None:
    """Record a failure; open the circuit if threshold exceeded."""
    cat = get_tool_category(tool_name)
    if not cat:
        return
    if cat not in _circuits:
        _circuits[cat] = _CircuitState()
    state = _circuits[cat]
    state.failures += 1
    state.last_failure = time.monotonic()
    if state.failures >= CIRCUIT_FAILURE_THRESHOLD:
        state.open_until = time.monotonic() + CIRCUIT_COOLDOWN_SECONDS
        logger.warning(
            "Circuit OPEN for category '%s' after %d consecutive failures — cooldown %.0fs",
            cat, state.failures, CIRCUIT_COOLDOWN_SECONDS,
        )


# ═══════════════════════════════════════════════════════════════════
# Response size guard — prevent OOM from oversized tool outputs
# ═══════════════════════════════════════════════════════════════════

MAX_RESPONSE_BYTES = 1_048_576  # 1 MB


def guard_response_size(text: str, tool_name: str) -> str:
    """Truncate tool response if it exceeds MAX_RESPONSE_BYTES."""
    if len(text) > MAX_RESPONSE_BYTES:
        logger.warning(
            "Tool %s response truncated from %d to %d bytes",
            tool_name, len(text), MAX_RESPONSE_BYTES,
        )
        return text[:MAX_RESPONSE_BYTES] + f"\n... [TRUNCATED — response exceeded {MAX_RESPONSE_BYTES} bytes]"
    return text
