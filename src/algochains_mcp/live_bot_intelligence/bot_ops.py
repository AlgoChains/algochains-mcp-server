"""
bot_ops.py — Operational bot management tools for AlgoChains MCP server.

Added in v26.0 (2026-04-08) after 2026-04-07 incident analysis:
  - Bot restart (owner-token gated)
  - Position flatten (owner-token gated)
  - Bracket status (read-only)
  - AI pipeline health (read-only)
  - Position state read (read-only)

SECURITY: Destructive ops require OWNER_API_TOKEN env var to match the
token passed by the caller. Read-only ops are unrestricted.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Path resolution ──────────────────────────────────────────────────────────
# Unified resolver honors ALGOCHAINS_CONTROL_TOWER first, then falls back to
# the shared legacy list (Mac, /home/trrey, WSL). Behavior on the MacBook is
# unchanged: env typically unset → first existing legacy path = Mac repo.
from algochains_mcp.paths import default_control_tower

CONTROL_TOWER = default_control_tower()

BOT_MAP = {
    "mnq": {"grep": "FUTURES_SCALPER_UPGRADED", "script": "FUTURES_SCALPER_UPGRADED.py", "log": "logs/futures_bot_live.log"},
    "cl":  {"grep": "CL_FUTURES_SCALPER",       "script": "CL_FUTURES_SCALPER.py",       "log": "logs/cl_futures_live.log"},
    # V2 FIX: use *_live.log paths — mes_swing.log / nq_swing.log are OLD backup files (stale)
    "mes": {"grep": "mes_swing_live",            "script": "mes_swing_live.py",            "log": "logs/mes_swing_live.log"},
    "nq":  {"grep": "nq_swing_live",             "script": "nq_swing_live.py",             "log": "logs/nq_swing_live.log"},
}

SYMBOL_MAP = {"mnq": "MNQ", "cl": "CL", "mes": "MES", "nq": "NQ"}


def _read_control_tower_env(control_tower: Path, key: str) -> str | None:
    """Read one non-secret setting from the control-tower .env file."""
    env_path = control_tower / ".env"
    if not env_path.exists():
        return None
    try:
        for raw_line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            env_key, env_value = line.split("=", 1)
            if env_key.strip() == key:
                return env_value.strip().strip('"').strip("'")
    except Exception:
        return None
    return None


def _pipeline_timeout_config(control_tower: Path) -> tuple[float, str]:
    """Return the live bot pipeline timeout and the source used."""
    ct_value = _read_control_tower_env(control_tower, "PIPELINE_TIMEOUT_SECONDS")
    if ct_value:
        try:
            return float(ct_value), "control_tower_.env"
        except ValueError:
            pass
    env_value = os.getenv("PIPELINE_TIMEOUT_SECONDS")
    if env_value:
        try:
            return float(env_value), "mcp_process_env"
        except ValueError:
            pass
    return 8.0, "default"


def _tail_jsonl(path: Path, limit: int) -> list[dict]:
    """Read recent JSONL telemetry rows without loading the whole file."""
    if not path.exists():
        return []
    rows: list[dict] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in deque(handle, maxlen=max(1, limit)):
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except Exception:
        return []
    return rows


def _pctl(values: list[float], pct: float) -> float:
    vals = sorted(v for v in values if isinstance(v, (int, float)))
    if not vals:
        return 0.0
    idx = min(len(vals) - 1, max(0, int(round((pct / 100.0) * (len(vals) - 1)))))
    return round(float(vals[idx]), 3)


def _rate(count: int, total: int) -> float:
    return round(count / max(total, 1), 4)


def _summarize_decision_latency(control_tower: Path, timeout_s: float) -> dict:
    rows = _tail_jsonl(control_tower / "logs" / "decision_latency.jsonl", 500)
    if not rows:
        return {"status": "missing_or_empty", "count": 0}

    event_counts: dict[str, int] = {}
    for row in rows:
        event = str(row.get("event", "unknown"))
        event_counts[event] = event_counts.get(event, 0) + 1

    multi_agent_values = [
        float(row["multi_agent_ms"])
        for row in rows
        if isinstance(row.get("multi_agent_ms"), (int, float))
    ]
    timeout_ms = timeout_s * 1000.0
    timeout_events = [
        row
        for row in rows
        if "timeout" in str(row.get("event", "")).lower()
        or (
            isinstance(row.get("multi_agent_ms"), (int, float))
            and float(row["multi_agent_ms"]) >= timeout_ms
        )
    ]

    metrics: dict[str, dict[str, float | int]] = {}
    if multi_agent_values:
        metrics["multi_agent_ms"] = {
            "count": len(multi_agent_values),
            "p50_ms": _pctl(multi_agent_values, 50),
            "p95_ms": _pctl(multi_agent_values, 95),
            "max_ms": round(max(multi_agent_values), 3),
            "over_timeout_count": sum(1 for value in multi_agent_values if value >= timeout_ms),
            "over_timeout_rate": _rate(
                sum(1 for value in multi_agent_values if value >= timeout_ms),
                len(multi_agent_values),
            ),
        }

    return {
        "status": "ok",
        "count": len(rows),
        "events": event_counts,
        "timeout_event_count": len(timeout_events),
        "timeout_event_rate": _rate(len(timeout_events), len(rows)),
        "metrics": metrics,
    }


def _summarize_desktop_inference(control_tower: Path) -> dict:
    rows = _tail_jsonl(control_tower / "logs" / "desktop_inference_latency.jsonl", 200)
    if not rows:
        return {"status": "missing_or_empty", "count": 0}

    groups: dict[str, list[dict]] = {}
    for row in rows:
        key = (
            f"{row.get('model_id', 'unknown')}|"
            f"{row.get('runtime', 'unknown')}|"
            f"{row.get('prompt_class', 'unknown')}"
        )
        groups.setdefault(key, []).append(row)

    summary: dict[str, dict] = {}
    for key, group_rows in groups.items():
        latencies = [
            float(row.get("latency_s") or 0.0)
            for row in group_rows
            if row.get("latency_s") is not None
        ]
        failures = [row for row in group_rows if not row.get("ok")]
        fallback_reasons = sorted(
            {str(row.get("fallback_reason")) for row in failures if row.get("fallback_reason")}
        )
        summary[key] = {
            "count": len(group_rows),
            "p50_s": _pctl(latencies, 50),
            "p95_s": _pctl(latencies, 95),
            "max_s": round(max(latencies), 3) if latencies else 0.0,
            "failure_rate": _rate(len(failures), len(group_rows)),
            "fallback_reasons": fallback_reasons[:10],
        }

    return {"status": "ok", "count": len(rows), "groups": summary}


def _verify_owner(owner_token: str) -> tuple[bool, str]:
    """Returns (authorized, error_message)."""
    expected = os.getenv("OWNER_API_TOKEN", "")
    if not expected:
        return False, "OWNER_API_TOKEN not configured in environment — cannot authorize destructive operation"
    if owner_token != expected:
        return False, "Invalid owner_token — destructive operation denied"
    return True, ""


# ── Read-only tools ───────────────────────────────────────────────────────────

def get_position_state(bot_id: str) -> dict:
    """Read the persisted position state file for a bot."""
    if bot_id not in BOT_MAP:
        return {"error": f"Unknown bot_id '{bot_id}'. Valid: {list(BOT_MAP)}"}

    symbol = SYMBOL_MAP[bot_id]
    state_path = CONTROL_TOWER / "logs" / f"{symbol.lower()}_position_state.json"
    if not state_path.exists():
        return {"bot": bot_id, "symbol": symbol, "state": "no_state_file", "flat": True}

    try:
        data = json.loads(state_path.read_text())
        return {
            "bot": bot_id,
            "symbol": symbol,
            "direction": data.get("direction"),
            "qty": int(data.get("qty", 0)),
            "entry_price": float(data.get("entry_price", 0)),
            "flat": bool(data.get("flat", data.get("qty", 0) == 0)),
            "timestamp": data.get("timestamp"),
        }
    except Exception as e:
        return {"bot": bot_id, "symbol": symbol, "error": f"Parse error: {e}"}


def get_bracket_status(bot_id: str) -> dict:
    """
    Parse the bot log to determine current bracket status.
    Returns bracket mode, order IDs, and stop/target prices.
    Mode: live | oso_only | none | unknown
    """
    if bot_id not in BOT_MAP:
        return {"error": f"Unknown bot_id '{bot_id}'. Valid: {list(BOT_MAP)}"}

    cfg = BOT_MAP[bot_id]
    log_path = CONTROL_TOWER / cfg["log"]

    if not log_path.exists():
        return {"bot": bot_id, "mode": "unknown", "detail": "Log file not found"}

    try:
        # Read last 6KB of log
        size = log_path.stat().st_size
        offset = max(0, size - 6144)
        with open(log_path, "rb") as f:
            f.seek(offset)
            tail = f.read().decode("utf-8", errors="replace")
    except Exception as e:
        return {"bot": bot_id, "mode": "unknown", "error": str(e)}

    lines = tail.split("\n")
    lines.reverse()

    for line in lines:
        if "NO AUTO-BRACKETS" in line or "no_brackets" in line:
            return {"bot": bot_id, "mode": "none", "label": "⚠ No brackets placed — position unprotected", "unprotected": True}
        if "OSO Order ID:" in line or "OSO bracket" in line:
            oso_m = re.search(r"OSO Order ID:\s*(\d+)", line)
            return {"bot": bot_id, "mode": "oso_only", "oso_order_id": oso_m.group(1) if oso_m else None,
                    "label": "🟡 OSO linked (atomic bracket)", "unprotected": False}
        stop_m = re.search(r"[Ss]top order(?:Id|ID)[:=]\s*(\d+)", line)
        tgt_m  = re.search(r"[Tt]arget order(?:Id|ID)[:=]\s*(\d+)", line)
        if stop_m or tgt_m:
            stop_p = re.search(r"[Ss]top.*?\$([\d.]+)", line)
            tgt_p  = re.search(r"[Tt]arget.*?\$([\d.]+)", line)
            return {
                "bot": bot_id, "mode": "live",
                "stop_order_id": stop_m.group(1) if stop_m else None,
                "target_order_id": tgt_m.group(1) if tgt_m else None,
                "stop_price": float(stop_p.group(1)) if stop_p else None,
                "target_price": float(tgt_p.group(1)) if tgt_p else None,
                "label": f"🟢 Stop {stop_m.group(1) if stop_m else '?'} / Target {tgt_m.group(1) if tgt_m else '?'}",
                "unprotected": False,
            }

    pos = get_position_state(bot_id)
    if pos.get("flat"):
        return {"bot": bot_id, "mode": "flat", "label": "FLAT — no active position", "unprotected": False}
    return {"bot": bot_id, "mode": "unknown", "label": "Could not determine bracket status from recent logs", "unprotected": None}


def get_ai_pipeline_health(bot_id: str = "mnq") -> dict:
    """
    Detect AI ensemble/pipeline health from bot logs.
    Checks for: Anthropic quota errors, Cerebras model errors, pipeline timeout events, shadow mode.
    """
    if bot_id not in BOT_MAP:
        return {"error": f"Unknown bot_id. Valid: {list(BOT_MAP)}"}

    timeout_s, timeout_source = _pipeline_timeout_config(CONTROL_TOWER)
    cfg = BOT_MAP[bot_id]
    log_path = CONTROL_TOWER / cfg["log"]
    if not log_path.exists():
        return {
            "bot": bot_id,
            "status": "unknown",
            "detail": "Log file not found",
            "pipeline_timeout_config_s": timeout_s,
            "pipeline_timeout_config_source": timeout_source,
            "decision_latency": _summarize_decision_latency(CONTROL_TOWER, timeout_s),
            "desktop_inference": _summarize_desktop_inference(CONTROL_TOWER),
        }

    try:
        size = log_path.stat().st_size
        offset = max(0, size - 10240)
        with open(log_path, "rb") as f:
            f.seek(offset)
            tail = f.read().decode("utf-8", errors="replace")
    except Exception as e:
        return {"bot": bot_id, "status": "unknown", "error": str(e)}

    anthropic_error = bool(re.search(r"insufficient_quota|credit balance|overloaded_error|529", tail, re.I))
    cerebras_error  = bool(re.search(r"llama3\.3.*not found|model.*404|cerebras.*error", tail, re.I))
    timeout_event   = bool(re.search(r"Pipeline timed out|multi_agent_timeout", tail))
    shadow_mode     = bool(re.search(r"shadow.?mode|shadow_mode.*True", tail, re.I))
    ensemble_active = bool(re.search(r"AI APPROVED|Multi-agent APPROVED", tail))
    ensemble_reject = bool(re.search(r"AI REJECTED|advisory REJECTED", tail))
    debate_timeout  = re.findall(r"(\d+\.?\d*)s[).] pipeline|pipeline.*?(\d+\.?\d*)s", tail)
    timeout_samples = [
        float(value)
        for match in debate_timeout
        for value in match
        if value
    ]
    decision_latency = _summarize_decision_latency(CONTROL_TOWER, timeout_s)
    desktop_inference = _summarize_desktop_inference(CONTROL_TOWER)

    mode = "unknown"
    if shadow_mode or timeout_event:
        mode = "shadow_timeout"
    elif ensemble_active:
        mode = "active"
    elif anthropic_error or cerebras_error:
        mode = "degraded"

    return {
        "bot": bot_id,
        "mode": mode,
        "advisory_only": True,
        "blocks_trades": False,
        "anthropic_quota_error": anthropic_error,
        "cerebras_model_error": cerebras_error,
        "pipeline_timeout_detected": timeout_event,
        "shadow_mode_active": shadow_mode,
        "last_ensemble_approved": ensemble_active,
        "last_ensemble_rejected": ensemble_reject,
        "pipeline_timeout_config_s": timeout_s,
        "pipeline_timeout_config_source": timeout_source,
        "recent_timeout_samples_s": timeout_samples[-10:],
        "decision_latency": decision_latency,
        "desktop_inference": desktop_inference,
        "cerebras_model": "llama3.1-8b",
        "note": (
            "Anthropic credits zero — top up console.anthropic.com to restore 7-AI voting"
            if anthropic_error else
            "Pipeline healthy" if mode == "active" else
            "Pipeline in shadow/timeout mode — all trades use primary confidence gate only"
        ),
    }


def get_all_bot_ops_status() -> dict:
    """Get bracket status, position state, and process status for all 4 bots."""
    result = {}
    import re as _re
    ps_output = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=5).stdout
    for bot_id, cfg in BOT_MAP.items():
        running = any(cfg["grep"] in line and "grep" not in line for line in ps_output.splitlines())
        pid = None
        for line in ps_output.splitlines():
            if cfg["grep"] in line and "grep" not in line:
                parts = line.split()
                if len(parts) > 1:
                    try: pid = int(parts[1])
                    except ValueError: pass
                break
        result[bot_id] = {
            "running": running,
            "pid": pid,
            "symbol": SYMBOL_MAP[bot_id],
            "position": get_position_state(bot_id),
            "bracket": get_bracket_status(bot_id),
        }
    result["pipeline_health"] = get_ai_pipeline_health("mnq")
    return result


# ── V2: Bracket integrity tools ───────────────────────────────────────────────

def check_unprotected_positions() -> dict:
    """
    Cross-check open positions vs working orders to detect unprotected exposure.
    An unprotected position (open, no stop/target orders) caused the Apr 14 2026
    -$4,917 incident. Run this before any P&L report or status check.

    Returns status: OK | UNPROTECTED_EXPOSURE | ERROR
    """
    import sys
    ct = str(CONTROL_TOWER)
    if ct not in sys.path:
        sys.path.insert(0, ct)
    try:
        from dotenv import load_dotenv
        load_dotenv(CONTROL_TOWER / ".env")
    except ImportError:
        pass

    try:
        from tradovate_client import TradovateClient
    except ImportError as e:
        return {"error": f"Cannot import tradovate_client: {e}", "status": "ERROR"}

    # BUG-05 FIX: Never silently default to "demo" — wrong environment means silently
    # checking the wrong book and reporting "OK" when live is exposed.
    _env = os.getenv("TRADOVATE_ENV")
    if not _env:
        return {
            "error": "TRADOVATE_ENV not set — cannot determine which account to check",
            "status": "CONFIG_ERROR",
            "action": "Set TRADOVATE_ENV=demo or TRADOVATE_ENV=live in .env",
        }

    try:
        client = TradovateClient(
            cid=os.getenv("TRADOVATE_CID"),
            secret=os.getenv("TRADOVATE_SECRET"),
            env=_env,
        )
        client.authenticate()
        positions = client.get_positions()
        working_orders = client.get_working_orders()
        if positions is None or working_orders is None:
            return {
                "error": "Tradovate API returned no data — positions or orders call failed",
                "status": "ERROR",
                "positions_ok": positions is not None,
                "orders_ok": working_orders is not None,
            }
    except Exception as e:
        return {"error": f"Tradovate connection failed: {e}", "status": "ERROR"}

    # Only stop-type orders actually protect a position
    _STOP_TYPES = {"Stop", "StopLimit", "TrailingStop", "MIT"}
    covered = set()
    for o in working_orders:
        if o.get("orderType", "") not in _STOP_TYPES:
            continue
        cid = o.get("contractId") or (o.get("contract") or {}).get("id")
        if cid is not None:
            try:
                covered.add(int(cid))
            except (TypeError, ValueError):
                covered.add(cid)

    unprotected = []
    protected = []
    for p in positions:
        net = p.get("netPos", 0)
        if net == 0:
            continue
        raw_cid = p.get("contractId")
        try:
            cid = int(raw_cid) if raw_cid is not None else None
        except (TypeError, ValueError):
            cid = raw_cid
        entry = {"contractId": raw_cid, "contractName": p.get("contractName"), "netPos": net, "netPrice": p.get("netPrice")}
        if cid in covered:
            protected.append(entry)
        else:
            unprotected.append(entry)

    return {
        "unprotected": unprotected,
        "protected": protected,
        "working_orders_count": len(working_orders),
        "all_flat": len([p for p in positions if p.get("netPos", 0) != 0]) == 0,
        "status": "UNPROTECTED_EXPOSURE" if unprotected else "OK",
        "message": (
            f"{len(unprotected)} unprotected position(s) — FLATTEN IMMEDIATELY" if unprotected
            else "All positions protected" if protected
            else "Account is flat"
        ),
        "environment": os.getenv("TRADOVATE_ENV", "demo").upper(),
    }


def get_bracket_guardian_status() -> dict:
    """
    Read the bracket integrity guardian state file.
    Returns whether the guardian is running, last check time, any unprotected positions
    it has flagged, and whether auto-flatten has fired.
    """
    state_path = CONTROL_TOWER / "state" / "bracket_guardian_state.json"
    if not state_path.exists():
        return {
            "guardian_active": False,
            "detail": "bracket_guardian_state.json not found — guardian may not be running",
            "action": "Load com.algochains.bracket-guardian plist to activate",
        }
    try:
        data = json.loads(state_path.read_text())
        unprotected_since = data.get("unprotected_since", {})
        last_check = data.get("last_check", "unknown")
        return {
            "guardian_active": True,
            "last_check": last_check,
            "positions_count": data.get("positions_count", 0),
            "working_orders_count": data.get("working_orders_count", 0),
            "currently_unprotected": list(unprotected_since.keys()),
            "unprotected_since": unprotected_since,
            "status": "ALERT" if unprotected_since else "OK",
        }
    except Exception as e:
        return {"guardian_active": False, "error": str(e)}


# ── Owner-gated destructive ops ───────────────────────────────────────────────

def restart_bot(bot_id: str, owner_token: str) -> dict:
    """
    Kill and restart a trading bot process.
    Requires owner_token matching OWNER_API_TOKEN env var.
    """
    authorized, err = _verify_owner(owner_token)
    if not authorized:
        return {"error": err, "authorized": False}

    if bot_id not in BOT_MAP:
        return {"error": f"Unknown bot_id '{bot_id}'. Valid: {list(BOT_MAP)}"}

    cfg = BOT_MAP[bot_id]
    script_path = CONTROL_TOWER / cfg["script"]
    if not script_path.exists():
        return {"error": f"Script not found at {script_path}", "bot": bot_id}

    # Kill existing
    killed_pids: list[int] = []
    ps = subprocess.run(["ps", "aux"], capture_output=True, text=True)
    for line in ps.stdout.splitlines():
        if cfg["grep"] in line and "grep" not in line:
            parts = line.split()
            if len(parts) > 1:
                try:
                    pid = int(parts[1])
                    os.kill(pid, signal.SIGKILL)
                    killed_pids.append(pid)
                except Exception:
                    pass

    time.sleep(1)

    # Restart
    log_path = CONTROL_TOWER / cfg["log"]
    log_handle = open(log_path, "a")
    proc = subprocess.Popen(
        ["python3", "-B", "-u", cfg["script"]],
        cwd=str(CONTROL_TOWER),
        stdout=log_handle,
        stderr=log_handle,
        start_new_session=True,
    )

    time.sleep(2)
    # Verify
    verify = subprocess.run(["ps", "aux"], capture_output=True, text=True)
    verified_pid = None
    for line in verify.stdout.splitlines():
        if cfg["grep"] in line and "grep" not in line:
            parts = line.split()
            if len(parts) > 1:
                try: verified_pid = int(parts[1])
                except ValueError: pass
            break

    return {
        "status": "restarted" if verified_pid else "restart_failed",
        "bot": bot_id,
        "symbol": SYMBOL_MAP[bot_id],
        "killed_pids": killed_pids,
        "new_pid": verified_pid,
        "log_file": str(log_path),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "note": "Verify position is flat on Tradovate before restarting to avoid phantom position tracking",
    }


def flatten_position_tradovate(symbol: str, owner_token: str) -> dict:
    """
    Flatten ALL open contracts for a symbol via Tradovate Buy/Sell MKT.
    Requires owner_token. Also marks the position_state.json as flat.

    CRITICAL: Call get_accounts() before place_order() — account_id is None until then.
    """
    authorized, err = _verify_owner(owner_token)
    if not authorized:
        return {"error": err, "authorized": False}

    import sys
    ct = str(CONTROL_TOWER)
    if ct not in sys.path:
        sys.path.insert(0, ct)

    try:
        from dotenv import load_dotenv
        load_dotenv(CONTROL_TOWER / ".env")
    except ImportError:
        pass

    try:
        from tradovate_client import TradovateClient
    except ImportError as e:
        return {"error": f"Cannot import tradovate_client: {e}. Run from control-tower venv."}

    cid  = os.getenv("TRADOVATE_CID")
    sec  = os.getenv("TRADOVATE_SECRET")
    env  = os.getenv("TRADOVATE_ENV", "demo")

    try:
        client = TradovateClient(cid, sec, env)
        client.get_accounts()  # CRITICAL: must call to populate account_id
        positions = client.get_positions() or []
    except Exception as e:
        return {"error": f"Tradovate connection failed: {e}"}

    # V2 FIX: Previous code had `str(cid).startswith(str(cid))` which is ALWAYS TRUE,
    # matching any first position regardless of symbol. Now we:
    #   1. First try matching on contractName (best signal when available)
    #   2. Fall back to first non-flat position (contractId is numeric, not symbol-based)
    target = None
    for p in positions:
        if p.get("netPos", 0) == 0:
            continue
        contract_name = str(p.get("contractName", "") or "")
        if symbol.upper() in contract_name.upper():
            target = p
            break

    if not target:
        # Fall back: take first non-flat position (single-symbol accounts only)
        for p in positions:
            if p.get("netPos", 0) != 0:
                target = p
                break

    if not target or target.get("netPos", 0) == 0:
        return {"status": "already_flat", "symbol": symbol, "positions_checked": len(positions)}

    net = int(target["netPos"])
    close_action = "Buy" if net < 0 else "Sell"
    close_qty = abs(net)

    contract_info = client.find_contract(symbol)
    if not contract_info:
        return {"error": f"Contract not found for symbol {symbol}"}

    cid_int = contract_info["id"] if isinstance(contract_info, dict) else contract_info
    cname   = contract_info.get("name", symbol) if isinstance(contract_info, dict) else symbol

    try:
        result = client.place_order(
            contract_id=cid_int,
            action=close_action,
            qty=close_qty,
            order_type="Market",
            full_contract_name=cname,
            symbol=symbol,
        )
    except Exception as e:
        return {"error": f"Order placement failed: {e}", "symbol": symbol}

    if result:
        # Mark position state flat
        bot_id = symbol.lower()[:3]
        state_path = CONTROL_TOWER / "logs" / f"{symbol.lower()}_position_state.json"
        try:
            with open(state_path, "w") as sf:
                json.dump({
                    "bot": symbol, "symbol": symbol, "direction": None, "qty": 0,
                    "entry_price": 0, "timestamp": datetime.now(timezone.utc).isoformat(),
                    "flat": True,
                }, sf, indent=2)
        except Exception:
            pass

        return {
            "status": "flattened",
            "symbol": symbol,
            "qty": close_qty,
            "action": close_action,
            "order_id": result.get("orderId"),
            "net_was": net,
            "environment": env,
            "state_file_updated": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    return {"error": "Order placement returned null — check Tradovate account", "symbol": symbol, "qty_attempted": close_qty}
