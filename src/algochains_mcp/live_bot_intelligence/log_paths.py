"""Bot log path resolution helpers.

The MNQ runtime can run in live or demo mode. Operational health tools should
read whichever log is actually active, while owner-gated restart paths continue
to use their primary configured log targets.
"""
from __future__ import annotations

from pathlib import Path

BOT_LOG_CANDIDATES: dict[str, tuple[str, ...]] = {
    "mnq": ("logs/futures_bot_live.log", "logs/futures_bot_demo.log"),
    "cl": ("logs/cl_futures_live.log",),
    "mes": ("logs/mes_swing_live.log",),
    "nq": ("logs/nq_swing_live.log",),
    "kalshi": ("logs/kalshi_bot.log",),
}


def bot_log_candidates(control_tower: Path, bot_id: str) -> list[Path]:
    """Return candidate log paths for a bot in priority order."""
    paths = BOT_LOG_CANDIDATES.get(bot_id.lower())
    if not paths:
        return []
    return [control_tower / relative_path for relative_path in paths]


def resolve_bot_log_path(control_tower: Path, bot_id: str) -> Path | None:
    """Select the active log path for read-only health/metrics parsing.

    If multiple candidates exist, use the newest mtime. If none exist, return
    the primary candidate so callers can report the expected missing path.
    """
    candidates = bot_log_candidates(control_tower, bot_id)
    if not candidates:
        return None

    existing: list[Path] = []
    for candidate in candidates:
        try:
            if candidate.exists():
                existing.append(candidate)
        except OSError:
            continue

    if existing:
        return max(existing, key=lambda path: path.stat().st_mtime)
    return candidates[0]
