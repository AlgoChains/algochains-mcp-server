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
import shlex
import subprocess
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path


from algochains_mcp.paths import default_heartbeat_paths

# Canonical ordered candidate list (control-tower/scripts first, then legacy
# WSL/Windows/Ubuntu fallbacks). The control-tower path is where the Mac bot
# actually writes the heartbeat, so the prior Linux-first order was inverted
# for the desktop tower.
_HEARTBEAT_PATHS = default_heartbeat_paths()

# Desktop failover runs four Tradovate futures bots plus the Kalshi daemon.
EXPECTED_DESKTOP_BOT_COUNT = 5

BOT_SCRIPT_NAMES: dict[str, tuple[str, ...]] = {
    "mnq": ("FUTURES_SCALPER_UPGRADED.py", "FUTURES_SCALPER.py", "FUTURES_SCALPER"),
    "cl": ("CL_FUTURES_SCALPER.py", "CL_FUTURES_SCALPER"),
    "mes": ("mes_swing_live.py", "mes_swing_live"),
    "nq": ("nq_swing_live.py", "nq_swing_live"),
    "kalshi": ("kalshi_daemon.py", "kalshi_daemon"),
}


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
    desktop_bots_expected: int = EXPECTED_DESKTOP_BOT_COUNT
    desktop_bot_processes: dict[str, bool] = field(default_factory=dict)
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


def _command_from_ps_line(line: str) -> str:
    """Return the COMMAND column from a ps aux output line."""
    parts = line.split(None, 10)
    return parts[10] if len(parts) >= 11 else ""


def _command_tokens(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _is_python_eval(tokens: list[str]) -> bool:
    if not tokens:
        return False
    executable = Path(tokens[0]).name
    return executable.startswith("python") and "-c" in tokens[1:]


def _is_shell_eval(tokens: list[str]) -> bool:
    if not tokens:
        return False
    executable = Path(tokens[0]).name
    return executable in {"sh", "bash", "zsh"} and any(
        arg in {"-c", "-lc"} for arg in tokens[1:]
    )


def matching_bot_key(command: str) -> str | None:
    """Return the canonical bot key if a command is a live bot process."""
    tokens = _command_tokens(command)
    if not tokens or _is_python_eval(tokens) or _is_shell_eval(tokens):
        return None

    token_basenames = {Path(token).name for token in tokens}
    for bot_key, script_names in BOT_SCRIPT_NAMES.items():
        if token_basenames.intersection(script_names):
            return bot_key
    return None


def scan_running_bot_keys(ps_output: str | None = None) -> set[str]:
    """Return canonical bot keys currently running on this node."""
    if ps_output is None:
        try:
            result = subprocess.run(
                ["ps", "aux"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            ps_output = result.stdout
        except (subprocess.SubprocessError, FileNotFoundError):
            return set()

    running: set[str] = set()
    for line in ps_output.splitlines():
        command = _command_from_ps_line(line)
        bot_key = matching_bot_key(command)
        if bot_key:
            running.add(bot_key)
    return running


def _count_running_bots() -> int:
    """Count how many canonical bot processes are running on this node."""
    return len(scan_running_bot_keys())


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
    running = scan_running_bot_keys()
    hb.desktop_bots_running = len(running)
    hb.desktop_bots_expected = EXPECTED_DESKTOP_BOT_COUNT
    hb.desktop_bot_processes = {
        bot_key: bot_key in running for bot_key in BOT_SCRIPT_NAMES
    }

    # Tailscale
    hb.desktop_tailscale_active = _check_tailscale()

    return hb
