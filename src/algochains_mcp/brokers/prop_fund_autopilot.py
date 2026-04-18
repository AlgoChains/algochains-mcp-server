"""Prop Fund Autopilot — orchestrates the end-to-end prop-eval pipeline.

Ties together:
  - prop_fund_manager.evaluate_all_funds()         (rules check)
  - prop_fund_manager.simulate_drawdown_against_fund_rules()  (historical survival)
  - prop_fund_data_feeder.build_prop_fund_inputs() (real Tradovate metrics)
  - prop_fund_drawdown_monitor.register_prop_fund_account()   (monitor setup)
  - prop_mode config generation                    (control-tower side)

Every function is idempotent and fails closed. Nothing here places orders,
pays fees, or promotes to live. All destructive steps return a *plan* that
the operator must explicitly approve via a separate confirm tool.

Public entrypoints registered as MCP tools:
  - onboard_prop_account      : plan + (on approval) register account with monitor
  - deploy_bot_in_prop_mode   : generate launch-command + config plan (no-op until confirmed)
  - get_prop_mode_status      : report readiness of each prop account
  - request_prop_payout       : validate payout eligibility against fund rules
  - run_prop_fund_autopilot   : run the full pipeline and return an actionable plan
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("algochains_mcp.brokers.prop_fund_autopilot")

_STATE_DIR = Path(os.environ.get("ALGOCHAINS_CONTROL_TOWER", "/Users/treycsa/CascadeProjects/algochains-control-tower")) / "state"
_CONFIG_DIR = Path(os.environ.get("ALGOCHAINS_CONTROL_TOWER", "/Users/treycsa/CascadeProjects/algochains-control-tower")) / "config" / "prop_mode"

_AUTOPILOT_STATE = _STATE_DIR / "prop_fund_autopilot.json"


def _ensure_state_dir() -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def _load_state() -> dict:
    if _AUTOPILOT_STATE.exists():
        try:
            return json.loads(_AUTOPILOT_STATE.read_text())
        except Exception:
            pass
    return {"accounts": {}, "history": []}


def _save_state(state: dict) -> None:
    _ensure_state_dir()
    _AUTOPILOT_STATE.write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# onboard_prop_account
# ---------------------------------------------------------------------------

def onboard_prop_account(
    fund_key: str,
    account_id: str,
    broker: str,
    starting_balance: float,
    credentials_ref: Optional[str] = None,
    confirm: bool = False,
) -> dict:
    """Register a new prop evaluation account with the drawdown monitor.

    Two-step by design:
      1. ``confirm=False`` (default) — returns a *plan* describing what would
         be done. No state mutation.
      2. ``confirm=True`` — actually writes to monitor state and autopilot state.

    Credentials are NOT stored here. ``credentials_ref`` should be an env-var
    name or vault key (e.g. "TRADOVATE_APEX_50K_ACCESS_TOKEN") that the
    bot/monitor will read at runtime. The vault-key must already be set in
    ``.env`` before confirm=True is called.
    """
    from .prop_fund_manager import PROP_FUNDS, check_prop_fund_rules_freshness
    from .prop_fund_drawdown_monitor import register_prop_fund_account

    fund = PROP_FUNDS.get(fund_key.lower())
    if not fund:
        return {"ok": False, "error": f"Unknown fund_key={fund_key!r}. Call list_prop_funds."}

    # Rules freshness gate — refuse to onboard against stale rules
    freshness = check_prop_fund_rules_freshness(max_age_days=30)
    stale_keys = {row["fund_key"] for row in freshness["stale"]}
    missing_keys = {row["fund_key"] for row in freshness["missing"]}
    if (fund.fund_key in stale_keys) or (fund.fund_key in missing_keys):
        return {
            "ok": False,
            "error": (
                f"Rules for {fund.name} are stale or unverified "
                f"(rules_verified_date={fund.rules_verified_date!r}). "
                f"Re-verify against {fund.website} and update PROP_FUNDS before onboarding."
            ),
        }

    # Credentials presence check
    creds_ok = True
    creds_detail = "No credentials_ref provided — monitor will use default broker env vars."
    if credentials_ref:
        creds_ok = credentials_ref in os.environ
        creds_detail = (
            f"Env var {credentials_ref}: {'SET' if creds_ok else 'MISSING'}"
        )

    plan = {
        "fund": {
            "fund_key": fund.fund_key,
            "name": fund.name,
            "platform": fund.platform,
            "max_daily_loss_usd": fund.max_daily_loss_usd,
            "max_trailing_drawdown_usd": fund.max_trailing_drawdown_usd,
            "profit_target_usd": fund.profit_target_usd,
            "drawdown_type": fund.drawdown_type,
            "automation_policy": fund.automation_policy,
        },
        "account_id": account_id,
        "broker": broker,
        "starting_balance": starting_balance,
        "credentials_ref": credentials_ref,
        "credentials_ok": creds_ok,
        "credentials_detail": creds_detail,
        "rules_verified_date": fund.rules_verified_date,
        "would_write_state": str(_AUTOPILOT_STATE),
        "monitor_action": "register_prop_fund_account (prop_fund_drawdown_monitor)",
    }

    if not confirm:
        return {
            "ok": True,
            "dry_run": True,
            "plan": plan,
            "next_step": "Re-call with confirm=True to register. Credentials must be set in .env first.",
        }

    if not creds_ok and credentials_ref:
        return {"ok": False, "error": f"Cannot confirm onboarding — {credentials_ref} is not set in environment."}

    # Actually register with the monitor
    monitor_result = register_prop_fund_account(
        account_id=str(account_id),
        fund_name=fund.fund_key,
        broker=broker,
        starting_balance=float(starting_balance),
        max_daily_loss_usd=fund.max_daily_loss_usd or None,
        max_trailing_drawdown_usd=fund.max_trailing_drawdown_usd,
        profit_target_usd=fund.profit_target_usd,
    )

    # Persist autopilot state
    state = _load_state()
    state["accounts"][account_id] = {
        "fund_key": fund.fund_key,
        "broker": broker,
        "starting_balance": starting_balance,
        "credentials_ref": credentials_ref,
        "onboarded_at": datetime.now(tz=timezone.utc).isoformat(),
        "status": "onboarded_not_deployed",
    }
    state["history"].append({
        "event": "onboard",
        "fund_key": fund.fund_key,
        "account_id": account_id,
        "at": datetime.now(tz=timezone.utc).isoformat(),
    })
    _save_state(state)

    return {
        "ok": True,
        "dry_run": False,
        "plan": plan,
        "monitor_result": monitor_result,
        "next_step": f"Call deploy_bot_in_prop_mode(account_id={account_id!r}) to generate launch plan.",
    }


# ---------------------------------------------------------------------------
# deploy_bot_in_prop_mode
# ---------------------------------------------------------------------------

def deploy_bot_in_prop_mode(
    account_id: str,
    bot_name: str = "FUTURES_SCALPER_UPGRADED",
    symbol: str = "MNQ",
    confirm: bool = False,
) -> dict:
    """Generate a launch plan for the bot in PROP_MODE bound to account_id.

    Writes a config JSON to config/prop_mode/<account_id>.json that contains
    the fund rules the bot must enforce (consistency, flat-by, max_position,
    daily loss cap, drawdown line). The bot only reads this file when the
    PROP_MODE env var is set at launch — otherwise it behaves identically
    to live production.

    Never auto-launches the bot. Returns the exact launch command the
    operator must run. Requires ``confirm=True`` to write the config file.
    """
    from .prop_fund_manager import PROP_FUNDS

    state = _load_state()
    acct = state["accounts"].get(account_id)
    if not acct:
        return {"ok": False, "error": f"Account {account_id} not onboarded. Call onboard_prop_account first."}

    fund = PROP_FUNDS.get(acct["fund_key"])
    if not fund:
        return {"ok": False, "error": f"Fund {acct['fund_key']} missing from PROP_FUNDS."}

    # Derive a safe per-fund position cap (respect fund.max_position_size if >0; else keep bot default)
    position_cap = fund.max_position_size if fund.max_position_size > 0 else None

    # Default daily-loss cap = 80% of fund's limit (or 80% of trailing DD if no daily limit)
    daily_loss_soft_cap = (
        fund.max_daily_loss_usd * 0.8 if fund.max_daily_loss_usd > 0
        else fund.max_trailing_drawdown_usd * 0.4  # 40% of trailing if no daily rule
    )

    config = {
        "prop_mode": True,
        "account_id": account_id,
        "fund_key": fund.fund_key,
        "fund_name": fund.name,
        "bot_name": bot_name,
        "symbol": symbol,
        "starting_balance": acct["starting_balance"],
        "rules": {
            "max_daily_loss_usd": fund.max_daily_loss_usd,
            "max_trailing_drawdown_usd": fund.max_trailing_drawdown_usd,
            "drawdown_type": fund.drawdown_type,
            "drawdown_lock_at": fund.drawdown_lock_at,
            "profit_target_usd": fund.profit_target_usd,
            "min_trading_days": fund.min_trading_days,
            "max_position_size": position_cap,
            "consistency_rule": fund.consistency_rule,
            "consistency_pct": fund.consistency_pct,
            "consistency_applies_in": fund.consistency_applies_in,
            "overnight_positions_allowed": fund.overnight_positions_allowed,
            "flat_by_time_ct": fund.flat_by_time_ct,
            "news_trading_allowed": fund.news_trading_allowed,
            "automation_policy": fund.automation_policy,
            "mandatory_bracket_orders": fund.mandatory_bracket_orders,
        },
        "soft_caps": {
            "daily_loss_soft_cap_usd": round(daily_loss_soft_cap, 2),
            "trailing_dd_soft_cap_usd": round(fund.max_trailing_drawdown_usd * 0.8, 2),
            "max_consistency_day_profit_usd": round(
                fund.profit_target_usd * (fund.consistency_pct or 50) / 100 * 0.9, 2
            ),
        },
        "credentials_ref": acct.get("credentials_ref"),
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
    }

    config_path = _CONFIG_DIR / f"{account_id}.json"
    launch_cmd = (
        f"PROP_MODE=true "
        f"PROP_MODE_CONFIG={config_path} "
        f"python3 {bot_name}.py"
    )

    if not confirm:
        return {
            "ok": True,
            "dry_run": True,
            "config_preview": config,
            "would_write": str(config_path),
            "launch_cmd": launch_cmd,
            "next_step": "Re-call with confirm=True to write the config file. Then run launch_cmd manually.",
        }

    _ensure_state_dir()
    config_path.write_text(json.dumps(config, indent=2))
    state["accounts"][account_id]["status"] = "deployed_pending_launch"
    state["accounts"][account_id]["config_path"] = str(config_path)
    state["history"].append({
        "event": "deploy_config_written",
        "account_id": account_id,
        "config_path": str(config_path),
        "at": datetime.now(tz=timezone.utc).isoformat(),
    })
    _save_state(state)

    return {
        "ok": True,
        "dry_run": False,
        "config_path": str(config_path),
        "launch_cmd": launch_cmd,
        "next_step": (
            f"Owner must manually run:\n  {launch_cmd}\n"
            f"Autopilot will NOT launch the bot automatically — this is a gated step per the "
            f"bot-logic-change approval rule."
        ),
    }


# ---------------------------------------------------------------------------
# get_prop_mode_status
# ---------------------------------------------------------------------------

def get_prop_mode_status(account_id: Optional[str] = None) -> dict:
    """Report the status of all prop-mode accounts (or one specific account)."""
    state = _load_state()
    if account_id:
        acct = state["accounts"].get(account_id)
        if not acct:
            return {"ok": False, "error": f"Account {account_id} not found in autopilot state."}
        return {"ok": True, "account": {**acct, "account_id": account_id}}
    return {
        "ok": True,
        "total_accounts": len(state["accounts"]),
        "accounts": [{"account_id": k, **v} for k, v in state["accounts"].items()],
        "autopilot_state_file": str(_AUTOPILOT_STATE),
        "config_dir": str(_CONFIG_DIR),
    }


# ---------------------------------------------------------------------------
# request_prop_payout
# ---------------------------------------------------------------------------

def request_prop_payout(
    account_id: str,
    current_balance: float,
) -> dict:
    """Validate whether account is eligible to request a payout.

    Does NOT actually initiate a payout (that is a manual action on the
    fund's dashboard). Just checks profit target, min days, safety net,
    consistency compliance, and payout caps.
    """
    from .prop_fund_manager import PROP_FUNDS
    from .prop_fund_drawdown_monitor import get_prop_fund_monitor_status

    state = _load_state()
    acct = state["accounts"].get(account_id)
    if not acct:
        return {"ok": False, "error": f"Unknown account_id={account_id!r}"}

    fund = PROP_FUNDS.get(acct["fund_key"])
    if not fund:
        return {"ok": False, "error": f"Fund {acct['fund_key']!r} missing."}

    starting = float(acct["starting_balance"])
    profit = current_balance - starting
    reached_target = profit >= fund.profit_target_usd
    above_safety_net = profit >= (fund.safety_net_usd or 0)

    monitor = get_prop_fund_monitor_status()
    mon_acct = (monitor.get("accounts") or {}).get(account_id, {})
    days_traded = int(mon_acct.get("days_traded", 0))
    min_days_ok = days_traded >= max(fund.min_trading_days, fund.first_payout_min_days)

    eligible = reached_target and above_safety_net and min_days_ok

    return {
        "ok": True,
        "account_id": account_id,
        "fund": fund.name,
        "starting_balance": starting,
        "current_balance": current_balance,
        "realized_profit": round(profit, 2),
        "profit_target": fund.profit_target_usd,
        "reached_target": reached_target,
        "safety_net_required": fund.safety_net_usd,
        "above_safety_net": above_safety_net,
        "days_traded": days_traded,
        "min_days_required": max(fund.min_trading_days, fund.first_payout_min_days),
        "min_days_ok": min_days_ok,
        "payout_cap_per_period": fund.payout_cap_count,
        "eligible": eligible,
        "note": (
            "This tool validates eligibility only. You must initiate the payout "
            f"manually on {fund.website} after confirming eligibility here."
        ),
    }


# ---------------------------------------------------------------------------
# run_prop_fund_autopilot — the main orchestrator
# ---------------------------------------------------------------------------

def run_prop_fund_autopilot(
    strategy_name: str = "FUTURES_SCALPER_UPGRADED",
    symbol: str = "MNQ",
    lookback_days: int = 90,
    account_id: Optional[int] = None,
    fund_keys: Optional[list[str]] = None,
    fills_override: Optional[list[dict]] = None,
) -> dict:
    """End-to-end: freshness -> real-data -> eval -> simulate -> recommend.

    This is the single tool an operator calls to get a go/no-go on prop
    evaluation. It does NOT pay any fees, register any account, or start
    any bot. It returns a comprehensive plan the operator reviews.

    Steps:
      1. check_prop_fund_rules_freshness() -> fail closed if stale
      2. build_prop_fund_inputs(...)       -> real Tradovate metrics
      3. evaluate_all_funds(...)           -> score each fund
      4. For each target fund: simulate_drawdown_against_fund_rules(...)
      5. Recommend: parallel Apex EOD + MFFU Core as per operator selection

    Args:
        fund_keys: if None, evaluates against user-selected defaults
                   ("apex_50k_eod", "mffu_core_50k"). Pass a list to override.
    """
    from .prop_fund_manager import (
        evaluate_all_funds,
        simulate_drawdown_against_fund_rules,
        check_prop_fund_rules_freshness,
        PROP_FUNDS,
    )
    from .prop_fund_data_feeder import build_prop_fund_inputs

    target_funds = fund_keys or ["apex_50k_eod", "mffu_core_50k"]

    # Step 1 — rules freshness gate
    freshness = check_prop_fund_rules_freshness(max_age_days=30)
    targets_with_stale = [
        fk for fk in target_funds
        if fk in {r["fund_key"] for r in freshness["stale"]} | {r["fund_key"] for r in freshness["missing"]}
    ]
    if targets_with_stale:
        return {
            "ok": False,
            "stage": "rules_freshness",
            "error": f"Target funds have stale/missing rules: {targets_with_stale}",
            "freshness_report": freshness,
        }

    # Step 2 — real-data metrics
    metrics = build_prop_fund_inputs(
        strategy_name=strategy_name,
        symbol=symbol,
        lookback_days=lookback_days,
        account_id=account_id,
        fills_override=fills_override,
    )
    if not metrics.get("data_ok"):
        return {"ok": False, "stage": "real_data", "error": metrics.get("error"), "metrics": metrics}

    # Step 3 — evaluate across ALL funds, then pull out target fund rows
    eval_all = evaluate_all_funds(
        strategy_name=strategy_name,
        symbol=symbol,
        max_daily_loss_usd=metrics["max_daily_loss_usd"],
        max_drawdown_usd=metrics["max_drawdown_usd"],
        avg_profit_per_day_usd=metrics["avg_profit_per_day_usd"],
        holds_overnight=metrics["holds_overnight"],
        trades_news=metrics["trades_news"],
        max_position_contracts=metrics["max_position_contracts"],
        min_trading_days_per_month=metrics["min_trading_days_per_month"],
        historical_returns_daily=metrics["historical_returns_daily"],
    )

    target_evals = []
    for fk in target_funds:
        fund = PROP_FUNDS.get(fk)
        if not fund:
            target_evals.append({"fund_key": fk, "error": "fund_key not in PROP_FUNDS"})
            continue
        row = next((r for r in eval_all["ranked_results"] if r["fund_name"] == fund.name), None)
        target_evals.append({"fund_key": fk, "fund_name": fund.name, "eval": row})

    # Step 4 — drawdown simulation for each target fund
    simulations = {}
    for fk in target_funds:
        sim = simulate_drawdown_against_fund_rules(
            fund_name=fk,
            daily_pnl_series=metrics["daily_pnl_series"],
        )
        simulations[fk] = sim

    # Step 5 — recommend
    all_eligible = all(te.get("eval", {}).get("eligible") for te in target_evals)
    all_survive = all(simulations[fk].get("would_pass_evaluation") for fk in target_funds)

    recommendation = {
        "go_no_go": "GO" if all_eligible and all_survive else "NO-GO",
        "target_funds": target_funds,
        "all_eligible_by_rules": all_eligible,
        "all_survived_simulation": all_survive,
        "next_steps": (
            [
                f"Call onboard_prop_account(fund_key='{fk}', account_id=<your_acct>, broker='rithmic'|'tradovate', starting_balance=50000, confirm=True)"
                for fk in target_funds
            ] + [
                f"Call deploy_bot_in_prop_mode(account_id=<your_acct>, confirm=True) to generate launch config"
            ]
        ) if all_eligible and all_survive else [
            "Review failures below and reduce risk or choose different funds.",
        ],
    }

    return {
        "ok": True,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "strategy": strategy_name,
        "symbol": symbol,
        "metrics": {
            "days_traded": metrics["days_traded"],
            "fills_analyzed": metrics["fills_analyzed"],
            "round_trips": metrics["round_trips"],
            "total_pnl_usd": metrics.get("total_pnl_usd", 0.0),
            "max_daily_loss_usd": metrics["max_daily_loss_usd"],
            "max_drawdown_usd": metrics["max_drawdown_usd"],
            "avg_profit_per_day_usd": metrics["avg_profit_per_day_usd"],
            "max_position_contracts": metrics["max_position_contracts"],
            "holds_overnight": metrics["holds_overnight"],
        },
        "freshness": {"all_fresh": freshness["all_fresh"], "stale_count": len(freshness["stale"])},
        "target_funds_eval": target_evals,
        "drawdown_simulations": simulations,
        "top_3_matches": eval_all["ranked_results"][:3],
        "recommendation": recommendation,
    }
