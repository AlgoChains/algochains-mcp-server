from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from algochains_mcp.live_bot_intelligence import heartbeat


def test_count_running_bots_includes_kalshi_daemon():
    ps_output = "\n".join(
        [
            "python /tower/autonomous/FUTURES_SCALPER_UPGRADED.py",
            "python3 -u /tower/autonomous/CL_FUTURES_SCALPER.py",
            "/home/trrey/.venv/bin/python /tower/autonomous/mes_swing_live.py",
            "/tower/autonomous/nq_swing_live.py",
            "python /tower/autonomous/kalshi_daemon.py",
        ]
    )

    with patch(
        "algochains_mcp.live_bot_intelligence.heartbeat.subprocess.run",
        return_value=SimpleNamespace(stdout=ps_output),
    ) as run:
        assert heartbeat._count_running_bots() == 5

    run.assert_called_once_with(["ps", "-eo", "args="], capture_output=True, text=True, timeout=5)


def test_count_running_bots_ignores_shell_and_search_mentions():
    ps_output = "\n".join(
        [
            "rg FUTURES_SCALPER /tower",
            "grep CL_FUTURES_SCALPER /tmp/ps.txt",
            "bash -c 'echo mes_swing_live.py nq_swing_live.py kalshi_daemon.py'",
            "python -c 'print(\"FUTURES_SCALPER_UPGRADED.py\")'",
        ]
    )

    with patch(
        "algochains_mcp.live_bot_intelligence.heartbeat.subprocess.run",
        return_value=SimpleNamespace(stdout=ps_output),
    ):
        assert heartbeat._count_running_bots() == 0
