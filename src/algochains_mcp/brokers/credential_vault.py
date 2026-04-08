"""Multi-broker credential vault — centralized credential management.

Single place to check which brokers are configured, what credentials are
missing, and the health of each connector. Used by:
  - Server.py dispatch (check_broker_credentials tool)
  - Onboarding flow (setup wizard)
  - Health audit (trading_system_health_audit skill)
  - Prop fund pipeline (know which brokers are live before deployment)

Credential security: credentials NEVER leave this module as plaintext.
All functions return presence booleans and masked previews only.

Supports:
  Tradovate (futures — live bots)
  Alpaca (equities/crypto — paper + live)
  OANDA (forex)
  Schwab (equities + options)
  E*TRADE (equities + options)
  Rithmic (prop fund futures — needs vendor agreement)
  Kalshi (prediction markets)
  Polymarket (prediction markets)
  Interactive Brokers (equities + futures + options)
  Databento (tick data)
  Polygon (bars + options)
  FRED (macro economics)
  EIA (energy data)
  Onyx (RAG intelligence)
  OpenAI / Anthropic (AI models)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Optional


# ---------------------------------------------------------------------------
# Broker specs
# ---------------------------------------------------------------------------

@dataclass
class BrokerSpec:
    name: str
    category: str          # execution | data | ai | prediction
    required_vars: list[str]
    optional_vars: list[str]
    connector_file: str
    is_live: bool
    signup_url: str
    notes: str = ""
    health_check_func: Optional[str] = None   # function name to call for health check


BROKER_SPECS: dict[str, BrokerSpec] = {
    # ── Execution brokers ────────────────────────────────────────────────────
    "tradovate": BrokerSpec(
        name="Tradovate",
        category="execution",
        required_vars=["TRADOVATE_CID", "TRADOVATE_SECRET", "TRADOVATE_DEVICE_ID"],
        optional_vars=["TRADOVATE_DEMO", "TRADOVATE_ACCOUNT_ID"],
        connector_file="brokers/tradovate.py",
        is_live=True,
        signup_url="https://trader.tradovate.com/",
        notes="Primary futures broker. CL, MNQ, MES, NQ live bots.",
        health_check_func="check_tradovate_status",
    ),
    "alpaca": BrokerSpec(
        name="Alpaca",
        category="execution",
        required_vars=["ALPACA_API_KEY", "ALPACA_SECRET_KEY"],
        optional_vars=["ALPACA_PAPER", "ALPACA_BASE_URL"],
        connector_file="brokers/alpaca_connector.py",
        is_live=True,
        signup_url="https://app.alpaca.markets/signup",
        notes="Equities + crypto paper trading. Subscribable Alpaca bots.",
    ),
    "oanda": BrokerSpec(
        name="OANDA",
        category="execution",
        required_vars=["OANDA_API_KEY", "OANDA_ACCOUNT_ID"],
        optional_vars=["OANDA_ENVIRONMENT"],
        connector_file="brokers/oanda_connector.py",
        is_live=True,
        signup_url="https://www.oanda.com/us-en/trading/",
        notes="Forex broker. GBPUSD breakout strategy.",
    ),
    "schwab": BrokerSpec(
        name="Charles Schwab",
        category="execution",
        required_vars=["SCHWAB_CLIENT_ID", "SCHWAB_CLIENT_SECRET"],
        optional_vars=["SCHWAB_REDIRECT_URI", "SCHWAB_ACCESS_TOKEN"],
        connector_file="brokers/schwab_connector.py",
        is_live=False,
        signup_url="https://developer.schwab.com/",
        notes="Largest US retail broker. Equities + options + futures. Stub not yet implemented.",
    ),
    "etrade": BrokerSpec(
        name="E*TRADE (Morgan Stanley)",
        category="execution",
        required_vars=["ETRADE_CONSUMER_KEY", "ETRADE_CONSUMER_SECRET",
                       "ETRADE_ACCESS_TOKEN", "ETRADE_ACCESS_TOKEN_SECRET"],
        optional_vars=["ETRADE_SANDBOX"],
        connector_file="brokers/etrade_connector.py",
        is_live=True,
        signup_url="https://developer.etrade.com/getting-started",
        notes="Equities + options via OAuth 1.0a. R-multiple sizing built in.",
    ),
    "rithmic": BrokerSpec(
        name="Rithmic",
        category="execution",
        required_vars=["RITHMIC_SYSTEM_NAME", "RITHMIC_USER_ID", "RITHMIC_PASSWORD"],
        optional_vars=["RITHMIC_PLANT_NAME", "RITHMIC_GATEWAY", "RITHMIC_DRY_RUN"],
        connector_file="brokers/rithmic_connector.py",
        is_live=False,
        signup_url="https://www.rithmic.com/contacts",
        notes=(
            "Prop fund execution backbone (Apex, Topstep, MyFundedFutures, TradeDay, Bulenox, Earn2Trade). "
            "Requires vendor NDA + developer agreement. 1-2 week business process. "
            "Run in DRY_RUN mode until credentials obtained."
        ),
        health_check_func="check_rithmic_status",
    ),
    "ibkr": BrokerSpec(
        name="Interactive Brokers",
        category="execution",
        required_vars=["IBKR_ACCOUNT_ID", "IBKR_CLIENT_ID"],
        optional_vars=["IBKR_HOST", "IBKR_PORT", "IBKR_PAPER"],
        connector_file="brokers/ibkr_connector.py",
        is_live=True,
        signup_url="https://www.interactivebrokers.com/en/trading/ib-api.php",
        notes="Equities, options, futures, forex via TWS API.",
    ),

    # ── Prediction markets ────────────────────────────────────────────────────
    "kalshi": BrokerSpec(
        name="Kalshi",
        category="prediction",
        required_vars=["KALSHI_API_KEY", "KALSHI_PRIVATE_KEY_PATH"],
        optional_vars=["KALSHI_ENVIRONMENT"],
        connector_file="brokers/kalshi_connector.py",
        is_live=True,
        signup_url="https://kalshi.com/",
        notes="Regulated prediction markets. RSA-PSS authentication.",
    ),
    "polymarket": BrokerSpec(
        name="Polymarket",
        category="prediction",
        required_vars=["POLYMARKET_PRIVATE_KEY", "POLYMARKET_WALLET_ADDRESS"],
        optional_vars=[],
        connector_file="polymarket.py",
        is_live=True,
        signup_url="https://polymarket.com/",
        notes="Crypto-based prediction markets on Polygon blockchain.",
    ),

    # ── Market data providers ──────────────────────────────────────────────────
    "databento": BrokerSpec(
        name="Databento",
        category="data",
        required_vars=["DATABENTO_API_KEY"],
        optional_vars=[],
        connector_file="",
        is_live=True,
        signup_url="https://databento.com/",
        notes="Primary tick data source. MNQ, NQ, MES, CL, ES historical + live.",
    ),
    "polygon": BrokerSpec(
        name="Polygon.io",
        category="data",
        required_vars=["POLYGON_API_KEY"],
        optional_vars=[],
        connector_file="",
        is_live=True,
        signup_url="https://polygon.io/",
        notes="OHLCV bars + options data + real-time feeds.",
    ),
    "fred": BrokerSpec(
        name="FRED (Federal Reserve)",
        category="data",
        required_vars=["FRED_API_KEY"],
        optional_vars=[],
        connector_file="us_economics.py",
        is_live=True,
        signup_url="https://fred.stlouisfed.org/docs/api/api_key.html",
        notes="Macro economic data: GDP, CPI, unemployment, Fed funds rate, yield curve.",
    ),
    "eia": BrokerSpec(
        name="EIA (Energy Information Administration)",
        category="data",
        required_vars=["EIA_API_KEY"],
        optional_vars=[],
        connector_file="us_economics.py",
        is_live=True,
        signup_url="https://www.eia.gov/opendata/register.php",
        notes="Crude oil inventory, production, storage data. Critical for CL bot.",
    ),

    # ── AI models ───────────────────────────────────────────────────────────────
    "openai": BrokerSpec(
        name="OpenAI",
        category="ai",
        required_vars=["OPENAI_API_KEY"],
        optional_vars=["OPENAI_ORG_ID"],
        connector_file="",
        is_live=True,
        signup_url="https://platform.openai.com/signup",
        notes="GPT-4o for FinBERT sentiment fallback + Onyx embeddings.",
    ),
    "anthropic": BrokerSpec(
        name="Anthropic",
        category="ai",
        required_vars=["ANTHROPIC_API_KEY"],
        optional_vars=[],
        connector_file="",
        is_live=True,
        signup_url="https://console.anthropic.com/",
        notes="Claude for MCP tool calls + multi-agent debate layer.",
    ),
    "onyx": BrokerSpec(
        name="Onyx Intelligence (AlgoChains)",
        category="ai",
        required_vars=["ONYX_API_KEY"],
        optional_vars=["ONYX_BASE_URL", "ONYX_CHAT_SESSION_ID"],
        connector_file="onyx_intelligence/onyx_client.py",
        is_live=True,
        signup_url="https://algochains.ai/onyx",
        notes="Internal RAG intelligence engine. Strategy research + institutional flow.",
    ),
}


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def _mask(value: str) -> str:
    """Mask credential for safe display (show first 4 + last 4 chars)."""
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}...{value[-4:]}"


def check_broker_credentials(broker: str) -> dict:
    """Check configured credentials for a specific broker.

    Returns presence status only — NEVER returns actual credential values.
    """
    spec = BROKER_SPECS.get(broker.lower())
    if not spec:
        available = list(BROKER_SPECS.keys())
        return {"error": f"Unknown broker: {broker}", "available_brokers": available}

    missing_required = []
    present_required = {}
    present_optional = {}

    for var in spec.required_vars:
        val = os.environ.get(var)
        if val:
            present_required[var] = _mask(val)
        else:
            missing_required.append(var)

    for var in spec.optional_vars:
        val = os.environ.get(var)
        if val:
            present_optional[var] = _mask(val)

    ready = len(missing_required) == 0

    return {
        "broker": broker,
        "display_name": spec.name,
        "category": spec.category,
        "ready": ready,
        "is_live_connector": spec.is_live,
        "missing_required_vars": missing_required,
        "configured_required_vars": present_required,
        "configured_optional_vars": present_optional,
        "connector_file": spec.connector_file,
        "signup_url": spec.signup_url,
        "notes": spec.notes,
    }


def check_all_broker_credentials() -> dict:
    """Check credentials for ALL supported brokers."""
    results = {}
    summary = {
        "execution": {"ready": 0, "total": 0},
        "data": {"ready": 0, "total": 0},
        "prediction": {"ready": 0, "total": 0},
        "ai": {"ready": 0, "total": 0},
    }

    for broker in BROKER_SPECS:
        result = check_broker_credentials(broker)
        results[broker] = result

        cat = BROKER_SPECS[broker].category
        if cat in summary:
            summary[cat]["total"] += 1
            if result["ready"]:
                summary[cat]["ready"] += 1

    critical_execution = ["tradovate", "alpaca"]
    critical_data = ["databento", "polygon"]
    critical_ai = ["openai", "anthropic"]

    missing_critical = [
        b for b in critical_execution + critical_data + critical_ai
        if not results[b]["ready"]
    ]

    return {
        "checked_at": datetime.now(tz=timezone.utc).isoformat(),
        "summary": summary,
        "critical_missing": missing_critical,
        "system_ready": len(missing_critical) == 0,
        "results": results,
        "prop_fund_ready": results.get("rithmic", {}).get("ready", False) or results.get("tradovate", {}).get("ready", False),
    }


def get_broker_onboarding_guide(broker: str) -> dict:
    """Get step-by-step onboarding instructions for a broker."""
    spec = BROKER_SPECS.get(broker.lower())
    if not spec:
        return {"error": f"Unknown broker: {broker}"}

    cfg = check_broker_credentials(broker)

    guide = {
        "broker": broker,
        "display_name": spec.name,
        "configured": cfg["ready"],
        "signup_url": spec.signup_url,
        "steps": [],
    }

    if broker == "tradovate":
        guide["steps"] = [
            "1. Create Tradovate account at https://trader.tradovate.com/",
            "2. Navigate to: Settings → API Access → New Application",
            "3. Copy Consumer ID (CID) and Device ID",
            "4. Add to .env: TRADOVATE_CID=xxx TRADOVATE_SECRET=xxx TRADOVATE_DEVICE_ID=xxx",
            "5. Run: python3 tradovate_token_guardian.py",
            "6. Verify: check tradovate_session.json exists and is not expired",
        ]
    elif broker == "rithmic":
        guide["steps"] = [
            "1. Go to https://www.rithmic.com/contacts and fill out API access form",
            "2. Sign the Rithmic Developer Agreement (NDA required, 1-2 weeks)",
            "3. Receive RITHMIC_SYSTEM_NAME from Rithmic (unique per app)",
            "4. Receive test credentials (plant: 'Chicago', user_id, password)",
            "5. Install: pip install pyrithmic",
            "6. Add to .env: RITHMIC_SYSTEM_NAME=xxx RITHMIC_PLANT_NAME=Chicago RITHMIC_USER_ID=xxx RITHMIC_PASSWORD=xxx",
            "7. Set RITHMIC_DRY_RUN=false to go live",
            "8. Connect your prop fund account via: register_prop_fund_account()",
            "9. Note: Apex Trader Funding supports Tradovate too — check their setup page",
        ]
    elif broker == "etrade":
        guide["steps"] = [
            "1. Sign up at https://developer.etrade.com/getting-started",
            "2. Create an application to get Consumer Key and Consumer Secret",
            "3. Start OAuth dance: GET /oauth/request_token with your consumer key",
            "4. Redirect user to: https://us.etrade.com/e/t/etws/authorize?key=REQUEST_TOKEN",
            "5. User authorizes → gets verifier code",
            "6. Exchange: GET /oauth/access_token?oauth_verifier=CODE",
            "7. Add to .env: ETRADE_CONSUMER_KEY=xxx ETRADE_CONSUMER_SECRET=xxx ETRADE_ACCESS_TOKEN=xxx ETRADE_ACCESS_TOKEN_SECRET=xxx",
            "8. Start with ETRADE_SANDBOX=true to test",
            "9. Tokens expire daily — renew with: GET /oauth/renew_access_token",
        ]
    elif broker == "schwab":
        guide["steps"] = [
            "1. Sign up at https://developer.schwab.com/ (requires Schwab brokerage account)",
            "2. Create an Individual Developer App",
            "3. OAuth 2.0 + PKCE flow: generate code_verifier and code_challenge",
            "4. Authorization URL: https://api.schwabapi.com/v1/oauth/authorize",
            "5. Exchange code for token: POST /v1/oauth/token",
            "6. Add to .env: SCHWAB_CLIENT_ID=xxx SCHWAB_CLIENT_SECRET=xxx",
            "7. Schwab API is rate-limited to 120 requests/min",
        ]
    elif broker == "alpaca":
        guide["steps"] = [
            "1. Create account at https://app.alpaca.markets/signup",
            "2. Navigate to: Paper Trading → API Keys → Generate New Key",
            "3. Copy API Key ID and Secret Key",
            "4. Add to .env: ALPACA_API_KEY=xxx ALPACA_SECRET_KEY=xxx ALPACA_PAPER=true",
            "5. For live trading, complete identity verification + fund account",
            "6. Set ALPACA_PAPER=false for live trading",
        ]

    if not guide["steps"]:
        guide["steps"] = [
            f"1. Sign up at: {spec.signup_url}",
            f"2. Obtain credentials: {', '.join(spec.required_vars)}",
            "3. Add credentials to .env file",
        ]

    return guide


def get_prop_fund_broker_options() -> dict:
    """List brokers that support prop fund evaluation accounts."""
    return {
        "prop_fund_brokers": {
            "rithmic": {
                "description": "Preferred: Works with all major US prop funds",
                "funds": ["Apex Trader Funding", "Topstep", "MyFundedFutures",
                          "TradeDay", "Bulenox", "Earn2Trade"],
                "configured": check_broker_credentials("rithmic")["ready"],
                "vendor_required": True,
                "dry_run_available": True,
            },
            "tradovate": {
                "description": "Alternative: Some funds accept Tradovate connections",
                "funds": ["Apex Trader Funding (Tradovate path)", "TradeDay"],
                "configured": check_broker_credentials("tradovate")["ready"],
                "vendor_required": False,
                "note": "Apex supports both Rithmic and Tradovate connections",
            },
        },
        "recommendation": (
            "Use Tradovate for immediate prop fund deployment (already configured). "
            "Sign Rithmic vendor agreement for full prop fund ecosystem access."
        ),
    }
