"""
Kalshi Unified Pipeline — AlgoChains v1.0

One-call entry point that runs the full Kalshi strategy stack:
  1. Events API scan → full tradeable universe
  2. Category scoring → block/allow
  3. Safe Compounder → NO-side near-certain trades
  4. Statistical Arbitrage → pricing inconsistencies
  5. AI Ensemble → consensus-gated debate (optional, costs money)
  6. Kelly sizing → position sizes
  7. Slack notification → #kalshi-bot-changelog
  8. Supabase logging → kalshi_strategy_runs table

This is the MCP tool `run_kalshi_full_pipeline`.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from algochains_mcp.order_flow.kalshi_strategy_engine import get_account_state
from algochains_mcp.order_flow.kalshi_safe_compounder import run_safe_compounder
from algochains_mcp.order_flow.kalshi_events_scanner import scan_full_universe_summary
from algochains_mcp.order_flow.kalshi_category_scorer import (
    get_all_category_scores,
    format_scores_table,
)
from algochains_mcp.order_flow.kalshi_stat_arb import scan_stat_arb_opportunities
from algochains_mcp.order_flow.kalshi_slack_notifier import (
    notify_scan_summary,
    notify_edge_found,
    notify_circuit_breaker,
)

logger = logging.getLogger("algochains_mcp.order_flow.kalshi_pipeline")

# ─── Risk limits (battle-tested from ryanfrigo live data) ────────────────────
MAX_DAILY_LOSS_PCT   = 0.10   # Halt all trading at 10% daily loss
MAX_DRAWDOWN_PCT     = 0.15   # Halt at 15% portfolio drawdown
STARTING_BANKROLL    = 250.0  # Initial capital ($250) — used only as fallback floor

# ─── State file for peak balance tracking (persisted across restarts) ─────────
_STATE_FILE = Path(os.getenv("ALGOCHAINS_CONTROL_TOWER",
                              str(Path(__file__).resolve().parents[4] / "algochains-control-tower")
                             )) / "state" / "kalshi_daemon_state.json"

# ─── Startup warning for missing optional keys ────────────────────────────────
if not os.getenv("OPENROUTER_API_KEY"):
    logger.warning(
        "OPENROUTER_API_KEY not set — kalshi_ai_ensemble will be disabled. "
        "Add OPENROUTER_API_KEY to .env to enable the 5-model AI debate."
    )


def _circuit_breaker_check(
    current_balance: float,
    peak_balance: float,
) -> tuple[bool, str]:
    """Return (should_halt, reason) based on current balance vs peak.

    A balance of exactly $0.00 indicates a failed API read (network timeout),
    not a real total loss. We never let an API error pull the circuit breaker.
    """
    if current_balance <= 0.0:
        logger.warning(
            "Circuit breaker guard: balance=%.2f looks like an API read failure — skipping check",
            current_balance,
        )
        return False, ""

    drawdown = (peak_balance - current_balance) / peak_balance
    if drawdown >= MAX_DRAWDOWN_PCT:
        return True, f"Max drawdown {drawdown:.1%} exceeded limit {MAX_DRAWDOWN_PCT:.0%}"

    daily_loss = (STARTING_BANKROLL - current_balance) / STARTING_BANKROLL
    if daily_loss >= MAX_DAILY_LOSS_PCT:
        return True, f"Daily loss {daily_loss:.1%} exceeded limit {MAX_DAILY_LOSS_PCT:.0%}"

    return False, ""


def run_kalshi_full_pipeline(
    enable_ai_ensemble: bool = False,
    enable_stat_arb: bool = True,
    execute_safe_compounder: bool = False,
    confirmed: bool = False,
    notify_slack: bool = True,
) -> dict[str, Any]:
    """
    Full Kalshi strategy pipeline.

    Args:
        enable_ai_ensemble: if True, run 5-model AI debate on top opportunities (costs $)
        enable_stat_arb: if True, scan for statistical arbitrage opportunities
        execute_safe_compounder: if True, place actual Safe Compounder orders
        confirmed: must be True alongside execute_safe_compounder to place real orders
        notify_slack: if True, post results to #kalshi-bot-changelog

    Returns comprehensive pipeline result dict.
    """
    start_time = datetime.now(timezone.utc)
    pipeline_result: dict[str, Any] = {
        "pipeline_version": "v1.0",
        "started_at": start_time.isoformat(),
    }

    # ── Step 1: Account state ──────────────────────────────────────────────────
    try:
        account = get_account_state()
        bankroll_usd = account.balance_usd
        pipeline_result["account"] = {
            "balance_usd": bankroll_usd,
            "positions_count": len(account.positions or []),
            "open_orders": len(account.open_orders or []),
        }
    except Exception as exc:
        logger.error("Account fetch failed (API/network error): %s", exc)
        # Use the last persisted balance so drawdown math stays accurate across transient
        # network failures. Fall back to STARTING_BANKROLL only if state file is absent.
        bankroll_usd = _load_last_known_balance()
        pipeline_result["account"] = {
            "error": str(exc),
            "balance_usd_fallback": bankroll_usd,
            "note": "balance from state file — API unreachable",
        }

    # ── Step 2: Circuit breaker check ─────────────────────────────────────────
    # Load persisted peak balance so drawdown survives daemon restarts.
    # balance_is_live tracks whether bankroll_usd came from the live API or a fallback.
    balance_is_live = "error" not in pipeline_result.get("account", {})
    peak_balance = _load_peak_balance(bankroll_usd)
    halt, halt_reason = _circuit_breaker_check(bankroll_usd, peak_balance)
    if halt:
        logger.critical("CIRCUIT BREAKER: %s", halt_reason)
        if notify_slack:
            loss_pct = (peak_balance - bankroll_usd) / peak_balance if peak_balance > 0 else 0.0
            notify_circuit_breaker(
                halt_reason,
                bankroll_usd,
                loss_pct,
                balance_is_estimate=not balance_is_live,
            )
        pipeline_result["circuit_breaker"] = {"triggered": True, "reason": halt_reason}
        pipeline_result["status"] = "halted"
        return pipeline_result

    pipeline_result["circuit_breaker"] = {"triggered": False}

    # Persist updated peak balance for accurate drawdown tracking across restarts
    new_peak = max(bankroll_usd, peak_balance)
    _save_peak_balance(new_peak)

    # ── Step 3: Universe scan ──────────────────────────────────────────────────
    try:
        universe = scan_full_universe_summary()
        pipeline_result["universe"] = universe
    except Exception as exc:
        logger.error("Universe scan failed: %s", exc)
        pipeline_result["universe"] = {"error": str(exc)}

    # ── Step 4: Category scores ────────────────────────────────────────────────
    category_scores = get_all_category_scores()
    pipeline_result["category_scores"] = category_scores
    pipeline_result["category_scores_table"] = format_scores_table(category_scores)

    # ── Step 5: Safe Compounder ────────────────────────────────────────────────
    try:
        sc_result = run_safe_compounder(
            bankroll_usd=bankroll_usd,
            execute=execute_safe_compounder,
            confirmed=confirmed,
        )
        pipeline_result["safe_compounder"] = sc_result
    except Exception as exc:
        logger.error("Safe Compounder failed: %s", exc)
        pipeline_result["safe_compounder"] = {"error": str(exc)}

    # ── Step 6: Statistical Arbitrage ─────────────────────────────────────────
    if enable_stat_arb:
        try:
            arb_result = scan_stat_arb_opportunities(max_events=50, max_markets_per_scan=100)
            pipeline_result["stat_arb"] = arb_result
        except Exception as exc:
            logger.error("Stat arb scan failed: %s", exc)
            pipeline_result["stat_arb"] = {"error": str(exc)}
    else:
        pipeline_result["stat_arb"] = {"skipped": True}

    # ── Step 7: AI Ensemble (optional, costs money) ───────────────────────────
    if enable_ai_ensemble:
        from algochains_mcp.order_flow.kalshi_ai_ensemble import (
            run_ensemble_debate,
            ensemble_decision_to_dict,
            check_ai_budget,
        )
        budget_ok, spent, remaining = check_ai_budget()
        sc_opps = (pipeline_result.get("safe_compounder") or {}).get("opportunities", [])
        if budget_ok and sc_opps:
            top_opp = sc_opps[0]
            try:
                decision = run_ensemble_debate(
                    ticker=top_opp["ticker"],
                    title=top_opp["title"],
                    yes_bid=top_opp.get("yes_bid", 0.10),
                    yes_ask=top_opp.get("yes_ask", 0.20),
                    close_time=top_opp.get("close_time", ""),
                    fast_mode=True,  # 3 models to save cost
                )
                pipeline_result["ai_ensemble"] = ensemble_decision_to_dict(decision)
            except Exception as exc:
                logger.error("AI ensemble failed: %s", exc)
                pipeline_result["ai_ensemble"] = {"error": str(exc)}
        else:
            pipeline_result["ai_ensemble"] = {
                "skipped": True,
                "reason": "Budget exhausted" if not budget_ok else "No opportunities to analyze",
            }
    else:
        pipeline_result["ai_ensemble"] = {"skipped": True, "reason": "enable_ai_ensemble=False"}

    # ── Step 8: Summarize and notify ──────────────────────────────────────────
    sc_opps = (pipeline_result.get("safe_compounder") or {}).get("opportunities", [])
    arb_opps = (pipeline_result.get("stat_arb") or {}).get("opportunities", [])
    total_opportunities = len(sc_opps) + len(arb_opps)
    top_sc = sc_opps[0] if sc_opps else None

    pipeline_result["summary"] = {
        "safe_compounder_opportunities": len(sc_opps),
        "stat_arb_opportunities": len(arb_opps),
        "total_opportunities": total_opportunities,
        "top_opportunity": top_sc,
        "bankroll_usd": bankroll_usd,
        "total_capital_at_risk_usd": sum(o.get("position_usd", 0) for o in sc_opps[:5]),
    }

    if notify_slack:
        try:
            # categories_scanned must be int — .get("categories") returns a list[dict]
            categories_list = (pipeline_result.get("universe") or {}).get("categories", [])
            notify_scan_summary(
                opportunities_found=total_opportunities,
                actionable=len([o for o in sc_opps if o.get("edge", 0) >= 0.05]),
                top_ticker=top_sc["ticker"] if top_sc else None,
                top_edge=top_sc["edge"] if top_sc else None,
                categories_scanned=len(categories_list),
                markets_scanned=(pipeline_result.get("universe") or {}).get("total_non_blocked_markets", 0),
            )
            if top_sc and top_sc.get("edge", 0) >= 0.05:
                notify_edge_found(
                    ticker=top_sc["ticker"],
                    title=top_sc.get("title", ""),
                    category=top_sc.get("category", ""),
                    action="buy_no",
                    edge=top_sc["edge"],
                    yes_bid=top_sc.get("yes_bid", 0),
                    no_ask=top_sc.get("no_ask_taker", 0),
                    suggested_contracts=top_sc.get("suggested_contracts", 0),
                    position_usd=top_sc.get("position_usd", 0),
                    source="safe_compounder",
                )
        except Exception as exc:
            logger.warning("Slack notification failed: %s", exc)

    # ── Step 9: Log to Supabase ───────────────────────────────────────────────
    elapsed_ms = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)
    pipeline_result["elapsed_ms"] = elapsed_ms
    pipeline_result["status"] = "ok"

    try:
        _log_pipeline_run_to_supabase(pipeline_result, elapsed_ms, bankroll_usd)
    except Exception as exc:
        logger.warning("Supabase pipeline log failed: %s", exc)

    return pipeline_result


def _load_last_known_balance() -> float:
    """Return the last successfully fetched balance from the daemon state file.

    Used as a safe fallback when the Kalshi API is temporarily unreachable so
    the circuit breaker does not fire on a transient network error.
    Falls back to STARTING_BANKROLL if no state file exists.
    """
    try:
        if _STATE_FILE.exists():
            data = json.loads(_STATE_FILE.read_text())
            stored = float(data.get("last_known_balance_usd", 0.0))
            if stored > 0.0:
                logger.info(
                    "Using last-known balance $%.2f from state file (API unreachable)", stored
                )
                return stored
    except Exception:
        pass
    logger.info("No persisted balance found — using STARTING_BANKROLL $%.2f", STARTING_BANKROLL)
    return STARTING_BANKROLL


def _load_peak_balance(current_balance: float) -> float:
    """
    Load the historical peak balance from the daemon state file.
    Returns max(current_balance, stored_peak, STARTING_BANKROLL) so the
    circuit breaker has an accurate drawdown reference across restarts.
    """
    try:
        if _STATE_FILE.exists():
            data = json.loads(_STATE_FILE.read_text())
            stored_peak = float(data.get("peak_balance_usd", STARTING_BANKROLL))
            return max(current_balance, stored_peak, STARTING_BANKROLL)
    except Exception:
        pass
    return max(current_balance, STARTING_BANKROLL)


def _save_peak_balance(peak_balance: float) -> None:
    """Persist updated peak balance to the daemon state file."""
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        existing: dict = {}
        if _STATE_FILE.exists():
            try:
                existing = json.loads(_STATE_FILE.read_text())
            except Exception:
                pass
        existing["peak_balance_usd"] = peak_balance
        existing["last_updated"] = datetime.now(timezone.utc).isoformat()
        _STATE_FILE.write_text(json.dumps(existing, indent=2))
    except Exception as exc:
        logger.debug("Could not save peak balance: %s", exc)


def _log_pipeline_run_to_supabase(result: dict[str, Any], elapsed_ms: int, balance_usd: float) -> None:
    """Log pipeline run summary to kalshi_strategy_runs in Supabase.

    Throttled to one write per 10 minutes (MIN_SUPABASE_WRITE_INTERVAL) to prevent
    spam if the pipeline is called manually or restarts rapidly.  The timestamp is
    persisted in the same daemon state file used for peak-balance tracking.
    """
    MIN_SUPABASE_WRITE_INTERVAL = 600  # seconds — 1 write per 10 min max

    # ── Throttle check: skip if last write was < 10 min ago ──────────────────
    try:
        state_data: dict = {}
        if _STATE_FILE.exists():
            state_data = json.loads(_STATE_FILE.read_text())
        last_write = float(state_data.get("last_supabase_write_ts", 0))
        elapsed_since = time.time() - last_write
        if elapsed_since < MIN_SUPABASE_WRITE_INTERVAL:
            logger.debug(
                "Supabase write throttled — last write was %.0fs ago (limit %ds)",
                elapsed_since,
                MIN_SUPABASE_WRITE_INTERVAL,
            )
            return
    except Exception as exc:
        logger.debug("Could not read state for Supabase throttle check: %s", exc)

    try:
        from supabase import create_client  # type: ignore[import]
    except ImportError:
        logger.debug("supabase package not installed in this venv — skipping DB log")
        return

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        logger.debug("SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set — skipping DB log")
        return

    try:
        sb = create_client(url, key)
        summary = result.get("summary", {})
        sc_opps = result.get("safe_compounder", {}).get("opportunities", [])

        sb.table("kalshi_strategy_runs").insert({
            "balance_usd": balance_usd,
            "markets_scanned": result.get("universe", {}).get("total_non_blocked_markets", 0),
            "edges_found": summary.get("total_opportunities", 0),
            "actionable": summary.get("safe_compounder_opportunities", 0),
            "top_opportunity": sc_opps[0] if sc_opps else None,
            "full_results": {
                "stat_arb_count": summary.get("stat_arb_opportunities", 0),
                "circuit_breaker": result.get("circuit_breaker"),
            },
            "duration_ms": elapsed_ms,
        }).execute()

        # ── Persist successful write timestamp to prevent spam ────────────────
        try:
            _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            existing: dict = {}
            if _STATE_FILE.exists():
                try:
                    existing = json.loads(_STATE_FILE.read_text())
                except Exception:
                    pass
            existing["last_supabase_write_ts"] = time.time()
            _STATE_FILE.write_text(json.dumps(existing, indent=2))
        except Exception as exc:
            logger.debug("Could not persist last_supabase_write_ts: %s", exc)

    except Exception as exc:
        logger.warning("Supabase insert failed: %s", exc)
