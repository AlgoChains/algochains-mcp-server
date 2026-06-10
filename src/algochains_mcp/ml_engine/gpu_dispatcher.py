"""Route compute to Mac M3 Max or Desktop RTX via Tailscale."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from typing import Any

try:
    from algochains_library.ops.compute_routing import compute_manifest, route_compute
except Exception:  # pragma: no cover - MCP can run without control-tower package installed
    compute_manifest = None  # type: ignore[assignment]
    route_compute = None  # type: ignore[assignment]


def _fallback_config() -> dict[str, dict[str, Any]]:
    return {
        "mac": {"device": "mps", "max_batch": 4096, "models": ["xgboost", "ensemble"]},
        "desktop": {
            # Configure your compute node via ALGOCHAINS_TOWER_HOST (e.g. a
            # private VPN/Tailscale address). No host is hard-coded.
            "host": os.environ.get("ALGOCHAINS_TOWER_HOST", ""),
            "device": "cuda",
            "max_batch": 32768,
            "models": ["lstm", "transformer", "rl", "optuna", "large_backtest"],
            "transfer": "rsync -avz --progress",
        },
    }


class GPUDispatcher:
    """Route compute to Mac M3 Max or Desktop RTX via Tailscale."""

    def __init__(self) -> None:
        self._platform = "desktop" if sys.platform == "linux" else "mac"

    async def dispatch(self, task_type: str, payload: dict, prefer_gpu: str = "auto") -> dict:
        try:
            if route_compute is not None:
                estimated_memory = payload.get("estimated_memory_mb") if isinstance(payload, dict) else None
                routed = route_compute(task_type, prefer=prefer_gpu, estimated_memory_mb=estimated_memory)
                node = routed["node"]
                return {
                    "status": "ok",
                    "target": routed["target"],
                    "device": node["device"],
                    "task_type": task_type,
                    "prefer_gpu": prefer_gpu,
                    "max_batch": node["max_batch"],
                    "dispatched_at": routed["routed_at"],
                    "routing_contract": routed["routing_contract"],
                }
            config = _fallback_config()
            if prefer_gpu == "desktop":
                target = "desktop"
            elif prefer_gpu == "mac":
                target = "mac"
            elif task_type in config["desktop"]["models"]:
                target = "desktop"
            else:
                target = self._platform
            return {
                "status": "ok",
                "target": target,
                "device": config[target]["device"],
                "task_type": task_type,
                "prefer_gpu": prefer_gpu,
                "max_batch": config[target]["max_batch"],
                "dispatched_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def status(self) -> dict:
        try:
            if compute_manifest is not None:
                manifest = compute_manifest()
                return {
                    "status": "ok",
                    "nodes": manifest["nodes"],
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "source": "algochains_library.ops.compute_routing",
                }
            config = _fallback_config()
            mac_status = {
                "platform": "mac",
                "device": "mps",
                "available": sys.platform == "darwin",
            }
            desktop_status = {
                "platform": "desktop",
                "host": config["desktop"]["host"],
                "device": "cuda",
                "available": False,
                "note": f"Check via SSH: ssh {config['desktop']['host']} nvidia-smi",
            }
            return {
                "status": "ok",
                "local": mac_status,
                "desktop": desktop_status,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def transfer_data(self, source: str, dest: str, files: list[str]) -> dict:
        try:
            return {
                "status": "ok",
                "source": source,
                "dest": dest,
                "files": files,
                "method": "rsync",
                "command": f"rsync -avz --progress {source} {dest}",
                "note": "NEVER use SSHFS/NFS over Tailscale — use rsync only",
                "queued_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}
