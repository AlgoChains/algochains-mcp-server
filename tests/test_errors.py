"""Tests for the error hierarchy."""
from algochains_mcp.errors import (
    AlgoChainsError,
    BrokerAuthError,
    BrokerConnectionError,
    BrokerError,
    BrokerNotConfiguredError,
    BrokerNotConnectedError,
    BrokerOrderError,
    BrokerQuoteError,
    ListingNotFoundError,
    MarketplaceError,
    MCPTValidationError,
    OverfitValidationError,
    PerformanceValidationError,
    RateLimitError,
    SchemaValidationError,
    SubscriptionError,
    ValidationError,
    WalkForwardValidationError,
)


class TestErrorHierarchy:
    def test_base_error_to_dict(self):
        err = AlgoChainsError("test error", details={"key": "value"})
        d = err.to_dict()
        assert d["error_type"] == "AlgoChainsError"
        assert d["message"] == "test error"
        assert d["details"] == {"key": "value"}

    def test_broker_error_includes_broker(self):
        err = BrokerError("connection failed", broker="alpaca")
        assert err.broker == "alpaca"
        assert "[alpaca]" in str(err)
        d = err.to_dict()
        assert d["broker"] == "alpaca"

    def test_broker_error_without_broker(self):
        err = BrokerError("generic failure")
        assert err.broker == ""
        assert "[" not in str(err)

    def test_broker_subtypes(self):
        assert issubclass(BrokerConnectionError, BrokerError)
        assert issubclass(BrokerAuthError, BrokerError)
        assert issubclass(BrokerOrderError, BrokerError)
        assert issubclass(BrokerQuoteError, BrokerError)
        assert issubclass(BrokerNotConnectedError, BrokerError)
        assert issubclass(BrokerNotConfiguredError, BrokerError)

    def test_broker_order_error_has_order_id(self):
        err = BrokerOrderError("insufficient funds", broker="alpaca", order_id="abc123")
        assert err.order_id == "abc123"
        assert err.broker == "alpaca"

    def test_validation_error_fields(self):
        err = ValidationError("failed gate 2", gate="performance", score=45.0)
        assert err.gate == "performance"
        assert err.score == 45.0
        d = err.to_dict()
        assert d["gate"] == "performance"
        assert d["score"] == 45.0

    def test_validation_subtypes(self):
        assert issubclass(SchemaValidationError, ValidationError)
        assert issubclass(PerformanceValidationError, ValidationError)
        assert issubclass(OverfitValidationError, ValidationError)
        assert issubclass(MCPTValidationError, ValidationError)
        assert issubclass(WalkForwardValidationError, ValidationError)

    def test_marketplace_subtypes(self):
        assert issubclass(ListingNotFoundError, MarketplaceError)
        assert issubclass(SubscriptionError, MarketplaceError)
        assert issubclass(MarketplaceError, AlgoChainsError)

    def test_rate_limit_error(self):
        err = RateLimitError("too many requests", retry_after=30)
        assert err.retry_after == 30
        d = err.to_dict()
        assert d["retry_after_seconds"] == 30

    def test_all_errors_inherit_from_base(self):
        errors = [
            BrokerError, BrokerConnectionError, BrokerAuthError,
            BrokerOrderError, BrokerQuoteError, BrokerNotConnectedError,
            BrokerNotConfiguredError, ValidationError, SchemaValidationError,
            PerformanceValidationError, OverfitValidationError,
            MCPTValidationError, WalkForwardValidationError,
            MarketplaceError, ListingNotFoundError, SubscriptionError,
            RateLimitError,
        ]
        for err_cls in errors:
            assert issubclass(err_cls, AlgoChainsError), f"{err_cls.__name__} should inherit AlgoChainsError"
