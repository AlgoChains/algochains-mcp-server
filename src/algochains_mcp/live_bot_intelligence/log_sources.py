"""
Shared live-bot log source helpers.

The MNQ bot has both live and demo runtime logs. Health surfaces should prefer
the freshest existing runtime log while still keeping the live path as the
primary write target for restart operations.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


BOT_LOG_CANDIDATES: dict[str, tuple[tuple[str, str], ...]] = {
    "mnq": (
        ("live", "logs/futures_bot_live.log"),
        ("demo", "logs/futures_bot_demo.log"),
    ),
    "cl": (("live", "logs/cl_futures_live.log"),),
    "mes": (("live", "logs/mes_swing_live.log"),),
    "nq": (("live", "logs/nq_swing_live.log"),),
}

BOT_PRIMARY_LOG_PATHS: dict[str, str] = {
    bot_id: candidates[0][1]
    for bot_id, candidates in BOT_LOG_CANDIDATES.items()
}


@dataclass(frozen=True)
class SelectedBotLog:
    bot_id: str
    variant: str
    path: Path
    candidates: tuple[tuple[str, Path], ...]
    exists: bool


def bot_log_candidates(control_tower: Path, bot_id: str) -> tuple[tuple[str, Path], ...]:
    """Return configured log candidates for a bot as absolute paths."""
    candidates = BOT_LOG_CANDIDATES.get(bot_id.lower())
    if not candidates:
        return ()
    return tuple((variant, control_tower / rel_path) for variant, rel_path in candidates)


def select_bot_log(control_tower: Path, bot_id: str) -> SelectedBotLog:
    """
    Select the freshest existing log candidate.

    If no candidate exists, return the primary configured path so callers can
    report a stable missing-log path.
    """
    normalized = bot_id.lower()
    candidates = bot_log_candidates(control_tower, normalized)
    if not candidates:
        fallback = control_tower / "logs" / f"{normalized}.log"
        return SelectedBotLog(normalized, "unknown", fallback, (), False)

    existing: list[tuple[str, Path]] = []
    for variant, path in candidates:
        try:
            if path.exists():
                existing.append((variant, path))
        except OSError:
            continue

    if existing:
        variant, path = max(existing, key=lambda item: item[1].stat().st_mtime)
        return SelectedBotLog(normalized, variant, path, candidates, True)

    variant, path = candidates[0]
    return SelectedBotLog(normalized, variant, path, candidates, False)


def summarize_price_source_health(lines: list[str]) -> dict[str, object]:
    """Summarize recent market-price source failures from a bounded log tail."""
    rest_failed = False
    md_feed_unavailable = False
    no_live_market_price = False
    order_aborted = False
    fail_closed = False
    last_event = ""

    marker_re = re.compile(
        r"T4-FAIL-CLOSED|no live market price|REST price fetch failed|"
        r"md_quote_feed unavailable|order aborted|fail-closed",
        re.IGNORECASE,
    )

    for line in lines:
        lower = line.lower()
        if "rest price fetch failed" in lower:
            rest_failed = True
        if "md_quote_feed unavailable" in lower:
            md_feed_unavailable = True
        if "no live market price" in lower:
            no_live_market_price = True
        if "order aborted" in lower:
            order_aborted = True
        if "fail-closed" in lower or "t4-fail-closed" in lower:
            fail_closed = True
        if marker_re.search(line):
            last_event = line.strip()[-300:]

    independent_sources_down = rest_failed and md_feed_unavailable
    if fail_closed or no_live_market_price or order_aborted:
        status = "fail_closed"
    elif independent_sources_down:
        status = "critical"
    elif rest_failed or md_feed_unavailable:
        status = "degraded"
    else:
        status = "ok"

    return {
        "status": status,
        "rest_price_fetch_failed": rest_failed,
        "md_quote_feed_unavailable": md_feed_unavailable,
        "independent_sources_down": independent_sources_down,
        "order_aborted": order_aborted,
        "fail_closed": fail_closed or status == "fail_closed",
        "last_event": last_event,
        "window_lines": len(lines),
    }
