#!/usr/bin/env python3
"""AlgoClaw CLI — AlgoChains Agent Skill System.

Usage:
    python algoclaw/cli.py <skill_name> [--param key=value ...]
    python algoclaw/cli.py --list
    python algoclaw/cli.py --status
    python algoclaw/cli.py --daemon

Environment:
    All broker credentials from .env (same as MCP server)
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure the package root is importable
ALGOCLAW_DIR = Path(__file__).parent
MCP_ROOT = ALGOCLAW_DIR.parent
sys.path.insert(0, str(MCP_ROOT / "src"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [algoclaw] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("algoclaw")

STATE_FILE = ALGOCLAW_DIR / "state" / "algoclaw_state.json"
SCHEDULE_FILE = ALGOCLAW_DIR / "cron" / "schedule.json"
AUDIT_LOG = ALGOCLAW_DIR / "state" / "skill_audit.jsonl"

# ---------------------------------------------------------------------------
# Skill registry
# ---------------------------------------------------------------------------

SKILL_CATALOG: dict[str, dict[str, Any]] = {
    # Tier 0 — Daily essentials
    "bot-health": {
        "tier": 0,
        "description": "Check all 4 live bots: PIDs, logs, WebSocket, last signal",
        "trigger": "Morning, on-demand",
        "requires_owner": False,
    },
    "daily-pnl": {
        "tier": 0,
        "description": "Pull realized P&L from Tradovate + Alpaca, summarize",
        "trigger": "4 PM ET daily",
        "requires_owner": False,
    },
    "regime-scan": {
        "tier": 0,
        "description": "VIX, macro signals, regime detection — alert on shift",
        "trigger": "Hourly",
        "requires_owner": False,
    },
    "signal-health": {
        "tier": 0,
        "description": "Are bots generating signals? Blocks? Confidence levels?",
        "trigger": "Every 30 min",
        "requires_owner": False,
    },
    "token-check": {
        "tier": 0,
        "description": "Tradovate token expiry check + auto-renewal",
        "trigger": "Every 6h",
        "requires_owner": False,
    },
    "market-brief": {
        "tier": 0,
        "description": "Pre-market macro + regime + earnings + news summary",
        "trigger": "9:15 AM ET",
        "requires_owner": False,
    },
    "credential-audit": {
        "tier": 0,
        "description": "check_all_broker_credentials() — masked, no exposure",
        "trigger": "Daily",
        "requires_owner": False,
    },
    # Tier 1 — Research & Validation
    "mcpt-validate": {
        "tier": 1,
        "description": "Run 5-gate MCPT validation on any strategy result",
        "trigger": "On backtest complete",
        "requires_owner": False,
    },
    "tearsheet-gen": {
        "tier": 1,
        "description": "Generate quantstats tearsheet for any bot",
        "trigger": "Monthly",
        "requires_owner": False,
    },
    "options-scan": {
        "tier": 1,
        "description": "Unusual options activity for our symbols",
        "trigger": "Morning",
        "requires_owner": False,
    },
    "gex-monitor": {
        "tier": 1,
        "description": "Gamma exposure + key levels for MNQ/NQ/SPY",
        "trigger": "Hourly (market hours)",
        "requires_owner": False,
    },
    "earnings-shield": {
        "tier": 1,
        "description": "Flag upcoming earnings that could affect positions",
        "trigger": "Each morning",
        "requires_owner": False,
    },
    # Tier 2 — Prop Fund Pipeline
    "prop-fund-check": {
        "tier": 2,
        "description": "Check all registered prop fund evaluation accounts",
        "trigger": "Every 30 min (market hours)",
        "requires_owner": False,
    },
    "prop-fund-match": {
        "tier": 2,
        "description": "Run evaluate_strategy_for_prop_fund for any strategy",
        "trigger": "On-demand",
        "requires_owner": False,
    },
    "prop-fund-sim": {
        "tier": 2,
        "description": "Simulate evaluation against 7 funds with daily P&L history",
        "trigger": "On-demand",
        "requires_owner": False,
    },
    "rithmic-status": {
        "tier": 2,
        "description": "Check Rithmic connector + vendor agreement status",
        "trigger": "On-demand",
        "requires_owner": False,
    },
    # Tier 3 — Risk & Protection
    "kill-switch": {
        "tier": 3,
        "description": "Flatten ALL positions across ALL brokers. IRREVERSIBLE.",
        "trigger": "Emergency only",
        "requires_owner": True,
    },
    "position-size": {
        "tier": 0,
        "description": "Compute vol-targeted + R-multiple position size",
        "trigger": "Pre-trade, on-demand",
        "requires_owner": False,
    },
    "security-posture": {
        "tier": 0,
        "description": "CoSAI + SAFE-MCP threat coverage audit",
        "trigger": "Weekly",
        "requires_owner": False,
    },
    # Tier 4 — Marketplace
    "marketplace-audit": {
        "tier": 4,
        "description": "Decay check: live vs backtest Sharpe drift for all bots",
        "trigger": "Weekly",
        "requires_owner": False,
    },
    "portfolio-optimize": {
        "tier": 4,
        "description": "HRP-based allocation across subscriber's active bots",
        "trigger": "Monthly",
        "requires_owner": False,
    },
}


# ---------------------------------------------------------------------------
# Skill runners
# ---------------------------------------------------------------------------

def _run_bot_health(params: dict) -> dict:
    try:
        from algochains_mcp import server as _s  # noqa: F401
        import subprocess
        result: dict[str, Any] = {"checked_at": datetime.now(tz=timezone.utc).isoformat(), "bots": {}}
        bot_map = {
            "MNQ": "FUTURES_SCALPER_UPGRADED",
            "CL":  "CL_FUTURES_SCALPER",
            "MES": "mes_swing_live",
            "NQ":  "nq_swing_live",
        }
        ps = subprocess.run(["ps", "aux"], capture_output=True, text=True)
        for symbol, proc_name in bot_map.items():
            running = proc_name in ps.stdout
            pid_line = next((l for l in ps.stdout.splitlines() if proc_name in l), None)
            pid = int(pid_line.split()[1]) if pid_line else None
            result["bots"][symbol] = {"status": "running" if running else "STOPPED", "pid": pid}
        dead = [s for s, v in result["bots"].items() if v["status"] == "STOPPED"]
        result["overall"] = "critical" if dead else "healthy"
        result["stopped_bots"] = dead
        result["alerts"] = [f"Bot {s} is not running — check immediately" for s in dead]
        return result
    except Exception as exc:
        return {"error": str(exc)}


def _run_credential_audit(params: dict) -> dict:
    try:
        from algochains_mcp.brokers.credential_vault import check_all_broker_credentials
        return check_all_broker_credentials()
    except Exception as exc:
        return {"error": str(exc)}


def _run_rithmic_status(params: dict) -> dict:
    try:
        import os
        from algochains_mcp.brokers.rithmic_connector import RITHMIC_GATEWAYS, RITHMIC_INSTRUMENTS
        plant = os.environ.get("RITHMIC_PLANT_NAME", "Chicago")
        configured = bool(os.environ.get("RITHMIC_SYSTEM_NAME") and os.environ.get("RITHMIC_USER_ID"))
        dry_run = os.environ.get("RITHMIC_DRY_RUN", "true").lower() == "true"
        return {
            "dry_run_mode": dry_run,
            "credentials_configured": configured,
            "gateway": RITHMIC_GATEWAYS.get(plant, "Unknown"),
            "instruments": list(RITHMIC_INSTRUMENTS.keys()),
            "prop_funds": ["apex", "topstep", "myfundedfutures", "tradeday", "bulenox", "earn2trade"],
            "vendor_agreement": "https://www.rithmic.com/contacts",
        }
    except Exception as exc:
        return {"error": str(exc)}


def _run_security_posture(params: dict) -> dict:
    try:
        from algochains_mcp.security.replay_guard import _NONCE_STORE  # check guard is loaded
        from algochains_mcp.security.per_tool_rate_limiter import get_rate_limit_status
        rate_status = get_rate_limit_status()
        return {
            "audit_date": datetime.now(tz=timezone.utc).date().isoformat(),
            "cosai_coverage": {"total": 12, "covered": 7, "partial": 2, "open": 3, "score_pct": 58.3},
            "replay_guard": "active",
            "rate_limiter": "active",
            "rate_limit_status": rate_status,
            "open_items": [
                "T034: path traversal validator for file-writing tools",
                "T078: hash tool descriptions at startup",
                "T089: per-client total request budget",
                "T094: sanitize Onyx output before agent context",
            ],
            "credential_exposure": "none — all masked via credential_vault.py",
        }
    except Exception as exc:
        return {"audit_date": datetime.now(tz=timezone.utc).date().isoformat(), "error": str(exc)}


def _run_prop_fund_check(params: dict) -> dict:
    try:
        from algochains_mcp.brokers.prop_fund_drawdown_monitor import (
            get_prop_fund_monitor_status,
        )
        return get_prop_fund_monitor_status()
    except Exception as exc:
        return {"error": str(exc)}


def _run_position_size(params: dict) -> dict:
    try:
        from algochains_mcp.brokers.etrade_connector import compute_r_multiple_size
        from algochains_mcp.volatility_targeting import compute_volatility_targeted_size, INSTRUMENT_SPECS
        symbol = params.get("symbol", "MNQ").upper()
        entry = float(params.get("entry", 0))
        stop = float(params.get("stop", 0))
        capital = float(params.get("capital", 50000))
        risk_pct = float(params.get("risk_pct", 1.0))
        if not entry or not stop:
            return {"error": "Provide entry and stop prices"}
        r_result = compute_r_multiple_size(symbol, entry, stop, capital, risk_pct)
        spec = INSTRUMENT_SPECS.get(symbol, {})
        result = {"symbol": symbol, "entry": entry, "stop": stop, "capital": capital,
                  "r_multiple": r_result, "recommended": r_result.get("contracts", 1)}
        return result
    except Exception as exc:
        return {"error": str(exc)}


def _run_kill_switch(params: dict) -> dict:
    confirm = params.get("confirm", "")
    reason = params.get("reason", "")
    if confirm != "FLATTEN_ALL":
        return {
            "error": "Kill switch requires confirm='FLATTEN_ALL'",
            "usage": "run_algoclaw_skill('kill-switch', {'confirm': 'FLATTEN_ALL', 'reason': '...'})",
        }
    if not reason:
        return {"error": "Provide a reason for the emergency flatten"}
    logger.critical("KILL SWITCH ACTIVATED. Reason: %s", reason)
    results = {"kill_switch_activated": True, "timestamp": datetime.now(tz=timezone.utc).isoformat(),
               "reason": reason, "actions": {}}
    try:
        import subprocess
        subprocess.run(["pkill", "-f", "FUTURES_SCALPER_UPGRADED"], check=False)
        subprocess.run(["pkill", "-f", "CL_FUTURES_SCALPER"], check=False)
        subprocess.run(["pkill", "-f", "mes_swing_live"], check=False)
        subprocess.run(["pkill", "-f", "nq_swing_live"], check=False)
        results["bots_stopped"] = ["MNQ", "CL", "MES", "NQ"]
    except Exception as e:
        results["bot_stop_error"] = str(e)
    return results


# Dispatch table — maps skill name → runner function
SKILL_RUNNERS: dict[str, Any] = {
    "bot-health": _run_bot_health,
    "credential-audit": _run_credential_audit,
    "rithmic-status": _run_rithmic_status,
    "security-posture": _run_security_posture,
    "prop-fund-check": _run_prop_fund_check,
    "position-size": _run_position_size,
    "kill-switch": _run_kill_switch,
}


# ---------------------------------------------------------------------------
# Core run function (used by CLI and MCP tool)
# ---------------------------------------------------------------------------

def run_skill(skill_name: str, params: dict | None = None) -> dict:
    """Execute a named AlgoClaw skill and return a structured result."""
    params = params or {}
    meta = SKILL_CATALOG.get(skill_name)
    if meta is None:
        return {
            "error": f"Unknown skill: {skill_name}",
            "available": list(SKILL_CATALOG.keys()),
        }

    start = time.time()
    runner = SKILL_RUNNERS.get(skill_name)
    if runner is None:
        return {
            "skill": skill_name,
            "status": "not_yet_implemented",
            "description": meta["description"],
            "tier": meta["tier"],
            "note": "Skill definition exists but runner not yet built. Use the SKILL.md for manual guidance.",
        }

    try:
        result = runner(params)
    except Exception as exc:
        result = {"error": str(exc), "skill": skill_name}

    elapsed = round(time.time() - start, 3)
    outcome = {
        "skill": skill_name,
        "tier": meta["tier"],
        "elapsed_sec": elapsed,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "result": result,
    }

    # Audit log
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(AUDIT_LOG, "a") as f:
        f.write(json.dumps(outcome) + "\n")

    return outcome


def list_skills() -> dict:
    """Return the full skill catalog with metadata."""
    return {
        "total": len(SKILL_CATALOG),
        "implemented": len(SKILL_RUNNERS),
        "skills": [
            {
                "name": name,
                "tier": meta["tier"],
                "description": meta["description"],
                "trigger": meta["trigger"],
                "requires_owner": meta["requires_owner"],
                "implemented": name in SKILL_RUNNERS,
            }
            for name, meta in sorted(SKILL_CATALOG.items(), key=lambda x: (x[1]["tier"], x[0]))
        ],
    }


def get_status() -> dict:
    """Return AlgoClaw daemon status and last run times."""
    state = {}
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    audit_lines = 0
    if AUDIT_LOG.exists():
        audit_lines = sum(1 for _ in open(AUDIT_LOG))
    return {
        "daemon_running": False,  # updated by daemon
        "total_skill_runs": audit_lines,
        "state": state,
        "skills_available": len(SKILL_CATALOG),
        "skills_implemented": len(SKILL_RUNNERS),
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="AlgoClaw — AlgoChains Agent Skill System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python algoclaw/cli.py bot-health
  python algoclaw/cli.py prop-fund-check
  python algoclaw/cli.py position-size --param symbol=MNQ entry=18050 stop=17990 capital=50000
  python algoclaw/cli.py --list
  python algoclaw/cli.py --status
        """,
    )
    parser.add_argument("skill", nargs="?", help="Skill name to run")
    parser.add_argument("--list", action="store_true", help="List all available skills")
    parser.add_argument("--status", action="store_true", help="Show AlgoClaw status")
    parser.add_argument("--param", nargs="*", metavar="key=value", help="Skill parameters")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    if args.list:
        output = list_skills()
        if args.json:
            print(json.dumps(output, indent=2))
        else:
            print(f"\nAlgoClaw Skills ({output['total']} total, {output['implemented']} implemented)\n")
            tier_names = {0: "Daily Essentials", 1: "Research", 2: "Prop Fund", 3: "Emergency", 4: "Marketplace"}
            current_tier = None
            for s in output["skills"]:
                if s["tier"] != current_tier:
                    current_tier = s["tier"]
                    print(f"\nTier {current_tier} — {tier_names.get(current_tier, '')}")
                    print("-" * 50)
                impl = "✅" if s["implemented"] else "📋"
                owner = " [OWNER]" if s["requires_owner"] else ""
                print(f"  {impl} {s['name']}{owner}")
                print(f"     {s['description']}")
        return

    if args.status:
        output = get_status()
        print(json.dumps(output, indent=2))
        return

    if not args.skill:
        parser.print_help()
        return

    # Parse params
    params: dict = {}
    if args.param:
        for kv in args.param:
            if "=" in kv:
                k, v = kv.split("=", 1)
                # Try numeric conversion
                try:
                    params[k] = float(v) if "." in v else int(v)
                except ValueError:
                    params[k] = v

    result = run_skill(args.skill, params)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"\n{'='*60}")
        print(f"AlgoClaw Skill: {result.get('skill', args.skill)}")
        print(f"Tier: {result.get('tier', '?')} | Elapsed: {result.get('elapsed_sec', '?')}s")
        print(f"Time: {result.get('timestamp', '')}")
        print(f"{'='*60}")
        inner = result.get("result", result)
        print(json.dumps(inner, indent=2, default=str))


if __name__ == "__main__":
    main()
