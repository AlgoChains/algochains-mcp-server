"""Resolve live bot log paths with legacy aliases for health/triage surfaces."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

# Canonical live log first; legacy names kept for watchdog / health-audit compatibility.
BOT_LOG_CANDIDATES: dict[str, tuple[str, ...]] = {
    "mnq": ("logs/futures_bot_live.log",),
    "cl": ("logs/cl_futures_live.log", "logs/cl_bot_live.log"),
    "mes": ("logs/mes_swing_live.log", "logs/mes_swing.log"),
    "nq": ("logs/nq_swing_live.log", "logs/nq_swing.log"),
    "kalshi": ("logs/kalshi_bot.log",),
}

BOT_SCRIPT_NAMES: dict[str, str] = {
    "mnq": "FUTURES_SCALPER_UPGRADED.py",
    "cl": "CL_FUTURES_SCALPER.py",
    "mes": "mes_swing_live.py",
    "nq": "nq_swing_live.py",
    "kalshi": "kalshi_daemon.py",
}

STALE_LOG_SECONDS = 300


def resolve_bot_log(
    control_tower: Path,
    bot_id: str,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """Pick the freshest existing candidate log and surface legacy-path drift."""
    bot_key = bot_id.lower()
    candidates = BOT_LOG_CANDIDATES.get(bot_key, ())
    current_time = time.time() if now is None else now

    checked: list[dict[str, Any]] = []
    best_path: Path | None = None
    best_age: float | None = None

    for relative in candidates:
        path = control_tower / relative
        exists = path.exists()
        age_seconds: int | None = None
        if exists:
            try:
                age_seconds = max(0, int(current_time - path.stat().st_mtime))
            except OSError:
                exists = False
            if exists and (best_age is None or age_seconds < best_age):
                best_path = path
                best_age = float(age_seconds)
        checked.append(
            {
                "relative": relative,
                "exists": exists,
                "age_seconds": age_seconds,
                "is_canonical": relative == candidates[0] if candidates else False,
            }
        )

    legacy_stale_mismatch = False
    if len(checked) >= 2:
        canonical = checked[0]
        legacy = checked[1]
        if (
            canonical.get("exists")
            and legacy.get("exists")
            and canonical.get("age_seconds") is not None
            and legacy.get("age_seconds") is not None
            and legacy["age_seconds"] >= STALE_LOG_SECONDS
            and canonical["age_seconds"] < STALE_LOG_SECONDS
        ):
            legacy_stale_mismatch = True

    return {
        "bot_id": bot_key,
        "path": best_path,
        "canonical_relative": candidates[0] if candidates else None,
        "candidates": checked,
        "legacy_stale_mismatch": legacy_stale_mismatch,
        "log_age_seconds": int(best_age) if best_age is not None else None,
        "log_fresh": best_age is not None and best_age < STALE_LOG_SECONDS,
    }


def bot_log_path(control_tower: Path, bot_id: str) -> Path | None:
    """Return the freshest existing log path for a bot, if any."""
    return resolve_bot_log(control_tower, bot_id).get("path")


def sync_bot_log_legacy_aliases(
    control_tower: Path,
    *,
    dry_run: bool = False,
    now: float | None = None,
) -> dict[str, Any]:
    """Replace stale legacy log files with symlinks to the canonical live log.

    Control-tower trading-system-health-audit still probes legacy paths such as
    logs/cl_bot_live.log. When CL writes only to logs/cl_futures_live.log the
    legacy file stops updating and triggers false SEV1 inactive alerts.
    """
    actions: list[dict[str, Any]] = []
    current_time = time.time() if now is None else now

    for bot_id, candidates in BOT_LOG_CANDIDATES.items():
        if len(candidates) < 2:
            continue

        resolved = resolve_bot_log(control_tower, bot_id, now=current_time)
        if not resolved.get("legacy_stale_mismatch"):
            continue

        canonical_rel = candidates[0]
        legacy_rel = candidates[1]
        canonical = control_tower / canonical_rel
        legacy = control_tower / legacy_rel
        if not canonical.exists():
            continue

        canonical_resolved = canonical.resolve()
        action: dict[str, Any] = {
            "bot_id": bot_id,
            "legacy_relative": legacy_rel,
            "canonical_relative": canonical_rel,
        }

        if legacy.is_symlink():
            try:
                if legacy.resolve() == canonical_resolved:
                    action["status"] = "already_synced"
                    actions.append(action)
                    continue
            except OSError:
                pass
            if not dry_run:
                legacy.unlink()
            action["prior_state"] = "wrong_symlink"
        elif legacy.exists() and not legacy.is_symlink():
            action["prior_state"] = "stale_file"
            if not dry_run:
                legacy.unlink()

        if dry_run:
            action["status"] = "would_symlink"
        else:
            legacy.parent.mkdir(parents=True, exist_ok=True)
            legacy.symlink_to(canonical_resolved)
            action["status"] = "symlinked"
        actions.append(action)

    return {
        "control_tower": str(control_tower),
        "dry_run": dry_run,
        "actions": actions,
        "synced_count": sum(1 for a in actions if a.get("status") == "symlinked"),
    }
