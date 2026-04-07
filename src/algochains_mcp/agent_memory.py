"""
Agent Memory Bridge — AlgoChains MCP Server
Provides read/write access to OpenClaw memory, regime state, heartbeat, and
agent evaluations via MCP tools.

Real data only: reads actual JSON files from ~/.openclaw/.
Fails closed if the OpenClaw directory is unavailable.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_OPENCLAW_ROOT = Path.home() / ".openclaw"

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
    """Read a JSON file, return default on any error."""
    try:
        text = path.read_text(encoding="utf-8")
        return json.loads(text)
    except FileNotFoundError:
        logger.debug(f"OpenClaw state file not found: {path}")
        return default
    except json.JSONDecodeError as e:
        logger.warning(f"JSON parse error in {path}: {e}")
        return {"error": f"JSON parse error: {e}", "path": str(path)}
    except Exception as e:
        logger.warning(f"Could not read {path}: {e}")
        return {"error": str(e), "path": str(path)}


def _write_json(path: Path, data: Any) -> bool:
    """Write JSON to path, return True on success."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        return True
    except Exception as e:
        logger.error(f"Could not write {path}: {e}")
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
            "hint": "Heartbeat is written by autonomous_watchdog.py every 5 minutes",
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
