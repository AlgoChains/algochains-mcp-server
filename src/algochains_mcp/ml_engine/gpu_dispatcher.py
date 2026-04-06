"""Route compute to Mac M3 Max or Desktop RTX via Tailscale."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from typing import Any


GPU_DISPATCH_CONFIG = {
    "mac": {
        "device": "mps",
        "max_batch": 4096,
        "models": ["xgboost", "ensemble"],
    },
    "desktop": {
        "host": "100.99.127.119",
        "device": "cuda",
        "max_batch": 32768,
        "models": ["lstm", "transformer", "rl"],
        "transfer": "rsync -avz --progress",
    },
}


class GPUDispatcher:
    """Route compute to Mac M3 Max or Desktop RTX via Tailscale."""

    def __init__(self) -> None:
        self._platform = "desktop" if sys.platform == "linux" else "mac"

    async def dispatch(self, task_type: str, payload: dict, prefer_gpu: str = "auto") -> dict:
        try:
            config = GPU_DISPATCH_CONFIG
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
            mac_status = {
                "platform": "mac",
                "device": "mps",
                "available": sys.platform == "darwin",
            }
            desktop_status = {
                "platform": "desktop",
                "host": GPU_DISPATCH_CONFIG["desktop"]["host"],
                "device": "cuda",
                "available": False,
                "note": "Check via SSH: ssh 100.99.127.119 nvidia-smi",
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
