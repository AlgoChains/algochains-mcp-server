"""Route compute across Mac M5, Desktop RTX tower, and Sonia's MacBook Air via Tailscale."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from typing import Any

try:
    from algochains_library.ops.compute_routing import (
        _SONIA_AIR_EXCLUDED_TASK_TERMS,
        compute_manifest,
        route_compute,
    )
except Exception:  # pragma: no cover - MCP can run without control-tower package installed
    compute_manifest = None  # type: ignore[assignment]
    route_compute = None  # type: ignore[assignment]
    _SONIA_AIR_EXCLUDED_TASK_TERMS = frozenset({  # type: ignore[assignment]
        "training", "optimization", "optuna", "rl", "transformer",
        "large_backtest", "lstm", "walk_forward", "retrain", "cuda",
    })


def _fallback_config() -> dict[str, dict[str, Any]]:
    return {
        "mac": {
            "device": "mps",
            "max_batch": 4096,
            "models": ["xgboost", "ensemble", "small_inference"],
        },
        "desktop": {
            # Configure via ALGOCHAINS_TOWER_HOST (Tailscale private IP). Not hard-coded.
            "host": os.environ.get("ALGOCHAINS_TOWER_HOST", ""),
            "device": "cuda",
            "max_batch": 32768,
            "models": ["lstm", "transformer", "rl", "optuna", "large_backtest"],
            "transfer": "rsync -avz --progress",
        },
        # Sonia's MacBook Air — Tailscale 100.109.159.111.
        # Practical MPS budget ~3–5 GB (16 GB unified, shared with macOS + daemons).
        # max_batch=512; XGBoost is CPU-only on Apple Silicon (no MPS).
        # Passively cooled: thermal throttle onset ~6 min sustained load.
        # NEVER route cuda/lstm/training/large_backtest to this node.
        "sonia_air": {
            "host": os.environ.get("ALGOCHAINS_SONIA_HOST", "100.109.159.111"),
            "device": "mps",
            "max_batch": 512,
            "models": ["small_inference", "event_polling", "cpu_predict"],
            "excluded_task_terms": list(_SONIA_AIR_EXCLUDED_TASK_TERMS),
            "transfer": "rsync -avz --progress",
            "note": (
                "Passively cooled; no CUDA; XGBoost CPU-only. "
                "Suitable for network-bound event polling and light CPU inference only."
            ),
        },
    }


class GPUDispatcher:
    """Route compute across Mac M5, Desktop RTX tower, and Sonia's MacBook Air via Tailscale."""

    def __init__(self) -> None:
        self._platform = "desktop" if sys.platform == "linux" else "mac"

    async def dispatch(self, task_type: str, payload: dict, prefer_gpu: str = "auto") -> dict:
        try:
            if route_compute is not None:
                estimated_memory = payload.get("estimated_memory_mb") if isinstance(payload, dict) else None
                routed = route_compute(task_type, prefer=prefer_gpu, estimated_memory_mb=estimated_memory)
                # route_compute returns status=error when sonia_air is requested for excluded tasks.
                if routed.get("status") == "error":
                    return routed
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
            task = (task_type or "").lower()
            # Fallback routing (no algochains_library available).
            normalized_prefer = prefer_gpu.lower() if prefer_gpu else "auto"
            if normalized_prefer in {"sonia_air", "sonia", "air"} and any(
                term in task for term in _SONIA_AIR_EXCLUDED_TASK_TERMS
            ):
                return {
                    "status": "error",
                    "error": (
                        f"task_type '{task_type}' is not permitted on sonia_air "
                        f"(passively cooled, no CUDA). Route to 'desktop' instead."
                    ),
                }
            if normalized_prefer in {"desktop", "tower", "cuda"}:
                target = "desktop"
            elif normalized_prefer == "mac":
                target = "mac"
            elif normalized_prefer in {"sonia_air", "sonia", "air"}:
                target = "sonia_air"
            elif task_type in config["desktop"]["models"]:
                target = "desktop"
            elif any(term in task for term in ("event_source_poll", "event_polling", "noaa", "usgs", "kalshi")):
                target = "sonia_air"
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
            nodes = {
                "mac": {
                    "platform": "mac",
                    "device": "mps",
                    "available": sys.platform == "darwin",
                },
                "desktop": {
                    "platform": "desktop",
                    "host": config["desktop"]["host"],
                    "device": "cuda",
                    "available": False,
                    "note": f"Check via SSH: ssh {config['desktop']['host']} nvidia-smi",
                },
                "sonia_air": {
                    "platform": "sonia_air",
                    "host": config["sonia_air"]["host"],
                    "device": "mps",
                    "available": False,
                    "note": (
                        f"Check via SSH: ssh {config['sonia_air']['host']} uptime  "
                        "| Passively cooled — no CUDA, no large_backtest"
                    ),
                },
            }
            return {
                "status": "ok",
                "local": nodes["mac"],
                "desktop": nodes["desktop"],
                "nodes": nodes,
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "source": "fallback_config",
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
