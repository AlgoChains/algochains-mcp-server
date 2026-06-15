from __future__ import annotations

from types import SimpleNamespace

from algochains_mcp.live_bot_intelligence import heartbeat


def test_count_running_bots_includes_kalshi_daemon(monkeypatch):
    ps_output = "\n".join(
        [
            "ubuntu 100 0.0 python FUTURES_SCALPER_UPGRADED.py",
            "ubuntu 101 0.0 python CL_FUTURES_SCALPER.py",
            "ubuntu 102 0.0 python mes_swing_live.py",
            "ubuntu 103 0.0 python nq_swing_live.py",
            "ubuntu 104 0.0 python kalshi_daemon.py",
            "ubuntu 105 0.0 python kalshi_daemon.py --health-helper",
        ]
    )

    def fake_run(*_args, **_kwargs):
        return SimpleNamespace(stdout=ps_output, returncode=0)

    monkeypatch.setattr(heartbeat.subprocess, "run", fake_run)

    assert heartbeat._count_running_bots() == 5


def test_system_heartbeat_reports_five_desktop_bots_in_primary_mode(monkeypatch):
    monkeypatch.setattr(heartbeat, "_read_heartbeat", lambda: ({}, ""))
    monkeypatch.setattr(heartbeat, "_is_desktop", lambda: True)
    monkeypatch.setattr(heartbeat, "_count_running_bots", lambda: 5)
    monkeypatch.setattr(heartbeat, "_check_tailscale", lambda: True)

    result = heartbeat.get_system_heartbeat()

    assert result.this_node == "desktop"
    assert result.desktop_mode == "primary"
    assert result.desktop_bots_running == 5
    assert result.desktop_tailscale_active is True
