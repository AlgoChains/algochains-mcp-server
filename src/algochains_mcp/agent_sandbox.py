"""
AlgoClaw agent:sandbox + spend:llm_budget helpers for MCP tool dispatch.

Portable fail-closed implementation (no control-tower import required).
Mirrors control-tower core.agent_sandbox_runtime contract.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

_lock = threading.Lock()

_FORBIDDEN_ENV_FRAGMENTS = (
    "TRADOVATE",
    "OWNER_API",
    "SUPABASE_SERVICE",
    "SLACK_BOT",
    "SLACK_APP",
    "DATABENTO",
    "OPENAI_API",
    "ANTHROPIC",
    "XAI_API",
    "GEMINI",
    "SENDGRID",
    "STRIPE_SECRET",
    "AWS_SECRET",
    "PRIVATE_KEY",
)
_ENV_ALLOWLIST = frozenset({"PATH", "HOME", "LANG", "LC_ALL", "TERM", "TMPDIR", "TMP", "TEMP"})

DEFAULT_DAILY_BUDGET_USD = float(os.environ.get("ALGOCLAW_DEFAULT_DAILY_LLM_BUDGET_USD", "5.0"))
DEFAULT_MAX_RUNTIME_SEC = int(os.environ.get("ALGOCLAW_SANDBOX_MAX_RUNTIME_SEC", "300"))


def _root() -> Path:
    override = os.environ.get("ALGOCLAW_SANDBOX_ROOT")
    if override:
        return Path(override)
    return Path(tempfile.gettempdir()) / "algoclaw_sandboxes"


def _budget_path() -> Path:
    override = os.environ.get("ALGOCLAW_BUDGET_LEDGER")
    if override:
        return Path(override)
    return _root() / "llm_budget_ledger.json"


def scrub_env() -> Dict[str, str]:
    out: Dict[str, str] = {}
    for key, val in os.environ.items():
        if key in _ENV_ALLOWLIST and val is not None:
            out[key] = val
            continue
        upper = key.upper()
        if any(frag in upper for frag in _FORBIDDEN_ENV_FRAGMENTS):
            continue
    out.setdefault("PATH", "/usr/bin:/bin:/usr/local/bin")
    out.setdefault("HOME", str(Path.home()))
    out["ALGOCLAW_SANDBOX"] = "1"
    return out


def start_sandboxed_agent(
    *,
    scopes: Sequence[str],
    task: str,
    clerk_user_id: str = "",
    max_runtime_sec: Optional[int] = None,
) -> Dict[str, Any]:
    if "agent:sandbox" not in set(scopes or ()):
        return {"ok": False, "error": "missing_scope:agent:sandbox", "authority": "agent_memory"}

    session_id = f"asb_{uuid.uuid4().hex[:16]}"
    root = _root()
    workspace = (root / session_id).resolve()
    if not str(workspace).startswith(str(root.resolve())):
        return {"ok": False, "error": "path_escape_blocked"}
    for sub in ("workspace", "scratch", "output"):
        (workspace / sub).mkdir(parents=True, exist_ok=True)
    (workspace / "workspace" / "task.txt").write_text((task or "")[:20_000], encoding="utf-8")
    meta = {
        "session_id": session_id,
        "workspace": str(workspace),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "clerk_user_id": clerk_user_id,
        "max_runtime_sec": int(max_runtime_sec or DEFAULT_MAX_RUNTIME_SEC),
        "allowed_relative_paths": ["workspace", "scratch", "output"],
        "inherited_broker_env": False,
        "env_keys": sorted(scrub_env().keys()),
    }
    (workspace / "session.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return {
        "ok": True,
        "authority": "agent_memory",
        **meta,
        "note": "Sandbox session created. Use MCP tools only; no host broker env inherited.",
    }


def reserve_llm_budget(
    *,
    scopes: Sequence[str],
    clerk_user_id: str,
    amount_usd: float,
    daily_cap_usd: Optional[float] = None,
) -> Dict[str, Any]:
    if "spend:llm_budget" not in set(scopes or ()):
        return {"ok": False, "error": "missing_scope:spend:llm_budget", "authority": "agent_memory"}
    if amount_usd <= 0:
        return {"ok": False, "error": "amount_usd_must_be_positive"}

    cap = float(daily_cap_usd if daily_cap_usd is not None else DEFAULT_DAILY_BUDGET_USD)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = _budget_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with _lock:
            if path.exists():
                try:
                    ledger = json.loads(path.read_text(encoding="utf-8"))
                except Exception as exc:
                    return {"ok": False, "error": f"budget_ledger_unavailable:{exc}"}
            else:
                ledger = {"days": {}}
            days = ledger.setdefault("days", {})
            day_bucket = days.setdefault(day, {})
            user_key = clerk_user_id or "anonymous"
            user_bucket = day_bucket.setdefault(
                user_key, {"reserved_usd": 0.0, "cap_usd": cap, "reservations": []}
            )
            user_bucket["cap_usd"] = cap
            used = float(user_bucket.get("reserved_usd") or 0.0)
            if used + amount_usd > cap + 1e-9:
                return {
                    "ok": False,
                    "error": "budget_exhausted",
                    "reserved_usd": used,
                    "cap_usd": cap,
                    "requested_usd": amount_usd,
                    "authority": "agent_memory",
                }
            reservation_id = f"rsv_{uuid.uuid4().hex[:12]}"
            user_bucket["reserved_usd"] = round(used + amount_usd, 6)
            user_bucket.setdefault("reservations", []).append(
                {
                    "id": reservation_id,
                    "amount_usd": amount_usd,
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
            )
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(ledger, indent=2, sort_keys=True), encoding="utf-8")
            tmp.replace(path)
    except Exception as exc:
        return {"ok": False, "error": f"budget_ledger_unavailable:{exc}"}

    return {
        "ok": True,
        "reservation_id": reservation_id,
        "reserved_usd": amount_usd,
        "day_total_usd": user_bucket["reserved_usd"],
        "cap_usd": cap,
        "authority": "agent_memory",
    }


def destroy_sandbox(session_id: str) -> Dict[str, Any]:
    if not session_id or ".." in session_id or "/" in session_id or "\\" in session_id:
        return {"ok": False, "error": "invalid_session_id"}
    root = _root().resolve()
    path = (root / session_id).resolve()
    if not str(path).startswith(str(root)):
        return {"ok": False, "error": "path_escape_blocked"}
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    return {"ok": True, "session_id": session_id}
