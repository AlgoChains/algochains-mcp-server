"""ASSP MCP audit emit — delegates to control-tower core.assp.mcp_audit_emit when available."""

from __future__ import annotations

import json
import os
import sys
import threading
import uuid
from pathlib import Path
from typing import Any, Optional

_lock = threading.Lock()
_import_attempted = False
_delegate: Any = None

_RULE_META: dict[str, tuple[str, str]] = {
    "AC-MCP-001": ("unauthenticated_mcp", "sse_fail_open"),
    "AC-MCP-002": ("danger_gate_bypass", "danger_gate_bypass"),
    "AC-MCP-005": ("ssrf_blocked", "ssrf_blocked"),
    "AC-MCP-006": ("rate_limit_denied", "rate_limit_exceeded"),
    "AC-MCP-007": ("owner_gate_denied", "owner_token_required"),
    "AC-MCP-008": ("owner_gate_denied", "owner_token_required"),
    "AC-MCP-009": ("anonymous_ingest", "owner_token_required"),
    "AC-MCP-010": ("bot_ops_secret_leak", "owner_token_required"),
    "AC-MCP-sqlite-write": ("owner_gate_denied", "owner_token_required"),
    "AC-MCP-metrics-owner": ("owner_gate_denied", "owner_token_required"),
    "AC-MCP-rithmic-owner": ("owner_gate_denied", "owner_token_required"),
}


def _control_tower() -> Optional[Path]:
    for var in ("ALGOCHAINS_CONTROL_TOWER", "ALGOCHAINS_CONTROL_TOWER_PATH"):
        val = os.environ.get(var)
        if val:
            return Path(val)
    try:
        sibling = Path(__file__).resolve().parents[3] / "algochains-control-tower"
        if sibling.exists():
            return sibling
    except Exception:
        pass
    return None


def _ensure_delegate() -> Any:
    global _import_attempted, _delegate
    if _import_attempted:
        return _delegate
    _import_attempted = True
    ct = _control_tower()
    if ct:
        ct_str = str(ct)
        if ct_str not in sys.path:
            sys.path.insert(0, ct_str)
        try:
            from core.assp import mcp_audit_emit as mod  # noqa: WPS433

            _delegate = mod
            return _delegate
        except Exception:
            pass
    _delegate = None
    return None


def _audit_paths() -> tuple[Path, ...]:
    mod = _ensure_delegate()
    if mod and hasattr(mod, "mcp_audit_paths"):
        return mod.mcp_audit_paths()
    ct = _control_tower()
    paths: list[Path] = []
    if ct:
        paths.append(ct / "artifacts" / "assp" / "intent_bus" / "mcp_audit.jsonl")
    paths.append(Path.home() / ".algochains" / "assp_mcp_audit.jsonl")
    return tuple(paths)


def _detect_node_id() -> str:
    mod = _ensure_delegate()
    if mod and hasattr(mod, "detect_node_id"):
        from core.assp.security_schema import detect_node_id

        return detect_node_id()
    user = os.environ.get("USER", "")
    if user == "treycsa":
        return "mac"
    if "michael" in user:
        return "michael-mini"
    return "sonia-air"


def _fallback_build_event(
    *,
    rule_id: str,
    tool_name: str,
    deny_reason: str,
    correlation_id: str,
    **extras: Any,
) -> dict[str, Any]:
    event_name = _RULE_META.get(rule_id, ("owner_gate_denied", "denied"))[0]
    default_deny = _RULE_META.get(rule_id, (event_name, "denied"))[1]
    from datetime import datetime, timezone

    evt: dict[str, Any] = {
        "event_id": str(uuid.uuid4()),
        "correlation_id": correlation_id,
        "node_id": _detect_node_id(),
        "lane": "assp_prod",
        "authority": "agent_memory",
        "provenance": "direct_mirror",
        "severity": "high",
        "sourcetype": "algochains:mcp_audit",
        "ts": datetime.now(timezone.utc).isoformat(),
        "event_type": "mcp_audit",
        "message": f"mcp deny rule={rule_id} tool={tool_name}",
        "tool_name": tool_name,
        "rule_id": rule_id,
        "intent_allowed": False,
        "deny_reason": deny_reason or default_deny,
        "event": event_name,
    }
    evt.update(extras)
    return evt


def _append_jsonl(row: dict[str, Any]) -> None:
    line = json.dumps(row, default=str, ensure_ascii=True) + "\n"
    with _lock:
        for path in _audit_paths():
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as fh:
                    fh.write(line)
            except Exception:
                continue


def emit_mcp_audit_denial(
    *,
    rule_id: str,
    tool_name: str,
    deny_reason: str = "",
    correlation_id: Optional[str] = None,
    session_id: str = "mcp-server",
    **extras: Any,
) -> Optional[dict[str, Any]]:
    """Emit algochains:mcp_audit denial. Never raises."""
    try:
        mod = _ensure_delegate()
        if mod:
            return mod.emit_mcp_audit_denial(
                rule_id=rule_id,
                tool_name=tool_name,
                deny_reason=deny_reason,
                correlation_id=correlation_id,
                session_id=session_id,
                **extras,
            )
        corr = correlation_id or str(uuid.uuid4())
        evt = _fallback_build_event(
            rule_id=rule_id,
            tool_name=tool_name,
            deny_reason=deny_reason,
            correlation_id=corr,
            session_id=session_id,
            **extras,
        )
        _append_jsonl(evt)
        return evt
    except Exception:
        return None
