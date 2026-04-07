"""
AlgoChains Learning Signals — Continuous Improvement via Outcome Capture

Adapted from danielmiessler/Personal_AI_Infrastructure Memory System concept.
Every agent interaction that produces a meaningful outcome gets a learning signal
captured here — rating, success/failure, skill used, notes.

After 30+ days of signals, patterns emerge:
  - Which skills produce the best outcomes?
  - Which agent actions fail most often?
  - Where should we invest improvement effort?

Storage: append-only JSONL file (state/learning_signals.jsonl)
No signal is ever deleted — the log is immutable for audit purposes.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("algochains_mcp.learning_signals")

_SIGNALS_FILE = Path(
    os.getenv("ALGOCHAINS_LEARNING_SIGNALS_FILE", "state/learning_signals.jsonl")
)

_VALID_OUTCOMES = {"success", "failure", "partial", "skipped", "unknown"}
_VALID_ACTION_TYPES = {
    "bot_diagnosis", "strategy_change", "bot_restart", "token_renewal",
    "backtest_run", "skill_invocation", "code_change", "research", "deploy",
    "market_analysis", "position_management", "alert_triage", "onboarding",
    "debate_invocation", "mcpt_validation", "regime_detection", "other",
}


def capture_learning_signal(
    action_type: str,
    action_description: str,
    outcome: str,
    rating: int | None = None,
    notes: str = "",
    skill_used: str = "",
    bot: str = "",
    agent: str = "",
    session_id: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Append a learning signal to the JSONL log.

    Args:
        action_type: Category of action performed (see _VALID_ACTION_TYPES)
        action_description: Short description of what was done (< 200 chars)
        outcome: "success" | "failure" | "partial" | "skipped" | "unknown"
        rating: 1-10 quality rating (1=terrible, 10=perfect/euphoric). None = unrated.
        notes: Free-text notes about what happened and why
        skill_used: Name of skill invoked (e.g. "bot-diagnostics", "moltbook-debate")
        bot: Which bot this relates to (e.g. "MNQ", "CL", "MES", "NQ", "all")
        agent: Which agent captured this (e.g. "cursor", "claude", "windsurf", "openclaw")
        session_id: Optional session identifier for grouping related signals
        extra: Optional dict of additional metadata

    Returns status dict with signal_id and success/error.
    """
    outcome = outcome.lower().strip()
    if outcome not in _VALID_OUTCOMES:
        return {
            "error": f"Invalid outcome '{outcome}'. Must be one of: {sorted(_VALID_OUTCOMES)}",
        }

    if rating is not None and not (1 <= rating <= 10):
        return {"error": f"rating must be 1-10, got {rating}"}

    if not action_description.strip():
        return {"error": "action_description cannot be empty"}

    action_type = action_type.lower().strip()
    if action_type not in _VALID_ACTION_TYPES:
        action_type = "other"

    signal_id = str(uuid.uuid4())[:8]
    signal: dict[str, Any] = {
        "signal_id": signal_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "unix_ts": time.time(),
        "action_type": action_type,
        "action_description": action_description[:500],
        "outcome": outcome,
        "rating": rating,
        "notes": notes[:1000],
        "skill_used": skill_used,
        "bot": bot,
        "agent": agent,
        "session_id": session_id,
    }
    if extra and isinstance(extra, dict):
        signal["extra"] = extra

    try:
        _SIGNALS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _SIGNALS_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(signal) + "\n")
    except Exception as exc:
        logger.error("Failed to write learning signal: %s", exc)
        return {"error": f"Write failed: {exc}", "signal": signal}

    return {
        "success": True,
        "signal_id": signal_id,
        "outcome": outcome,
        "rating": rating,
        "action_type": action_type,
        "log_file": str(_SIGNALS_FILE),
    }


def get_learning_signals(
    limit: int = 100,
    action_type: str | None = None,
    outcome: str | None = None,
    bot: str | None = None,
    min_rating: int | None = None,
    max_rating: int | None = None,
    summarize: bool = True,
) -> dict[str, Any]:
    """
    Read and analyze learning signals from the JSONL log.

    Args:
        limit: Max signals to return (most recent first)
        action_type: Filter by action type
        outcome: Filter by outcome ("success", "failure", etc.)
        bot: Filter by bot name ("MNQ", "CL", etc.)
        min_rating: Only return signals with rating >= min_rating
        max_rating: Only return signals with rating <= max_rating
        summarize: Include summary statistics (default True)

    Returns signals list and optional summary statistics.
    """
    if not _SIGNALS_FILE.exists():
        return {
            "signals": [],
            "total": 0,
            "message": "No learning signals captured yet. Use capture_learning_signal to start.",
            "log_file": str(_SIGNALS_FILE),
        }

    try:
        lines = _SIGNALS_FILE.read_text(encoding="utf-8").strip().split("\n")
        all_signals = [json.loads(l) for l in lines if l.strip()]
    except Exception as exc:
        return {"error": f"Failed to read signals: {exc}"}

    # Filter
    filtered = all_signals
    if action_type:
        filtered = [s for s in filtered if s.get("action_type") == action_type.lower()]
    if outcome:
        filtered = [s for s in filtered if s.get("outcome") == outcome.lower()]
    if bot:
        filtered = [s for s in filtered if s.get("bot", "").upper() == bot.upper()]
    if min_rating is not None:
        filtered = [s for s in filtered if s.get("rating") is not None and s["rating"] >= min_rating]
    if max_rating is not None:
        filtered = [s for s in filtered if s.get("rating") is not None and s["rating"] <= max_rating]

    # Most recent first
    filtered.sort(key=lambda s: s.get("unix_ts", 0), reverse=True)
    paginated = filtered[:limit]

    result: dict[str, Any] = {
        "signals": paginated,
        "total_in_log": len(all_signals),
        "total_matching": len(filtered),
        "returned": len(paginated),
        "log_file": str(_SIGNALS_FILE),
        "filters": {
            "action_type": action_type,
            "outcome": outcome,
            "bot": bot,
            "min_rating": min_rating,
            "max_rating": max_rating,
        },
    }

    if summarize and all_signals:
        result["summary"] = _compute_summary(all_signals)

    return result


def _compute_summary(signals: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute summary statistics from learning signals — PAI-style insight generation."""
    from collections import Counter

    total = len(signals)
    rated = [s for s in signals if s.get("rating") is not None]
    avg_rating = round(sum(s["rating"] for s in rated) / len(rated), 2) if rated else None

    outcome_counts = Counter(s.get("outcome", "unknown") for s in signals)
    action_counts = Counter(s.get("action_type", "other") for s in signals)
    skill_counts = Counter(s.get("skill_used", "") for s in signals if s.get("skill_used"))
    bot_counts = Counter(s.get("bot", "") for s in signals if s.get("bot"))

    success_rate = round(outcome_counts.get("success", 0) / total * 100, 1) if total else 0

    # Top skills by success rate
    skill_outcomes: dict[str, dict[str, int]] = {}
    for s in signals:
        sk = s.get("skill_used", "")
        if not sk:
            continue
        if sk not in skill_outcomes:
            skill_outcomes[sk] = {"total": 0, "success": 0}
        skill_outcomes[sk]["total"] += 1
        if s.get("outcome") == "success":
            skill_outcomes[sk]["success"] += 1

    top_skills = sorted(
        [
            {
                "skill": sk,
                "total": counts["total"],
                "success_rate": round(counts["success"] / counts["total"] * 100, 1),
            }
            for sk, counts in skill_outcomes.items()
            if counts["total"] >= 3  # only skills with meaningful sample
        ],
        key=lambda x: x["success_rate"],
        reverse=True,
    )[:10]

    return {
        "total_signals": total,
        "success_rate_pct": success_rate,
        "average_rating": avg_rating,
        "rated_signals": len(rated),
        "outcome_distribution": dict(outcome_counts),
        "top_action_types": dict(action_counts.most_common(5)),
        "top_skills_by_success_rate": top_skills,
        "top_skills_by_volume": dict(skill_counts.most_common(5)),
        "bot_activity": dict(bot_counts.most_common()),
    }
