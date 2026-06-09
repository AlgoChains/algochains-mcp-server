"""
Kalshi AI Ensemble — AlgoChains v1.0

Five frontier LLMs debate every trade via OpenRouter (single API key).
Based on ryanfrigo/kalshi-ai-trading-bot multi-model debate pattern,
adapted to route through AlgoChains MCP infrastructure.

Model roles (OpenRouter slugs — see ENSEMBLE_MODELS for the source of truth):
  anthropic/claude-opus-4.8 → Lead Analyst    (30% weight)
  google/gemini-2.5-pro     → Forecaster      (30% weight)
  openai/gpt-4o             → Risk Manager    (20% weight)
  deepseek/deepseek-chat    → Bull Researcher (10% weight)
  x-ai/grok-4.3             → Bear Researcher (10% weight)
Each role has a `fallback` slug used automatically if the primary 400/404s.
OpenRouter uses DOT notation (claude-opus-4.8), not the direct-API dash form.

Consensus gating: position skipped if models diverge beyond CONFIDENCE_THRESHOLD.
Cost control: daily spend cap enforced before every API call.
Temperature: 0 (deterministic — reproducible reasoning).
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

logger = logging.getLogger("algochains_mcp.order_flow.kalshi_ai_ensemble")

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_REFERER  = "https://algochains.com"

# ─── Ensemble config (mirroring ryanfrigo model weights) ─────────────────────
# All slugs verified LIVE via the OpenRouter /models endpoint (2026-05-29).
# OpenRouter uses DOT notation for the Claude 4.x gen (anthropic/claude-opus-4.8),
# NOT the direct-API dash form (claude-opus-4-8). The previous slugs
# (anthropic/claude-sonnet-4-5, google/gemini-pro-1.5, x-ai/grok-2-1212) were all
# DEAD on OpenRouter and silently dropped votes → false "no consensus".
# Each entry carries a `fallback` slug used automatically on a 400/404 model error.
ENSEMBLE_MODELS = [
    {"model": "anthropic/claude-opus-4.8",    "role": "lead_analyst",    "weight": 0.30, "fallback": "anthropic/claude-sonnet-4.5"},
    {"model": "google/gemini-2.5-pro",        "role": "forecaster",      "weight": 0.30, "fallback": "google/gemini-2.0-flash-001"},
    {"model": "openai/gpt-4o",                "role": "risk_manager",    "weight": 0.20, "fallback": "openai/gpt-4o-mini"},
    {"model": "deepseek/deepseek-chat",       "role": "bull_researcher", "weight": 0.10, "fallback": "meta-llama/llama-3.3-70b-instruct"},
    {"model": "x-ai/grok-4.3",                "role": "bear_researcher", "weight": 0.10, "fallback": "meta-llama/llama-3.3-70b-instruct"},
]

MIN_CONFIDENCE_TO_TRADE  = 0.45    # Skip if ensemble confidence < 45%
DAILY_AI_COST_LIMIT_USD  = 5.00   # Max AI spend per day
AI_TEMPERATURE           = 0       # Deterministic outputs
AI_MAX_TOKENS            = 1500    # Per model call

# Cost tracking file (lightweight, no Supabase for speed)
COST_TRACKING_FILE = Path(os.path.expanduser("~/.algochains/kalshi_ai_daily_cost.json"))


# ─── Data structures ──────────────────────────────────────────────────────────
@dataclass
class ModelVote:
    model: str
    role: str
    weight: float
    yes_probability: float       # model's P(YES) estimate
    confidence: float            # 0.0 to 1.0
    reasoning: str
    tokens_used: int = 0
    cost_usd: float = 0.0


@dataclass
class EnsembleDecision:
    ticker: str
    weighted_yes_prob: float
    ensemble_confidence: float
    votes: list[ModelVote] = field(default_factory=list)
    action: str = "skip"          # "buy_yes" | "buy_no" | "skip"
    consensus: bool = False
    disagreement_pct: float = 0.0
    total_cost_usd: float = 0.0
    decided_at: str = ""


# ─── Cost tracking ────────────────────────────────────────────────────────────

def _load_daily_cost() -> float:
    """Load today's accumulated AI cost."""
    try:
        COST_TRACKING_FILE.parent.mkdir(parents=True, exist_ok=True)
        if not COST_TRACKING_FILE.exists():
            return 0.0
        data = json.loads(COST_TRACKING_FILE.read_text())
        if data.get("date") == str(date.today()):
            return float(data.get("total_cost_usd", 0.0))
    except Exception:
        pass
    return 0.0


def _record_cost(cost_usd: float) -> None:
    """Add cost to today's tally.

    P2-6 FIX: use fcntl.flock for exclusive write access so concurrent pipeline
    calls cannot race and corrupt the cost tally or silently allow budget overspend.
    """
    try:
        import fcntl
        COST_TRACKING_FILE.parent.mkdir(parents=True, exist_ok=True)
        today = str(date.today())
        # Open for read+write (create if needed) then lock before read-modify-write
        with open(str(COST_TRACKING_FILE), "a+") as _fh:
            fcntl.flock(_fh.fileno(), fcntl.LOCK_EX)
            try:
                _fh.seek(0)
                _raw = _fh.read().strip()
                _data = json.loads(_raw) if _raw else {}
                _existing = float(_data.get("total_cost_usd", 0.0)) if _data.get("date") == today else 0.0
                _fh.seek(0)
                _fh.truncate()
                json.dump({"date": today, "total_cost_usd": round(_existing + cost_usd, 6)}, _fh)
            finally:
                fcntl.flock(_fh.fileno(), fcntl.LOCK_UN)
    except Exception as exc:
        logger.debug("Cost tracking write failed: %s", exc)


def check_ai_budget() -> tuple[bool, float, float]:
    """Return (ok, spent_today, remaining). ok=False → skip AI call."""
    spent = _load_daily_cost()
    remaining = max(0.0, DAILY_AI_COST_LIMIT_USD - spent)
    return remaining > 0.01, spent, remaining


# ─── OpenRouter client ────────────────────────────────────────────────────────

_MAX_RETRIES = 2          # transient retries on 429 / 5xx
_RETRY_BACKOFF_S = 0.75   # base backoff (doubled each retry)


def _post_openrouter(api_key: str, model: str, messages: list[dict], max_tokens: int) -> dict[str, Any]:
    """One OpenRouter HTTP call. Returns parsed JSON or {'error', '_status'}."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": OPENROUTER_REFERER,
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": AI_TEMPERATURE,
        "max_tokens": max_tokens,
        # Enforce machine-parseable JSON server-side instead of relying on the
        # prompt alone (fragile json.loads after stripping code fences).
        "response_format": {"type": "json_object"},
    }
    try:
        resp = httpx.post(
            f"{OPENROUTER_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": f"HTTP {exc.response.status_code}: {exc.response.text[:300]}",
            "_status": exc.response.status_code,
        }
    except Exception as exc:
        return {"error": str(exc)[:300], "_status": None}


def _call_openrouter(
    model: str,
    messages: list[dict],
    max_tokens: int = AI_MAX_TOKENS,
    fallback: Optional[str] = None,
) -> dict[str, Any]:
    """OpenRouter call with retry on 429/5xx and model-fallback on 400/404.

    Returns parsed response dict, or {'error': ...} when all attempts fail.
    """
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        return {"error": "OPENROUTER_API_KEY not set"}

    candidates = [model] + ([fallback] if fallback and fallback != model else [])

    last: dict[str, Any] = {"error": "no attempt made"}
    for slug in candidates:
        for attempt in range(_MAX_RETRIES + 1):
            last = _post_openrouter(api_key, slug, messages, max_tokens)
            if "error" not in last:
                if slug != model:
                    logger.warning("Model %s failed; succeeded on fallback %s", model, slug)
                return last

            status = last.get("_status")
            # Transient → retry same slug with backoff.
            if status == 429 or (isinstance(status, int) and 500 <= status < 600):
                if attempt < _MAX_RETRIES:
                    time.sleep(_RETRY_BACKOFF_S * (2 ** attempt))
                    continue
                break  # exhausted retries on this slug
            # 400/404 (bad/removed model) → stop retrying, try fallback slug.
            if status in (400, 404):
                logger.warning("Model %s returned %s; trying fallback", slug, status)
                break
            # Other errors (timeout, auth) → no point retrying a different slug.
            break

    return last


# ─── Prompt templates ─────────────────────────────────────────────────────────

def _build_prompt(
    role: str,
    ticker: str,
    title: str,
    yes_bid: float,
    yes_ask: float,
    close_time: str,
    extra_context: str = "",
) -> list[dict[str, str]]:
    """Build role-specific prompt for a Kalshi market."""

    role_instructions = {
        "lead_analyst": (
            "You are the lead analyst. Provide a balanced, evidence-based probability estimate. "
            "Consider historical base rates for similar events, current information, and market efficiency."
        ),
        "forecaster": (
            "You are the forecaster specializing in probabilistic prediction. "
            "Focus on calibration — what percentage of similar events resolved YES historically?"
        ),
        "risk_manager": (
            "You are the risk manager. Your primary job is to identify tail risks and scenarios "
            "where the trade goes wrong. Be conservative and focus on downside protection."
        ),
        "bull_researcher": (
            "You are the bull researcher. Make the strongest possible case for YES. "
            "Find every reason the YES side might be underpriced."
        ),
        "bear_researcher": (
            "You are the bear researcher. Make the strongest possible case for NO. "
            "Find every reason YES might be overpriced or the outcome is unlikely."
        ),
    }

    system = (
        f"{role_instructions.get(role, 'You are a prediction market analyst.')}\n\n"
        "Respond ONLY in this exact JSON format (no markdown):\n"
        "{\"yes_probability\": 0.XX, \"confidence\": 0.XX, \"reasoning\": \"...\"}\n\n"
        "yes_probability: your P(YES) estimate (0.0 to 1.0)\n"
        "confidence: how confident you are in this estimate (0.0 to 1.0)\n"
        "reasoning: 1-2 sentences max\n"
    )

    user = (
        f"Market: {ticker}\n"
        f"Question: {title}\n"
        f"Current market: YES bid={yes_bid:.2f} YES ask={yes_ask:.2f}\n"
        f"Market implied YES probability: {(yes_bid + yes_ask) / 2:.2%}\n"
        f"Closes: {close_time[:10] if close_time else 'unknown'}\n"
    )
    if extra_context:
        user += f"\nContext: {extra_context}"

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _parse_model_response(response: dict[str, Any], model_config: dict) -> Optional[ModelVote]:
    """Parse OpenRouter response into a ModelVote."""
    if "error" in response:
        logger.warning("Model %s error: %s", model_config["model"], response["error"])
        return None

    try:
        content = response["choices"][0]["message"]["content"].strip()
        # Strip markdown code fences if present
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]

        data = json.loads(content)
        yes_prob = float(data["yes_probability"])
        confidence = float(data["confidence"])
        reasoning = str(data.get("reasoning", ""))[:500]

        # Cost estimation (rough)
        usage = response.get("usage", {})
        total_tokens = usage.get("total_tokens", AI_MAX_TOKENS)
        # OpenRouter pricing varies; rough estimate at $0.003/1K tokens
        cost_usd = total_tokens / 1000 * 0.003

        return ModelVote(
            model=model_config["model"],
            role=model_config["role"],
            weight=model_config["weight"],
            yes_probability=max(0.01, min(0.99, yes_prob)),
            confidence=max(0.0, min(1.0, confidence)),
            reasoning=reasoning,
            tokens_used=total_tokens,
            cost_usd=cost_usd,
        )
    except Exception as exc:
        logger.warning("Failed to parse %s response: %s", model_config["model"], exc)
        return None


# ─── Ensemble runner ──────────────────────────────────────────────────────────

def run_ensemble_debate(
    ticker: str,
    title: str,
    yes_bid: float,
    yes_ask: float,
    close_time: str = "",
    extra_context: str = "",
    fast_mode: bool = False,
) -> EnsembleDecision:
    """
    Run the full 5-model ensemble debate on a single Kalshi market.

    Args:
        ticker: Kalshi market ticker
        title: market question text
        yes_bid: current best YES bid price
        yes_ask: current best YES ask price
        close_time: ISO timestamp when market closes
        extra_context: optional additional context (news, sentiment, etc.)
        fast_mode: if True, only use 3 models (lead_analyst + forecaster + risk_manager)

    Returns EnsembleDecision with consensus probability and action recommendation.
    """
    # Budget check
    budget_ok, spent, remaining = check_ai_budget()
    if not budget_ok:
        logger.warning("AI daily budget exhausted (spent=$%.2f limit=$%.2f). Skipping ensemble.", spent, DAILY_AI_COST_LIMIT_USD)
        return EnsembleDecision(
            ticker=ticker,
            weighted_yes_prob=(yes_bid + yes_ask) / 2,
            ensemble_confidence=0.0,
            action="skip",
            consensus=False,
            decided_at=datetime.now(timezone.utc).isoformat(),
        )

    models_to_use = ENSEMBLE_MODELS
    if fast_mode:
        # Use only the 3 main models to reduce cost
        models_to_use = [m for m in ENSEMBLE_MODELS if m["role"] in ("lead_analyst", "forecaster", "risk_manager")]

    votes: list[ModelVote] = []
    total_cost = 0.0

    for model_cfg in models_to_use:
        messages = _build_prompt(
            role=model_cfg["role"],
            ticker=ticker,
            title=title,
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            close_time=close_time,
            extra_context=extra_context,
        )

        response = _call_openrouter(
            model_cfg["model"], messages, fallback=model_cfg.get("fallback")
        )
        vote = _parse_model_response(response, model_cfg)

        if vote:
            votes.append(vote)
            total_cost += vote.cost_usd
            _record_cost(vote.cost_usd)
        time.sleep(0.2)  # Rate limit between models

    if not votes:
        return EnsembleDecision(
            ticker=ticker,
            weighted_yes_prob=(yes_bid + yes_ask) / 2,
            ensemble_confidence=0.0,
            action="skip",
            consensus=False,
            total_cost_usd=total_cost,
            decided_at=datetime.now(timezone.utc).isoformat(),
        )

    # Renormalize weights to sum to 1.0 (some models may have failed)
    total_weight = sum(v.weight for v in votes)
    weighted_yes = sum(v.yes_probability * v.weight for v in votes) / total_weight
    avg_confidence = sum(v.confidence * v.weight for v in votes) / total_weight

    # Disagreement: std dev of probability estimates
    probs = [v.yes_probability for v in votes]
    mean_prob = sum(probs) / len(probs)
    variance = sum((p - mean_prob) ** 2 for p in probs) / len(probs)
    disagreement_pct = variance ** 0.5  # std dev in probability units

    # Determine action
    consensus = avg_confidence >= MIN_CONFIDENCE_TO_TRADE and disagreement_pct < 0.20
    action = "skip"
    if consensus:
        if weighted_yes > yes_ask + 0.05:      # Model says YES underpriced
            action = "buy_yes"
        elif weighted_yes < yes_bid - 0.05:    # Model says YES overpriced → buy NO
            action = "buy_no"

    return EnsembleDecision(
        ticker=ticker,
        weighted_yes_prob=round(weighted_yes, 4),
        ensemble_confidence=round(avg_confidence, 4),
        votes=votes,
        action=action,
        consensus=consensus,
        disagreement_pct=round(disagreement_pct, 4),
        total_cost_usd=round(total_cost, 6),
        decided_at=datetime.now(timezone.utc).isoformat(),
    )


def ensemble_decision_to_dict(decision: EnsembleDecision) -> dict[str, Any]:
    """Serialize EnsembleDecision to dict for MCP tool output."""
    return {
        "ticker": decision.ticker,
        "action": decision.action,
        "consensus": decision.consensus,
        "weighted_yes_probability": decision.weighted_yes_prob,
        "ensemble_confidence": decision.ensemble_confidence,
        "disagreement_pct": decision.disagreement_pct,
        "total_cost_usd": decision.total_cost_usd,
        "decided_at": decision.decided_at,
        "model_votes": [
            {
                "model": v.model,
                "role": v.role,
                "weight": v.weight,
                "yes_probability": v.yes_probability,
                "confidence": v.confidence,
                "reasoning": v.reasoning,
            }
            for v in decision.votes
        ],
        "note": (
            f"Models {'AGREE' if decision.consensus else 'DISAGREE (skip)'} — "
            f"weighted P(YES)={decision.weighted_yes_prob:.2%} "
            f"confidence={decision.ensemble_confidence:.2%}"
        ),
    }
