"""
Configuration for the AlgoChains MCP Server.
Reads from environment variables / .env file.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

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
class TradovateConfig:
    cid: str = field(default_factory=lambda: _env("TRADOVATE_CID"))
    secret: str = field(default_factory=lambda: _env("TRADOVATE_SECRET"))
    env: str = field(default_factory=lambda: _env("TRADOVATE_ENV", "live"))
    device_id: str = field(default_factory=lambda: _env("TRADOVATE_DEVICE_ID", ""))
    # Full-credential auth (used by live bots via tradovate_client.py).
    # The MCP connector will use these when present — matching the auth format the
    # broker actually expects: {"name": username, "password": password, "cid": oauth_cid, "sec": oauth_sec}.
    username: str = field(default_factory=lambda: _env("TRADOVATE_USERNAME", ""))
    password: str = field(default_factory=lambda: _env("TRADOVATE_PASSWORD", ""))
    oauth_cid: str = field(default_factory=lambda: _env("TRADOVATE_OAUTH_CLIENT_ID", ""))
    oauth_sec: str = field(default_factory=lambda: _env("TRADOVATE_OAUTH_CLIENT_SECRET", ""))
    # Pre-existing access token (written by tradovate_token_guardian.py).
    # If set and not expired, connector skips re-auth and uses it directly.
    access_token: str = field(default_factory=lambda: (
        _env("TRADOVATE_ACCESS_TOKEN", "")
        .strip("'\"")           # guardian sometimes writes quoted values
        .replace("Bearer ", "") # strip prefix if present
        .splitlines()[0]        # first line only (guardian may append timestamp)
        .strip()
    ))

    @property
    def base_url(self) -> str:
        return "https://live.tradovateapi.com" if self.env == "live" \
            else "https://demo.tradovateapi.com"

    @property
    def ws_url(self) -> str:
        return "wss://live.tradovateapi.com/v1/websocket" if self.env == "live" \
            else "wss://demo.tradovateapi.com/v1/websocket"


@dataclass
class MassiveConfig:
    api_key: str = field(default_factory=lambda: _env("MASSIVE_API_KEY"))
    base_url: str = field(default_factory=lambda: _env("MASSIVE_API_BASE_URL", "https://api.massive.com"))
    llms_txt_url: str = field(default_factory=lambda: _env("MASSIVE_LLMS_TXT_URL", "https://massive.com/docs/rest/llms.txt"))
    max_tables: int = field(default_factory=lambda: int(_env("MASSIVE_MAX_TABLES", "50")))
    max_rows: int = field(default_factory=lambda: int(_env("MASSIVE_MAX_ROWS", "50000")))


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
    # Marketplace integrity: when True, MCPT must pass (not just warn) for validate() to return passed.
    require_mcpt: bool = True
    # When True, paper_trading gate must pass (Django-verified paper phase) before publish-tier validation.
    require_paper_graduation: bool = False


@dataclass
class SupabaseConfig:
    url: str = field(default_factory=lambda: _env("SUPABASE_URL"))
    anon_key: str = field(default_factory=lambda: _env("SUPABASE_ANON_KEY"))
    service_key: str = field(default_factory=lambda: _env("SUPABASE_SERVICE_KEY"))

    @property
    def available(self) -> bool:
        return bool(self.url and self.service_key)


@dataclass
class EmailConfig:
    resend_api_key: str = field(default_factory=lambda: _env("RESEND_API_KEY"))
    from_support: str = field(default_factory=lambda: _env("SUPPORT_FROM_EMAIL", "support@algochains.ai"))
    from_waitlist: str = field(default_factory=lambda: _env("WAITLIST_FROM_EMAIL", "waitlist@algochains.ai"))
    from_noreply: str = field(default_factory=lambda: _env("VERIFICATION_FROM_EMAIL", "noreply@algochains.ai"))


@dataclass
class SchwabConfig:
    client_id: str = field(default_factory=lambda: _env("SCHWAB_CLIENT_ID"))
    client_secret: str = field(default_factory=lambda: _env("SCHWAB_CLIENT_SECRET"))
    access_token: str = field(default_factory=lambda: _env("SCHWAB_ACCESS_TOKEN"))
    account_hash: str = field(default_factory=lambda: _env("SCHWAB_ACCOUNT_HASH"))
    paper: bool = field(default_factory=lambda: _env("SCHWAB_PAPER", "false").lower() == "true")


@dataclass
class TwilioConfig:
    account_sid: str = field(default_factory=lambda: _env("TWILIO_ACCOUNT_SID"))
    auth_token: str = field(default_factory=lambda: _env("TWILIO_AUTH_TOKEN"))
    from_number: str = field(default_factory=lambda: _env("TWILIO_FROM_NUMBER"))

    @property
    def available(self) -> bool:
        return bool(self.account_sid and self.auth_token and self.from_number)


@dataclass
class NotionConfig:
    api_key: str = field(default_factory=lambda: _env("NOTION_API_KEY"))
    support_db_id: str = field(default_factory=lambda: _env("NOTION_SUPPORT_DB_ID"))

    @property
    def available(self) -> bool:
        return bool(self.api_key and self.support_db_id)


@dataclass
class ServerConfig:
    alpaca: AlpacaConfig = field(default_factory=AlpacaConfig)
    ibkr: IBKRConfig = field(default_factory=IBKRConfig)
    oanda: OandaConfig = field(default_factory=OandaConfig)
    traderspost: TradersPostConfig = field(default_factory=TradersPostConfig)
    quantconnect: QuantConnectConfig = field(default_factory=QuantConnectConfig)
    tradovate: TradovateConfig = field(default_factory=TradovateConfig)
    massive: MassiveConfig = field(default_factory=MassiveConfig)
    marketplace: MarketplaceConfig = field(default_factory=MarketplaceConfig)
    gating: GatingConfig = field(default_factory=GatingConfig)
    supabase: SupabaseConfig = field(default_factory=SupabaseConfig)
    email: EmailConfig = field(default_factory=EmailConfig)
    schwab: SchwabConfig = field(default_factory=SchwabConfig)
    twilio: TwilioConfig = field(default_factory=TwilioConfig)
    notion: NotionConfig = field(default_factory=NotionConfig)
    tool_mode: str = field(default_factory=lambda: _env("ALGOCHAINS_TOOL_MODE", "smart"))

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
        if self.tradovate.cid:
            brokers.append("tradovate")
        if self.schwab.client_id or self.schwab.access_token:
            brokers.append("schwab")
        return brokers


def load_config() -> ServerConfig:
    return ServerConfig()
