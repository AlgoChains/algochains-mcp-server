"""
Configuration for the AlgoChains MCP Server.
Reads from environment variables / .env file.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


@dataclass
class AlpacaConfig:
    api_key: str = field(default_factory=lambda: _env("ALPACA_API_KEY"))
    secret_key: str = field(default_factory=lambda: _env("ALPACA_SECRET_KEY"))
    base_url: str = field(default_factory=lambda: _env("ALPACA_BASE_URL", "https://paper-api.alpaca.markets"))
    paper: bool = True


@dataclass
class IBKRConfig:
    host: str = field(default_factory=lambda: _env("IBKR_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_env("IBKR_PORT", "7497")))
    client_id: int = field(default_factory=lambda: int(_env("IBKR_CLIENT_ID", "1")))


@dataclass
class OandaConfig:
    account_id: str = field(default_factory=lambda: _env("OANDA_ACCOUNT_ID"))
    access_token: str = field(default_factory=lambda: _env("OANDA_ACCESS_TOKEN"))
    environment: str = field(default_factory=lambda: _env("OANDA_ENVIRONMENT", "practice"))


@dataclass
class TradersPostConfig:
    webhook_url: str = field(default_factory=lambda: _env("TRADERSPOST_WEBHOOK_URL"))
    api_key: str = field(default_factory=lambda: _env("TRADERSPOST_API_KEY"))


@dataclass
class QuantConnectConfig:
    user_id: str = field(default_factory=lambda: _env("QUANTCONNECT_USER_ID"))
    api_token: str = field(default_factory=lambda: _env("QUANTCONNECT_API_TOKEN"))
    base_url: str = "https://www.quantconnect.com/api/v2"


@dataclass
class MarketplaceConfig:
    django_url: str = field(default_factory=lambda: _env("ALGOCHAINS_DJANGO_URL", "https://algochains.ai"))
    listing_api_key: str = field(default_factory=lambda: _env("LISTING_API_KEY"))
    ingest_api_key: str = field(default_factory=lambda: _env("METRICS_INGEST_API_KEY"))
    creator_username: str = field(default_factory=lambda: _env("ALGOCHAINS_CREATOR_USERNAME", "tyler"))


@dataclass
class GatingConfig:
    min_oos_sharpe: float = 1.0
    min_oos_trades: int = 50
    max_drawdown_pct: float = 40.0
    min_oos_is_ratio: float = 0.5
    max_is_sharpe: float = 8.0
    min_paper_days: int = 30
    min_paper_trades: int = 50
    mcpt_permutations: int = 1000
    mcpt_max_p_value: float = 0.05
    require_walk_forward: bool = True


@dataclass
class ServerConfig:
    alpaca: AlpacaConfig = field(default_factory=AlpacaConfig)
    ibkr: IBKRConfig = field(default_factory=IBKRConfig)
    oanda: OandaConfig = field(default_factory=OandaConfig)
    traderspost: TradersPostConfig = field(default_factory=TradersPostConfig)
    quantconnect: QuantConnectConfig = field(default_factory=QuantConnectConfig)
    marketplace: MarketplaceConfig = field(default_factory=MarketplaceConfig)
    gating: GatingConfig = field(default_factory=GatingConfig)

    def available_brokers(self) -> list[str]:
        brokers = []
        if self.alpaca.api_key:
            brokers.append("alpaca")
        if self.ibkr.host:
            brokers.append("ibkr")
        if self.oanda.access_token:
            brokers.append("oanda")
        if self.traderspost.webhook_url:
            brokers.append("traderspost")
        if self.quantconnect.api_token:
            brokers.append("quantconnect")
        return brokers


def load_config() -> ServerConfig:
    return ServerConfig()
