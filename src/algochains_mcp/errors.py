"""
AlgoChains MCP Server — typed error hierarchy.

Provides structured exceptions for every failure domain so callers
(AI agents, CLI tools, tests) get machine-readable error context
instead of opaque tracebacks.
"""
from __future__ import annotations


class AlgoChainsError(Exception):
    """Base error for all AlgoChains MCP Server errors."""

    def __init__(self, message: str, *, details: dict | None = None):
        self.details = details or {}
        super().__init__(message)

    def to_dict(self) -> dict:
        return {
            "error_type": type(self).__name__,
            "message": str(self),
            "details": self.details,
        }


# ═══════════════════════════════════════════════════════════════════
# Broker errors
# ═══════════════════════════════════════════════════════════════════

class BrokerError(AlgoChainsError):
    """Base error for broker-related issues."""

    def __init__(self, message: str, broker: str = "", **kwargs):
        self.broker = broker
        super().__init__(
            f"[{broker}] {message}" if broker else message,
            **kwargs,
        )

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["broker"] = self.broker
        return d


class BrokerConnectionError(BrokerError):
    """Cannot connect to broker API (network, DNS, TLS)."""
    pass


class BrokerAuthError(BrokerError):
    """Authentication failed — invalid credentials, expired token, or revoked key."""
    pass


class BrokerOrderError(BrokerError):
    """Order placement failed — insufficient funds, invalid symbol, market closed, etc."""

    def __init__(self, message: str, broker: str = "", order_id: str = "", **kwargs):
        self.order_id = order_id
        super().__init__(message, broker=broker, **kwargs)


class BrokerQuoteError(BrokerError):
    """Cannot retrieve quote data — symbol not found, market data not subscribed."""
    pass


class BrokerNotConnectedError(BrokerError):
    """Broker is configured but not connected. Call connect_broker first."""
    pass


class BrokerNotConfiguredError(BrokerError):
    """Broker is not configured. Set required environment variables."""
    pass


# ═══════════════════════════════════════════════════════════════════
# Validation errors
# ═══════════════════════════════════════════════════════════════════

class ValidationError(AlgoChainsError):
    """Strategy validation failed at one or more gates."""

    def __init__(self, message: str, gate: str = "", score: float = 0, **kwargs):
        self.gate = gate
        self.score = score
        super().__init__(message, **kwargs)

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["gate"] = self.gate
        d["score"] = self.score
        return d


class SchemaValidationError(ValidationError):
    """Gate 1 — required fields missing or wrong type."""
    pass


class PerformanceValidationError(ValidationError):
    """Gate 2 — Sharpe, trades, or drawdown below thresholds."""
    pass


class OverfitValidationError(ValidationError):
    """Gate 3 — IS/OOS ratio too low or IS Sharpe suspiciously high."""
    pass


class MCPTValidationError(ValidationError):
    """Gate 4 — MCPT p-value too high, not statistically significant."""
    pass


class WalkForwardValidationError(ValidationError):
    """Gate 5 — insufficient folds or inconsistent OOS performance."""
    pass


# ═══════════════════════════════════════════════════════════════════
# Marketplace errors
# ═══════════════════════════════════════════════════════════════════

class MarketplaceError(AlgoChainsError):
    """Marketplace operation failed (listing, subscription, publishing)."""
    pass


class ListingNotFoundError(MarketplaceError):
    """Requested marketplace listing does not exist."""
    pass


class SubscriptionError(MarketplaceError):
    """Subscription creation/management failed."""
    pass


# ═══════════════════════════════════════════════════════════════════
# Rate limiting
# ═══════════════════════════════════════════════════════════════════

class RateLimitError(AlgoChainsError):
    """Rate limit exceeded — retry after the specified interval."""

    def __init__(self, message: str, retry_after: int = 60, **kwargs):
        self.retry_after = retry_after
        super().__init__(message, **kwargs)

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["retry_after_seconds"] = self.retry_after
        return d
