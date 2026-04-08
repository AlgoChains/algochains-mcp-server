"""Pysystemtrade-style volatility targeting and IDM for futures position sizing.

Implements Robert Carver's methodology from 'Systematic Trading' and
'Leveraged Trader' (pst-group/pysystemtrade — 2.5k stars).

Key concepts:
  - Volatility Targeting:  size positions to produce consistent % vol of capital
  - IDM (Instrument Diversification Multiplier): scale up when instruments uncorrelated
  - Forecast Scaling:      normalize all signals to [-20, +20] range
  - Cost-Adjusted Sizing:  reduce position for high-cost instruments

For AlgoChains live bots (MNQ, CL, MES, NQ):
  - Run alongside Kelly criterion (dual-sizing, take the more conservative)
  - IDM auto-reduces MNQ + NQ when both signal same direction (they're correlated)
  - Vol targeting gives marketplace subscribers consistent drawdown profiles

No external dependencies (uses only stdlib + numpy if available, falls back to pure Python).
"""
from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("algochains_mcp.volatility_targeting")

# ---------------------------------------------------------------------------
# Instrument specifications for AlgoChains live bots
# ---------------------------------------------------------------------------

INSTRUMENT_SPECS: dict[str, dict] = {
    "MNQ": {
        "exchange": "CME",
        "multiplier": 2.0,         # $2 per point
        "tick_size": 0.25,
        "tick_value": 0.50,        # $0.50 per tick
        "asset_class": "equity_index",
        "currency": "USD",
    },
    "NQ": {
        "exchange": "CME",
        "multiplier": 20.0,        # $20 per point
        "tick_size": 0.25,
        "tick_value": 5.0,         # $5 per tick
        "asset_class": "equity_index",
        "currency": "USD",
    },
    "MES": {
        "exchange": "CME",
        "multiplier": 5.0,         # $5 per point
        "tick_size": 0.25,
        "tick_value": 1.25,
        "asset_class": "equity_index",
        "currency": "USD",
    },
    "CL": {
        "exchange": "NYMEX",
        "multiplier": 1000.0,      # 1000 barrels per contract
        "tick_size": 0.01,
        "tick_value": 10.0,        # $10 per tick
        "asset_class": "commodity_energy",
        "currency": "USD",
    },
}

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class VolTargetResult:
    symbol: str
    contracts: int
    method: str
    target_vol_pct: float
    instrument_vol_pct: float
    capital_usd: float
    notional_per_contract: float
    raw_contracts: float
    idm_applied: float
    vol_scalar: float
    details: dict

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "contracts": self.contracts,
            "method": self.method,
            "target_vol_pct": round(self.target_vol_pct, 4),
            "instrument_vol_pct": round(self.instrument_vol_pct, 4),
            "capital_usd": self.capital_usd,
            "notional_per_contract": round(self.notional_per_contract, 2),
            "raw_contracts": round(self.raw_contracts, 4),
            "idm_applied": round(self.idm_applied, 4),
            "vol_scalar": round(self.vol_scalar, 4),
            "details": self.details,
        }


@dataclass
class IDMResult:
    instruments: list[str]
    idm: float
    correlation_matrix: dict
    interpretation: str

    def to_dict(self) -> dict:
        return {
            "instruments": self.instruments,
            "idm": round(self.idm, 4),
            "correlation_matrix": self.correlation_matrix,
            "interpretation": self.interpretation,
        }


# ---------------------------------------------------------------------------
# Core volatility targeting calculation
# ---------------------------------------------------------------------------

def compute_volatility_targeted_size(
    symbol: str,
    current_price: float,
    annualized_vol_pct: float,
    capital_usd: float,
    target_vol_pct: float = 20.0,
    idm: float = 1.0,
    forecast_scalar: float = 1.0,
    max_leverage: float = 4.0,
) -> VolTargetResult:
    """Compute position size using volatility targeting methodology.

    Formula (Carver):
        contracts = (target_vol * capital * IDM * forecast_scalar) /
                    (instrument_vol * notional_per_contract)

    Args:
        symbol:              Instrument ticker (MNQ, CL, MES, NQ)
        current_price:       Current price of the futures contract
        annualized_vol_pct:  Annualized price volatility as % (e.g., 15.0 = 15%)
        capital_usd:         Total trading capital in USD
        target_vol_pct:      Target annualized portfolio volatility % (default 20%)
        idm:                 Instrument Diversification Multiplier (1.0 = single instrument)
        forecast_scalar:     Normalized forecast strength [0.0, 2.0] (1.0 = full signal)
        max_leverage:        Hard cap on leverage multiple (default 4x)

    Returns:
        VolTargetResult with integer contracts and full calculation detail
    """
    spec = INSTRUMENT_SPECS.get(symbol.upper())
    if not spec:
        spec = {"multiplier": 1.0}

    notional_per_contract = current_price * spec["multiplier"]

    if notional_per_contract <= 0 or annualized_vol_pct <= 0:
        logger.warning("Invalid inputs: price=%s vol=%s", current_price, annualized_vol_pct)
        return VolTargetResult(
            symbol=symbol, contracts=0, method="vol_targeting_error",
            target_vol_pct=target_vol_pct, instrument_vol_pct=annualized_vol_pct,
            capital_usd=capital_usd, notional_per_contract=notional_per_contract,
            raw_contracts=0.0, idm_applied=idm, vol_scalar=0.0,
            details={"error": "invalid_price_or_vol"}
        )

    # Core Carver formula
    vol_scalar = (target_vol_pct / 100.0) / (annualized_vol_pct / 100.0)
    raw_contracts = (capital_usd * vol_scalar * idm * forecast_scalar) / notional_per_contract

    # Apply leverage cap
    max_notional = capital_usd * max_leverage
    max_by_leverage = max_notional / notional_per_contract
    capped_contracts = min(raw_contracts, max_by_leverage)

    contracts = max(0, round(capped_contracts))

    details = {
        "notional_per_contract_usd": round(notional_per_contract, 2),
        "vol_scalar": round(vol_scalar, 4),
        "raw_contracts_before_idm": round(raw_contracts / idm, 4) if idm else 0,
        "raw_contracts_after_idm": round(raw_contracts, 4),
        "leverage_cap_applied": capped_contracts < raw_contracts,
        "max_by_leverage": round(max_by_leverage, 4),
        "effective_notional_usd": round(contracts * notional_per_contract, 2),
        "effective_leverage": round((contracts * notional_per_contract) / capital_usd, 3) if capital_usd else 0,
    }

    return VolTargetResult(
        symbol=symbol,
        contracts=contracts,
        method="volatility_targeting",
        target_vol_pct=target_vol_pct,
        instrument_vol_pct=annualized_vol_pct,
        capital_usd=capital_usd,
        notional_per_contract=notional_per_contract,
        raw_contracts=raw_contracts,
        idm_applied=idm,
        vol_scalar=vol_scalar,
        details=details,
    )


# ---------------------------------------------------------------------------
# IDM — Instrument Diversification Multiplier
# ---------------------------------------------------------------------------

# Pre-computed correlations for AlgoChains instruments (based on 2022-2025 data)
# These are starting estimates — ideally updated weekly from live returns
_DEFAULT_CORRELATIONS: dict[tuple, float] = {
    ("MNQ", "NQ"):  0.98,   # Micro vs full — nearly identical
    ("MNQ", "MES"): 0.75,   # Nasdaq vs S&P 500 micro — moderate correlation
    ("MNQ", "CL"):  0.12,   # Nasdaq vs crude — low, regime-dependent
    ("NQ",  "MES"): 0.75,   # Same as MNQ/MES
    ("NQ",  "CL"):  0.12,
    ("MES", "CL"):  0.08,   # S&P vs crude — lowest correlation in our set
}


def get_correlation(i1: str, i2: str, custom_correlations: dict = None) -> float:
    """Return correlation between two instruments (symmetric lookup)."""
    if i1 == i2:
        return 1.0
    correlations = custom_correlations or _DEFAULT_CORRELATIONS
    key = (min(i1, i2), max(i1, i2))
    return correlations.get(key, correlations.get((i1, i2), correlations.get((i2, i1), 0.3)))


def compute_idm(
    instruments: list[str],
    custom_correlations: dict = None,
    weights: list[float] = None,
) -> IDMResult:
    """Compute the Instrument Diversification Multiplier for a portfolio.

    IDM = 1 / sqrt(w^T * C * w)
    where w = instrument weights, C = correlation matrix

    IDM > 1 means the portfolio is less risky than its components (diversification benefit).
    Cap at 2.5 per Carver — never let IDM inflate sizing too aggressively.

    Args:
        instruments:         List of instrument tickers
        custom_correlations: Override default correlations {(i1,i2): corr}
        weights:             Capital allocation weights (equal if None)

    Returns:
        IDMResult with IDM value and correlation matrix
    """
    n = len(instruments)
    if n == 0:
        return IDMResult(instruments=[], idm=1.0, correlation_matrix={}, interpretation="No instruments")
    if n == 1:
        return IDMResult(
            instruments=instruments, idm=1.0,
            correlation_matrix={instruments[0]: {instruments[0]: 1.0}},
            interpretation="Single instrument — no diversification benefit (IDM=1.0)"
        )

    w = weights if weights and len(weights) == n else [1.0 / n] * n

    # Build correlation matrix
    C = [[get_correlation(instruments[i], instruments[j], custom_correlations) for j in range(n)] for i in range(n)]

    # Compute w^T * C * w
    portfolio_var = 0.0
    for i in range(n):
        for j in range(n):
            portfolio_var += w[i] * w[j] * C[i][j]

    raw_idm = 1.0 / math.sqrt(max(portfolio_var, 1e-10))
    idm = min(raw_idm, 2.5)  # Carver's cap

    corr_matrix = {
        instruments[i]: {instruments[j]: round(C[i][j], 4) for j in range(n)}
        for i in range(n)
    }

    if idm >= 2.4:
        interp = f"Maximum diversification benefit (IDM={idm:.3f} — capped at 2.5). Portfolio instruments are highly uncorrelated."
    elif idm >= 1.5:
        interp = f"Good diversification (IDM={idm:.3f}). Safely increases position sizing by {(idm-1)*100:.0f}%."
    elif idm >= 1.1:
        interp = f"Moderate diversification (IDM={idm:.3f}). Some benefit from running multiple instruments."
    else:
        interp = f"Low diversification (IDM={idm:.3f}). Instruments are highly correlated — minimal benefit from running multiple."

    return IDMResult(instruments=instruments, idm=idm, correlation_matrix=corr_matrix, interpretation=interp)


# ---------------------------------------------------------------------------
# Forecast scaling (normalize raw signals to [-20, +20])
# ---------------------------------------------------------------------------

def compute_forecast_scalar(
    raw_forecast: float,
    target_abs_forecast: float = 10.0,
    scalar: float = None,
    raw_forecast_history: list[float] = None,
) -> dict:
    """Scale a raw trading signal to Carver's target absolute forecast scale.

    In pysystemtrade, all signals are normalized so that avg(|forecast|) = 10.
    This ensures consistent position sizing regardless of the signal's native scale.

    Args:
        raw_forecast:           The raw signal output (e.g., EMA crossover, RSI divergence)
        target_abs_forecast:    Target absolute forecast value (default 10)
        scalar:                 Pre-computed forecast scalar (overrides history-based calc)
        raw_forecast_history:   Historical signal values to compute scalar from

    Returns:
        dict with scaled_forecast, capped forecast, and scalar applied
    """
    if scalar is None and raw_forecast_history:
        # Compute scalar from history: target_abs / mean(|forecasts|)
        mean_abs = sum(abs(f) for f in raw_forecast_history) / max(len(raw_forecast_history), 1)
        scalar = target_abs_forecast / max(mean_abs, 1e-10)
    elif scalar is None:
        scalar = 1.0  # No scaling if no history provided

    scaled = raw_forecast * scalar
    # Carver caps at ±20
    capped = max(-20.0, min(20.0, scaled))

    return {
        "raw_forecast": raw_forecast,
        "scalar_applied": round(scalar, 4),
        "scaled_forecast": round(scaled, 4),
        "capped_forecast": round(capped, 4),
        "is_capped": abs(scaled) > 20.0,
        "forecast_strength": round(abs(capped) / 20.0, 4),  # 0=no signal, 1=max signal
        "direction": "LONG" if capped > 0 else "SHORT" if capped < 0 else "FLAT",
    }


# ---------------------------------------------------------------------------
# Dual sizing — compare Kelly vs Vol Targeting and take conservative
# ---------------------------------------------------------------------------

def dual_size_conservative(
    symbol: str,
    current_price: float,
    annualized_vol_pct: float,
    capital_usd: float,
    kelly_contracts: int,
    target_vol_pct: float = 20.0,
    idm: float = 1.0,
    forecast_scalar: float = 1.0,
) -> dict:
    """Run both Kelly and vol-targeting sizing, return the more conservative (smaller).

    This is the recommended approach for the transition period:
    - Kelly gives wealth-maximizing size
    - Vol targeting gives consistent-risk size
    - Taking the min prevents runaway sizing from either method

    Args:
        kelly_contracts: Pre-computed Kelly position size (contracts)
        (other args): Same as compute_volatility_targeted_size

    Returns:
        dict with both sizes, chosen size, and reasoning
    """
    vol_result = compute_volatility_targeted_size(
        symbol=symbol,
        current_price=current_price,
        annualized_vol_pct=annualized_vol_pct,
        capital_usd=capital_usd,
        target_vol_pct=target_vol_pct,
        idm=idm,
        forecast_scalar=forecast_scalar,
    )

    chosen = min(kelly_contracts, vol_result.contracts)
    method = "kelly" if kelly_contracts < vol_result.contracts else "vol_targeting"

    return {
        "symbol": symbol,
        "kelly_contracts": kelly_contracts,
        "vol_target_contracts": vol_result.contracts,
        "chosen_contracts": chosen,
        "chosen_method": method,
        "reason": f"Conservative dual: {method} was smaller ({chosen} < {max(kelly_contracts, vol_result.contracts)})",
        "vol_target_details": vol_result.to_dict(),
    }
