"""Reusable helpers for CLI and agent-facing MCP contracts."""
from __future__ import annotations

from typing import Any


def filter_manifest_tools(
    manifest: dict[str, Any],
    *,
    max_danger_tier: int | None = None,
    implementation_status: str | None = None,
) -> list[dict[str, Any]]:
    """Return manifest tools filtered without hiding implementation status."""
    tools = [tool for tool in manifest.get("tools", []) if isinstance(tool, dict)]
    if max_danger_tier is not None:
        tools = [tool for tool in tools if int(tool.get("danger_tier") or 0) <= max_danger_tier]
    if implementation_status is not None:
        tools = [tool for tool in tools if tool.get("implementation_status") == implementation_status]
    return sorted(tools, key=lambda tool: str(tool.get("name", "")))


def command_contract_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    """Summarize the MCP manifest for bounded CLI schema/agent output."""
    return {
        "schema_version": manifest.get("schema_version"),
        "tool_mode": manifest.get("tool_mode"),
        "total_tools": manifest.get("total_tools", len(manifest.get("tools", []))),
        "summary_by_status": dict(manifest.get("summary_by_status", {})),
    }
