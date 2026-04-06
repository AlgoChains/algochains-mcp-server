"""
V18 Intent Parser — LLM-powered natural language → structured intent.

Transforms user intents like "Get me $10K AI exposure" into structured
ParsedIntent objects with goal, universe, constraints, preferences, and risk params.
"""

from __future__ import annotations

import re
import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger("algochains.intent_parser")


class IntentGoal(str, Enum):
    """High-level trading goal categories."""
    BUY = "buy"
    SELL = "sell"
    REBALANCE = "rebalance"
    HEDGE = "hedge"
    REDUCE_EXPOSURE = "reduce_exposure"
    INCREASE_EXPOSURE = "increase_exposure"
    RESEARCH = "research"
    MONITOR = "monitor"
    CLOSE = "close"
    PROTECT = "protect"
    OPTIMIZE = "optimize"
    ARBITRAGE = "arbitrage"


@dataclass
class ParsedIntent:
    """Structured representation of a user's trading intent."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    raw_text: str = ""
    goal: IntentGoal = IntentGoal.RESEARCH
    asset_class: str = "equities"
    universe: list[str] = field(default_factory=list)
    universe_filter: str = ""
    notional: Optional[float] = None
    qty: Optional[float] = None
    max_pct_per_asset: Optional[float] = None
    preferred_broker: Optional[str] = None
    order_type: str = "market"
    time_horizon: str = "now"
    risk_profile: str = "default"
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
    confidence: float = 0.0
    parsed_entities: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "raw_text": self.raw_text,
            "goal": self.goal.value,
            "asset_class": self.asset_class,
            "universe": self.universe,
            "universe_filter": self.universe_filter,
            "notional": self.notional,
            "qty": self.qty,
            "max_pct_per_asset": self.max_pct_per_asset,
            "preferred_broker": self.preferred_broker,
            "order_type": self.order_type,
            "time_horizon": self.time_horizon,
            "risk_profile": self.risk_profile,
            "stop_loss_pct": self.stop_loss_pct,
            "take_profit_pct": self.take_profit_pct,
            "confidence": self.confidence,
            "parsed_entities": self.parsed_entities,
        }


# ── Pattern-based intent extraction (fast path, no LLM needed) ──────

_GOAL_PATTERNS: list[tuple[re.Pattern, IntentGoal]] = [
    (re.compile(r"\b(buy|long|get\s+me|acquire|add)\b", re.I), IntentGoal.BUY),
    (re.compile(r"\b(sell|short|dump|exit|liquidate)\b", re.I), IntentGoal.SELL),
    (re.compile(r"\b(close|flatten|unwind)\b", re.I), IntentGoal.CLOSE),
    (re.compile(r"\b(rebalance|reallocate|redistribute)\b", re.I), IntentGoal.REBALANCE),
    (re.compile(r"\b(hedge|protect|insure)\b", re.I), IntentGoal.PROTECT),
    (re.compile(r"\b(reduce\s+exposure|trim|cut)\b", re.I), IntentGoal.REDUCE_EXPOSURE),
    (re.compile(r"\b(increase\s+exposure|scale\s+up|add\s+to)\b", re.I), IntentGoal.INCREASE_EXPOSURE),
    (re.compile(r"\b(research|analyze|compare|study|find)\b", re.I), IntentGoal.RESEARCH),
    (re.compile(r"\b(monitor|watch|alert|track)\b", re.I), IntentGoal.MONITOR),
    (re.compile(r"\b(optimize|improve|tune)\b", re.I), IntentGoal.OPTIMIZE),
    (re.compile(r"\b(arbitrage|arb|spread)\b", re.I), IntentGoal.ARBITRAGE),
]

_DOLLAR_PATTERN = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)\s*[kK]?", re.I)
_PCT_PER_ASSET = re.compile(r"(?:max|no\s+more\s+than)\s+([\d.]+)\s*%\s*(?:per|each|any)", re.I)
_STOP_LOSS = re.compile(r"stop\s*(?:loss)?\s*(?:at|of|@)?\s*([\d.]+)\s*%", re.I)
_TAKE_PROFIT = re.compile(r"(?:take\s*profit|tp|target)\s*(?:at|of|@)?\s*([\d.]+)\s*%", re.I)
_QTY_PATTERN = re.compile(r"\b(\d+)\s*(?:shares?|contracts?|lots?|units?)\b", re.I)
_TICKER_PATTERN = re.compile(r"\b([A-Z]{1,5})\b")

_BROKER_ALIASES: dict[str, str] = {
    "alpaca": "alpaca", "ibkr": "ibkr", "interactive brokers": "ibkr",
    "tradovate": "tradovate", "oanda": "oanda", "schwab": "schwab",
    "tastytrade": "tastytrade", "tradestation": "tradestation",
    "cheapest": "_cheapest", "lowest fee": "_cheapest",
}

_ASSET_CLASS_PATTERNS: dict[str, re.Pattern] = {
    "equities": re.compile(r"\b(stock|equit|share|ticker)\b", re.I),
    "futures": re.compile(r"\b(future|contract|es|nq|cl|mnq|mes)\b", re.I),
    "options": re.compile(r"\b(option|call|put|strike|expir)\b", re.I),
    "forex": re.compile(r"\b(forex|fx|currency|eur|gbp|jpy|usd)\b", re.I),
    "crypto": re.compile(r"\b(crypto|bitcoin|btc|eth|sol|defi)\b", re.I),
}

_UNIVERSE_KEYWORDS: dict[str, list[str]] = {
    "ai": ["NVDA", "MSFT", "GOOGL", "META", "AMD", "AVGO", "PLTR", "CRM", "NOW", "SNOW"],
    "tech": ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "AMD", "CRM", "ORCL"],
    "energy": ["XOM", "CVX", "COP", "EOG", "SLB", "MPC", "VLO", "PSX", "OXY", "HES"],
    "healthcare": ["UNH", "JNJ", "LLY", "PFE", "ABBV", "MRK", "TMO", "ABT", "DHR", "BMY"],
    "finance": ["JPM", "BAC", "WFC", "GS", "MS", "C", "BLK", "SCHW", "AXP", "USB"],
    "defense": ["LMT", "RTX", "NOC", "GD", "BA", "HII", "LHX", "TDG", "TXT", "LDOS"],
    "semiconductor": ["NVDA", "AMD", "AVGO", "QCOM", "TXN", "INTC", "MU", "MRVL", "LRCX", "AMAT"],
    "mag7": ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA"],
    "faang": ["META", "AAPL", "AMZN", "NFLX", "GOOGL"],
    "index": ["SPY", "QQQ", "IWM", "DIA"],
}

_TIME_PATTERNS: dict[str, re.Pattern] = {
    "now": re.compile(r"\b(now|immediately|asap|right\s+now)\b", re.I),
    "today": re.compile(r"\b(today|by\s+close|eod)\b", re.I),
    "this_week": re.compile(r"\b(this\s+week|next\s+few\s+days)\b", re.I),
    "gradual": re.compile(r"\b(gradual|over\s+time|twap|vwap|stagger)\b", re.I),
}


class IntentParser:
    """Parse natural language trading intent into structured form.

    Uses fast regex-based extraction as primary path. Falls back to
    LLM parsing for complex/ambiguous intents if an LLM callable is provided.
    """

    def __init__(self, llm_callable=None):
        self._llm = llm_callable

    async def parse(self, text: str) -> ParsedIntent:
        """Parse user intent text into a structured ParsedIntent."""
        intent = ParsedIntent(raw_text=text)

        self._extract_goal(text, intent)
        self._extract_notional(text, intent)
        self._extract_qty(text, intent)
        self._extract_constraints(text, intent)
        self._extract_broker(text, intent)
        self._extract_asset_class(text, intent)
        self._extract_universe(text, intent)
        self._extract_time_horizon(text, intent)

        intent.confidence = self._compute_confidence(intent)

        if intent.confidence < 0.4 and self._llm is not None:
            try:
                intent = await self._llm_enhance(text, intent)
            except Exception as e:
                logger.warning("LLM enhancement failed, using pattern-based: %s", e)

        logger.info(
            "Parsed intent: goal=%s conf=%.0f%% notional=%s universe=%d assets",
            intent.goal.value, intent.confidence * 100,
            f"${intent.notional:,.0f}" if intent.notional else "None",
            len(intent.universe),
        )
        return intent

    def _extract_goal(self, text: str, intent: ParsedIntent) -> None:
        for pattern, goal in _GOAL_PATTERNS:
            if pattern.search(text):
                intent.goal = goal
                return

    def _extract_notional(self, text: str, intent: ParsedIntent) -> None:
        match = _DOLLAR_PATTERN.search(text)
        if match:
            raw = match.group(1).replace(",", "")
            val = float(raw)
            full = match.group(0)
            if "k" in full.lower() or "K" in full:
                val *= 1000
            intent.notional = val

    def _extract_qty(self, text: str, intent: ParsedIntent) -> None:
        match = _QTY_PATTERN.search(text)
        if match:
            intent.qty = float(match.group(1))

    def _extract_constraints(self, text: str, intent: ParsedIntent) -> None:
        pct_match = _PCT_PER_ASSET.search(text)
        if pct_match:
            intent.max_pct_per_asset = float(pct_match.group(1))

        sl_match = _STOP_LOSS.search(text)
        if sl_match:
            intent.stop_loss_pct = float(sl_match.group(1))

        tp_match = _TAKE_PROFIT.search(text)
        if tp_match:
            intent.take_profit_pct = float(tp_match.group(1))

    def _extract_broker(self, text: str, intent: ParsedIntent) -> None:
        text_lower = text.lower()
        for alias, broker in _BROKER_ALIASES.items():
            if alias in text_lower:
                intent.preferred_broker = broker
                return

    def _extract_asset_class(self, text: str, intent: ParsedIntent) -> None:
        for cls, pattern in _ASSET_CLASS_PATTERNS.items():
            if pattern.search(text):
                intent.asset_class = cls
                return

    def _extract_universe(self, text: str, intent: ParsedIntent) -> None:
        text_lower = text.lower()
        for keyword, tickers in _UNIVERSE_KEYWORDS.items():
            if keyword in text_lower:
                intent.universe = tickers
                intent.universe_filter = keyword
                return

        tickers = _TICKER_PATTERN.findall(text)
        noise = {"I", "A", "AM", "IS", "ON", "AT", "BY", "TO", "IN", "OF", "MY",
                 "THE", "FOR", "AND", "OR", "GET", "ME", "ALL", "MAX", "NO", "UP",
                 "BUY", "SELL", "WITH", "SET", "ADD", "CUT", "PUT", "AI", "US"}
        filtered = [t for t in tickers if t not in noise and len(t) >= 2]
        if filtered:
            intent.universe = filtered

    def _extract_time_horizon(self, text: str, intent: ParsedIntent) -> None:
        for horizon, pattern in _TIME_PATTERNS.items():
            if pattern.search(text):
                intent.time_horizon = horizon
                return

    def _compute_confidence(self, intent: ParsedIntent) -> float:
        """Heuristic confidence score based on how much was extracted."""
        score = 0.0
        if intent.goal != IntentGoal.RESEARCH:
            score += 0.25
        if intent.notional or intent.qty:
            score += 0.25
        if intent.universe:
            score += 0.20
        if intent.preferred_broker:
            score += 0.10
        if intent.max_pct_per_asset or intent.stop_loss_pct:
            score += 0.10
        if intent.time_horizon != "now":
            score += 0.10
        return min(score, 1.0)

    async def _llm_enhance(self, text: str, intent: ParsedIntent) -> ParsedIntent:
        """Use LLM to fill gaps in pattern-based parsing."""
        prompt = (
            "Parse this trading intent into structured fields. "
            "Return JSON with: goal, asset_class, universe (list of tickers), "
            "notional (dollar amount), max_pct_per_asset, preferred_broker, "
            "time_horizon, stop_loss_pct, take_profit_pct.\n\n"
            f"Intent: \"{text}\"\n\n"
            f"Already extracted: {intent.to_dict()}\n\n"
            "Fill in any missing fields. Return only valid JSON."
        )
        result = await self._llm(prompt)
        if isinstance(result, dict):
            if "goal" in result and not intent.goal:
                try:
                    intent.goal = IntentGoal(result["goal"])
                except ValueError:
                    pass
            if "universe" in result and not intent.universe:
                intent.universe = result["universe"]
            if "notional" in result and not intent.notional:
                intent.notional = result["notional"]
            intent.confidence = max(intent.confidence, 0.7)
        return intent
