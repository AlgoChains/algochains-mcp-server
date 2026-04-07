"""
heartbeat.py — System heartbeat awareness for MCP server.

Reads the Mac heartbeat file to determine:
  - Is MacBook alive? (heartbeat age < 15 min)
  - Is this server running as primary (Mac offline) or standby (Mac online)?
  - How many bots are running locally?

This enables the MCP server to self-identify its role in the dual-node setup.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


_HEARTBEAT_PATHS = [
    Path("/mnt/c/Users/trrey/mac_heartbeat.json"),   # Desktop WSL → Windows path
    Path("/home/trrey/mac_heartbeat.json"),            # Desktop WSL home
    Path("/Users/treycsa/CascadeProjects/algochains-control-tower/scripts/mac_heartbeat.json"),  # Mac
]


@dataclass
class SystemHeartbeat:
    # Mac state
    mac_alive: bool = False
    mac_last_seen_ago_sec: float = 0.0
    mac_bots_running: str = ""
    mac_heartbeat_source: str = ""
    # Desktop state
    desktop_mode: str = "unknown"  # "primary" | "standby" | "mac"
    desktop_bots_running: int = 0
    desktop_tailscale_active: bool = False
    # This node
    this_node: str = "unknown"  # "macbook" | "desktop"
    # System
    timestamp: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _read_heartbeat() -> tuple[dict, str]:
    """Read heartbeat JSON from first available path."""
    for path in _HEARTBEAT_PATHS:
        if path.exists():
            try:
                with open(path) as f:
                    data = json.load(f)
                return data, str(path)
            except (json.JSONDecodeError, OSError):
                continue
    return {}, ""


def _count_running_bots() -> int:
    """Count how many bot processes are running on this node."""
    try:
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True, text=True, timeout=5
        )
        count = 0
        for pattern in ["FUTURES_SCALPER", "CL_FUTURES", "mes_swing", "nq_swing"]:
            if pattern in result.stdout:
                count += 1
        return count
    except (subprocess.SubprocessError, FileNotFoundError):
        return 0


def _is_desktop() -> bool:
    """Detect if we're running on the desktop tower (WSL/Linux) vs Mac."""
    import platform
    system = platform.system()
    if system == "Linux":
        return True
    if system == "Darwin":
        return False
    return False


def _check_tailscale() -> bool:
    """Check if Tailscale is connected on this node."""
    try:
        result = subprocess.run(
            ["tailscale", "status"],
            capture_output=True, text=True, timeout=5
        )
        return "active" in result.stdout.lower() or result.returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def get_system_heartbeat() -> SystemHeartbeat:
    """
    Return system heartbeat state for this node.
    Indicates whether this server is the primary (Mac offline) or standby.
    """
    hb = SystemHeartbeat()
    hb.timestamp = datetime.now(timezone.utc).isoformat()

    # Read Mac heartbeat
    mac_data, source = _read_heartbeat()
    hb.mac_heartbeat_source = source
    if mac_data:
        mac_unix = mac_data.get("unix", 0)
        age = time.time() - mac_unix
        hb.mac_last_seen_ago_sec = round(age, 1)
        hb.mac_alive = age < 900  # 15 min threshold
        hb.mac_bots_running = mac_data.get("bots_running", "")

    # Determine this node identity
    hb.this_node = "desktop" if _is_desktop() else "macbook"

    # Determine role
    if hb.this_node == "macbook":
        hb.desktop_mode = "mac"  # We ARE the Mac
    elif hb.mac_alive:
        hb.desktop_mode = "standby"  # Mac is alive, desktop is backup
    else:
        hb.desktop_mode = "primary"  # Mac is offline, desktop is trading

    # Count local bots
    hb.desktop_bots_running = _count_running_bots()

    # Tailscale
    hb.desktop_tailscale_active = _check_tailscale()

    return hb
