from __future__ import annotations

import asyncio
import json
import os
import time
from types import SimpleNamespace

from algochains_mcp.bot_log_paths import resolve_bot_log
from algochains_mcp.trading_system_health import get_system_health


def _ps_line(pid: int, command: str) -> str:
    return f"trey {pid} 0.0 0.1 123 456 ?? S 04:00 0:00 {command}"


def _all_bots_ps(extra: str = "") -> str:
    lines = [
        "USER PID %CPU %MEM VSZ RSS TTY STAT START TIME COMMAND",
        _ps_line(101, "/usr/bin/python3 FUTURES_SCALPER_UPGRADED.py"),
        _ps_line(102, "/usr/bin/python3 CL_FUTURES_SCALPER.py"),
        _ps_line(103, "/usr/bin/python3 mes_swing_live.py"),
        _ps_line(104, "/usr/bin/python3 nq_swing_live.py"),
        _ps_line(105, "/usr/bin/python3 kalshi_daemon.py"),
    ]
    if extra:
        lines.append(extra)
    return "\n".join(lines)


def _decode_tool_result(result) -> dict:
    text = result[0].text if hasattr(result[0], "text") else str(result[0])
    return json.loads(text)


def test_resolve_bot_log_prefers_fresh_canonical_over_stale_legacy(tmp_path):
    root = tmp_path / "tower"
    logs = root / "logs"
    logs.mkdir(parents=True)
    canonical = logs / "cl_futures_live.log"
    legacy = logs / "cl_bot_live.log"
    canonical.write_text("fresh heartbeat\n", encoding="utf-8")
    legacy.write_text("stale\n", encoding="utf-8")

    now = time.time()
    canonical.touch()
    stale_time = now - 600
    os.utime(legacy, (stale_time, stale_time))

    resolved = resolve_bot_log(root, "cl", now=now)

    assert resolved["path"] == canonical
    assert resolved["legacy_stale_mismatch"] is True
    assert resolved["log_fresh"] is True


def test_system_health_flags_legacy_false_positive_not_critical(tmp_path):
    root = tmp_path / "tower"
    logs = root / "logs"
    logs.mkdir(parents=True)
    canonical = logs / "cl_futures_live.log"
    legacy = logs / "cl_bot_live.log"
    canonical.write_text("CL heartbeat\n", encoding="utf-8")
    legacy.write_text("old\n", encoding="utf-8")

    now = time.time()
    canonical.touch()
    stale_time = now - 600
    os.utime(legacy, (stale_time, stale_time))

    ps_output = _all_bots_ps()

    payload = get_system_health(control_tower=root, ps_output=ps_output, now=now)

    assert payload["bots"]["cl"]["active"] is True
    assert payload["bots"]["cl"]["legacy_stale_mismatch"] is True
    assert any("false inactive" in issue for issue in payload["issues"])
    assert not any(
        "cl_bot_live.log" in issue and "inactive" in issue.lower()
        for issue in payload["critical_issues"]
    )
    assert payload["status"] == "degraded"
    assert payload["effective_critical_issues"] == []
    assert payload["false_positive_count"] >= 1
    assert "legacy log false positive" in payload["summary"]
    assert payload["formatted_line"].startswith("[DEGRADED]")


def test_system_health_reconciles_snapshot_cl_false_positive_with_real_disk(tmp_path, monkeypatch):
    root = tmp_path / "tower"
    logs = root / "logs"
    logs.mkdir(parents=True)
    canonical = logs / "cl_futures_live.log"
    legacy = logs / "cl_bot_live.log"
    canonical.write_text("CL heartbeat\n", encoding="utf-8")
    legacy.write_text("old\n", encoding="utf-8")
    snapshot = logs / "health_snapshot.json"
    snapshot.write_text(
        json.dumps(
            {
                "critical_issues": [
                    "Bot appears inactive in cl_bot_live.log",
                    "Disk space critical: 1% free",
                ]
            }
        ),
        encoding="utf-8",
    )

    now = time.time()
    canonical.touch()
    stale_time = now - 600
    os.utime(legacy, (stale_time, stale_time))

    class _Usage:
        total = 100
        used = 99
        free = 1

    monkeypatch.setattr(
        "algochains_mcp.trading_system_health.shutil.disk_usage",
        lambda _p: _Usage(),
    )

    ps_output = _all_bots_ps()

    payload = get_system_health(control_tower=root, ps_output=ps_output, now=now)

    assert payload["snapshot_reconciliation"]["cl_legacy_inactive_false_positive"] is True
    assert payload["status"] == "failed"
    disk_critical = [
        issue for issue in payload["effective_critical_issues"] if "Disk space critical" in issue
    ]
    assert len(disk_critical) >= 1
    assert not any(
        "cl_bot_live.log" in issue and "inactive" in issue.lower()
        for issue in payload["effective_critical_issues"]
    )
    assert payload["sev1_eligible"] is True
    assert payload["false_positive_count"] >= 1
    assert "legacy log false positive" in payload["summary"]


def test_system_health_reports_critical_disk(tmp_path, monkeypatch):
    root = tmp_path / "tower"
    root.mkdir()

    class _Usage:
        total = 100
        used = 99
        free = 1

    monkeypatch.setattr(
        "algochains_mcp.trading_system_health.shutil.disk_usage",
        lambda _p: _Usage(),
    )

    payload = get_system_health(control_tower=root, ps_output="", now=time.time())

    assert payload["status"] == "failed"
    assert any("Disk space critical" in issue for issue in payload["critical_issues"])


def test_get_system_health_registered_and_callable(monkeypatch, tmp_path):
    import algochains_mcp.server as srv
    import algochains_mcp.trading_system_health as health_mod

    root = tmp_path / "tower"
    logs = root / "logs"
    logs.mkdir(parents=True)
    (logs / "futures_bot_live.log").write_text("mnq ok\n", encoding="utf-8")

    monkeypatch.setenv("ALGOCHAINS_CONTROL_TOWER", str(root))

    def fake_run(*_args, **_kwargs):
        return SimpleNamespace(stdout="USER PID COMMAND\n")

    monkeypatch.setattr(health_mod.subprocess, "run", fake_run)

    assert "get_system_health" in {tool.name for tool in srv.TOOLS_ANNOTATED}
    assert "get_system_health" in {tool.name for tool in srv.TOOLS_TIER1}

    result = asyncio.run(srv.call_tool("get_system_health", {}))
    payload = _decode_tool_result(result)

    assert payload["component"] == "trading-system-health"
    assert "bots" in payload
