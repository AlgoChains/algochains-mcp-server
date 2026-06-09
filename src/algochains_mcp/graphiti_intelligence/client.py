"""Bridge from the MCP server to the control-tower Graphiti client.

ISOLATION CONTRACT (mirrors the `ac graphiti` CLI): graphiti-core lives ONLY in the
control-tower's `.venv-graphiti` (Python 3.13). To keep it out of the MCP server's
interpreter entirely, this bridge SHELLS OUT to
`.venv-graphiti/bin/python -m intelligence_platform.graphiti_cli <cmd>` and parses the
single JSON object it prints on stdout. We never `import graphiti_core` (or the
control-tower client that imports it) in-process.

Fails closed with a typed graphiti_unavailable payload when the venv / Neo4j are
absent on this host — mirroring the codegraph_index_missing convention.

ADVISORY ONLY. agent_memory authority. Never a trading dependency.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

_RECOVERY = (
    "bash scripts/setup_graphiti_env.sh && "
    "docker compose -f docker/docker-compose.graphiti.yml up -d"
)

_DEFAULT_TIMEOUT = float(os.environ.get("GRAPHITI_BRIDGE_TIMEOUT", "120"))


class GraphitiBridgeError(RuntimeError):
    """Raised when the control-tower Graphiti venv cannot be located/run."""


def _control_tower() -> Path:
    for var in ("ALGOCHAINS_CONTROL_TOWER", "ALGOCHAINS_CONTROL_TOWER_PATH"):
        val = os.environ.get(var)
        if val:
            return Path(val)
    try:
        sibling = Path(__file__).resolve().parents[4] / "algochains-control-tower"
        if sibling.exists():
            return sibling
    except Exception:
        pass
    return Path("/Users/treycsa/CascadeProjects/algochains-control-tower")


def _venv_python() -> Path:
    return _control_tower() / ".venv-graphiti" / "bin" / "python"


def _unavailable(error: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error_kind": "graphiti_unavailable",
        "error": error,
        "recovery_command": _RECOVERY,
        "note": "Graphiti is advisory (agent_memory) — execution facts still require broker verification.",
    }


async def _run_cli(args: list[str], *, timeout: float = _DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Invoke `.venv-graphiti/bin/python -m intelligence_platform.graphiti_cli <args>`.

    Returns the parsed JSON payload, or a typed graphiti_unavailable dict on any
    failure (missing venv, non-JSON output, timeout, crash). Never raises.
    """
    py = _venv_python()
    if not py.exists():
        return _unavailable(
            f".venv-graphiti not found at {py}. Graphiti runs in an isolated Python 3.13 "
            "venv on the control-tower host (per-host, not synced)."
        )
    cwd = _control_tower()
    try:
        proc = await asyncio.create_subprocess_exec(
            str(py), "-m", "intelligence_platform.graphiti_cli", *args,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as exc:  # noqa: BLE001
        return _unavailable(f"could not launch graphiti venv: {exc}")
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return _unavailable(f"graphiti CLI timed out after {timeout}s")
    out = (out_b or b"").decode("utf-8", "replace").strip()
    err = (err_b or b"").decode("utf-8", "replace").strip()
    if not out:
        return _unavailable(f"graphiti CLI produced no output (stderr: {err[:400]})")
    try:
        return json.loads(out)
    except (ValueError, TypeError):
        return _unavailable(f"graphiti CLI returned non-JSON: {out[:400]}")


async def graphiti_health() -> dict[str, Any]:
    return await _run_cli(["health"])


async def graphiti_search(query: str, limit: int = 10) -> dict[str, Any]:
    if not query.strip():
        return {"ok": False, "error_kind": "usage", "error": "query is required"}
    return await _run_cli(["search", query, "--limit", str(int(limit))])


async def graphiti_temporal_query(query: str, limit: int = 10) -> dict[str, Any]:
    if not query.strip():
        return {"ok": False, "error_kind": "usage", "error": "query is required"}
    return await _run_cli(["temporal", query, "--limit", str(int(limit))])


async def graphiti_add_episode(name: str, body: str, source_description: str = "mcp_tool",
                               source_kind: str = "text") -> dict[str, Any]:
    if not name.strip() or not body.strip():
        return {"ok": False, "error_kind": "usage", "error": "name and body are required"}
    # body is redacted inside the control-tower client before ingest.
    return await _run_cli([
        "add-episode", name, body,
        "--source-description", source_description,
        "--source-kind", source_kind,
    ])
