"""
Consumer-facing onboarding meta-tools — public, no auth, fast.

Three "wow / first 30 seconds" tools that make a brand-new user landing via
Claude.ai immediately productive:
  - get_started(goal)   — a guided next-steps map (no auth)
  - get_pricing()       — transparent tiers, referral, creator-share (no auth)
  - get_system_status() — consumer-facing platform health (no auth, best-effort)

All three are read-only, never require credentials, never return secrets, and
fail safe (return a useful static payload if a backend is unavailable). Product
framing follows outside-counsel guidance: signals are *published for the
subscriber to review and act on* — the platform does not auto-execute.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("algochains.onboarding_meta")

# Single source of truth for pricing (kept in sync with billing_engine TIER_CONFIG
# and usage_metering). If you change a price, change it here and there.
PRICING = {
    "paper": {"price_usd_month": 29, "included_calls": 1000},
    "live": {"price_usd_month": 99, "included_calls": 1000},
    "overage_per_call_usd": 0.01,
    "referral": {"rate_pct": 20, "months": 3},
    "creator_revenue_share_pct": 80,
}

_GOALS = {
    "subscriber": {
        "label": "Get copy-trade signals (paper, no broker needed)",
        "steps": [
            "1. get_pricing() — see what's included",
            "2. get_checkout_url(email='you@example.com', tier='paper') — subscribe ($29/mo)",
            "3. After payment, set ALGOCHAINS_SUBSCRIBER_KEY=<emailed key>",
            "4. accept_subscriber_terms() — review + acknowledge the futures risk disclosure (required)",
            "5. join_bot(bot='MNQ') — subscribe to a strategy's published signals you act on",
            "6. get_signal_stream() then get_my_portfolio() — see signals + your paper account",
        ],
        "next_call": "get_pricing",
    },
    "creator": {
        "label": "Publish a strategy and earn revenue share",
        "steps": [
            "1. get_submission_guide() — gate requirements + IP protection",
            "2. submit_strategy(...) — run the 6-gate MCPT validation",
            "3. create_creator_onboarding_link(creator_id=..., creator_email=...) — Stripe Connect KYC",
            "4. get_my_creator_earnings(creator_id=...) — track accrual (you keep 80%)",
        ],
        "next_call": "get_submission_guide",
    },
    "developer": {
        "label": "Build on the API (ac_live_* key)",
        "steps": [
            "1. Visit algochains.ai or run `stripe projects link algochains` for a developer key",
            "2. Set the ac_live_* key; call discover_tools() to explore 503 tools",
            "3. get_my_usage() — monitor metered usage + overage",
        ],
        "next_call": "discover_tools",
    },
    "explore": {
        "label": "Just look around (no signup)",
        "steps": [
            "1. detect_market_regime() — what's the market environment right now?",
            "2. get_quote(symbol='AAPL') — live prices",
            "3. get_marketplace_listings() — browse validated strategies",
            "4. discover_tools(query='...') — find any of the 503 tools",
        ],
        "next_call": "detect_market_regime",
    },
}


def get_started(goal: str | None = None) -> dict[str, Any]:
    """Guided next-steps for a brand-new user. No auth. `goal` is one of
    subscriber / creator / developer / explore (defaults to a menu)."""
    if goal:
        g = goal.strip().lower()
        # accept a few synonyms
        alias = {"trade": "subscriber", "trader": "subscriber", "signals": "subscriber",
                 "sell": "creator", "publish": "creator", "build": "developer",
                 "api": "developer", "look": "explore", "browse": "explore"}
        g = alias.get(g, g)
        if g in _GOALS:
            return {"goal": g, **_GOALS[g], "note": "Signals are published for you to review and act on — no automated execution."}
    # No/unknown goal → menu
    return {
        "welcome": "AlgoChains MCP — 503 trading tools, live futures signal bots, hosted paper account.",
        "choose_a_goal": {k: v["label"] for k, v in _GOALS.items()},
        "how": "Call get_started(goal='subscriber'|'creator'|'developer'|'explore').",
        "fastest_win": "get_started(goal='explore') → detect_market_regime()",
    }


def get_pricing() -> dict[str, Any]:
    """Transparent pricing, referral, and creator revenue share. No auth."""
    p = PRICING
    return {
        "tiers": {
            "paper": {
                "price": f"${p['paper']['price_usd_month']}/mo",
                "includes": "published copy-trade signals + simulated paper account (no broker), "
                            f"{p['paper']['included_calls']} API calls/mo",
                "overage": f"${p['overage_per_call_usd']}/call above included",
                "start": "get_checkout_url(email='you@example.com', tier='paper')",
            },
            "live": {
                "price": f"${p['live']['price_usd_month']}/mo",
                "includes": "same as paper + connect your OWN broker and place your OWN trades",
                "overage": f"${p['overage_per_call_usd']}/call above included",
                "start": "get_checkout_url(email='you@example.com', tier='live')",
            },
        },
        "referral": f"Earn {p['referral']['rate_pct']}% of each referral's first "
                    f"{p['referral']['months']} months — create_referral_code()",
        "creator_revenue_share": f"Strategy creators keep {p['creator_revenue_share_pct']}% of subscriber revenue",
        "compensation_model": "Flat subscription + usage. No performance fees.",
        "note": "Signals are published for the subscriber to review and act on; the platform does not auto-execute.",
    }


def get_system_status() -> dict[str, Any]:
    """Consumer-facing platform health. No auth, best-effort, no secrets.

    Returns version + live-bot roster + (if reachable) public marketplace listing
    count. Never raises; returns a static-but-useful payload on any backend miss.
    """
    status: dict[str, Any] = {
        "platform": "operational",
        "live_signal_bots": ["MNQ", "CL", "MES", "NQ"],
        "tool_count": 503,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }
    try:
        from . import __version__ as _v  # type: ignore
        status["version"] = _v
    except Exception:
        status["version"] = os.environ.get("ALGOCHAINS_VERSION", "22.6.0")

    # Best-effort public marketplace listing count (RLS-filtered; no secrets).
    try:
        from .marketplace.supabase_tools import get_marketplace_listings as _ml
        ml = _ml(status="all", asset_class="all", limit=1)
        if isinstance(ml, dict) and "total" in ml:
            status["marketplace_listings"] = ml.get("total")
    except Exception as exc:
        log.debug("system_status marketplace lookup unavailable: %s", exc)

    return status


__all__ = ["get_started", "get_pricing", "get_system_status", "PRICING"]
