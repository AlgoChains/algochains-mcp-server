"""
AlgoChains MCP Server — End-User Onboarding Module (V22)

PURPOSE:
    Guides a new user from zero → live trading through a compliance-gated,
    step-by-step wizard exposed as MCP tools.

    The AI calls these tools sequentially when a user says "set me up" or
    "connect my broker". Each step includes:
      - Compliance disclosures at the appropriate moment (not buried in TOS)
      - Credential validation BEFORE storing (fail loud, never silent)
      - Explicit user confirmation required for destructive or financial steps
      - Real connectivity tests — no mock responses

COMPLIANCE PHILOSOPHY:
    CFTC / NFA rules require disclosure before anyone trades futures.
    SEC Reg BI applies to any investment recommendations.
    We don't give investment advice. We provide infrastructure.
    These disclosures are shown once per session and persisted so we know
    the user acknowledged them — timestamped, with their session ID.

ONBOARDING STEPS:
    Step 0:  Risk disclosure + compliance acknowledgment (REQUIRED FIRST)
    Step 1:  Choose asset class and broker (futures / equities / forex / crypto)
    Step 2:  Connect broker — provide credentials + validate live connection
    Step 3:  Connect market data — Databento / Polygon / Alpaca / FRED
    Step 4:  Configure AlgoChains API key (for marketplace access)
    Step 5:  Run connectivity smoke test — all systems go check
    Step 6:  Set up guardrail preferences (within hard-coded limits)
    Step 7:  Generate mcporter.json config for their IDE
    Step 8:  Onboarding complete summary

PROPRIETARY DATA INGESTION:
    After onboarding, users can ingest proprietary data via:
      ingest_csv_data     — Custom OHLCV history
      ingest_json_signals — Pre-computed signals / features
      connect_onyx_docs   — Index their own research docs into Onyx
      register_strategy   — Upload their own strategy spec
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("algochains_mcp.onboarding")

# ═══════════════════════════════════════════════════════════════════════════
# COMPLIANCE DISCLOSURES — shown inline, not buried in TOS
# ═══════════════════════════════════════════════════════════════════════════

RISK_DISCLOSURE = """
╔══════════════════════════════════════════════════════════════════════════╗
║          RISK DISCLOSURE — READ BEFORE PROCEEDING                      ║
╠══════════════════════════════════════════════════════════════════════════╣
║                                                                          ║
║  FUTURES TRADING INVOLVES SUBSTANTIAL RISK OF LOSS AND IS NOT           ║
║  SUITABLE FOR ALL INVESTORS. PAST PERFORMANCE IS NOT INDICATIVE         ║
║  OF FUTURE RESULTS.                                                      ║
║                                                                          ║
║  • You may lose more than your initial investment.                       ║
║  • Leveraged futures contracts can amplify both gains AND losses.        ║
║  • AI-assisted execution is NOT investment advice.                       ║
║  • AlgoChains does NOT manage your money or give trading advice.         ║
║  • Backtested performance does not guarantee future results.             ║
║  • The Sharpe ratios shown are computed on historical data               ║
║    using the Deflated Sharpe Ratio (DSR) methodology to account          ║
║    for overfitting — they are not predictions.                           ║
║                                                                          ║
║  REGULATORY:                                                             ║
║  • Futures trading is regulated by the CFTC in the United States.       ║
║  • You are responsible for compliance with all applicable laws.          ║
║  • AlgoChains is a software tool provider, not a registered CTA.        ║
║  • Consult a licensed financial advisor before trading with real money.  ║
║                                                                          ║
║  SYSTEM RISK:                                                            ║
║  • Internet outages, API failures, and software bugs can cause           ║
║    missed orders, duplicate orders, or unintended positions.             ║
║  • Always monitor your positions independently of this system.           ║
║  • The $500/day loss limit in the circuit breaker is a SOFT FLOOR —     ║
║    broker-level losses beyond this limit remain your responsibility.     ║
║                                                                          ║
╚══════════════════════════════════════════════════════════════════════════╝

To continue, you must acknowledge: "I have read and understand the risk
disclosure above. I accept full responsibility for my trading decisions."
"""

DATA_PRIVACY_NOTICE = """
DATA PRIVACY NOTICE:

Your broker credentials and API keys are stored LOCALLY in your environment:
  - Credentials go in your .env file or shell environment — never in this codebase
  - AlgoChains does NOT transmit your credentials to any server
  - The MCP server runs locally on your machine (stdio transport)
  - If you use the SSE bridge (HTTP transport), keys stay local
  - Onyx knowledge base is self-hosted — your data stays on your machine

What AlgoChains does NOT do:
  ✗ Does not store API keys on any cloud server
  ✗ Does not send your trading history to third parties
  ✗ Does not monetize your trading data
  ✗ Does not share your positions or P&L with anyone

Your responsibility:
  • Keep your .env file out of version control (.gitignore it)
  • Rotate broker API keys periodically
  • Never share your ALGOCHAINS_BRIDGE_API_KEY with untrusted parties
"""

BROKER_SPECIFIC_WARNINGS = {
    "tradovate": """
⚠️  TRADOVATE FUTURES — IMPORTANT:
  • You are trading leveraged futures contracts (MNQ, CL, MES, NQ, ES, etc.)
  • Margin requirements can change intraday during volatile markets
  • Tradovate enforces: 80 API requests/min, 5000/hour — violations = P-ticket ban
  • AlgoChains limits you to 10 orders/min to stay well within limits
  • Use the LIVE environment ONLY after confirming strategy in paper/sim first
  • CIDs (Client IDs) are tied to your account — do not share them
""",
    "alpaca": """
⚠️  ALPACA EQUITIES — IMPORTANT:
  • Alpaca offers commission-free trading but is NOT FDIC insured
  • Pattern Day Trader (PDT) rule applies: <$25k account → max 3 day trades/week
  • Extended hours trading has lower liquidity and wider spreads
  • Paper trading credentials ≠ live trading credentials — use separate env vars
""",
    "oanda": """
⚠️  OANDA FOREX — IMPORTANT:
  • Forex is traded 24/5 — positions can move significantly overnight
  • OANDA uses spread-based pricing — effective cost is the spread
  • Leverage up to 50:1 available — can wipe account quickly
  • Lot sizes: Standard (100k), Mini (10k), Micro (1k) — confirm before trading
""",
}

# ═══════════════════════════════════════════════════════════════════════════
# ONBOARDING STATE
# ═══════════════════════════════════════════════════════════════════════════

_STATE_DIR = Path(os.environ.get("ALGOCHAINS_STATE_DIR", "state"))
_ONBOARDING_FILE = _STATE_DIR / "onboarding_state.json"


@dataclass
class OnboardingState:
    session_id: str = ""
    risk_acknowledged: bool = False
    risk_ack_timestamp: float = 0.0
    privacy_acknowledged: bool = False
    brokers_connected: list[str] = field(default_factory=list)
    data_providers_connected: list[str] = field(default_factory=list)
    algochains_key_set: bool = False
    smoke_test_passed: bool = False
    onboarding_complete: bool = False
    completed_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "risk_acknowledged": self.risk_acknowledged,
            "risk_ack_timestamp": self.risk_ack_timestamp,
            "privacy_acknowledged": self.privacy_acknowledged,
            "brokers_connected": self.brokers_connected,
            "data_providers_connected": self.data_providers_connected,
            "algochains_key_set": self.algochains_key_set,
            "smoke_test_passed": self.smoke_test_passed,
            "onboarding_complete": self.onboarding_complete,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "OnboardingState":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def _load_state() -> OnboardingState:
    try:
        if _ONBOARDING_FILE.exists():
            return OnboardingState.from_dict(json.loads(_ONBOARDING_FILE.read_text()))
    except Exception:
        pass
    return OnboardingState(session_id=hashlib.md5(str(time.time()).encode()).hexdigest()[:12])


def _save_state(state: OnboardingState) -> None:
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        _ONBOARDING_FILE.write_text(json.dumps(state.to_dict(), indent=2))
    except Exception as exc:
        logger.warning("Could not save onboarding state: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════
# STEP HANDLERS — each returns a dict the AI presents to the user
# ═══════════════════════════════════════════════════════════════════════════

def start_onboarding() -> dict:
    """
    Step 0: Show risk disclosure and request acknowledgment.
    MUST be called and acknowledged before any other onboarding step.
    """
    state = _load_state()

    if state.risk_acknowledged:
        elapsed_days = (time.time() - state.risk_ack_timestamp) / 86400
        return {
            "status": "already_acknowledged",
            "acknowledged_at": state.risk_ack_timestamp,
            "days_ago": round(elapsed_days, 1),
            "next_step": "connect_broker",
            "message": "Risk disclosure previously acknowledged. Proceed with broker connection.",
        }

    return {
        "status": "disclosure_required",
        "step": 0,
        "disclosure": RISK_DISCLOSURE,
        "privacy_notice": DATA_PRIVACY_NOTICE,
        "required_action": (
            "Call acknowledge_risk_disclosure(acknowledgment='I have read and understand the risk "
            "disclosure above. I accept full responsibility for my trading decisions.') to proceed."
        ),
        "warning": "⚠️  NO TRADING TOOLS WILL WORK UNTIL RISK DISCLOSURE IS ACKNOWLEDGED.",
    }


def acknowledge_risk_disclosure(acknowledgment: str) -> dict:
    """
    User must type the exact acknowledgment text to proceed.
    This creates an auditable record that they read the disclosure.
    """
    required = "I have read and understand the risk disclosure above. I accept full responsibility for my trading decisions."
    if acknowledgment.strip() != required:
        return {
            "status": "invalid_acknowledgment",
            "error": "Acknowledgment text does not match required text exactly.",
            "required_text": required,
            "your_text": acknowledgment,
            "hint": "Copy and paste the exact required text.",
        }

    state = _load_state()
    state.risk_acknowledged = True
    state.risk_ack_timestamp = time.time()
    state.privacy_acknowledged = True
    _save_state(state)

    return {
        "status": "acknowledged",
        "timestamp": state.risk_ack_timestamp,
        "next_step": "connect_broker",
        "message": "✅ Risk disclosure acknowledged. You may now connect a broker.",
        "reminder": (
            "Remember: AlgoChains is infrastructure software, not investment advice. "
            "Always monitor positions independently."
        ),
    }


def get_broker_setup_guide(broker: str) -> dict:
    """
    Step 1/2: Return the setup guide for a specific broker.
    Includes the broker-specific warning and exact env vars needed.
    """
    state = _load_state()
    if not state.risk_acknowledged:
        return {"error": "Risk disclosure must be acknowledged first. Call start_onboarding()."}

    guides = {
        "tradovate": {
            "broker": "tradovate",
            "asset_class": "futures",
            "warning": BROKER_SPECIFIC_WARNINGS["tradovate"],
            "required_env_vars": {
                "TRADOVATE_CID": "Your Tradovate Client ID (from developer portal)",
                "TRADOVATE_SECRET": "Your Tradovate Client Secret",
                "TRADOVATE_DEVICE_ID": "Unique device ID string (generate: python -c \"import uuid; print(uuid.uuid4())\")",
                "TRADOVATE_ENV": "'live' for real trading, 'demo' for paper trading (STRONGLY recommend demo first)",
                "TRADOVATE_USERNAME": "Your Tradovate account username",
                "TRADOVATE_PASSWORD": "Your Tradovate account password",
            },
            "where_to_get_credentials": (
                "1. Log into https://trader.tradovate.com\n"
                "2. Go to Settings → API → Create Application\n"
                "3. Name your app, agree to API terms\n"
                "4. Copy your CID and Secret immediately (shown once)\n"
                "5. Generate a device_id: python -c \"import uuid; print(uuid.uuid4())\"\n"
            ),
            "verification_command": "validate_broker_connection(broker='tradovate')",
            "paper_trading_note": "Set TRADOVATE_ENV=demo to test without real money. STRONGLY recommended.",
            "rate_limits": "80 req/min (AlgoChains limits you to 10/min for safety)",
            "supports": ["MNQ", "NQ", "MES", "ES", "CL", "GC", "SI"],
        },
        "alpaca": {
            "broker": "alpaca",
            "asset_class": "equities + crypto",
            "warning": BROKER_SPECIFIC_WARNINGS["alpaca"],
            "required_env_vars": {
                "ALPACA_API_KEY": "Your Alpaca API key (from https://alpaca.markets/)",
                "ALPACA_SECRET_KEY": "Your Alpaca secret key",
                "ALPACA_BASE_URL": "https://paper-api.alpaca.markets (paper) OR https://api.alpaca.markets (live)",
            },
            "where_to_get_credentials": (
                "1. Create account at https://alpaca.markets\n"
                "2. Paper trading is available immediately\n"
                "3. Go to Dashboard → API Keys\n"
                "4. Generate new key pair\n"
                "5. Copy both key and secret (secret shown once)\n"
            ),
            "verification_command": "validate_broker_connection(broker='alpaca')",
            "paper_trading_note": "Use ALPACA_BASE_URL=https://paper-api.alpaca.markets for paper trading.",
            "rate_limits": "200 req/min",
            "supports": ["Any US equity", "Crypto: BTC, ETH, etc."],
        },
        "oanda": {
            "broker": "oanda",
            "asset_class": "forex",
            "warning": BROKER_SPECIFIC_WARNINGS["oanda"],
            "required_env_vars": {
                "OANDA_API_KEY": "Your OANDA API token (from My Account → Manage API Access)",
                "OANDA_ACCOUNT_ID": "Your OANDA account ID (e.g., 001-001-1234567-001)",
                "OANDA_ENVIRONMENT": "'practice' for demo, 'live' for real money",
            },
            "where_to_get_credentials": (
                "1. Log into https://www.oanda.com/account/login\n"
                "2. My Account → Manage API Access\n"
                "3. Generate API key\n"
                "4. Copy your Account ID from the dashboard\n"
            ),
            "verification_command": "validate_broker_connection(broker='oanda')",
            "paper_trading_note": "Set OANDA_ENVIRONMENT=practice for demo account.",
            "rate_limits": "120 req/min",
            "supports": ["EUR/USD", "GBP/USD", "USD/JPY", "100+ forex pairs"],
        },
    }

    if broker not in guides:
        return {
            "error": f"Unknown broker: {broker}",
            "supported_brokers": list(guides.keys()),
        }

    return {
        "step": 2,
        "status": "setup_guide",
        **guides[broker],
    }


def get_data_provider_setup_guide(provider: str) -> dict:
    """
    Step 3: Market data provider setup guides.
    """
    state = _load_state()
    if not state.risk_acknowledged:
        return {"error": "Risk disclosure must be acknowledged first."}

    guides = {
        "polygon": {
            "provider": "polygon",
            "description": "US stocks, options, forex, crypto bars and trades",
            "required_env_vars": {
                "POLYGON_API_KEY": "Your Polygon.io API key (from https://polygon.io/dashboard)",
            },
            "where_to_get": "https://polygon.io/dashboard → API Keys",
            "free_tier": "Free tier: 5 API calls/min, end-of-day data. Starter ($29/mo): real-time.",
            "verification_command": "validate_data_provider(provider='polygon')",
            "used_for": ["Equity bars", "Options flow", "Market regime detection", "Factor models"],
        },
        "databento": {
            "provider": "databento",
            "description": "Institutional tick data for futures (CME Group, CBOT)",
            "required_env_vars": {
                "DATABENTO_API_KEY": "Your Databento API key (from https://databento.com/)",
            },
            "where_to_get": "https://databento.com/ → API Keys in your dashboard",
            "free_tier": "Free $125 of data on signup. Then pay-as-you-go.",
            "verification_command": "validate_data_provider(provider='databento')",
            "used_for": ["MNQ/NQ/CL/ES tick data", "Backtesting", "Intraday signal research"],
        },
        "fred": {
            "provider": "fred",
            "description": "Federal Reserve economic data (VIX, T-bill rates, macro)",
            "required_env_vars": {
                "FRED_API_KEY": "Your FRED API key (from https://fred.stlouisfed.org/)",
            },
            "where_to_get": "https://fred.stlouisfed.org/docs/api/api_key.html — free registration",
            "free_tier": "Free, unlimited for personal use.",
            "verification_command": "validate_data_provider(provider='fred')",
            "used_for": ["VIX term structure", "Risk-free rate (DSR calculation)", "Macro regime"],
        },
        "onyx": {
            "provider": "onyx",
            "description": "Self-hosted RAG knowledge base (research, skills, strategies)",
            "required_env_vars": {
                "ONYX_API_URL": "URL of your Onyx instance (e.g., http://localhost:8085)",
                "ONYX_API_KEY": "Onyx API key (optional if auth disabled)",
                "ONYX_ADMIN_EMAIL": "Onyx admin email for document ingestion",
                "ONYX_ADMIN_PASS": "Onyx admin password",
            },
            "where_to_get": (
                "Onyx is self-hosted. Install at https://docs.onyx.app\n"
                "Then set ONYX_API_URL to your instance's base URL.\n"
                "AlgoChains default: http://localhost:8085 (desktop tower via Tailscale)"
            ),
            "free_tier": "Self-hosted, open source (Apache 2.0)",
            "verification_command": "validate_data_provider(provider='onyx')",
            "used_for": ["Strategy research Q&A", "Blueprint discovery", "Skills lookup", "Trade memory"],
        },
    }

    if provider not in guides:
        return {
            "error": f"Unknown provider: {provider}",
            "supported_providers": list(guides.keys()),
        }

    return {
        "step": 3,
        "status": "setup_guide",
        **guides[provider],
    }


async def validate_broker_connection(broker: str) -> dict:
    """
    Step 2b: Test broker connectivity with real credentials from env.
    Fails loudly if credentials are missing or invalid.
    """
    state = _load_state()
    if not state.risk_acknowledged:
        return {"error": "Risk disclosure must be acknowledged first."}

    result: dict = {"broker": broker, "test": "connectivity"}

    try:
        if broker == "tradovate":
            cid = os.getenv("TRADOVATE_CID", "")
            secret = os.getenv("TRADOVATE_SECRET", "")
            env = os.getenv("TRADOVATE_ENV", "demo")

            if not cid or not secret:
                return {
                    "status": "missing_credentials",
                    "broker": broker,
                    "missing": [k for k, v in {"TRADOVATE_CID": cid, "TRADOVATE_SECRET": secret}.items() if not v],
                    "action": "Set the missing environment variables and retry.",
                }

            import httpx
            base = "https://demo.tradovateapi.com/v1" if env == "demo" else "https://live.tradovateapi.com/v1"
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
                resp = await client.post(
                    f"{base}/auth/accesstokenrequest",
                    json={"name": os.getenv("TRADOVATE_USERNAME", ""), "password": os.getenv("TRADOVATE_PASSWORD", ""),
                          "appId": "AlgoChains", "appVersion": "22.0", "cid": int(cid), "sec": secret,
                          "deviceId": os.getenv("TRADOVATE_DEVICE_ID", "algochains-onboarding")},
                )
                if resp.status_code == 200 and "accessToken" in resp.text:
                    result["status"] = "connected"
                    result["environment"] = env
                    result["message"] = f"✅ Tradovate {env.upper()} connection verified"
                else:
                    result["status"] = "auth_failed"
                    result["http_status"] = resp.status_code
                    result["message"] = f"❌ Authentication failed: {resp.text[:200]}"

        elif broker == "alpaca":
            key = os.getenv("ALPACA_API_KEY", "")
            secret = os.getenv("ALPACA_SECRET_KEY", "")
            base_url = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

            if not key or not secret:
                return {
                    "status": "missing_credentials",
                    "broker": broker,
                    "missing": [k for k, v in {"ALPACA_API_KEY": key, "ALPACA_SECRET_KEY": secret}.items() if not v],
                    "action": "Set the missing environment variables and retry.",
                }

            import httpx
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
                resp = await client.get(
                    f"{base_url}/v2/account",
                    headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
                )
                if resp.status_code == 200:
                    acct = resp.json()
                    result["status"] = "connected"
                    result["account_status"] = acct.get("status")
                    result["buying_power"] = acct.get("buying_power")
                    result["environment"] = "paper" if "paper" in base_url else "live"
                    result["message"] = f"✅ Alpaca account connected ({result['environment'].upper()})"
                else:
                    result["status"] = "auth_failed"
                    result["http_status"] = resp.status_code
                    result["message"] = f"❌ Alpaca auth failed: {resp.text[:200]}"

        elif broker == "oanda":
            key = os.getenv("OANDA_API_KEY", "")
            account_id = os.getenv("OANDA_ACCOUNT_ID", "")
            env = os.getenv("OANDA_ENVIRONMENT", "practice")

            if not key or not account_id:
                return {
                    "status": "missing_credentials",
                    "broker": broker,
                    "missing": [k for k, v in {"OANDA_API_KEY": key, "OANDA_ACCOUNT_ID": account_id}.items() if not v],
                    "action": "Set the missing environment variables and retry.",
                }

            import httpx
            base = "https://api-fxpractice.oanda.com" if env == "practice" else "https://api-fxtrade.oanda.com"
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
                resp = await client.get(
                    f"{base}/v3/accounts/{account_id}",
                    headers={"Authorization": f"Bearer {key}"},
                )
                if resp.status_code == 200:
                    result["status"] = "connected"
                    result["environment"] = env
                    result["message"] = f"✅ OANDA {env.upper()} connection verified"
                else:
                    result["status"] = "auth_failed"
                    result["http_status"] = resp.status_code
                    result["message"] = f"❌ OANDA auth failed: {resp.text[:200]}"
        else:
            return {"status": "unsupported_broker", "broker": broker,
                    "supported": ["tradovate", "alpaca", "oanda"]}

    except Exception as exc:
        result["status"] = "connection_error"
        result["error"] = str(exc)
        result["message"] = f"❌ Connection failed: {exc}"

    if result.get("status") == "connected":
        state = _load_state()
        if broker not in state.brokers_connected:
            state.brokers_connected.append(broker)
        _save_state(state)

    return result


async def validate_data_provider(provider: str) -> dict:
    """
    Step 3b: Test data provider connectivity.
    """
    state = _load_state()
    if not state.risk_acknowledged:
        return {"error": "Risk disclosure must be acknowledged first."}

    result: dict = {"provider": provider, "test": "connectivity"}

    try:
        if provider == "polygon":
            key = os.getenv("POLYGON_API_KEY", "")
            if not key:
                return {"status": "missing_credentials", "provider": provider,
                        "missing": ["POLYGON_API_KEY"], "action": "Set POLYGON_API_KEY env var."}
            import httpx
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                resp = await client.get(
                    "https://api.polygon.io/v2/aggs/ticker/AAPL/range/1/day/2024-01-01/2024-01-02",
                    params={"apiKey": key},
                )
                if resp.status_code == 200:
                    result["status"] = "connected"
                    result["message"] = "✅ Polygon.io API key valid"
                elif resp.status_code == 403:
                    result["status"] = "auth_failed"
                    result["message"] = "❌ Invalid Polygon API key (403)"
                else:
                    result["status"] = "error"
                    result["http_status"] = resp.status_code

        elif provider == "databento":
            key = os.getenv("DATABENTO_API_KEY", "")
            if not key:
                return {"status": "missing_credentials", "provider": provider,
                        "missing": ["DATABENTO_API_KEY"], "action": "Set DATABENTO_API_KEY env var."}
            import httpx
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                resp = await client.get(
                    "https://hist.databento.com/v0/metadata.list_publishers",
                    auth=(key, ""),
                )
                if resp.status_code == 200:
                    result["status"] = "connected"
                    result["message"] = "✅ Databento API key valid"
                    result["publishers_count"] = len(resp.json())
                elif resp.status_code == 401:
                    result["status"] = "auth_failed"
                    result["message"] = "❌ Invalid Databento API key (401)"
                else:
                    result["status"] = "error"
                    result["http_status"] = resp.status_code

        elif provider == "onyx":
            url = os.getenv("ONYX_API_URL", "http://localhost:8085")
            import httpx
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0)) as client:
                    resp = await client.get(f"{url}/health")
                    if resp.status_code == 200:
                        result["status"] = "connected"
                        result["url"] = url
                        result["message"] = f"✅ Onyx reachable at {url}"
                    else:
                        result["status"] = "error"
                        result["http_status"] = resp.status_code
            except Exception as exc:
                result["status"] = "unreachable"
                result["url"] = url
                result["message"] = f"❌ Onyx unreachable: {exc}"
                result["action"] = (
                    "Check that Onyx is running (docker compose up) and Tailscale is connected "
                    f"to the desktop at {url}. See blueprints/ONYX_ALGOCHAINS_IMPLEMENTATION_BLUEPRINT.md"
                )

        elif provider == "fred":
            key = os.getenv("FRED_API_KEY", "")
            if not key:
                return {"status": "missing_credentials", "provider": provider,
                        "missing": ["FRED_API_KEY"],
                        "action": "Get free key at https://fred.stlouisfed.org/docs/api/api_key.html"}
            import httpx
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                resp = await client.get(
                    "https://api.stlouisfed.org/fred/series",
                    params={"series_id": "DGS3MO", "api_key": key, "file_type": "json"},
                )
                if resp.status_code == 200:
                    result["status"] = "connected"
                    result["message"] = "✅ FRED API key valid"
                else:
                    result["status"] = "auth_failed"
                    result["message"] = f"❌ FRED key invalid: HTTP {resp.status_code}"
        else:
            return {"status": "unsupported_provider", "provider": provider,
                    "supported": ["polygon", "databento", "onyx", "fred"]}

    except Exception as exc:
        result["status"] = "connection_error"
        result["error"] = str(exc)

    if result.get("status") == "connected":
        state = _load_state()
        if provider not in state.data_providers_connected:
            state.data_providers_connected.append(provider)
        _save_state(state)

    return result


def generate_mcporter_config(
    ide: str,
    tool_mode: str = "smart",
    extra_env: Optional[dict] = None,
) -> dict:
    """
    Step 7: Generate the mcporter.json / mcp.json config for the user's IDE.
    Includes all connected brokers and providers from onboarding state.

    IDE options: cursor | windsurf | claude | vscode
    tool_mode: 'smart' (25 core tools) or 'full' (all 262 tools)
    """
    state = _load_state()

    env_block: dict = {"ALGOCHAINS_TOOL_MODE": tool_mode}

    # Add broker env vars (values from their actual environment)
    broker_env_map = {
        "tradovate": ["TRADOVATE_CID", "TRADOVATE_SECRET", "TRADOVATE_ENV",
                      "TRADOVATE_DEVICE_ID", "TRADOVATE_USERNAME"],
        "alpaca": ["ALPACA_API_KEY", "ALPACA_SECRET_KEY", "ALPACA_BASE_URL"],
        "oanda": ["OANDA_API_KEY", "OANDA_ACCOUNT_ID", "OANDA_ENVIRONMENT"],
    }
    data_env_map = {
        "polygon": ["POLYGON_API_KEY"],
        "databento": ["DATABENTO_API_KEY"],
        "onyx": ["ONYX_API_URL", "ONYX_API_KEY", "ONYX_ADMIN_EMAIL"],
        "fred": ["FRED_API_KEY"],
    }

    for broker in state.brokers_connected:
        for key in broker_env_map.get(broker, []):
            val = os.getenv(key, "")
            if val:
                env_block[key] = val

    for provider in state.data_providers_connected:
        for key in data_env_map.get(provider, []):
            val = os.getenv(key, "")
            if val:
                env_block[key] = val

    if extra_env:
        env_block.update(extra_env)

    config = {
        "mcpServers": {
            "algochains": {
                "command": "algochains-mcp",
                "env": env_block,
            }
        }
    }

    config_paths = {
        "cursor": "~/.cursor/mcp.json",
        "windsurf": "~/.windsurf/mcp_config.json",
        "claude": "~/Library/Application Support/Claude/claude_desktop_config.json",
        "vscode": "~/.vscode/mcp.json",
    }

    install_command = "pip install algochains-mcp-server"

    return {
        "step": 7,
        "status": "config_generated",
        "ide": ide,
        "config_path": config_paths.get(ide, "~/.cursor/mcp.json"),
        "config": json.dumps(config, indent=2),
        "install_command": install_command,
        "instructions": [
            f"1. Install: {install_command}",
            f"2. Copy config to {config_paths.get(ide, 'your IDE config path')}",
            "3. Restart your IDE",
            "4. The 'algochains' MCP server will appear in your tools list",
            "5. Start with: 'What tools do you have?' or 'Check my bot status'",
        ],
        "warning": (
            "⚠️  The generated config contains your API keys. "
            "Do NOT commit this file to git. Add it to .gitignore."
        ),
        "sse_bridge_optional": (
            "For real-time streaming (SSE), start the SSE bridge: "
            "python -m algochains_mcp.sse_server "
            f"(listens on 127.0.0.1:{os.getenv('ALGOCHAINS_SSE_PORT', '8765')})"
        ),
    }


async def run_smoke_test() -> dict:
    """
    Step 5: End-to-end connectivity check — all configured systems.
    """
    state = _load_state()
    if not state.risk_acknowledged:
        return {"error": "Risk disclosure must be acknowledged first."}

    results = []

    # Test each connected broker
    for broker in state.brokers_connected:
        r = await validate_broker_connection(broker)
        results.append({"system": f"Broker: {broker}", "status": r.get("status"), "message": r.get("message", "")})

    # Test each connected data provider
    for provider in state.data_providers_connected:
        r = await validate_data_provider(provider)
        results.append({"system": f"Data: {provider}", "status": r.get("status"), "message": r.get("message", "")})

    # Test MCP server itself
    try:
        from algochains_mcp.server import app as _mcp_app
        results.append({"system": "MCP Server", "status": "connected", "message": "✅ MCP server importable"})
    except Exception as exc:
        results.append({"system": "MCP Server", "status": "error", "message": str(exc)})

    # Test guardrails
    try:
        from algochains_mcp.trading_guardrails import get_guardrails
        gs = get_guardrails().get_status()
        all_clear = gs.get("all_clear", False)
        results.append({
            "system": "Trading Guardrails",
            "status": "connected" if all_clear else "warning",
            "message": "✅ Circuit breakers active, all CLOSED" if all_clear else
                       "⚠️  Some circuit breakers OPEN — check get_circuit_breaker_status",
        })
    except Exception as exc:
        results.append({"system": "Trading Guardrails", "status": "error", "message": str(exc)})

    all_passed = all(r["status"] == "connected" for r in results)

    if all_passed:
        state.smoke_test_passed = True
        state.onboarding_complete = True
        state.completed_at = time.time()
        _save_state(state)

    return {
        "step": 5,
        "status": "passed" if all_passed else "partial",
        "all_systems_go": all_passed,
        "results": results,
        "passed_count": sum(1 for r in results if r["status"] == "connected"),
        "total_count": len(results),
        "next_step": "generate_mcporter_config" if all_passed else "fix_failing_systems",
        "onboarding_complete": all_passed,
        "message": (
            "🚀 All systems connected! You're ready to trade." if all_passed
            else "⚠️  Some systems need attention. Fix failing systems before trading."
        ),
    }


def set_algochains_api_key(api_key: str) -> dict:
    """
    Step 4: Configure your AlgoChains developer API key (ac_live_* or ac_test_*).

    This key enables marketplace access, bridge connectivity, and the hosted API.
    Create one at algochains.ai/account/developer-keys/ or via create_developer_key MCP tool.
    The key is validated against an authenticated bridge endpoint before being accepted.
    """
    import re
    if not api_key or not re.match(r"^ac_(live|test)_", api_key):
        return {
            "error": "Invalid key format",
            "message": "AlgoChains developer keys must start with 'ac_live_' or 'ac_test_'.",
            "hint": "Get a key at algochains.ai/account/developer-keys/ or call create_developer_key.",
        }

    # Attempt live validation against an authenticated bridge endpoint.
    _bridge_url = os.environ.get("ALGOCHAINS_BRIDGE_URL", "https://api.algochains.ai").rstrip("/")
    validated = False
    validation_detail = "Bridge validation skipped (ALGOCHAINS_BRIDGE_URL not reachable)"
    try:
        import json
        import urllib.request
        body = json.dumps({"tool": "detect_market_regime", "arguments": {}}).encode("utf-8")
        req = urllib.request.Request(
            f"{_bridge_url}/api/mcp",
            data=body,
            headers={"X-Api-Key": api_key, "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                validated = True
                validation_detail = f"Validated against {_bridge_url}"
    except Exception as exc:
        validation_detail = f"Bridge validation failed: {exc}"

    state = _load_state()
    state.algochains_key_set = validated
    _save_state(state)

    # Set in process env so subsequent tools can use it immediately
    os.environ["AC_DEV_KEY"] = api_key

    return {
        "status": "ok" if validated else "error",
        "step": 4,
        "api_key_prefix": api_key[:12] + "***",
        "validated_against_bridge": validated,
        "validation_detail": validation_detail,
        "message": (
            "✅ AlgoChains API key configured and validated."
            if validated
            else "⚠️  API key format accepted, but bridge validation failed; onboarding progress was not advanced."
        ),
        "next_step": "run_onboarding_smoke_test" if validated else "test_bridge_connection",
    }


def set_guardrail_preferences(
    notify_on_daily_loss_pct: float = 80.0,
    pause_on_consecutive_losses: int = 3,
    slack_alerts_enabled: bool = False,
) -> dict:
    """
    Step 6: Configure guardrail notification preferences.

    Note: Hard-coded safety limits (daily loss $500, 15% max drawdown, VIX>35 gate)
    CANNOT be changed here — they are enforced at the server level. This step
    only configures when you want to be notified (not when trading stops).

    Parameters:
      notify_on_daily_loss_pct: Alert when daily loss reaches this % of $500 limit
                                (default 80% = $400 loss triggers notification)
      pause_on_consecutive_losses: Pause and alert after N consecutive losing trades
      slack_alerts_enabled: Enable Slack notifications (requires SLACK_BOT_TOKEN)
    """
    if not (0 < notify_on_daily_loss_pct <= 100):
        return {"error": "notify_on_daily_loss_pct must be between 0 and 100"}
    if not (0 < pause_on_consecutive_losses <= 20):
        return {"error": "pause_on_consecutive_losses must be between 1 and 20"}

    prefs = {
        "notify_on_daily_loss_pct": notify_on_daily_loss_pct,
        "notify_on_daily_loss_usd": round(500 * notify_on_daily_loss_pct / 100, 2),
        "pause_on_consecutive_losses": pause_on_consecutive_losses,
        "slack_alerts_enabled": slack_alerts_enabled,
    }

    # Persist to state directory
    try:
        prefs_file = _STATE_DIR / "guardrail_prefs.json"
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        prefs_file.write_text(json.dumps(prefs, indent=2))
    except Exception as exc:
        logger.warning("Could not persist guardrail prefs: %s", exc)

    return {
        "status": "ok",
        "step": 6,
        "preferences": prefs,
        "hard_limits": {
            "daily_loss_hard_stop_usd": 500,
            "max_drawdown_pct": 15,
            "vix_gate_threshold": 35,
            "note": "These limits cannot be changed — they are enforced server-side.",
        },
        "next_step": "generate_ide_config",
        "message": "✅ Guardrail preferences saved.",
    }


def get_onboarding_status() -> dict:
    """Returns current onboarding progress for the user."""
    state = _load_state()
    steps_done = []
    steps_remaining = []

    # Check guardrail prefs saved
    _prefs_saved = (_STATE_DIR / "guardrail_prefs.json").exists()

    step_map = [
        ("risk_disclosure", state.risk_acknowledged,
         "Call start_onboarding() then acknowledge_risk_disclosure()"),
        ("broker_connected", bool(state.brokers_connected),
         "Call get_broker_setup_guide(broker='tradovate') then validate_broker_connection()"),
        ("data_providers", bool(state.data_providers_connected),
         "Call get_data_provider_setup_guide(provider='polygon') etc."),
        ("algochains_api_key", state.algochains_key_set,
         "Call set_algochains_api_key(api_key='ac_live_...') — get key from create_developer_key or algochains.ai"),
        ("smoke_test", state.smoke_test_passed,
         "Call run_smoke_test() to verify all connections"),
        ("guardrail_prefs", _prefs_saved,
         "Call set_guardrail_preferences() to configure notification thresholds (optional)"),
        ("onboarding_complete", state.onboarding_complete,
         "Call generate_ide_config(ide='cursor') to finish"),
    ]

    for step_name, done, todo in step_map:
        if done:
            steps_done.append(step_name)
        else:
            steps_remaining.append({"step": step_name, "action": todo})

    return {
        "session_id": state.session_id,
        "progress_pct": int(len(steps_done) / len(step_map) * 100),
        "steps_done": steps_done,
        "steps_remaining": steps_remaining,
        "brokers_connected": state.brokers_connected,
        "data_providers_connected": state.data_providers_connected,
        "algochains_key_set": state.algochains_key_set,
        "onboarding_complete": state.onboarding_complete,
        "next_action": steps_remaining[0]["action"] if steps_remaining else "✅ Onboarding complete!",
    }
