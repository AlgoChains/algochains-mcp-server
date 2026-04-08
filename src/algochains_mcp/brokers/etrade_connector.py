"""E*TRADE OAuth 1.0a connector for equities, options, and ETFs.

E*TRADE (owned by Morgan Stanley) is a top-5 US retail broker providing:
  - Full US equities + options + ETF trading
  - Real-time Level 1 + Level 2 quotes
  - Options chains with complete Greeks (delta, gamma, theta, vega, IV)
  - R-multiple position sizing for options and equities

API docs: https://developer.etrade.com/getting-started
Auth: OAuth 1.0a (consumer key/secret + access token/secret per-user)

Required environment variables:
  ETRADE_CONSUMER_KEY           — From E*TRADE developer portal
  ETRADE_CONSUMER_SECRET        — From E*TRADE developer portal
  ETRADE_ACCESS_TOKEN           — User-level token (after OAuth dance)
  ETRADE_ACCESS_TOKEN_SECRET    — User-level token secret
  ETRADE_SANDBOX=true/false     — Use sandbox (true by default for safety)

OAuth 1.0a flow:
  1. Get request token: GET /oauth/request_token
  2. User authorizes: browser → https://us.etrade.com/e/t/etws/authorize
  3. Get access token: GET /oauth/access_token?verifier=CODE
  4. Access tokens expire daily — must renew via /oauth/renew_access_token

Rate limits: 2,000 API calls/day (standard tier), 5,000/day (premium)
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode

logger = logging.getLogger("algochains_mcp.brokers.etrade")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_CONSUMER_KEY = os.environ.get("ETRADE_CONSUMER_KEY", "")
_CONSUMER_SECRET = os.environ.get("ETRADE_CONSUMER_SECRET", "")
_ACCESS_TOKEN = os.environ.get("ETRADE_ACCESS_TOKEN", "")
_ACCESS_SECRET = os.environ.get("ETRADE_ACCESS_TOKEN_SECRET", "")
_SANDBOX = os.environ.get("ETRADE_SANDBOX", "true").lower() != "false"

BASE_URL_LIVE = "https://api.etrade.com/v1"
BASE_URL_SANDBOX = "https://apisb.etrade.com/v1"

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ETradeQuote:
    symbol: str
    last_price: float
    bid: float
    ask: float
    bid_size: int
    ask_size: int
    volume: int
    change_pct: float
    high: float
    low: float
    open: float
    prev_close: float
    market_cap: float = 0.0
    pe_ratio: float = 0.0
    timestamp: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class OptionContract:
    symbol: str
    option_type: str    # "CALL" or "PUT"
    strike: float
    expiry: str         # "YYYY-MM-DD"
    bid: float
    ask: float
    last: float
    volume: int
    open_interest: int
    implied_vol: float  # 0.35 = 35% IV
    delta: float
    gamma: float
    theta: float
    vega: float
    intrinsic_value: float
    time_value: float
    in_the_money: bool

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2


# ---------------------------------------------------------------------------
# R-Multiple position sizing (Van Tharp methodology)
# ---------------------------------------------------------------------------

def compute_r_multiple_size(
    symbol: str,
    entry_price: float,
    stop_loss_price: float,
    capital_usd: float,
    risk_pct: float = 1.0,
    asset_type: str = "equity",
) -> dict:
    """Compute position size using R-Multiple methodology.

    R = Entry - StopLoss (risk per share/contract in price points)
    Position = (Capital × Risk%) / (R × share/tick_value)

    This is a third sizing method alongside Kelly and Vol Targeting.
    Particularly useful for options (defined risk) and equities.

    Args:
        symbol:          Ticker symbol
        entry_price:     Planned entry price
        stop_loss_price: Stop loss price
        capital_usd:     Total capital to risk from
        risk_pct:        % of capital to risk per trade (default 1%)
        asset_type:      "equity", "futures", "option_contract"

    Returns:
        dict with contracts/shares, R values, and R-multiple targets
    """
    r_points = abs(entry_price - stop_loss_price)
    risk_dollars = capital_usd * (risk_pct / 100)

    if asset_type == "futures":
        # Use futures tick values from volatility_targeting module
        try:
            from algochains_mcp.volatility_targeting import INSTRUMENT_SPECS
            spec = INSTRUMENT_SPECS.get(symbol.upper(), {"tick_value": 10.0, "tick_size": 0.25})
            r_ticks = r_points / spec["tick_size"]
            r_dollars_per_contract = r_ticks * spec["tick_value"]
            contracts = int(risk_dollars / max(r_dollars_per_contract, 0.01))
            qty_label = "contracts"
            qty = max(1, contracts)
        except Exception:
            r_dollars_per_contract = r_points
            qty = int(risk_dollars / max(r_dollars_per_contract, 0.01))
            qty_label = "contracts"
    elif asset_type == "option_contract":
        # Each option contract = 100 shares
        r_dollars_per_contract = r_points * 100
        qty = int(risk_dollars / max(r_dollars_per_contract, 0.01))
        qty_label = "contracts"
    else:
        # Equity: risk per share = R in dollars
        r_dollars_per_contract = r_points
        qty = int(risk_dollars / max(r_dollars_per_contract, 0.01))
        qty_label = "shares"

    return {
        "symbol": symbol,
        "asset_type": asset_type,
        f"{qty_label}": max(1, qty),
        "r_points": round(r_points, 4),
        "r_dollars_per_unit": round(r_dollars_per_contract, 2),
        "risk_dollars_total": round(risk_dollars, 2),
        "risk_pct": risk_pct,
        "method": "r_multiple",
        "entry_price": entry_price,
        "stop_loss_price": stop_loss_price,
        "r_multiple_targets": {
            "1R_profit": round(risk_dollars, 2),
            "2R_profit": round(risk_dollars * 2, 2),
            "3R_profit": round(risk_dollars * 3, 2),
            "5R_profit": round(risk_dollars * 5, 2),
        },
        "risk_reward_needed_for_breakeven": "1:1 (win rate > 50% needed)",
    }


# ---------------------------------------------------------------------------
# Black-Scholes option pricing (for Greeks computation)
# ---------------------------------------------------------------------------

def _norm_cdf(x: float) -> float:
    """Cumulative normal distribution function (Abramowitz & Stegun approximation)."""
    import math
    if x < 0:
        return 1 - _norm_cdf(-x)
    k = 1 / (1 + 0.2316419 * x)
    poly = k * (0.319381530 + k * (-0.356563782 + k * (1.781477937 + k * (-1.821255978 + k * 1.330274429))))
    return 1 - (1 / math.sqrt(2 * math.pi)) * math.exp(-x * x / 2) * poly


def compute_option_greeks(
    option_type: str,
    underlying_price: float,
    strike: float,
    time_to_expiry_years: float,
    risk_free_rate: float,
    implied_vol: float,
) -> dict:
    """Compute option Greeks using Black-Scholes model.

    Args:
        option_type:          "CALL" or "PUT"
        underlying_price:     Current stock/index price (S)
        strike:               Option strike price (K)
        time_to_expiry_years: Time to expiry in years (e.g., 30 days = 30/365)
        risk_free_rate:       Annual risk-free rate (e.g., 0.05 = 5%)
        implied_vol:          Implied volatility (e.g., 0.25 = 25%)

    Returns:
        dict with price, delta, gamma, theta, vega, rho
    """
    import math

    S, K, T, r, σ = (underlying_price, strike, time_to_expiry_years,
                     risk_free_rate, implied_vol)

    if T <= 0 or σ <= 0:
        return {"error": "Invalid inputs: T and σ must be positive"}

    d1 = (math.log(S / K) + (r + 0.5 * σ ** 2) * T) / (σ * math.sqrt(T))
    d2 = d1 - σ * math.sqrt(T)

    N_d1 = _norm_cdf(d1)
    N_d2 = _norm_cdf(d2)
    N_neg_d1 = _norm_cdf(-d1)
    N_neg_d2 = _norm_cdf(-d2)
    n_d1 = (1 / math.sqrt(2 * math.pi)) * math.exp(-0.5 * d1 ** 2)

    if option_type.upper() == "CALL":
        price = S * N_d1 - K * math.exp(-r * T) * N_d2
        delta = N_d1
        rho = K * T * math.exp(-r * T) * N_d2 / 100
    else:
        price = K * math.exp(-r * T) * N_neg_d2 - S * N_neg_d1
        delta = N_d1 - 1
        rho = -K * T * math.exp(-r * T) * N_neg_d2 / 100

    gamma = n_d1 / (S * σ * math.sqrt(T))
    theta = (-(S * n_d1 * σ) / (2 * math.sqrt(T)) - r * K * math.exp(-r * T) * N_d2) / 365
    vega = S * n_d1 * math.sqrt(T) / 100

    return {
        "option_type": option_type.upper(),
        "underlying_price": S,
        "strike": K,
        "time_to_expiry_years": round(T, 6),
        "implied_vol": round(σ * 100, 2),
        "risk_free_rate": round(r * 100, 2),
        "price": round(price, 4),
        "delta": round(delta, 4),
        "gamma": round(gamma, 6),
        "theta_per_day": round(theta, 4),
        "vega_per_1pct_iv": round(vega, 4),
        "rho_per_1pct_rate": round(rho, 4),
        "in_the_money": (S > K if option_type.upper() == "CALL" else S < K),
        "intrinsic_value": round(max(0, S - K if option_type.upper() == "CALL" else K - S), 4),
        "time_value": round(max(0, price - max(0, S - K if option_type.upper() == "CALL" else K - S)), 4),
    }


def find_optimal_strike(
    option_type: str,
    underlying_price: float,
    target_delta: float,
    expiry_str: str,
    risk_free_rate: float = 0.05,
    implied_vol: float = 0.25,
) -> dict:
    """Find the option strike closest to a target delta.

    Used to select strikes for common strategies:
      - target_delta=0.30 → 30-delta for covered calls / cash-secured puts
      - target_delta=0.16 → 16-delta (1 standard deviation OTM)
      - target_delta=0.50 → ATM strike

    Args:
        option_type:    "CALL" or "PUT"
        underlying_price: Current underlying price
        target_delta:   Target delta magnitude (0.01 to 0.99)
        expiry_str:     Expiry date "YYYY-MM-DD"
        risk_free_rate: Annual risk-free rate
        implied_vol:    Implied volatility estimate

    Returns:
        dict with recommended strike, actual delta, and nearby strikes
    """
    import math
    from datetime import date

    expiry = datetime.strptime(expiry_str, "%Y-%m-%d").date()
    today = date.today()
    T = max((expiry - today).days / 365.0, 1 / 365)

    # Search strikes in 0.5-point increments around current price
    strikes = [underlying_price + (i * 0.5) for i in range(-100, 101)]
    best_strike = underlying_price
    best_diff = float("inf")

    for strike in strikes:
        greeks = compute_option_greeks(option_type, underlying_price, strike, T, risk_free_rate, implied_vol)
        if "error" in greeks:
            continue
        actual_delta = abs(greeks["delta"])
        diff = abs(actual_delta - target_delta)
        if diff < best_diff:
            best_diff = diff
            best_strike = strike

    # Get greeks for the best strike
    best_greeks = compute_option_greeks(option_type, underlying_price, best_strike, T, risk_free_rate, implied_vol)

    return {
        "recommended_strike": best_strike,
        "target_delta": target_delta,
        "actual_delta": best_greeks.get("delta"),
        "option_type": option_type.upper(),
        "expiry": expiry_str,
        "days_to_expiry": (expiry - today).days,
        "underlying_price": underlying_price,
        "greeks": best_greeks,
        "strategy_suggestion": _suggest_strategy(option_type, target_delta),
    }


def _suggest_strategy(option_type: str, target_delta: float) -> str:
    if option_type.upper() == "CALL":
        if target_delta >= 0.45:
            return "Buy ATM call (directional long delta)"
        elif target_delta >= 0.25:
            return "Sell covered call (income on long stock position)"
        else:
            return "Sell far OTM call (high probability income, limited upside)"
    else:
        if target_delta >= 0.45:
            return "Buy ATM put (directional short delta / portfolio hedge)"
        elif target_delta >= 0.25:
            return "Sell cash-secured put (acquire stock at discount)"
        else:
            return "Sell far OTM put (high probability income)"


# ---------------------------------------------------------------------------
# Main connector class
# ---------------------------------------------------------------------------

class ETradeConnector:
    """E*TRADE REST API connector with OAuth 1.0a authentication.

    Handles request signing, token management, quotes, options chains,
    and order placement for equities and options.
    """

    def __init__(self, sandbox: bool = None):
        self.sandbox = sandbox if sandbox is not None else _SANDBOX
        self.base_url = BASE_URL_SANDBOX if self.sandbox else BASE_URL_LIVE
        self._session = None

    def _get_session(self):
        if not self._session:
            try:
                from requests_oauthlib import OAuth1Session
                self._session = OAuth1Session(
                    client_key=_CONSUMER_KEY,
                    client_secret=_CONSUMER_SECRET,
                    resource_owner_key=_ACCESS_TOKEN,
                    resource_owner_secret=_ACCESS_SECRET,
                )
            except ImportError:
                raise RuntimeError("requests_oauthlib not installed. Run: pip install requests requests-oauthlib")
        return self._session

    def _is_configured(self) -> dict:
        missing = []
        if not _CONSUMER_KEY:
            missing.append("ETRADE_CONSUMER_KEY")
        if not _CONSUMER_SECRET:
            missing.append("ETRADE_CONSUMER_SECRET")
        if not _ACCESS_TOKEN:
            missing.append("ETRADE_ACCESS_TOKEN")
        if not _ACCESS_SECRET:
            missing.append("ETRADE_ACCESS_TOKEN_SECRET")
        if missing:
            return {
                "configured": False,
                "missing": missing,
                "signup": "https://developer.etrade.com/getting-started",
            }
        return {"configured": True}

    def check_status(self) -> dict:
        """Check E*TRADE credential configuration."""
        cfg = self._is_configured()
        return {
            **cfg,
            "mode": "sandbox" if self.sandbox else "live",
            "base_url": self.base_url,
            "supported_asset_classes": ["equities", "options", "etfs", "mutual_funds"],
            "supported_tools": [
                "get_quotes", "get_option_chain", "place_order",
                "get_account_portfolio", "compute_r_multiple_size",
                "compute_option_greeks", "find_optimal_strike",
            ],
        }

    def get_quotes(self, symbols: list[str], detail: str = "INTRADAY") -> dict:
        """Get real-time quotes for a list of symbols.

        Args:
            symbols: List of tickers (e.g., ["AAPL", "SPY", "QQQ"])
            detail: FUNDAMENTAL | INTRADAY | OPTIONS | WEEK_52 | MF_ETF

        Returns:
            dict with quotes per symbol
        """
        cfg = self._is_configured()
        if not cfg.get("configured"):
            return cfg

        symbol_str = ",".join(s.upper() for s in symbols)
        url = f"{self.base_url}/market/quote/{symbol_str}"
        try:
            session = self._get_session()
            resp = session.get(url, params={"detailFlag": detail})
            resp.raise_for_status()
            data = resp.json()
            quotes = {}
            for q in data.get("QuoteResponse", {}).get("QuoteData", []):
                sym = q.get("Product", {}).get("symbol", "")
                intraday = q.get("Intraday", q.get("All", {}))
                quotes[sym] = {
                    "symbol": sym,
                    "last": intraday.get("lastTrade", 0),
                    "bid": intraday.get("bid", 0),
                    "ask": intraday.get("ask", 0),
                    "volume": intraday.get("totalVolume", 0),
                    "change_pct": intraday.get("changeClose", 0),
                    "high": intraday.get("high", 0),
                    "low": intraday.get("low", 0),
                    "open": intraday.get("open", 0),
                    "prev_close": intraday.get("previousClose", 0),
                }
            return {"quotes": quotes, "timestamp": datetime.now(tz=timezone.utc).isoformat()}
        except Exception as exc:
            return {"error": str(exc), "symbols": symbols}

    def get_option_chain(
        self,
        symbol: str,
        expiry_year: int = None,
        expiry_month: int = None,
        chain_type: str = "CALLPUT",
        strikes_near_money: int = 10,
    ) -> dict:
        """Get options chain with full Greeks for a symbol.

        Args:
            symbol:              Underlying symbol (e.g., "SPY", "AAPL")
            expiry_year:         Year (e.g., 2026)
            expiry_month:        Month (1-12)
            chain_type:          CALL | PUT | CALLPUT
            strikes_near_money:  Number of strikes above/below current price

        Returns:
            dict with calls and puts lists, each with Greeks
        """
        cfg = self._is_configured()
        if not cfg.get("configured"):
            return cfg

        now = datetime.now()
        url = f"{self.base_url}/market/optionchains"
        params = {
            "symbol": symbol.upper(),
            "expiryYear": expiry_year or now.year,
            "expiryMonth": expiry_month or now.month,
            "chainType": chain_type,
            "noOfStrikes": strikes_near_money * 2,
            "skipAdjusted": True,
            "optionCategory": "STANDARD",
        }
        try:
            session = self._get_session()
            resp = session.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

            calls = []
            puts = []
            chain_data = data.get("OptionChainResponse", {}).get("OptionPair", [])
            for pair in chain_data:
                for side, contract_list in [("Call", calls), ("Put", puts)]:
                    opt = pair.get(side)
                    if opt:
                        contract_list.append({
                            "symbol": opt.get("symbol"),
                            "strike": opt.get("strikePrice"),
                            "expiry": opt.get("expirationDate"),
                            "bid": opt.get("bid", 0),
                            "ask": opt.get("ask", 0),
                            "last": opt.get("lastPrice", 0),
                            "volume": opt.get("volume", 0),
                            "open_interest": opt.get("openInterest", 0),
                            "implied_vol": opt.get("impliedVolatility", 0),
                            "delta": opt.get("delta", 0),
                            "gamma": opt.get("gamma", 0),
                            "theta": opt.get("theta", 0),
                            "vega": opt.get("vega", 0),
                            "in_the_money": opt.get("inTheMoney", False),
                        })

            return {
                "symbol": symbol.upper(),
                "expiry": f"{expiry_year or now.year}-{expiry_month or now.month:02d}",
                "calls": calls,
                "puts": puts,
                "total_strikes": len(calls),
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            }
        except Exception as exc:
            return {"error": str(exc), "symbol": symbol}

    def place_order(
        self,
        account_id_key: str,
        symbol: str,
        side: str,
        qty: int,
        order_type: str = "MARKET",
        limit_price: float = None,
        is_option: bool = False,
        option_symbol: str = None,
        stop_price: float = None,
        time_in_force: str = "DAY",
    ) -> dict:
        """Place an equity, ETF, or option order via E*TRADE.

        Args:
            account_id_key: E*TRADE encrypted account ID (from get_accounts)
            symbol:          Underlying symbol for equities/ETFs
            side:            BUY | SELL
            qty:             Shares or option contracts
            order_type:      MARKET | LIMIT | STOP | STOP_LIMIT
            limit_price:     Required for LIMIT/STOP_LIMIT
            is_option:       True to place an option order
            option_symbol:   Option symbol string (e.g., "AAPL--260117C00200000")
            stop_price:      For STOP/STOP_LIMIT orders

        Returns:
            dict with order ID, status, and fill details
        """
        cfg = self._is_configured()
        if not cfg.get("configured"):
            return cfg

        url = f"{self.base_url}/accounts/{account_id_key}/orders/place"
        order_action = "BUY" if side.upper() == "BUY" else "SELL"

        if is_option and option_symbol:
            instrument = {
                "Product": {"securityType": "OPTN", "symbol": option_symbol},
                "orderAction": order_action,
                "quantityType": "QUANTITY",
                "quantity": qty,
            }
        else:
            instrument = {
                "Product": {"securityType": "EQ", "symbol": symbol.upper()},
                "orderAction": order_action,
                "quantityType": "QUANTITY",
                "quantity": qty,
            }

        order_payload = {
            "PlaceOrderRequest": {
                "orderType": order_type.upper(),
                "limitPrice": limit_price,
                "stopPrice": stop_price,
                "allOrNone": False,
                "marketSession": "REGULAR",
                "price": limit_price,
                "priceType": order_type.upper(),
                "routing": "AUTO",
                "duration": time_in_force.upper(),
                "orderTerm": time_in_force.upper(),
                "Instrument": [instrument],
                "clientOrderId": f"AC_{int(time.time())}",
            }
        }

        try:
            session = self._get_session()
            resp = session.post(
                url,
                json=order_payload,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
            response = data.get("PlaceOrderResponse", {})
            return {
                "order_id": response.get("orderId"),
                "status": "submitted",
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "order_type": order_type,
                "mode": "sandbox" if self.sandbox else "live",
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            }
        except Exception as exc:
            return {"error": str(exc), "symbol": symbol, "side": side, "qty": qty}

    def get_account_portfolio(self, account_id_key: str) -> dict:
        """Get all open positions for an account."""
        cfg = self._is_configured()
        if not cfg.get("configured"):
            return cfg

        url = f"{self.base_url}/accounts/{account_id_key}/portfolio"
        try:
            session = self._get_session()
            resp = session.get(url, params={"view": "COMPLETE"})
            resp.raise_for_status()
            data = resp.json()
            portfolio = data.get("PortfolioResponse", {}).get("AccountPortfolio", [{}])[0]
            positions = portfolio.get("Position", [])
            return {
                "account_id": account_id_key,
                "positions": [
                    {
                        "symbol": p.get("symbolDescription"),
                        "qty": p.get("quantity"),
                        "cost_basis": p.get("costBasis"),
                        "market_value": p.get("marketValue"),
                        "unrealized_pnl": p.get("totalGain"),
                        "pnl_pct": p.get("totalGainPct"),
                    }
                    for p in positions
                ],
                "total_market_value": portfolio.get("totalMarketValue"),
                "total_gain_loss": portfolio.get("totalGainLoss"),
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            }
        except Exception as exc:
            return {"error": str(exc)}
