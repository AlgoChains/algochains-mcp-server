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
    max_daily_loss_usd: float      # Hard daily drawdown limit
    max_trailing_drawdown_usd: float  # Max trailing drawdown from high water mark
    profit_target_usd: float       # To pass evaluation
    min_trading_days: int          # Min days must be active
    max_position_size: int         # Max contracts (varies by instrument)
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

    def to_dict(self) -> dict:
        return asdict(self)


PROP_FUNDS: dict[str, PropFundRules] = {
    "apex": PropFundRules(
        name="Apex Trader Funding",
        platform="rithmic",  # Also supports Tradovate
        account_size_usd=50000,
        max_daily_loss_usd=2500,
        max_trailing_drawdown_usd=2500,
        profit_target_usd=3000,
        min_trading_days=7,
        max_position_size=4,       # For MNQ: up to 14 micros, NQ: up to 4
        consistency_rule=False,
        consistency_pct=0.0,
        instruments_allowed=["MNQ", "NQ", "MES", "ES", "CL", "MCL", "GC", "MGC", "RTY", "M2K"],
        news_trading_allowed=True,
        overnight_positions_allowed=True,
        evaluation_fee_usd=147,
        monthly_fee_usd=85,
        profit_split_pct=90.0,
        website="https://apextraderfunding.com",
        notes="Most popular. No consistency rule. Supports both Rithmic and Tradovate."
    ),
    "topstep": PropFundRules(
        name="Topstep Trader",
        platform="rithmic",
        account_size_usd=50000,
        max_daily_loss_usd=1000,
        max_trailing_drawdown_usd=2000,
        profit_target_usd=3000,
        min_trading_days=5,
        max_position_size=5,
        consistency_rule=True,
        consistency_pct=30.0,      # No day > 30% of total profit
        instruments_allowed=["MNQ", "NQ", "MES", "ES", "CL", "GC", "RTY"],
        news_trading_allowed=False,
        overnight_positions_allowed=False,  # Must close before 4:59 PM CT
        evaluation_fee_usd=165,
        monthly_fee_usd=99,
        profit_split_pct=90.0,
        website="https://topstep.com",
        notes="Has consistency rule + overnight restriction. No news trading."
    ),
    "myfundedfutures": PropFundRules(
        name="MyFundedFutures",
        platform="rithmic",
        account_size_usd=50000,
        max_daily_loss_usd=1500,
        max_trailing_drawdown_usd=2500,
        profit_target_usd=3000,
        min_trading_days=5,
        max_position_size=5,
        consistency_rule=True,
        consistency_pct=40.0,
        instruments_allowed=["MNQ", "NQ", "MES", "ES", "CL", "GC", "RTY"],
        news_trading_allowed=False,
        overnight_positions_allowed=False,
        evaluation_fee_usd=129,
        monthly_fee_usd=79,
        profit_split_pct=90.0,
        website="https://myfundedfutures.com",
        notes="Rithmic only. Consistency rule applies."
    ),
    "tradeday": PropFundRules(
        name="TradeDay",
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
        evaluation_fee_usd=99,
        monthly_fee_usd=49,
        profit_split_pct=90.0,
        website="https://tradeday.com",
        notes="Good entry-level option. No consistency rule."
    ),
    "bulenox": PropFundRules(
        name="Bulenox",
        platform="rithmic",
        account_size_usd=50000,
        max_daily_loss_usd=1500,
        max_trailing_drawdown_usd=2500,
        profit_target_usd=3000,
        min_trading_days=0,         # No minimum trading days
        max_position_size=4,
        consistency_rule=False,
        consistency_pct=0.0,
        instruments_allowed=["MNQ", "NQ", "MES", "ES", "CL", "GC", "RTY"],
        news_trading_allowed=True,
        overnight_positions_allowed=True,
        evaluation_fee_usd=99,
        monthly_fee_usd=49,
        profit_split_pct=90.0,
        website="https://bulenox.com",
        notes="No min trading days. Crypto futures also available."
    ),
    "earn2trade": PropFundRules(
        name="Earn2Trade (Gauntlet Mini)",
        platform="rithmic",
        account_size_usd=25000,
        max_daily_loss_usd=1000,
        max_trailing_drawdown_usd=1500,
        profit_target_usd=1500,
        min_trading_days=15,        # Minimum 15 days to pass Gauntlet
        max_position_size=2,
        consistency_rule=True,
        consistency_pct=50.0,
        instruments_allowed=["MNQ", "MES", "MCL"],
        news_trading_allowed=False,
        overnight_positions_allowed=False,
        evaluation_fee_usd=150,
        monthly_fee_usd=0,          # One-time fee
        profit_split_pct=80.0,
        website="https://earn2trade.com",
        notes="Educational platform. Strict rules. Good for micro-only bots (MNQ/MES)."
    ),
    "ftmo": PropFundRules(
        name="FTMO",
        platform="mt5",             # MetaTrader 5 only — forex and CFDs
        account_size_usd=100000,
        max_daily_loss_usd=1000,    # 1% daily
        max_trailing_drawdown_usd=10000,  # 10% max drawdown
        profit_target_usd=10000,    # 10% profit target
        min_trading_days=4,         # Minimum 4 days
        max_position_size=0,        # No contract limit (% of account)
        consistency_rule=False,
        consistency_pct=0.0,
        instruments_allowed=["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "US30", "NAS100"],
        news_trading_allowed=False,
        overnight_positions_allowed=True,
        evaluation_fee_usd=540,     # For 100K challenge
        monthly_fee_usd=0,
        profit_split_pct=80.0,
        website="https://ftmo.com",
        notes="Forex/CFD only. No Rithmic. Requires MetaTrader 5. Not for futures bots."
    ),
}


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

    # 2. Daily loss limit
    if max_daily_loss_usd > fund.max_daily_loss_usd:
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
            f"{fund.name} requires all positions closed before end of trading day. "
            f"Strategy holds overnight — would trigger automatic violation."
        )
        score_penalties += 0.5

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
                warnings.append(
                    f"{fund.name} consistency rule: no single day may exceed "
                    f"{fund.consistency_pct}% of total profit. "
                    f"Strategy's best day = {max_day_pct:.1f}% — would likely violate rule."
                )
                score_penalties += 0.3
                recommendations.append(
                    f"Cap daily profit at {fund.consistency_pct * 0.9:.0f}% of running "
                    f"total to comply with consistency rule."
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
    for fund_key in PROP_FUNDS:
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

        # Daily loss check
        if daily_pnl < 0 and abs(daily_pnl) > fund.max_daily_loss_usd:
            daily_violations.append({
                "day": day,
                "daily_pnl": daily_pnl,
                "limit": -fund.max_daily_loss_usd,
                "overage": abs(daily_pnl) - fund.max_daily_loss_usd,
            })

        balance += daily_pnl
        if daily_pnl != 0:
            days_traded += 1

        # High water mark trailing drawdown
        if balance > high_water_mark:
            high_water_mark = balance
        trailing_dd = high_water_mark - balance
        if trailing_dd > fund.max_trailing_drawdown_usd:
            dd_violations.append({
                "day": day,
                "balance": balance,
                "high_water_mark": high_water_mark,
                "trailing_drawdown": trailing_dd,
                "limit": fund.max_trailing_drawdown_usd,
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
    """List all supported prop funds with their rules summary."""
    funds = []
    for key, fund in PROP_FUNDS.items():
        if platform and fund.platform != platform:
            continue
        funds.append({
            "key": key,
            "name": fund.name,
            "platform": fund.platform,
            "account_size_usd": fund.account_size_usd,
            "max_daily_loss_usd": fund.max_daily_loss_usd,
            "max_trailing_drawdown_usd": fund.max_trailing_drawdown_usd,
            "profit_target_usd": fund.profit_target_usd,
            "profit_split_pct": fund.profit_split_pct,
            "evaluation_fee_usd": fund.evaluation_fee_usd,
            "overnight_allowed": fund.overnight_positions_allowed,
            "consistency_rule": fund.consistency_rule,
            "instruments": fund.instruments_allowed,
            "website": fund.website,
        })

    return {
        "total_funds": len(funds),
        "platform_filter": platform or "all",
        "funds": funds,
        "rithmic_funds": [f["key"] for f in funds if f["platform"] == "rithmic"],
        "note": (
            "FTMO (mt5) supports forex/CFDs only. All others support US futures. "
            "Apex and TradeDay are best fit for AlgoChains MNQ/CL/MES/NQ bots."
        ),
    }
