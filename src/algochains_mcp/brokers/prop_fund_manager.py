"""Prop Fund Account Manager — pipeline validated AlgoChains strategies to funded accounts.

Supports the major US futures prop funds that use Rithmic or Tradovate:
  - Apex Trader Funding  (Rithmic + Tradovate)
  - Topstep Trader       (Rithmic)
  - MyFundedFutures      (Rithmic)
  - TradeDay             (Rithmic)
  - Bulenox              (Rithmic)
  - Earn2Trade           (Rithmic — Gauntlet)
  - FTMO                 (MetaTrader — forex/CFDs only)

The pipeline:
  1. Strategy passes MCPT 5-gate validation in AlgoChains
  2. prop_fund_manager.evaluate_for_prop_fund() checks strategy against fund rules
  3. Deploy to funded evaluation account via Rithmic or Tradovate connection
  4. Drawdown monitor tracks daily/max drawdown against fund limits
  5. On passing evaluation: notify for funded account upgrade

Key insight: Rithmic API (R|Protocol) is the execution backbone of most US futures
prop funds. One Rithmic connection = access to all Rithmic-based funds simultaneously.

Rithmic API docs: https://ririmi.rithmic.com (requires NDA/agreement)
For now we use paper trading with rule-simulation to validate readiness.

IMPORTANT: Actual order routing to prop accounts requires a live Rithmic API license.
This module handles:
  a) Strategy-to-fund compliance checking (always available)
  b) Drawdown rule simulation against historical returns (always available)
  c) Live order routing via Rithmic (requires RITHMIC_API_KEY + approved credentials)
  d) Live order routing via Tradovate for Apex/compatible funds (uses existing connector)
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("algochains_mcp.brokers.prop_fund_manager")

# ---------------------------------------------------------------------------
# Prop fund definitions — rules as of Q1 2026
# These should be verified against current fund websites before live deployment
# ---------------------------------------------------------------------------

@dataclass
class PropFundRules:
    name: str
    platform: str                  # "rithmic", "tradovate", "mt5"
    account_size_usd: float
    max_daily_loss_usd: float      # 0 = no daily loss limit (use trailing only)
    max_trailing_drawdown_usd: float  # Max trailing drawdown from high water mark
    profit_target_usd: float       # To pass evaluation
    min_trading_days: int          # Min days must be active
    max_position_size: int         # Max contracts (varies by instrument). 0 = no explicit cap.
    consistency_rule: bool         # True = no single day > X% of total profit
    consistency_pct: float         # If consistency_rule: max % of profit in one day
    instruments_allowed: list[str]
    news_trading_allowed: bool
    overnight_positions_allowed: bool
    evaluation_fee_usd: float
    monthly_fee_usd: float
    profit_split_pct: float        # % paid to trader after passing
    website: str
    notes: str = ""
    # --- Extended fields (added 2026-04 — prop fund autopilot) ---
    drawdown_type: str = "trailing_intraday"    # "trailing_intraday" | "trailing_eod" | "static" | "none"
    consistency_applies_in: str = "live"        # "eval" | "live" | "both"
    consistency_min_days: int = 0               # days required before consistency rule activates
    activation_fee_usd: float = 0.0             # one-time PA/funded activation cost
    safety_net_usd: float = 0.0                 # min balance above start needed for first payout
    first_payout_min_days: int = 0              # min trading days on PA before first payout
    payout_cap_count: int = 0                   # 0 = uncapped; else max payouts per period
    payout_period: str = "monthly"              # "monthly" | "per_account" | "uncapped"
    automation_policy: str = "allowed_with_flag"  # "allowed_with_flag" | "manual_only" | "copy_only" | "disallowed"
    mandatory_bracket_orders: bool = False      # Apex PA rule: stop must be attached before entry
    flat_by_time_ct: str = ""                   # e.g., "15:55" — must be flat by this CT time (empty = no rule)
    drawdown_lock_at: str = ""                  # "funded" | "starting_plus_buffer" | "" (apex trails until starting+$100)
    rules_verified_date: str = ""               # ISO date when these rules were last verified against fund site
    rules_source_url: str = ""                  # URL or source for verification
    tier: str = "eval"                          # "eval" | "pa" | "funded"
    fund_key: str = ""                          # stable programmatic key (e.g., "apex_50k_eod")

    def to_dict(self) -> dict:
        return asdict(self)


#
# PROP_FUNDS registry — refreshed 2026-04
#
# IMPORTANT: Every entry has a `rules_verified_date`. Operator MUST re-verify
# against the fund website before committing to an evaluation fee. The autopilot
# refuses to start an evaluation against any fund whose verified_date is older
# than PROP_FUND_RULES_MAX_AGE_DAYS (default 30).
#
# Keys use explicit `<firm>_<size>_<variant>` naming. Legacy short keys
# (apex, topstep, myfundedfutures) are kept as aliases for backwards
# compatibility but point at the primary 50K entry for that firm.
#

PROP_FUNDS: dict[str, PropFundRules] = {
    # ── Apex Trader Funding 4.0 — EOD Trail (primary; user-selected) ──────
    "apex_50k_eod": PropFundRules(
        name="Apex Trader Funding — 50K Full (EOD Trail)",
        platform="rithmic",  # Also works via Tradovate via Apex relationship
        account_size_usd=50000,
        max_daily_loss_usd=0,              # Apex has no daily loss limit on eval
        max_trailing_drawdown_usd=2500,    # Trails END-OF-DAY; locks at start+$100
        profit_target_usd=3000,
        min_trading_days=7,
        max_position_size=10,              # 50K allows up to 10 micros (MNQ) or 4-5 minis
        consistency_rule=True,
        consistency_pct=30.0,              # 30% rule: no single day > 30% of total profit (PA phase)
        consistency_applies_in="live",     # Apex consistency applies on PA, not eval
        consistency_min_days=8,            # Rule kicks in after 8+ PA trading days
        instruments_allowed=["MNQ", "NQ", "MES", "ES", "CL", "MCL", "GC", "MGC", "RTY", "M2K", "6E", "6B", "ZB", "ZN"],
        news_trading_allowed=True,
        overnight_positions_allowed=False,  # Must be flat by 4:59 PM ET on eval/PA
        flat_by_time_ct="15:55",           # 16:55 ET = 15:55 CT
        evaluation_fee_usd=147,            # Monthly for 50K Full
        activation_fee_usd=85,             # PA activation per month, or $340 lifetime
        monthly_fee_usd=85,                # PA monthly after eval
        profit_split_pct=90.0,
        first_payout_min_days=8,           # 8 winning trading days post-activation for first payout
        payout_cap_count=5,
        payout_period="monthly",
        automation_policy="allowed_with_flag",  # CME Rule 575 isAutomated=True required
        mandatory_bracket_orders=True,     # PA requires stop attached before entry
        drawdown_type="trailing_eod",      # USER SELECTED: EOD trail
        drawdown_lock_at="starting_plus_buffer",  # Locks at starting + $100 when hit
        website="https://apextraderfunding.com",
        rules_verified_date="2026-04-17",
        rules_source_url="apextraderfunding.com/pricing (verify manually)",
        tier="eval",
        fund_key="apex_50k_eod",
        notes=(
            "USER-SELECTED primary. EOD Trail variant — drawdown updates only at end of day. "
            "Safer for scalpers since intraday drawdown excursions don't tighten the DD line. "
            "No daily loss limit on eval; drawdown comes entirely from the $2,500 trailing. "
            "CME Rule 575: orders must be tagged isAutomated=True (bot already complies)."
        ),
    ),

    # ── Apex 4.0 — Intraday Trail variant (alternative) ─────────────────
    "apex_50k_intraday": PropFundRules(
        name="Apex Trader Funding — 50K Full (Intraday Trail)",
        platform="rithmic",
        account_size_usd=50000,
        max_daily_loss_usd=0,
        max_trailing_drawdown_usd=2500,
        profit_target_usd=3000,
        min_trading_days=7,
        max_position_size=10,
        consistency_rule=True,
        consistency_pct=30.0,
        consistency_applies_in="live",
        consistency_min_days=8,
        instruments_allowed=["MNQ", "NQ", "MES", "ES", "CL", "MCL", "GC", "MGC", "RTY", "M2K", "6E", "6B", "ZB", "ZN"],
        news_trading_allowed=True,
        overnight_positions_allowed=False,
        flat_by_time_ct="15:55",
        evaluation_fee_usd=147,
        activation_fee_usd=85,
        monthly_fee_usd=85,
        profit_split_pct=90.0,
        first_payout_min_days=8,
        payout_cap_count=5,
        payout_period="monthly",
        automation_policy="allowed_with_flag",
        mandatory_bracket_orders=True,
        drawdown_type="trailing_intraday",
        drawdown_lock_at="starting_plus_buffer",
        website="https://apextraderfunding.com",
        rules_verified_date="2026-04-17",
        rules_source_url="apextraderfunding.com/pricing (verify manually)",
        tier="eval",
        fund_key="apex_50k_intraday",
        notes="Higher risk of early trip from intraday spikes. EOD variant recommended for scalpers.",
    ),

    # ── MFFU Core 50K (primary; user-selected for parallel) ──────────────
    "mffu_core_50k": PropFundRules(
        name="MyFundedFutures — Core 50K",
        platform="rithmic",
        account_size_usd=50000,
        max_daily_loss_usd=1250,           # Expert/Core daily loss limit
        max_trailing_drawdown_usd=2000,    # Trails at EOD until LIVE funded, then locks
        profit_target_usd=3000,
        min_trading_days=1,
        max_position_size=5,               # 5 minis for 50K, scales with micros (50 micros)
        consistency_rule=True,
        consistency_pct=40.0,
        consistency_applies_in="live",     # Consistency applies on LIVE, not eval
        consistency_min_days=5,
        instruments_allowed=["MNQ", "NQ", "MES", "ES", "CL", "MCL", "GC", "MGC", "RTY", "M2K"],
        news_trading_allowed=True,
        overnight_positions_allowed=True,   # MFFU allows overnight on Core/Pro plans
        flat_by_time_ct="",                # No forced flat-by-time (overnight allowed)
        evaluation_fee_usd=80,             # ~$75-85/mo for 50K Core
        activation_fee_usd=99,             # One-time activation to funded
        monthly_fee_usd=80,
        profit_split_pct=100.0,            # 100% first $10K then 90%
        first_payout_min_days=5,
        payout_cap_count=0,                # Uncapped
        payout_period="uncapped",
        automation_policy="allowed_with_flag",
        mandatory_bracket_orders=False,    # Not required on MFFU eval
        drawdown_type="trailing_eod",
        drawdown_lock_at="starting_balance",  # Trails until LIVE then locks at starting
        safety_net_usd=2000,               # Must hit start+$2K for first payout
        website="https://myfundedfutures.com",
        rules_verified_date="2026-04-17",
        rules_source_url="myfundedfutures.com/pricing (verify manually)",
        tier="eval",
        fund_key="mffu_core_50k",
        notes=(
            "USER-SELECTED parallel. Overnight allowed (Core/Pro plans per myfundedfutures.com). "
            "Daily loss limit $1,250 is the key guard. EOD trail until LIVE locks the DD line. "
            "Consistency applies on LIVE only. 100% split on first $10K is a nice edge vs Apex."
        ),
    ),

    # ── MFFU Rapid 50K (no consistency on eval) ──────────────────────────
    "mffu_rapid_50k": PropFundRules(
        name="MyFundedFutures — Rapid 50K",
        platform="rithmic",
        account_size_usd=50000,
        max_daily_loss_usd=1250,
        max_trailing_drawdown_usd=2000,
        profit_target_usd=3000,
        min_trading_days=1,
        max_position_size=5,
        consistency_rule=False,
        consistency_pct=0.0,
        instruments_allowed=["MNQ", "NQ", "MES", "ES", "CL", "MCL", "GC", "MGC"],
        news_trading_allowed=True,
        overnight_positions_allowed=False,
        flat_by_time_ct="14:55",
        evaluation_fee_usd=100,
        activation_fee_usd=99,
        monthly_fee_usd=100,
        profit_split_pct=100.0,
        first_payout_min_days=5,
        automation_policy="allowed_with_flag",
        drawdown_type="trailing_eod",
        safety_net_usd=2000,
        website="https://myfundedfutures.com",
        rules_verified_date="2026-04-17",
        tier="eval",
        fund_key="mffu_rapid_50k",
        notes="Rapid variant — faster payout cadence; pricier monthly fee.",
    ),

    # ── Topstep Combine 50K ──────────────────────────────────────────────
    "topstep_50k": PropFundRules(
        name="Topstep — Trading Combine 50K",
        platform="rithmic",
        account_size_usd=50000,
        max_daily_loss_usd=1000,
        max_trailing_drawdown_usd=2000,
        profit_target_usd=3000,
        min_trading_days=2,                 # Topstep reduced to 2 days (Funded account requires 5)
        max_position_size=5,
        consistency_rule=True,
        consistency_pct=30.0,
        consistency_applies_in="both",      # 30% rule applies on Combine AND Funded
        instruments_allowed=["MNQ", "NQ", "MES", "ES", "CL", "MCL", "GC", "MGC", "RTY", "M2K", "6E", "ZN"],
        news_trading_allowed=True,
        overnight_positions_allowed=False,
        flat_by_time_ct="15:10",            # 4:10 PM ET close = 15:10 CT
        evaluation_fee_usd=99,              # Monthly for 50K Combine
        activation_fee_usd=149,
        monthly_fee_usd=99,
        profit_split_pct=100.0,             # 100% first $10K then 90%
        first_payout_min_days=5,
        automation_policy="allowed_with_flag",
        drawdown_type="trailing_eod",
        drawdown_lock_at="starting_balance",
        website="https://topstep.com",
        rules_verified_date="2026-04-17",
        tier="eval",
        fund_key="topstep_50k",
        notes="Strict 30% consistency rule on both phases. Otherwise very bot-friendly.",
    ),

    # ── Tradeify Select Flex 50K ─────────────────────────────────────────
    "tradeify_flex_50k": PropFundRules(
        name="Tradeify — Select Flex 50K",
        platform="rithmic",
        account_size_usd=50000,
        max_daily_loss_usd=0,              # No daily loss on Flex
        max_trailing_drawdown_usd=2500,
        profit_target_usd=3000,
        min_trading_days=1,
        max_position_size=10,
        consistency_rule=False,            # Flex has no consistency rule on eval
        consistency_pct=0.0,
        instruments_allowed=["MNQ", "NQ", "MES", "ES", "CL", "MCL", "GC", "MGC", "RTY", "M2K"],
        news_trading_allowed=True,
        overnight_positions_allowed=False,
        flat_by_time_ct="15:55",
        evaluation_fee_usd=99,
        activation_fee_usd=149,
        monthly_fee_usd=99,
        profit_split_pct=90.0,
        first_payout_min_days=5,
        automation_policy="allowed_with_flag",
        drawdown_type="trailing_eod",
        website="https://tradeify.co",
        rules_verified_date="2026-04-17",
        tier="eval",
        fund_key="tradeify_flex_50k",
        notes="Flex variant — no daily loss limit, no consistency rule. Very bot-friendly.",
    ),

    # ── Tradeify Select Daily 50K ────────────────────────────────────────
    "tradeify_daily_50k": PropFundRules(
        name="Tradeify — Select Daily 50K",
        platform="rithmic",
        account_size_usd=50000,
        max_daily_loss_usd=1250,
        max_trailing_drawdown_usd=2500,
        profit_target_usd=3000,
        min_trading_days=1,
        max_position_size=10,
        consistency_rule=True,
        consistency_pct=30.0,
        consistency_applies_in="both",
        instruments_allowed=["MNQ", "NQ", "MES", "ES", "CL", "MCL", "GC", "MGC"],
        news_trading_allowed=True,
        overnight_positions_allowed=False,
        flat_by_time_ct="15:55",
        evaluation_fee_usd=99,
        activation_fee_usd=149,
        monthly_fee_usd=99,
        profit_split_pct=90.0,
        first_payout_min_days=5,
        automation_policy="allowed_with_flag",
        drawdown_type="trailing_eod",
        website="https://tradeify.co",
        rules_verified_date="2026-04-17",
        tier="eval",
        fund_key="tradeify_daily_50k",
        notes="Daily variant — has daily loss and consistency rule.",
    ),

    # ── TradeDay 50K ─────────────────────────────────────────────────────
    "tradeday_50k": PropFundRules(
        name="TradeDay 50K",
        platform="rithmic",
        account_size_usd=50000,
        max_daily_loss_usd=1000,
        max_trailing_drawdown_usd=2000,
        profit_target_usd=3000,
        min_trading_days=5,
        max_position_size=3,
        consistency_rule=False,
        consistency_pct=0.0,
        instruments_allowed=["MNQ", "NQ", "MES", "ES", "CL", "GC"],
        news_trading_allowed=True,
        overnight_positions_allowed=True,
        flat_by_time_ct="",
        evaluation_fee_usd=99,
        activation_fee_usd=0,
        monthly_fee_usd=49,
        profit_split_pct=90.0,
        automation_policy="allowed_with_flag",
        drawdown_type="trailing_eod",
        website="https://tradeday.com",
        rules_verified_date="2026-04-17",
        tier="eval",
        fund_key="tradeday_50k",
        notes="No consistency rule. Small max position (3 contracts) on 50K is the main constraint.",
    ),

    # ── Bulenox 50K ──────────────────────────────────────────────────────
    "bulenox_50k": PropFundRules(
        name="Bulenox 50K",
        platform="rithmic",
        account_size_usd=50000,
        max_daily_loss_usd=1500,
        max_trailing_drawdown_usd=2500,
        profit_target_usd=3000,
        min_trading_days=0,
        max_position_size=4,
        consistency_rule=False,
        consistency_pct=0.0,
        instruments_allowed=["MNQ", "NQ", "MES", "ES", "CL", "GC", "RTY"],
        news_trading_allowed=True,
        overnight_positions_allowed=True,
        evaluation_fee_usd=99,
        monthly_fee_usd=49,
        profit_split_pct=90.0,
        automation_policy="allowed_with_flag",
        drawdown_type="trailing_eod",
        website="https://bulenox.com",
        rules_verified_date="2026-04-17",
        tier="eval",
        fund_key="bulenox_50k",
        notes="No min trading days. Overnight allowed.",
    ),

    # ── Earn2Trade Gauntlet Mini (25K) ───────────────────────────────────
    "earn2trade_25k": PropFundRules(
        name="Earn2Trade (Gauntlet Mini 25K)",
        platform="rithmic",
        account_size_usd=25000,
        max_daily_loss_usd=1000,
        max_trailing_drawdown_usd=1500,
        profit_target_usd=1500,
        min_trading_days=15,
        max_position_size=2,
        consistency_rule=True,
        consistency_pct=50.0,
        consistency_applies_in="both",
        instruments_allowed=["MNQ", "MES", "MCL"],
        news_trading_allowed=False,
        overnight_positions_allowed=False,
        flat_by_time_ct="15:00",
        evaluation_fee_usd=150,
        monthly_fee_usd=0,
        profit_split_pct=80.0,
        automation_policy="manual_only",
        drawdown_type="trailing_intraday",
        website="https://earn2trade.com",
        rules_verified_date="2026-04-17",
        tier="eval",
        fund_key="earn2trade_25k",
        notes="Micro-only. Manual-trading oriented; automation policy is restrictive. Not recommended for bots.",
    ),

    # ── FTMO (forex/CFDs — NOT for futures bots) ─────────────────────────
    "ftmo_100k": PropFundRules(
        name="FTMO 100K",
        platform="mt5",
        account_size_usd=100000,
        max_daily_loss_usd=5000,
        max_trailing_drawdown_usd=10000,
        profit_target_usd=10000,
        min_trading_days=4,
        max_position_size=0,
        consistency_rule=True,
        consistency_pct=50.0,
        consistency_applies_in="both",
        instruments_allowed=["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "US30", "NAS100"],
        news_trading_allowed=False,
        overnight_positions_allowed=True,
        evaluation_fee_usd=540,
        monthly_fee_usd=0,
        profit_split_pct=80.0,
        automation_policy="allowed_with_flag",
        drawdown_type="trailing_intraday",
        website="https://ftmo.com",
        rules_verified_date="2026-04-17",
        tier="eval",
        fund_key="ftmo_100k",
        notes="Forex/CFD only. No Rithmic. Not applicable to US futures bots like MNQ scalper.",
    ),
}

# Backwards-compat aliases — legacy callers using short keys keep working
PROP_FUNDS["apex"] = PROP_FUNDS["apex_50k_eod"]
PROP_FUNDS["topstep"] = PROP_FUNDS["topstep_50k"]
PROP_FUNDS["myfundedfutures"] = PROP_FUNDS["mffu_core_50k"]
PROP_FUNDS["tradeday"] = PROP_FUNDS["tradeday_50k"]
PROP_FUNDS["bulenox"] = PROP_FUNDS["bulenox_50k"]
PROP_FUNDS["earn2trade"] = PROP_FUNDS["earn2trade_25k"]
PROP_FUNDS["ftmo"] = PROP_FUNDS["ftmo_100k"]


# ---------------------------------------------------------------------------
# Strategy compliance checker
# ---------------------------------------------------------------------------

@dataclass
class ComplianceResult:
    fund_name: str
    strategy_name: str
    eligible: bool
    violations: list[str]
    warnings: list[str]
    recommendations: list[str]
    compatibility_score: float  # 0.0 to 1.0

    def to_dict(self) -> dict:
        return {
            "fund_name": self.fund_name,
            "strategy_name": self.strategy_name,
            "eligible": self.eligible,
            "violations": self.violations,
            "warnings": self.warnings,
            "recommendations": self.recommendations,
            "compatibility_score": round(self.compatibility_score, 3),
        }


def evaluate_strategy_for_fund(
    strategy_name: str,
    fund_name: str,
    symbol: str,
    max_daily_loss_usd: float,
    max_drawdown_usd: float,
    avg_profit_per_day_usd: float,
    holds_overnight: bool,
    trades_news: bool,
    max_position_contracts: int,
    min_trading_days_per_month: int,
    historical_returns_daily: list[float] = None,
    account_size_usd: float = None,
) -> ComplianceResult:
    """Check if a strategy meets a prop fund's evaluation rules.

    This is the critical pre-evaluation compliance check. Run this BEFORE
    committing to an evaluation account to ensure the strategy won't violate
    fund rules that result in immediate disqualification.

    Args:
        strategy_name:              Name of the AlgoChains strategy
        fund_name:                  Key from PROP_FUNDS dict (e.g., "apex", "topstep")
        symbol:                     Trading instrument (e.g., "MNQ")
        max_daily_loss_usd:         Maximum loss in any single day historically
        max_drawdown_usd:           Maximum trailing drawdown historically
        avg_profit_per_day_usd:     Average daily P&L when trading
        holds_overnight:            True if strategy holds positions overnight
        trades_news:                True if strategy trades during news events
        max_position_contracts:     Maximum simultaneous contracts
        min_trading_days_per_month: Minimum active days per month
        historical_returns_daily:   Optional list of daily returns for analysis
        account_size_usd:           Override account size (uses fund default if None)

    Returns:
        ComplianceResult with violations, warnings, and compatibility score
    """
    fund = PROP_FUNDS.get(fund_name.lower())
    if not fund:
        return ComplianceResult(
            fund_name=fund_name, strategy_name=strategy_name,
            eligible=False, violations=[f"Unknown fund: {fund_name}"],
            warnings=[], recommendations=[],
            compatibility_score=0.0
        )

    account = account_size_usd or fund.account_size_usd
    violations = []
    warnings = []
    recommendations = []
    score_penalties = 0.0

    # --- Hard violations (automatic disqualification) ---

    # 1. Instrument check
    sym_upper = symbol.upper()
    if sym_upper not in fund.instruments_allowed:
        violations.append(
            f"Instrument {sym_upper} not allowed at {fund.name}. "
            f"Allowed: {', '.join(fund.instruments_allowed)}"
        )
        score_penalties += 1.0

    # 2. Daily loss limit (0 = fund has no explicit daily limit; skip)
    if fund.max_daily_loss_usd > 0 and max_daily_loss_usd > fund.max_daily_loss_usd:
        violations.append(
            f"Max daily loss ${max_daily_loss_usd:.0f} exceeds {fund.name} limit "
            f"of ${fund.max_daily_loss_usd:.0f}. Strategy would be disqualified."
        )
        score_penalties += 0.5

    # 3. Trailing drawdown
    if max_drawdown_usd > fund.max_trailing_drawdown_usd:
        violations.append(
            f"Max drawdown ${max_drawdown_usd:.0f} exceeds {fund.name} trailing "
            f"limit of ${fund.max_trailing_drawdown_usd:.0f}."
        )
        score_penalties += 0.5

    # 4. Position size
    if max_position_contracts > fund.max_position_size > 0:
        violations.append(
            f"Max position {max_position_contracts} contracts exceeds {fund.name} "
            f"limit of {fund.max_position_size} contracts."
        )
        score_penalties += 0.3

    # 5. Platform compatibility
    if fund.platform == "mt5" and sym_upper in ["MNQ", "NQ", "MES", "ES", "CL"]:
        violations.append(
            f"{fund.name} uses MetaTrader 5 (forex/CFDs only). "
            f"US futures contracts ({sym_upper}) are not available."
        )
        score_penalties += 1.0

    # --- Overnight position check ---
    if holds_overnight and not fund.overnight_positions_allowed:
        violations.append(
            f"{fund.name} requires all positions closed before end of trading day"
            + (f" (flat by {fund.flat_by_time_ct} CT)" if fund.flat_by_time_ct else "")
            + ". Strategy holds overnight — would trigger automatic violation."
        )
        score_penalties += 0.5

    # --- Automation policy check (CME Rule 575, isAutomated flag) ---
    if fund.automation_policy == "disallowed":
        violations.append(
            f"{fund.name} does not allow automated trading. Bot deployment is not permitted."
        )
        score_penalties += 1.0
    elif fund.automation_policy == "manual_only":
        warnings.append(
            f"{fund.name} has a manual-only policy. Automation may be tolerated but "
            f"is not officially supported and accounts can be disqualified without warning."
        )
        score_penalties += 0.3
    elif fund.automation_policy == "copy_only":
        warnings.append(
            f"{fund.name} requires copy-trading proxy. Direct API order routing is not allowed. "
            f"Plan to run a 1-contract master and copy into the prop eval via copy-trader."
        )
        score_penalties += 0.2
    elif fund.automation_policy == "allowed_with_flag":
        recommendations.append(
            "Automated orders must be tagged isAutomated=True (CME Rule 575). "
            "Tradovate client already complies."
        )

    # --- Bracket order requirement (Apex PA rule) ---
    if fund.mandatory_bracket_orders:
        recommendations.append(
            f"{fund.name} requires a stop attached before entry. Ensure bracket orders "
            "(OCO with stop) are placed atomically — no naked market entries."
        )

    # --- Rules freshness check ---
    if fund.rules_verified_date:
        try:
            verified_dt = datetime.strptime(fund.rules_verified_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            age_days = (datetime.now(tz=timezone.utc) - verified_dt).days
            max_age = int(os.environ.get("PROP_FUND_RULES_MAX_AGE_DAYS", "30"))
            if age_days > max_age:
                warnings.append(
                    f"Rules for {fund.name} last verified {age_days} days ago "
                    f"(threshold: {max_age}). Re-verify against {fund.website} before paying evaluation fee."
                )
                score_penalties += 0.1
        except ValueError:
            warnings.append(f"Could not parse rules_verified_date='{fund.rules_verified_date}' for {fund.name}")
    else:
        warnings.append(f"{fund.name} has no rules_verified_date set. Verify rules manually before eval.")
        score_penalties += 0.1

    # --- News trading check ---
    if trades_news and not fund.news_trading_allowed:
        warnings.append(
            f"{fund.name} prohibits trading during major news events (FOMC, NFP, CPI). "
            f"Strategy should implement news blackout windows."
        )
        score_penalties += 0.2
        recommendations.append(
            "Add news event filter: block 5 min before and 15 min after major releases. "
            "See: economic_calendar_guard() in guards.py"
        )

    # --- Consistency rule check ---
    if fund.consistency_rule and historical_returns_daily:
        total_profit = sum(r for r in historical_returns_daily if r > 0) * account
        if total_profit > 0:
            max_day_profit = max(r * account for r in historical_returns_daily if r > 0)
            max_day_pct = (max_day_profit / total_profit) * 100
            if max_day_pct > fund.consistency_pct:
                # "eval" vs "live" vs "both" — if it only applies on live, note but don't penalize eval score
                phase_note = fund.consistency_applies_in or "live"
                severity = "warning" if phase_note == "live" else "violation"
                msg = (
                    f"{fund.name} consistency rule ({phase_note} phase): no single day may exceed "
                    f"{fund.consistency_pct}% of total profit. "
                    f"Strategy's best day = {max_day_pct:.1f}% — would likely violate rule."
                )
                if severity == "violation" and phase_note in ("eval", "both"):
                    warnings.append(msg + " Applies during evaluation — plan to cap daily P&L.")
                    score_penalties += 0.3
                else:
                    warnings.append(msg + " Only applies after LIVE funding; still plan a cap.")
                    score_penalties += 0.1
                recommendations.append(
                    f"Cap daily profit extraction at {fund.consistency_pct * 0.9:.0f}% of running "
                    f"total to comply with consistency rule ({phase_note} phase)."
                )

    # --- Minimum trading days ---
    if fund.min_trading_days > 0 and min_trading_days_per_month < fund.min_trading_days:
        warnings.append(
            f"{fund.name} requires minimum {fund.min_trading_days} trading days to pass evaluation. "
            f"Strategy only trades ~{min_trading_days_per_month} days/month."
        )
        score_penalties += 0.1
        recommendations.append(
            f"Ensure strategy is active on at least {fund.min_trading_days} different calendar days."
        )

    # --- Profit target feasibility ---
    if avg_profit_per_day_usd > 0:
        days_to_target = fund.profit_target_usd / avg_profit_per_day_usd
        if days_to_target > 45:
            warnings.append(
                f"At current avg ${ avg_profit_per_day_usd:.0f}/day, reaching "
                f"${fund.profit_target_usd:.0f} profit target takes {days_to_target:.0f} days. "
                f"Most funds expire evaluations at 60-90 days."
            )
            score_penalties += 0.1
    else:
        warnings.append("avg_profit_per_day_usd not provided — cannot estimate time to target.")

    # --- Compute compatibility score ---
    eligible = len(violations) == 0
    compatibility_score = max(0.0, 1.0 - score_penalties)

    # --- Add positive recommendations if eligible ---
    if eligible:
        recommendations.append(
            f"Strategy is ELIGIBLE for {fund.name} evaluation. "
            f"Evaluation fee: ${fund.evaluation_fee_usd:.0f}. "
            f"Profit split: {fund.profit_split_pct}%."
        )
        if fund.platform == "rithmic":
            recommendations.append(
                "Deploy via Rithmic connector (see brokers/rithmic_connector.py). "
                "Set RITHMIC_SYSTEM_NAME and RITHMIC_PLANT_NAME in .env."
            )
        elif fund.platform == "tradovate":
            recommendations.append(
                "Deploy via existing Tradovate connector (tradovate.py). "
                "Use prop fund paper credentials during evaluation."
            )

    return ComplianceResult(
        fund_name=fund.name,
        strategy_name=strategy_name,
        eligible=eligible,
        violations=violations,
        warnings=warnings,
        recommendations=recommendations,
        compatibility_score=compatibility_score,
    )


def evaluate_all_funds(
    strategy_name: str,
    symbol: str,
    max_daily_loss_usd: float,
    max_drawdown_usd: float,
    avg_profit_per_day_usd: float,
    holds_overnight: bool = False,
    trades_news: bool = False,
    max_position_contracts: int = 2,
    min_trading_days_per_month: int = 15,
    historical_returns_daily: list[float] = None,
) -> dict:
    """Evaluate strategy against ALL prop funds and rank by compatibility.

    Args:
        strategy_name: Name of the strategy
        symbol: Trading instrument
        ... (same as evaluate_strategy_for_fund)

    Returns:
        dict with ranked fund recommendations
    """
    results = []
    seen = set()
    for fund_key, fund_obj in PROP_FUNDS.items():
        # De-dupe alias keys (apex -> apex_50k_eod are the same object)
        if id(fund_obj) in seen:
            continue
        seen.add(id(fund_obj))
        result = evaluate_strategy_for_fund(
            strategy_name=strategy_name,
            fund_name=fund_key,
            symbol=symbol,
            max_daily_loss_usd=max_daily_loss_usd,
            max_drawdown_usd=max_drawdown_usd,
            avg_profit_per_day_usd=avg_profit_per_day_usd,
            holds_overnight=holds_overnight,
            trades_news=trades_news,
            max_position_contracts=max_position_contracts,
            min_trading_days_per_month=min_trading_days_per_month,
            historical_returns_daily=historical_returns_daily,
        )
        results.append(result.to_dict())

    # Sort: eligible first, then by score desc
    results.sort(key=lambda r: (-int(r["eligible"]), -r["compatibility_score"]))

    eligible = [r for r in results if r["eligible"]]
    ineligible = [r for r in results if not r["eligible"]]

    return {
        "strategy": strategy_name,
        "symbol": symbol,
        "evaluated_at": datetime.now(tz=timezone.utc).isoformat(),
        "total_funds_evaluated": len(results),
        "eligible_funds": len(eligible),
        "top_recommendation": eligible[0]["fund_name"] if eligible else None,
        "ranked_results": results,
        "pipeline_recommendation": (
            f"Best match: {eligible[0]['fund_name']} (score {eligible[0]['compatibility_score']:.0%}). "
            f"Deploy after MCPT validation passes."
        ) if eligible else "No eligible funds found. Review strategy against fund rules."
    }


def simulate_drawdown_against_fund_rules(
    fund_name: str,
    daily_pnl_series: list[float],
    account_size_usd: float = None,
) -> dict:
    """Simulate running a daily P&L series against a prop fund's drawdown rules.

    Shows where the strategy would have hit fund limits historically,
    giving a true picture of evaluation survival probability.

    Args:
        fund_name:          Prop fund to simulate against
        daily_pnl_series:   List of daily P&L in USD (not percent)
        account_size_usd:   Override account size

    Returns:
        dict with survival analysis — would strategy pass evaluation?
    """
    fund = PROP_FUNDS.get(fund_name.lower())
    if not fund:
        return {"error": f"Unknown fund: {fund_name}"}

    account = account_size_usd or fund.account_size_usd
    balance = account
    high_water_mark = account
    daily_violations = []
    dd_violations = []
    consistency_violations = []
    cumulative_profit = 0.0
    days_traded = 0
    profit_days = []

    for i, daily_pnl in enumerate(daily_pnl_series):
        day = i + 1

        # Daily loss check (skip if fund has no explicit daily limit)
        if fund.max_daily_loss_usd > 0 and daily_pnl < 0 and abs(daily_pnl) > fund.max_daily_loss_usd:
            daily_violations.append({
                "day": day,
                "daily_pnl": daily_pnl,
                "limit": -fund.max_daily_loss_usd,
                "overage": abs(daily_pnl) - fund.max_daily_loss_usd,
            })

        balance += daily_pnl
        if daily_pnl != 0:
            days_traded += 1

        # Trailing drawdown line — Apex locks at (start + $100) once HWM - MaxDD >= start + $100
        if balance > high_water_mark:
            high_water_mark = balance
        dd_line = high_water_mark - fund.max_trailing_drawdown_usd
        if fund.drawdown_lock_at == "starting_plus_buffer":
            dd_line = max(dd_line, account + 100)   # Apex: DD line locks at start+$100
        elif fund.drawdown_lock_at == "starting_balance":
            dd_line = max(dd_line, account)
        trailing_dd = high_water_mark - balance
        if balance < dd_line:
            dd_violations.append({
                "day": day,
                "balance": balance,
                "high_water_mark": high_water_mark,
                "drawdown_line": dd_line,
                "trailing_drawdown": trailing_dd,
                "limit": fund.max_trailing_drawdown_usd,
                "drawdown_type": fund.drawdown_type,
            })

        # Track profit for consistency rule
        if daily_pnl > 0:
            cumulative_profit += daily_pnl
            profit_days.append({"day": day, "profit": daily_pnl})

    # Consistency rule simulation
    if fund.consistency_rule and cumulative_profit > 0:
        for pd in profit_days:
            day_pct = (pd["profit"] / cumulative_profit) * 100
            if day_pct > fund.consistency_pct:
                consistency_violations.append({
                    "day": pd["day"],
                    "day_profit": pd["profit"],
                    "day_pct_of_total": round(day_pct, 1),
                    "limit_pct": fund.consistency_pct,
                })

    # Profit target check
    final_profit = balance - account
    reached_target = final_profit >= fund.profit_target_usd
    min_days_met = days_traded >= fund.min_trading_days

    total_violations = len(daily_violations) + len(dd_violations) + len(consistency_violations)
    survival_probability = max(0.0, 1.0 - (total_violations / max(len(daily_pnl_series), 1)))

    return {
        "fund": fund.name,
        "simulation_days": len(daily_pnl_series),
        "days_traded": days_traded,
        "starting_balance": account,
        "final_balance": round(balance, 2),
        "final_profit": round(final_profit, 2),
        "profit_target": fund.profit_target_usd,
        "reached_profit_target": reached_target,
        "min_trading_days_required": fund.min_trading_days,
        "min_trading_days_met": min_days_met,
        "would_pass_evaluation": reached_target and min_days_met and total_violations == 0,
        "total_violations": total_violations,
        "daily_loss_violations": len(daily_violations),
        "trailing_drawdown_violations": len(dd_violations),
        "consistency_violations": len(consistency_violations),
        "survival_probability": round(survival_probability, 4),
        "violation_details": {
            "daily_violations": daily_violations[:5],  # First 5 for brevity
            "drawdown_violations": dd_violations[:5],
            "consistency_violations": consistency_violations[:5],
        },
        "recommendations": _generate_pass_recommendations(
            fund, daily_violations, dd_violations, consistency_violations, reached_target
        ),
    }


def _generate_pass_recommendations(
    fund: PropFundRules,
    daily_viol: list,
    dd_viol: list,
    consistency_viol: list,
    reached_target: bool,
) -> list[str]:
    recs = []
    if daily_viol:
        recs.append(
            f"Reduce daily max loss to under ${fund.max_daily_loss_usd:.0f}. "
            f"Use StoplossGuard or tighten stop loss on {len(daily_viol)} violation days."
        )
    if dd_viol:
        recs.append(
            f"Trailing drawdown exceeded {len(dd_viol)}x. "
            f"Reduce position size by {int((1 - fund.max_trailing_drawdown_usd / (fund.max_trailing_drawdown_usd * 1.3)) * 100)}% "
            f"or add drawdown-based position reduction."
        )
    if consistency_viol:
        recs.append(
            f"Consistency rule violated {len(consistency_viol)}x. "
            f"Cap daily profit extraction at {fund.consistency_pct * 0.8:.0f}% of running total."
        )
    if not reached_target:
        recs.append(
            f"Strategy didn't reach ${fund.profit_target_usd:.0f} profit target. "
            f"Consider increasing size within fund limits or choosing a smaller account tier."
        )
    if not recs:
        recs.append("No violations detected. Strategy is ready for live evaluation account deployment.")
    return recs


def list_prop_funds(platform: str = None) -> dict:
    """List all supported prop funds with their rules summary.

    De-duplicates alias keys (e.g., "apex" -> "apex_50k_eod") so each fund
    appears once with its canonical fund_key.
    """
    funds = []
    seen_identities = set()
    for key, fund in PROP_FUNDS.items():
        identity = id(fund)
        if identity in seen_identities:
            continue
        seen_identities.add(identity)
        if platform and fund.platform != platform:
            continue
        funds.append({
            "key": key,
            "fund_key": fund.fund_key or key,
            "name": fund.name,
            "platform": fund.platform,
            "account_size_usd": fund.account_size_usd,
            "max_daily_loss_usd": fund.max_daily_loss_usd,
            "max_trailing_drawdown_usd": fund.max_trailing_drawdown_usd,
            "drawdown_type": fund.drawdown_type,
            "profit_target_usd": fund.profit_target_usd,
            "profit_split_pct": fund.profit_split_pct,
            "evaluation_fee_usd": fund.evaluation_fee_usd,
            "activation_fee_usd": fund.activation_fee_usd,
            "overnight_allowed": fund.overnight_positions_allowed,
            "flat_by_time_ct": fund.flat_by_time_ct,
            "consistency_rule": fund.consistency_rule,
            "consistency_pct": fund.consistency_pct,
            "consistency_applies_in": fund.consistency_applies_in,
            "automation_policy": fund.automation_policy,
            "mandatory_bracket_orders": fund.mandatory_bracket_orders,
            "instruments": fund.instruments_allowed,
            "rules_verified_date": fund.rules_verified_date,
            "website": fund.website,
            "notes": fund.notes,
        })

    return {
        "total_funds": len(funds),
        "platform_filter": platform or "all",
        "funds": funds,
        "rithmic_funds": [f["fund_key"] for f in funds if f["platform"] == "rithmic"],
        "user_selected_primary": "apex_50k_eod",
        "user_selected_parallel": "mffu_core_50k",
        "note": (
            "Primary + parallel evaluation setup (2026-04): Apex 50K EOD Trail + MFFU Core 50K. "
            "FTMO (mt5) supports forex/CFDs only — not applicable to futures bots. "
            "Rules dates are indicative; always re-verify against fund website before paying evaluation fee. "
            "Use check_prop_fund_rules_freshness() to audit verification ages."
        ),
    }


def check_prop_fund_rules_freshness(max_age_days: int = 30) -> dict:
    """Audit all prop fund entries for how recently their rules were verified.

    Returns a dict flagging entries whose rules_verified_date is older than
    ``max_age_days`` days (or missing). Used by the autopilot to fail closed
    if it's about to commit to an evaluation fee against a stale rule set.
    """
    now = datetime.now(tz=timezone.utc)
    fresh, stale, missing = [], [], []
    seen = set()
    for key, fund in PROP_FUNDS.items():
        if id(fund) in seen:
            continue
        seen.add(id(fund))
        fkey = fund.fund_key or key
        if not fund.rules_verified_date:
            missing.append({"fund_key": fkey, "name": fund.name})
            continue
        try:
            verified = datetime.strptime(fund.rules_verified_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            age = (now - verified).days
            row = {
                "fund_key": fkey,
                "name": fund.name,
                "verified_date": fund.rules_verified_date,
                "age_days": age,
                "source": fund.rules_source_url,
            }
            if age > max_age_days:
                stale.append(row)
            else:
                fresh.append(row)
        except ValueError:
            missing.append({"fund_key": fkey, "name": fund.name, "bad_date": fund.rules_verified_date})

    return {
        "checked_at": now.isoformat(),
        "max_age_days": max_age_days,
        "fresh": fresh,
        "stale": stale,
        "missing": missing,
        "all_fresh": len(stale) == 0 and len(missing) == 0,
    }
