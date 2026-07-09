"""
Agent Memory Bridge — AlgoChains MCP Server
Provides read/write access to OpenClaw memory, regime state, heartbeat, and
agent evaluations via MCP tools.

Primary backend: ~/.openclaw/ JSON files (single-machine dev and desktop).
Fallback backend: Supabase `agent_memory` table — used automatically when the
~/.openclaw directory is absent (fresh Docker deploy, cloud worker, CI).

Fail-closed only on *complete* loss of both backends.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_OPENCLAW_ROOT = Path.home() / ".openclaw"
_OPENCLAW_PRESENT = _OPENCLAW_ROOT.exists()

_SUPABASE_URL = os.getenv("SUPABASE_URL", "")
_SUPABASE_KEY = (
    os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    or os.getenv("SUPABASE_SERVICE_KEY", "")
    or os.getenv("SUPABASE_ANON_KEY", "")
)
_AGENT_MEMORY_TABLE = "agent_memory"


if not _OPENCLAW_PRESENT:
    if _SUPABASE_URL and _SUPABASE_KEY:
        logger.info(
            "agent_memory: ~/.openclaw not found — using Supabase fallback (%s)",
            _SUPABASE_URL,
        )
    else:
        logger.warning(
            "agent_memory: ~/.openclaw not found AND Supabase not configured. "
            "All memory reads will return empty. "
            "Set SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY to enable cloud fallback."
        )


def _sb_get(key: str) -> Any:
    """Read a single key from Supabase agent_memory table (sync via asyncio.run)."""
    if not _SUPABASE_URL or not _SUPABASE_KEY:
        return None
    try:
        import httpx as _httpx
        url = f"{_SUPABASE_URL}/rest/v1/{_AGENT_MEMORY_TABLE}?key=eq.{key}&select=value&limit=1"
        headers = {
            "apikey": _SUPABASE_KEY,
            "Authorization": f"Bearer {_SUPABASE_KEY}",
        }
        resp = _httpx.get(url, headers=headers, timeout=5.0)
        if resp.status_code == 200:
            rows = resp.json()
            if rows:
                raw = rows[0].get("value")
                try:
                    return json.loads(raw) if isinstance(raw, str) else raw
                except Exception:
                    return raw
    except Exception as _e:
        logger.debug("agent_memory Supabase get(%s) failed: %s", key, _e)
    return None


def _sb_set(key: str, value: Any) -> bool:
    """Upsert a key into Supabase agent_memory table."""
    if not _SUPABASE_URL or not _SUPABASE_KEY:
        return False
    try:
        import httpx as _httpx
        url = f"{_SUPABASE_URL}/rest/v1/{_AGENT_MEMORY_TABLE}"
        headers = {
            "apikey": _SUPABASE_KEY,
            "Authorization": f"Bearer {_SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates",
        }
        payload = {
            "key": key,
            "value": json.dumps(value, default=str),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        resp = _httpx.post(url, headers=headers, json=payload, timeout=5.0)
        return resp.status_code in (200, 201)
    except Exception as _e:
        logger.debug("agent_memory Supabase set(%s) failed: %s", key, _e)
    return False

# Key state files
_STATE_FILES = {
    "memory":            _OPENCLAW_ROOT / "memory.json",
    "current_regime":    _OPENCLAW_ROOT / "current_regime.json",
    "bot_heartbeat":     _OPENCLAW_ROOT / "bot_heartbeat.json",
    "monitor_state":     _OPENCLAW_ROOT / "monitor_state.json",
    "agent_evaluations": _OPENCLAW_ROOT / "agent_evaluations.json",
    "ai_cost_state":     _OPENCLAW_ROOT / "ai_cost_state.json",
    "calibration":       _OPENCLAW_ROOT / "calibration_history.json",
    "optimizer_state":   _OPENCLAW_ROOT / "optimizer_state.json",
    "fix_tracker":       _OPENCLAW_ROOT / "fix_tracker.json",
    "session_notes":     _OPENCLAW_ROOT / "session_notes.json",
    "qa_state":          _OPENCLAW_ROOT / "qa_state.json",
}


def _read_json(path: Path, default: Any = None) -> Any:
    """Read a JSON file; fall back to Supabase agent_memory when the file is missing."""
    try:
        text = path.read_text(encoding="utf-8")
        return json.loads(text)
    except FileNotFoundError:
        # Local file missing — try Supabase fallback keyed by the filename stem
        _sb_key = path.stem
        _sb_val = _sb_get(_sb_key)
        if _sb_val is not None:
            logger.debug("agent_memory: local file missing, served %s from Supabase", _sb_key)
            return _sb_val
        logger.debug("OpenClaw state file not found and Supabase returned nothing: %s", path)
        return default
    except json.JSONDecodeError as e:
        logger.warning(f"JSON parse error in {path}: {e}")
        return {"error": f"JSON parse error: {e}", "path": str(path)}
    except Exception as e:
        logger.warning(f"Could not read {path}: {e}")
        return {"error": str(e), "path": str(path)}


def _write_json(path: Path, data: Any) -> bool:
    """Write JSON to path; mirror to Supabase when local write fails (OpenClaw absent)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        return True
    except Exception as e:
        logger.error(f"Could not write {path}: {e}")
        # Attempt Supabase mirror as fallback
        if _sb_set(path.stem, data):
            logger.info("agent_memory: local write failed, mirrored %s to Supabase", path.stem)
            return True
        return False


def get_openclaw_memory(key_prefix: Optional[str] = None, limit: int = 50) -> dict:
    """
    Read OpenClaw memory state.

    Args:
        key_prefix: Optional prefix filter on memory keys (e.g. 'trade', 'bot', 'regime')
        limit: Max number of keys to return

    Returns:
        Dict with memory keys and values (truncated for large values)
    """
    memory = _read_json(_STATE_FILES["memory"], default={})
    if not isinstance(memory, dict):
        return {"error": "Memory file is not a dict", "raw": str(memory)[:500]}

    if key_prefix:
        prefix = key_prefix.lower()
        memory = {k: v for k, v in memory.items() if prefix in k.lower()}

    # Truncate large values for MCP response
    truncated = {}
    for k, v in list(memory.items())[:limit]:
        if isinstance(v, str) and len(v) > 500:
            truncated[k] = v[:497] + "..."
        elif isinstance(v, (dict, list)):
            s = json.dumps(v)
            truncated[k] = json.loads(s[:2000]) if len(s) > 2000 else v
        else:
            truncated[k] = v

    return {
        "total_keys": len(memory),
        "returned_keys": len(truncated),
        "key_prefix_filter": key_prefix,
        "memory": truncated,
        "source": str(_STATE_FILES["memory"]),
    }


def get_current_regime() -> dict:
    """Read the current market regime from OpenClaw state."""
    regime = _read_json(_STATE_FILES["current_regime"], default={})
    if not regime:
        return {
            "error": "No regime state found",
            "path": str(_STATE_FILES["current_regime"]),
            "hint": "Regime is written by the autonomous regime_detector skill",
        }
    return {
        "regime": regime,
        "source": str(_STATE_FILES["current_regime"]),
        "read_at": datetime.now(timezone.utc).isoformat(),
    }


def get_bot_heartbeat() -> dict:
    """Read the bot heartbeat state — shows which bots are alive."""
    heartbeat = _read_json(_STATE_FILES["bot_heartbeat"], default={})
    if not heartbeat:
        return {
            "error": "No bot heartbeat found",
            "path": str(_OPENCLAW_ROOT / "bot_heartbeat.json"),
            "hint": "MNQ fill-triggered only (FUTURES_SCALPER_UPGRADED._track_openclaw_feedback). Not written by autonomous_watchdog. For fleet liveness use get_bot_health.",
        }
    # Annotate with staleness where data is a dict with last_seen
    now = time.time()
    if isinstance(heartbeat, dict):
        for bot_name, bot_data in heartbeat.items():
            if isinstance(bot_data, dict) and "last_seen" in bot_data:
                try:
                    age_seconds = now - float(bot_data["last_seen"])
                    bot_data["age_seconds"] = round(age_seconds, 1)
                    bot_data["status"] = "LIVE" if age_seconds < 600 else "STALE"
                except (ValueError, TypeError):
                    pass
    return {
        "heartbeat": heartbeat,
        "source": str(_STATE_FILES["bot_heartbeat"]),
        "read_at": datetime.now(timezone.utc).isoformat(),
    }


def get_agent_evaluations(limit: int = 20) -> dict:
    """Read agent evaluation records from OpenClaw."""
    evals = _read_json(_STATE_FILES["agent_evaluations"], default={})
    if not evals:
        return {
            "error": "No agent evaluations found",
            "path": str(_STATE_FILES["agent_evaluations"]),
        }
    # If it's a dict with agent names as keys
    if isinstance(evals, dict):
        return {
            "agents_evaluated": len(evals),
            "evaluations": dict(list(evals.items())[:limit]),
            "source": str(_STATE_FILES["agent_evaluations"]),
        }
    # If it's a list
    return {
        "agents_evaluated": len(evals),
        "evaluations": evals[:limit],
        "source": str(_STATE_FILES["agent_evaluations"]),
    }


def store_trade_lesson(lesson: dict) -> dict:
    """
    Store a trade lesson in OpenClaw memory for future retrieval.

    Args:
        lesson: Dict with keys: symbol, direction, outcome, regime, lesson, pnl (optional)

    Returns:
        Success/failure status
    """
    required = {"symbol", "direction", "outcome", "lesson"}
    missing = required - set(lesson.keys())
    if missing:
        return {"error": f"Missing required fields: {missing}", "required": list(required)}

    # Read existing memory
    memory = _read_json(_STATE_FILES["memory"], default={})
    if not isinstance(memory, dict):
        memory = {}

    # Append to trade_lessons list
    lessons_key = "trade_lessons"
    lessons = memory.get(lessons_key, [])
    if not isinstance(lessons, list):
        lessons = []

    entry = {
        **lesson,
        "stored_at": datetime.now(timezone.utc).isoformat(),
    }
    lessons.append(entry)

    # Keep last 500 lessons to bound file size
    memory[lessons_key] = lessons[-500:]

    success = _write_json(_STATE_FILES["memory"], memory)
    if success:
        return {
            "stored": True,
            "lesson": entry,
            "total_lessons": len(lessons),
        }
    return {"stored": False, "error": "Failed to write memory file"}


def get_monitor_state() -> dict:
    """Read the OpenClaw monitor state (agent activity, last run times)."""
    state = _read_json(_STATE_FILES["monitor_state"], default={})
    return {
        "monitor_state": state,
        "source": str(_STATE_FILES["monitor_state"]),
        "read_at": datetime.now(timezone.utc).isoformat(),
    }


def get_ai_cost_state() -> dict:
    """Read the AI cost state (token usage, model costs, budget burn)."""
    cost = _read_json(_STATE_FILES["ai_cost_state"], default={})
    return {
        "ai_cost_state": cost,
        "source": str(_STATE_FILES["ai_cost_state"]),
        "read_at": datetime.now(timezone.utc).isoformat(),
    }


def get_all_state_files() -> dict:
    """Return a quick summary of all OpenClaw state files (existence + modification time)."""
    summary = {}
    for key, path in _STATE_FILES.items():
        if path.exists():
            stat = path.stat()
            summary[key] = {
                "exists": True,
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                "path": str(path),
            }
        else:
            summary[key] = {
                "exists": False,
                "path": str(path),
            }
    return {
        "openclaw_root": str(_OPENCLAW_ROOT),
        "openclaw_exists": _OPENCLAW_ROOT.exists(),
        "state_files": summary,
    }
