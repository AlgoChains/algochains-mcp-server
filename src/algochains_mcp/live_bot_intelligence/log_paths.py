"""Resolve bot log paths across live and demo control-tower modes."""
from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from algochains_mcp.paths import default_control_tower


BOT_LOG_FILES: dict[str, dict[str, str]] = {
    "mnq": {
        "live": "logs/futures_bot_live.log",
        "demo": "logs/futures_bot_demo.log",
    },
    "cl": {"live": "logs/cl_futures_live.log"},
    "mes": {"live": "logs/mes_swing_live.log"},
    "nq": {"live": "logs/nq_swing_live.log"},
    "kalshi": {"live": "logs/kalshi_bot.log"},
}

PRICE_SOURCE_PATTERNS = (
    re.compile(r"T4[-_]FAIL[-_]CLOSED", re.IGNORECASE),
    re.compile(r"no live market price", re.IGNORECASE),
    re.compile(r"REST price fetch failed", re.IGNORECASE),
    re.compile(r"md_quote_feed unavailable", re.IGNORECASE),
)


@dataclass(frozen=True)
class BotLogResolution:
    """Selected log path plus the evidence used for selection."""

    bot_id: str
    path: Path
    mode: str
    source: str
    candidates: dict[str, str]


def _read_control_tower_env(control_tower: Path, key: str) -> str | None:
    env_file = control_tower / ".env"
    if not env_file.exists():
        return None
    try:
        for raw_line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            env_key, value = line.split("=", 1)
            if env_key.strip() == key:
                return value.strip().strip('"').strip("'")
    except OSError:
        return None
    return None


def _signal_health_mode(control_tower: Path) -> str | None:
    path = control_tower / "state" / "signal_health.json"
    if not path.exists():
        return None
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    ws_health = payload.get("ws_health")
    status = ws_health.get("status") if isinstance(ws_health, dict) else None
    if not isinstance(status, str):
        return None
    normalized = status.strip().lower()
    if "demo" in normalized:
        return "demo"
    if "live" in normalized:
        return "live"
    return None


def _normalize_mode(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().lower()
    if normalized in {"demo", "paper", "sandbox", "test"}:
        return "demo"
    if normalized in {"live", "prod", "production"}:
        return "live"
    return None


def resolve_bot_log_path(
    bot_id: str,
    control_tower: Path | None = None,
) -> BotLogResolution:
    """Return the best log path for a bot in the current control-tower mode.

    MNQ has distinct demo/live logs. The control-tower ``TRADOVATE_ENV`` setting
    is preferred, followed by ``signal_health.ws_health.status`` and then file
    existence. Other bots keep their existing live log paths.
    """

    bot_key = bot_id.lower()
    root = control_tower or default_control_tower()
    files = BOT_LOG_FILES.get(bot_key)
    if not files:
        fallback = root / "logs" / f"{bot_key}.log"
        return BotLogResolution(
            bot_id=bot_key,
            path=fallback,
            mode="unknown",
            source="unknown_bot",
            candidates={"unknown": str(fallback)},
        )

    candidates = {mode: str(root / rel_path) for mode, rel_path in files.items()}
    env_mode = _normalize_mode(_read_control_tower_env(root, "TRADOVATE_ENV"))
    if env_mode in files:
        return BotLogResolution(bot_key, root / files[env_mode], env_mode, "control_tower_env", candidates)

    signal_mode = _signal_health_mode(root)
    if signal_mode in files:
        return BotLogResolution(bot_key, root / files[signal_mode], signal_mode, "signal_health", candidates)

    for mode, rel_path in files.items():
        path = root / rel_path
        if path.exists():
            return BotLogResolution(bot_key, path, mode, "existing_file", candidates)

    rel_path = files.get("live") or next(iter(files.values()))
    mode = "live" if "live" in files else next(iter(files))
    return BotLogResolution(bot_key, root / rel_path, mode, "default", candidates)


def summarize_price_source_failures(lines: Iterable[str]) -> dict[str, Any]:
    """Summarize recent price-source fail-closed evidence from log lines."""

    latest = ""
    count = 0
    rest_price_fetch_failed = False
    md_quote_feed_unavailable = False
    no_live_market_price = False

    for line in lines:
        matched = any(pattern.search(line) for pattern in PRICE_SOURCE_PATTERNS)
        rest_price_fetch_failed = rest_price_fetch_failed or bool(
            re.search(r"REST price fetch failed", line, re.IGNORECASE)
        )
        md_quote_feed_unavailable = md_quote_feed_unavailable or bool(
            re.search(r"md_quote_feed unavailable", line, re.IGNORECASE)
        )
        no_live_market_price = no_live_market_price or bool(
            re.search(r"no live market price", line, re.IGNORECASE)
        )
        if matched:
            count += 1
            latest = line.strip()[-300:]

    both_sources_down = rest_price_fetch_failed and md_quote_feed_unavailable
    status = "fail_closed" if count else "ok"
    return {
        "status": status,
        "fail_closed_count": count,
        "rest_price_fetch_failed": rest_price_fetch_failed,
        "md_quote_feed_unavailable": md_quote_feed_unavailable,
        "no_live_market_price": no_live_market_price,
        "both_price_sources_down": both_sources_down,
        "latest": latest,
    }

