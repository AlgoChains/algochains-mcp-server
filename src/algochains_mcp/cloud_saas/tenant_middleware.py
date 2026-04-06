"""
Per-Tenant Rate Limiting + Immutable Audit Log.

Per-tenant limits enforced per API key in an in-memory token-bucket.
Optionally Redis-backed for distributed deployments.

Audit log:
  - Immutable JSONL append-only log at ~/.algochains/audit.jsonl
  - Every MCP tool call is recorded with: timestamp, tenant_id, tool, params (sanitized), result_status
  - Sensitive values redacted by _KEY_PATTERNS before writing
  - JSONL format enables easy compliance exports (CSV, BigQuery, Splunk)

Sandbox environments:
  - Per-tenant sandboxes are isolated execution contexts
  - Paper broker only — no real money in sandboxes
  - Lifecycle: create → activate → operations → destroy
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("algochains_mcp.cloud_saas.tenant")

AUDIT_LOG_PATH = Path.home() / ".algochains" / "audit.jsonl"

# Redaction patterns — matches the main server's _KEY_PATTERNS set
_KEY_PATTERNS = [
    re.compile(r'(sk[_-](live|test|prod|staging|ant)[_-][A-Za-z0-9]{20,})', re.I),
    re.compile(r'(xox[baprs][_-][A-Za-z0-9\-]{10,})', re.I),
    re.compile(r'([a-zA-Z0-9_\-]{20,}\.ey[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,})'),
    re.compile(r'(APCA[_-]API[_-][A-Z]{6}[_-]KEY[=:]\s*[A-Za-z0-9]{20,})', re.I),
    re.compile(r'(access[_-]?token["\s:=]+[A-Za-z0-9._\-]{20,})', re.I),
    re.compile(r'(refresh[_-]?token["\s:=]+[A-Za-z0-9._\-]{20,})', re.I),
    re.compile(r'(password["\s:=]+\S+)', re.I),
    re.compile(r'(secret["\s:=]+[A-Za-z0-9._\-]{8,})', re.I),
    re.compile(r'(api[_-]key["\s:=]+[A-Za-z0-9._\-]{16,})', re.I),
]


def _redact(value: str) -> str:
    for pattern in _KEY_PATTERNS:
        value = pattern.sub("[REDACTED]", value)
    return value


def _sanitize_params(params: dict) -> dict:
    """Redact sensitive values from tool params before audit logging."""
    sanitized = {}
    for k, v in params.items():
        if any(word in k.lower() for word in ("key", "secret", "token", "password", "passphrase")):
            sanitized[k] = "[REDACTED]"
        elif isinstance(v, str):
            sanitized[k] = _redact(v)
        else:
            sanitized[k] = v
    return sanitized


@dataclass
class TenantLimits:
    """Rate limits and quotas for a tenant tier."""
    requests_per_minute: int
    requests_per_day: int
    max_positions: int
    max_notional_usd: float
    allowed_tools: list[str] | None = None  # None = all tools allowed

    def to_dict(self) -> dict[str, Any]:
        return {
            "requests_per_minute": self.requests_per_minute,
            "requests_per_day": self.requests_per_day,
            "max_positions": self.max_positions,
            "max_notional_usd": self.max_notional_usd,
            "allowed_tools": self.allowed_tools or "all",
        }


TIER_LIMITS: dict[str, TenantLimits] = {
    "free": TenantLimits(
        requests_per_minute=10,
        requests_per_day=500,
        max_positions=3,
        max_notional_usd=5000,
    ),
    "starter": TenantLimits(
        requests_per_minute=60,
        requests_per_day=5000,
        max_positions=10,
        max_notional_usd=25000,
    ),
    "pro": TenantLimits(
        requests_per_minute=300,
        requests_per_day=50000,
        max_positions=25,
        max_notional_usd=250000,
    ),
    "enterprise": TenantLimits(
        requests_per_minute=3000,
        requests_per_day=1000000,
        max_positions=999,
        max_notional_usd=float("inf"),
    ),
}


@dataclass
class _TokenBucket:
    capacity: int
    tokens: float
    refill_rate: float
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


class TenantMiddleware:
    """
    Per-tenant rate limiting and immutable audit logging.

    Rate limiting: in-memory token bucket per tenant_id.
    Upgrade path: Redis-backed for multi-instance deployments
    (set REDIS_URL env var for automatic Redis usage).

    Audit log: append-only JSONL at ~/.algochains/audit.jsonl.
    """

    def __init__(self) -> None:
        AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._buckets: dict[str, _TokenBucket] = {}
        self._daily_counts: dict[str, tuple[int, str]] = {}  # tenant_id → (count, date_str)
        self._tenant_tiers: dict[str, str] = {}
        self._tenant_limits: dict[str, TenantLimits] = {}
        self._sandboxes: dict[str, dict[str, Any]] = {}
        self._redis = None
        self._init_redis()

    def _init_redis(self) -> None:
        redis_url = os.environ.get("REDIS_URL", "")
        if not redis_url:
            return
        try:
            import redis
            self._redis = redis.from_url(redis_url, decode_responses=True)
            self._redis.ping()
            logger.info("TenantMiddleware: Redis connected at %s", redis_url)
        except Exception as exc:
            logger.warning("Redis unavailable (%s) — using in-memory rate limiting", exc)
            self._redis = None

    def set_tenant_tier(self, tenant_id: str, tier: str) -> dict[str, Any]:
        """Set a tenant's rate limit tier."""
        if tier not in TIER_LIMITS:
            raise ValueError(f"Unknown tier '{tier}'. Valid: {', '.join(TIER_LIMITS)}")
        self._tenant_tiers[tenant_id] = tier
        limits = TIER_LIMITS[tier]
        self._tenant_limits[tenant_id] = limits
        # Reset bucket to new limits
        self._buckets[tenant_id] = _TokenBucket(
            capacity=limits.requests_per_minute,
            tokens=float(limits.requests_per_minute),
            refill_rate=limits.requests_per_minute / 60.0,
        )
        return {
            "tenant_id": tenant_id,
            "tier": tier,
            "limits": limits.to_dict(),
        }

    def get_tenant_limits(self, tenant_id: str) -> TenantLimits:
        return self._tenant_limits.get(tenant_id, TIER_LIMITS["free"])

    def check_rate_limit(self, tenant_id: str, tool_name: str) -> dict[str, Any]:
        """
        Check if tenant is within rate limits.
        Returns {"allowed": True/False, "reason": str}.
        """
        limits = self.get_tenant_limits(tenant_id)

        # Check tool allowlist
        if limits.allowed_tools and tool_name not in limits.allowed_tools:
            return {"allowed": False, "reason": f"Tool '{tool_name}' not allowed on current plan."}

        # Redis-backed rate limiting (distributed)
        if self._redis:
            try:
                key = f"rl:{tenant_id}:minute"
                count = self._redis.incr(key)
                if count == 1:
                    self._redis.expire(key, 60)
                if count > limits.requests_per_minute:
                    return {
                        "allowed": False,
                        "reason": f"Rate limit exceeded: {count}/{limits.requests_per_minute} req/min.",
                        "retry_after_seconds": 60,
                    }
                return {"allowed": True, "count": count, "limit": limits.requests_per_minute}
            except Exception:
                pass  # fallback to in-memory

        # In-memory token bucket
        if tenant_id not in self._buckets:
            self._buckets[tenant_id] = _TokenBucket(
                capacity=limits.requests_per_minute,
                tokens=float(limits.requests_per_minute),
                refill_rate=limits.requests_per_minute / 60.0,
            )

        bucket = self._buckets[tenant_id]
        if not bucket.consume():
            return {
                "allowed": False,
                "reason": f"Rate limit exceeded: {limits.requests_per_minute} req/min.",
                "retry_after_seconds": max(1, int(1 / bucket.refill_rate)),
            }

        # Daily limit check
        today = time.strftime("%Y-%m-%d")
        daily_count, date_str = self._daily_counts.get(tenant_id, (0, today))
        if date_str != today:
            daily_count, date_str = 0, today
        daily_count += 1
        self._daily_counts[tenant_id] = (daily_count, date_str)
        if daily_count > limits.requests_per_day:
            return {
                "allowed": False,
                "reason": f"Daily quota exceeded: {daily_count}/{limits.requests_per_day} req/day.",
            }

        return {"allowed": True, "rpm_count": daily_count}

    def audit_log(
        self,
        tenant_id: str,
        tool_name: str,
        params: dict,
        result_status: str,
        error: str | None = None,
        latency_ms: float | None = None,
    ) -> None:
        """
        Write immutable audit log entry.

        Format: newline-delimited JSON (JSONL), append-only.
        Sensitive values automatically redacted.
        """
        entry = {
            "event_id": str(uuid.uuid4()),
            "ts": time.time(),
            "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "tenant_id": tenant_id,
            "tool": tool_name,
            "params_sanitized": _sanitize_params(params),
            "result_status": result_status,  # "success" | "error" | "rate_limited"
            "error": error,
            "latency_ms": latency_ms,
        }
        try:
            with open(AUDIT_LOG_PATH, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as exc:
            logger.error("Failed to write audit log: %s", exc)

    def export_audit_log(
        self,
        tenant_id: str | None = None,
        start_ts: float | None = None,
        end_ts: float | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """
        Export audit log entries for compliance review.

        Filters by tenant_id and time range.
        """
        if not AUDIT_LOG_PATH.exists():
            return []

        entries = []
        with open(AUDIT_LOG_PATH) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if tenant_id and entry.get("tenant_id") != tenant_id:
                        continue
                    if start_ts and entry.get("ts", 0) < start_ts:
                        continue
                    if end_ts and entry.get("ts", 0) > end_ts:
                        continue
                    entries.append(entry)
                except Exception:
                    continue

        return entries[-limit:]  # Return last N entries

    def get_tenant_usage(self, tenant_id: str) -> dict[str, Any]:
        """Get real-time usage stats for a tenant."""
        daily_count, date_str = self._daily_counts.get(tenant_id, (0, time.strftime("%Y-%m-%d")))
        limits = self.get_tenant_limits(tenant_id)
        return {
            "tenant_id": tenant_id,
            "tier": self._tenant_tiers.get(tenant_id, "free"),
            "today": date_str,
            "requests_today": daily_count,
            "daily_limit": limits.requests_per_day,
            "daily_remaining": max(0, limits.requests_per_day - daily_count),
            "limits": limits.to_dict(),
        }

    # ── Sandbox management ──────────────────────────────────────────────

    def create_tenant_sandbox(
        self,
        tenant_id: str,
        sandbox_name: str = "default",
    ) -> dict[str, Any]:
        """
        Create an isolated sandbox environment for a tenant.

        Sandboxes use paper-mode execution only.
        All positions, orders, and P&L are isolated per sandbox.
        """
        sandbox_id = f"{tenant_id}_{sandbox_name}_{uuid.uuid4().hex[:8]}"
        self._sandboxes[sandbox_id] = {
            "sandbox_id": sandbox_id,
            "tenant_id": tenant_id,
            "name": sandbox_name,
            "status": "active",
            "broker": "paper",  # Sandboxes ALWAYS use paper broker
            "created_at": time.time(),
            "positions": {},
            "orders": [],
            "cash_usd": 100_000.0,  # $100K paper capital per sandbox
            "realized_pnl": 0.0,
        }
        logger.info("Sandbox created: %s for tenant %s", sandbox_id, tenant_id)
        return {
            "sandbox_id": sandbox_id,
            "tenant_id": tenant_id,
            "name": sandbox_name,
            "status": "active",
            "paper_capital_usd": 100_000.0,
            "note": "Sandbox uses paper execution only. No real money.",
        }

    def destroy_tenant_sandbox(self, sandbox_id: str) -> dict[str, Any]:
        if sandbox_id not in self._sandboxes:
            raise ValueError(f"Sandbox {sandbox_id} not found.")
        del self._sandboxes[sandbox_id]
        return {"destroyed": True, "sandbox_id": sandbox_id}

    def list_tenant_sandboxes(self, tenant_id: str) -> list[dict[str, Any]]:
        return [
            {"sandbox_id": sid, "name": s["name"], "status": s["status"], "created_at": s["created_at"]}
            for sid, s in self._sandboxes.items()
            if s["tenant_id"] == tenant_id
        ]


_tenant_middleware: TenantMiddleware | None = None


def get_tenant_middleware() -> TenantMiddleware:
    global _tenant_middleware
    if _tenant_middleware is None:
        _tenant_middleware = TenantMiddleware()
    return _tenant_middleware
