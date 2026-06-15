from __future__ import annotations

from types import SimpleNamespace

from algochains_mcp.live_bot_intelligence import heartbeat


def _ps_line(pid: int, command: str) -> str:
    return f"ubuntu {pid} 0.0 0.0 0 0 ? S 08:00 0:00 {command}"


def test_running_bot_ids_include_kalshi_daemon():
    ps_output = "\n".join(
        [
            _ps_line(101, "/usr/bin/python3 /ct/FUTURES_SCALPER_UPGRADED.py"),
            _ps_line(102, "/usr/bin/python3 /ct/CL_FUTURES_SCALPER.py"),
            _ps_line(103, "/usr/bin/python3 /ct/mes_swing_live.py"),
            _ps_line(104, "/usr/bin/python3 /ct/nq_swing_live.py"),
            _ps_line(105, "/usr/bin/python3 /ct/kalshi_daemon.py"),
        ]
    )

    assert heartbeat._running_bot_ids_from_ps(ps_output) == {
        "mnq",
        "cl",
        "mes",
        "nq",
        "kalshi",
    }


def test_running_bot_ids_ignore_shell_and_search_false_positives():
    ps_output = "\n".join(
        [
            _ps_line(
                201,
                "bash -lc 'rg FUTURES_SCALPER_UPGRADED.py CL_FUTURES_SCALPER.py "
                "mes_swing_live.py nq_swing_live.py kalshi_daemon.py'",
            ),
            _ps_line(202, "python3 -c 'print(\"kalshi_daemon.py\")'"),
            _ps_line(203, "rg kalshi_daemon.py /workspace"),
            _ps_line(204, "pgrep -f CL_FUTURES_SCALPER.py"),
        ]
    )

    assert heartbeat._running_bot_ids_from_ps(ps_output) == set()


def test_count_running_bots_deduplicates_process_signatures(monkeypatch):
    ps_output = "\n".join(
        [
            _ps_line(301, "/usr/bin/python3 /ct/FUTURES_SCALPER_UPGRADED.py"),
            _ps_line(302, "/usr/bin/python3 /ct/FUTURES_SCALPER_UPGRADED.py"),
            _ps_line(303, "/usr/bin/python3 /ct/kalshi_daemon.py"),
        ]
    )

    def fake_run(*_args, **_kwargs):
        return SimpleNamespace(stdout=ps_output)

    monkeypatch.setattr(heartbeat.subprocess, "run", fake_run)

    assert heartbeat._count_running_bots() == 2
