from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from algochains_mcp.live_bot_intelligence import heartbeat


def _ps_line(pid: int, command: str) -> str:
    return f"ubuntu {pid} 0.0 0.1 1000 100 ? S 09:00 0:00 {command}"


def test_count_running_bots_includes_kalshi_daemon():
    ps_output = "\n".join(
        [
            "USER PID %CPU %MEM VSZ RSS TTY STAT START TIME COMMAND",
            _ps_line(101, "python /opt/algo/FUTURES_SCALPER_UPGRADED.py"),
            _ps_line(102, "python /opt/algo/CL_FUTURES_SCALPER.py"),
            _ps_line(103, "python /opt/algo/mes_swing_live.py"),
            _ps_line(104, "python /opt/algo/nq_swing_live.py"),
            _ps_line(105, "python /opt/algo/autonomous/kalshi_daemon.py"),
        ]
    )

    with patch(
        "algochains_mcp.live_bot_intelligence.heartbeat.subprocess.run",
        return_value=SimpleNamespace(stdout=ps_output),
    ):
        assert heartbeat._count_running_bots() == 5


def test_count_running_bots_ignores_shell_search_false_positives():
    ps_output = "\n".join(
        [
            "USER PID %CPU %MEM VSZ RSS TTY STAT START TIME COMMAND",
            _ps_line(201, "python /opt/algo/CL_FUTURES_SCALPER.py"),
            _ps_line(202, "bash -lc rg FUTURES_SCALPER_UPGRADED.py /workspace"),
            _ps_line(203, "python -c print('kalshi_daemon.py')"),
            _ps_line(204, "sh -c ps aux | rg mes_swing_live.py"),
        ]
    )

    with patch(
        "algochains_mcp.live_bot_intelligence.heartbeat.subprocess.run",
        return_value=SimpleNamespace(stdout=ps_output),
    ):
        assert heartbeat._count_running_bots() == 1

