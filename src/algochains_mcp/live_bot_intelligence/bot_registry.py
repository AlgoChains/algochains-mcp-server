"""Shared read-only bot log metadata for MCP triage surfaces."""
from __future__ import annotations

from typing import Final

READ_ONLY_BOT_LOGS: Final[dict[str, dict[str, object]]] = {
    "mnq": {
        "symbol": "MNQ",
        "environment": "live",
        "log": "logs/futures_bot_live.log",
        "process_markers": ("FUTURES_SCALPER_UPGRADED.py",),
    },
    "mnq_demo": {
        "symbol": "MNQ",
        "environment": "demo",
        "log": "logs/futures_bot_demo.log",
        "process_markers": (
            "futures_bot_demo",
            "FUTURES_SCALPER_DEMO",
            "FUTURES_SCALPER_UPGRADED.py --mode demo",
            "TRADOVATE_ENV=demo",
        ),
    },
    "cl": {
        "symbol": "CL",
        "environment": "live",
        "log": "logs/cl_futures_live.log",
        "process_markers": ("CL_FUTURES_SCALPER.py",),
    },
    "mes": {
        "symbol": "MES",
        "environment": "live",
        "log": "logs/mes_swing_live.log",
        "process_markers": ("mes_swing_live.py",),
    },
    "nq": {
        "symbol": "NQ",
        "environment": "live",
        "log": "logs/nq_swing_live.log",
        "process_markers": ("nq_swing_live.py",),
    },
    "kalshi": {
        "symbol": "KALSHI",
        "environment": "live",
        "log": "logs/kalshi_bot.log",
        "process_markers": ("kalshi_daemon.py",),
    },
}

FAIL_CLOSED_TOKENS: Final[tuple[str, ...]] = (
    "FAIL-CLOSED",
    "FAIL_CLOSED",
    "T4-FAIL-CLOSED",
    "T4_FAIL_CLOSED",
    "fail closed",
    "fail-closed",
)

ERROR_TOKENS: Final[tuple[str, ...]] = (
    "ERROR",
    "Exception",
    "Traceback",
    " 401",
    " 422",
    *FAIL_CLOSED_TOKENS,
)


def normalize_bot_id(bot_id: str | None) -> str:
    """Normalize external bot identifiers without broad alias guessing."""
    return (bot_id or "all").strip().lower().replace("-", "_")


def is_fail_closed_line(line: str) -> bool:
    lower_line = line.lower()
    return any(token.lower() in lower_line for token in FAIL_CLOSED_TOKENS)


def is_actionable_error_line(line: str) -> bool:
    lower_line = line.lower()
    return any(token.lower() in lower_line for token in ERROR_TOKENS)
