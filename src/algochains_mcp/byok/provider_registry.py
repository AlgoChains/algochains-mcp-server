"""
Provider metadata registry — defines every supported data provider,
its env var names, validation endpoints, signup URLs, free tier info,
and key format patterns.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ProviderCategory(str, Enum):
    MARKET_DATA = "market_data"
    TICK_DATA = "tick_data"
    OPTIONS_FLOW = "options_flow"
    NEWS_SENTIMENT = "news_sentiment"
    FUNDAMENTALS = "fundamentals"
    ECONOMIC_DATA = "economic_data"
    ALTERNATIVE_DATA = "alternative_data"
    AGGREGATOR = "aggregator"


@dataclass
class ProviderMeta:
    name: str
    display_name: str
    env_vars: list[str]
    categories: list[ProviderCategory]
    signup_url: str
    docs_url: str
    free_tier: bool
    free_tier_limits: str
    validation_url: str
    validation_method: str  # "header", "query_param", "bearer"
    key_pattern: Optional[str] = None  # regex for key format validation
    requires_key: bool = True
    data_types: list[str] = field(default_factory=list)
    notes: str = ""

    def matches_key_format(self, key: str) -> bool:
        if not self.key_pattern:
            return True
        return bool(re.match(self.key_pattern, key))


PROVIDER_REGISTRY: dict[str, ProviderMeta] = {
    "polygon": ProviderMeta(
        name="polygon",
        display_name="Polygon.io",
        env_vars=["POLYGON_API_KEY"],
        categories=[ProviderCategory.MARKET_DATA, ProviderCategory.NEWS_SENTIMENT, ProviderCategory.FUNDAMENTALS],
        signup_url="https://polygon.io/dashboard/signup",
        docs_url="https://polygon.io/docs",
        free_tier=True,
        free_tier_limits="5 API calls/minute, delayed data",
        validation_url="https://api.polygon.io/v3/reference/tickers?limit=1&apiKey={key}",
        validation_method="query_param",
        key_pattern=r"^[A-Za-z0-9_]{20,40}$",
        data_types=["bars", "quotes", "trades", "news", "fundamentals", "options", "forex", "crypto"],
    ),
    "alpha_vantage": ProviderMeta(
        name="alpha_vantage",
        display_name="Alpha Vantage",
        env_vars=["ALPHA_VANTAGE_API_KEY"],
        categories=[ProviderCategory.MARKET_DATA, ProviderCategory.FUNDAMENTALS],
        signup_url="https://www.alphavantage.co/support/#api-key",
        docs_url="https://www.alphavantage.co/documentation/",
        free_tier=True,
        free_tier_limits="25 API calls/day",
        validation_url="https://www.alphavantage.co/query?function=TIME_SERIES_INTRADAY&symbol=IBM&interval=5min&apikey={key}&outputsize=compact",
        validation_method="query_param",
        key_pattern=r"^[A-Z0-9]{12,20}$",
        data_types=["bars", "quotes", "fundamentals", "forex", "crypto", "economic_indicators"],
    ),
    "finnhub": ProviderMeta(
        name="finnhub",
        display_name="Finnhub",
        env_vars=["FINNHUB_API_KEY"],
        categories=[ProviderCategory.MARKET_DATA, ProviderCategory.NEWS_SENTIMENT],
        signup_url="https://finnhub.io/register",
        docs_url="https://finnhub.io/docs/api",
        free_tier=True,
        free_tier_limits="60 API calls/minute",
        validation_url="https://finnhub.io/api/v1/quote?symbol=AAPL&token={key}",
        validation_method="query_param",
        key_pattern=r"^[a-z0-9]{20,30}$",
        data_types=["bars", "quotes", "news", "sentiment", "insider_transactions", "earnings"],
    ),
    "twelve_data": ProviderMeta(
        name="twelve_data",
        display_name="Twelve Data",
        env_vars=["TWELVE_DATA_API_KEY"],
        categories=[ProviderCategory.MARKET_DATA],
        signup_url="https://twelvedata.com/apikey",
        docs_url="https://twelvedata.com/docs",
        free_tier=True,
        free_tier_limits="8 API calls/minute, 800 calls/day",
        validation_url="https://api.twelvedata.com/quote?symbol=AAPL&apikey={key}",
        validation_method="query_param",
        key_pattern=r"^[a-f0-9]{32}$",
        data_types=["bars", "quotes", "technical_indicators", "forex", "crypto", "etf"],
        notes="800+ built-in technical indicators",
    ),
    "yahoo_finance": ProviderMeta(
        name="yahoo_finance",
        display_name="Yahoo Finance",
        env_vars=[],
        categories=[ProviderCategory.MARKET_DATA, ProviderCategory.FUNDAMENTALS],
        signup_url="",
        docs_url="https://pypi.org/project/yfinance/",
        free_tier=True,
        free_tier_limits="Unlimited (unofficial API via yfinance)",
        validation_url="",
        validation_method="",
        requires_key=False,
        data_types=["bars", "quotes", "fundamentals", "options_chain", "dividends", "splits"],
        notes="No API key needed. Uses yfinance Python library.",
    ),
    "databento": ProviderMeta(
        name="databento",
        display_name="Databento",
        env_vars=["DATABENTO_API_KEY"],
        categories=[ProviderCategory.TICK_DATA],
        signup_url="https://databento.com/signup",
        docs_url="https://databento.com/docs",
        free_tier=False,
        free_tier_limits="Pay-per-use ($0.01-0.05/query)",
        validation_url="https://hist.databento.com/v0/metadata.list_datasets",
        validation_method="bearer",
        key_pattern=r"^db-[A-Za-z0-9]{32,}$",
        data_types=["tick_trades", "tick_quotes", "l2_book", "l3_book", "ohlcv", "statistics"],
        notes="Institutional-grade tick data. L2/L3 order book.",
    ),
    "unusual_whales": ProviderMeta(
        name="unusual_whales",
        display_name="Unusual Whales",
        env_vars=["UW_API_KEY", "UNUSUAL_WHALES_API_KEY"],
        categories=[ProviderCategory.OPTIONS_FLOW, ProviderCategory.ALTERNATIVE_DATA],
        signup_url="https://unusualwhales.com/pricing",
        docs_url="https://docs.unusualwhales.com",
        free_tier=False,
        free_tier_limits="Paid plans start at $57/mo",
        validation_url="https://api.unusualwhales.com/api/market/overview",
        validation_method="bearer",
        data_types=["options_flow", "dark_pool", "gex", "dex", "institutional_holdings"],
        notes="Real-time options flow, dark pool prints, GEX.",
    ),
    "intrinio": ProviderMeta(
        name="intrinio",
        display_name="Intrinio",
        env_vars=["INTRINIO_API_KEY"],
        categories=[ProviderCategory.FUNDAMENTALS, ProviderCategory.MARKET_DATA],
        signup_url="https://intrinio.com/signup",
        docs_url="https://docs.intrinio.com",
        free_tier=True,
        free_tier_limits="Limited sandbox data",
        validation_url="https://api-v2.intrinio.com/companies/AAPL?api_key={key}",
        validation_method="query_param",
        data_types=["fundamentals", "prices", "options", "economic_data", "etf"],
    ),
    "quandl": ProviderMeta(
        name="quandl",
        display_name="Quandl / Nasdaq Data Link",
        env_vars=["QUANDL_API_KEY", "NASDAQ_DATA_LINK_API_KEY"],
        categories=[ProviderCategory.ECONOMIC_DATA, ProviderCategory.ALTERNATIVE_DATA],
        signup_url="https://data.nasdaq.com/sign-up",
        docs_url="https://docs.data.nasdaq.com",
        free_tier=True,
        free_tier_limits="Limited free datasets",
        validation_url="https://data.nasdaq.com/api/v3/datasets/WIKI/AAPL.json?rows=1&api_key={key}",
        validation_method="query_param",
        data_types=["economic_data", "alternative_data", "commodity_prices", "interest_rates"],
    ),
    "openbb": ProviderMeta(
        name="openbb",
        display_name="OpenBB",
        env_vars=["OPENBB_TOKEN"],
        categories=[ProviderCategory.AGGREGATOR],
        signup_url="https://my.openbb.co/app/hub",
        docs_url="https://docs.openbb.co",
        free_tier=True,
        free_tier_limits="Community tier (aggregated sources)",
        validation_url="",
        validation_method="bearer",
        data_types=["aggregated_bars", "aggregated_fundamentals", "aggregated_news"],
        notes="Aggregates multiple data sources. Great as a fallback.",
    ),
}


def get_all_env_var_names() -> dict[str, str]:
    """Return mapping of env_var_name -> provider_name for all providers."""
    result = {}
    for provider_name, meta in PROVIDER_REGISTRY.items():
        for env_var in meta.env_vars:
            result[env_var] = provider_name
    return result


def get_provider(name: str) -> Optional[ProviderMeta]:
    return PROVIDER_REGISTRY.get(name)


def get_providers_by_category(category: ProviderCategory) -> list[ProviderMeta]:
    return [m for m in PROVIDER_REGISTRY.values() if category in m.categories]
