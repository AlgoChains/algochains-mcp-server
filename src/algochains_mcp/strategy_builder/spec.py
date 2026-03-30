"""StrategySpec — declarative strategy definition format with validation."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class AssetClass(str, Enum):
    EQUITY = "equity"
    FOREX = "forex"
    FUTURES = "futures"
    CRYPTO = "crypto"
    OPTIONS = "options"


class StrategyStatus(str, Enum):
    DRAFT = "draft"
    BACKTESTED = "backtested"
    VALIDATED = "validated"
    DEPLOYED = "deployed"


VALID_INDICATORS = {
    "rsi", "bbands", "ema", "sma", "macd", "atr", "adx", "stochastic",
    "cci", "obv", "vwap", "ichimoku", "supertrend", "keltner", "donchian",
    "williams_r", "mfi", "roc", "trix", "dema", "tema", "wma", "hma",
}

VALID_TIMEFRAMES = {"1min", "5min", "15min", "30min", "1h", "4h", "daily", "weekly"}

VALID_ORDER_TYPES = {"market", "limit", "stop", "stop_limit"}

VALID_SIZING_METHODS = {"risk_pct", "fixed_qty", "kelly", "equal_weight", "volatility_target"}

VALID_EXIT_TYPES = {"atr_multiple", "percent", "fixed_price", "indicator_cross", "time_bars"}


@dataclass
class StrategySpec:
    """Declarative strategy specification that AI agents can generate and engines can execute."""

    name: str
    version: str = "1.0.0"
    author: str = ""
    description: str = ""

    # Universe
    symbols: list[str] = field(default_factory=list)
    asset_class: str = "equity"
    timeframe: str = "daily"
    train_start: str = ""
    train_end: str = ""
    test_start: str = ""
    test_end: str = ""

    # Indicators
    indicators: list[dict[str, Any]] = field(default_factory=list)

    # Entry rules
    entry_rules: dict[str, Any] = field(default_factory=dict)

    # Exit rules
    exit_rules: dict[str, Any] = field(default_factory=dict)

    # Position sizing
    position_sizing: dict[str, Any] = field(default_factory=dict)

    # Filters
    filters: dict[str, Any] = field(default_factory=dict)

    # Metadata
    id: str = field(default_factory=lambda: f"spec_{uuid.uuid4().hex[:12]}")
    status: str = "draft"
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    backtest_results: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "author": self.author,
            "description": self.description,
            "universe": {
                "symbols": self.symbols,
                "asset_class": self.asset_class,
                "timeframe": self.timeframe,
                "data_range": {
                    "train": [self.train_start, self.train_end],
                    "test": [self.test_start, self.test_end],
                },
            },
            "indicators": self.indicators,
            "entry_rules": self.entry_rules,
            "exit_rules": self.exit_rules,
            "position_sizing": self.position_sizing,
            "filters": self.filters,
            "status": self.status,
            "created_at": self.created_at,
            "backtest_results": self.backtest_results,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StrategySpec:
        universe = data.get("universe", {})
        dr = universe.get("data_range", {})
        train = dr.get("train", ["", ""])
        test = dr.get("test", ["", ""])
        return cls(
            name=data.get("name", "Untitled Strategy"),
            version=data.get("version", "1.0.0"),
            author=data.get("author", ""),
            description=data.get("description", ""),
            symbols=universe.get("symbols", data.get("symbols", [])),
            asset_class=universe.get("asset_class", data.get("asset_class", "equity")),
            timeframe=universe.get("timeframe", data.get("timeframe", "daily")),
            train_start=train[0] if len(train) > 0 else "",
            train_end=train[1] if len(train) > 1 else "",
            test_start=test[0] if len(test) > 0 else "",
            test_end=test[1] if len(test) > 1 else "",
            indicators=data.get("indicators", []),
            entry_rules=data.get("entry_rules", {}),
            exit_rules=data.get("exit_rules", {}),
            position_sizing=data.get("position_sizing", {}),
            filters=data.get("filters", {}),
            id=data.get("id", f"spec_{uuid.uuid4().hex[:12]}"),
            status=data.get("status", "draft"),
            created_at=data.get("created_at", datetime.utcnow().isoformat()),
            backtest_results=data.get("backtest_results"),
        )


class StrategySpecValidator:
    """Validate a StrategySpec for schema correctness and parameter sanity."""

    def validate(self, spec: StrategySpec) -> dict[str, Any]:
        errors: list[str] = []
        warnings: list[str] = []

        # Required fields
        if not spec.name or spec.name == "Untitled Strategy":
            errors.append("Strategy must have a name.")
        if not spec.symbols:
            errors.append("At least one symbol is required.")
        if spec.asset_class not in [e.value for e in AssetClass]:
            errors.append(f"Invalid asset_class '{spec.asset_class}'. Must be one of: {[e.value for e in AssetClass]}")
        if spec.timeframe not in VALID_TIMEFRAMES:
            errors.append(f"Invalid timeframe '{spec.timeframe}'. Must be one of: {sorted(VALID_TIMEFRAMES)}")

        # Indicators
        for ind in spec.indicators:
            ind_name = ind.get("name", "")
            if ind_name not in VALID_INDICATORS:
                warnings.append(f"Unknown indicator '{ind_name}'. Custom indicators are allowed but may not be supported by all engines.")
            period = ind.get("period")
            if period is not None and (not isinstance(period, (int, float)) or period < 1):
                errors.append(f"Indicator '{ind_name}' has invalid period: {period}")

        # Entry rules
        if not spec.entry_rules:
            warnings.append("No entry rules defined. Strategy will not generate trades.")
        else:
            for direction in ("long", "short"):
                rules = spec.entry_rules.get(direction, {})
                conditions = rules.get("conditions", [])
                for cond in conditions:
                    if "indicator" not in cond:
                        errors.append(f"Entry condition missing 'indicator' field in {direction} rules.")
                    if "operator" not in cond:
                        errors.append(f"Entry condition missing 'operator' field in {direction} rules.")

        # Exit rules
        if not spec.exit_rules:
            warnings.append("No exit rules defined. Consider adding stop_loss and take_profit.")
        else:
            for exit_type in ("stop_loss", "take_profit", "trailing_stop"):
                rule = spec.exit_rules.get(exit_type, {})
                if rule:
                    etype = rule.get("type", "")
                    if etype and etype not in VALID_EXIT_TYPES:
                        errors.append(f"Invalid exit type '{etype}' for {exit_type}.")
                    mult = rule.get("multiplier")
                    if mult is not None and (not isinstance(mult, (int, float)) or mult <= 0):
                        errors.append(f"{exit_type} multiplier must be positive, got {mult}")

        # Position sizing
        if spec.position_sizing:
            method = spec.position_sizing.get("method", "")
            if method and method not in VALID_SIZING_METHODS:
                errors.append(f"Invalid sizing method '{method}'. Must be one of: {sorted(VALID_SIZING_METHODS)}")
            risk = spec.position_sizing.get("risk_per_trade")
            if risk is not None and (risk <= 0 or risk > 0.1):
                warnings.append(f"risk_per_trade={risk} seems {'too low' if risk <= 0 else 'high (>10%)'}")

        # Date ranges
        if spec.train_start and spec.train_end:
            try:
                ts = datetime.fromisoformat(spec.train_start)
                te = datetime.fromisoformat(spec.train_end)
                if ts >= te:
                    errors.append("train_start must be before train_end.")
            except ValueError:
                errors.append("Invalid date format in train range. Use YYYY-MM-DD.")
        if spec.test_start and spec.test_end:
            try:
                ts = datetime.fromisoformat(spec.test_start)
                te = datetime.fromisoformat(spec.test_end)
                if ts >= te:
                    errors.append("test_start must be before test_end.")
            except ValueError:
                errors.append("Invalid date format in test range. Use YYYY-MM-DD.")

        passed = len(errors) == 0
        return {
            "valid": passed,
            "errors": errors,
            "warnings": warnings,
            "spec_id": spec.id,
            "checks_run": 7,
        }
