from __future__ import annotations

import asyncio
import json
import os
import time
from unittest.mock import patch

from algochains_mcp.latency_monitor_status import get_latency_monitor_status
from algochains_mcp.tradovate_token_status import resolve_tradovate_probe_token


def test_resolve_probe_token_prefers_live_file(tmp_path):
    root = tmp_path / "tower"
    root.mkdir()
    (root / "tradovate_token_live.txt").write_text("Bearer live-token\n", encoding="utf-8")
    (root / ".env").write_text("TRADOVATE_ACCESS_TOKEN=stale-env-token\n", encoding="utf-8")

    token, source = resolve_tradovate_probe_token(root)

    assert token == "live-token"
    assert source == "tradovate_token_live.txt"


def test_latency_monitor_flags_probe_mismatch_when_bots_fresh(tmp_path, monkeypatch):
    root = tmp_path / "tower"
    logs = root / "logs"
    logs.mkdir(parents=True)
    (logs / "futures_bot_live.log").write_text("mnq heartbeat\n", encoding="utf-8")
    (logs / "cl_futures_live.log").write_text("cl heartbeat\n", encoding="utf-8")
    (root / "tradovate_token_live.txt").write_text("Bearer valid-token\n", encoding="utf-8")

    now = time.time()
    for path in (logs / "futures_bot_live.log", logs / "cl_futures_live.log"):
        os.utime(path, (now - 10, now - 10))

    def _fake_probe(token: str, *, base_url: str, timeout_s: float = 10.0):
        assert token == "valid-token"
        return {"http_status": 401, "latency_ms": 105.0, "ok": False, "error": "Unauthorized"}

    with patch(
        "algochains_mcp.latency_monitor_status._probe_tradovate",
        side_effect=_fake_probe,
    ):
        payload = get_latency_monitor_status(control_tower=root, now=now)

    assert payload["status"] == "DEGRADED"
    assert payload["execution_layer_healthy"] is True
    assert "401" in payload["formatted_line"]
    assert "LOG_AGE MNQ=10s" in payload["formatted_line"]
    assert "LOG_AGE CL=10s" in payload["formatted_line"]
    assert any("monitor token/env mismatch" in issue for issue in payload["issues"])


def test_latency_monitor_ok_when_probe_succeeds(tmp_path, monkeypatch):
    root = tmp_path / "tower"
    logs = root / "logs"
    logs.mkdir(parents=True)
    (logs / "futures_bot_live.log").write_text("mnq heartbeat\n", encoding="utf-8")
    (logs / "cl_futures_live.log").write_text("cl heartbeat\n", encoding="utf-8")
    (root / "tradovate_token_live.txt").write_text("Bearer valid-token\n", encoding="utf-8")

    now = time.time()
    for path in (logs / "futures_bot_live.log", logs / "cl_futures_live.log"):
        os.utime(path, (now - 19, now - 19))

    with patch(
        "algochains_mcp.latency_monitor_status._probe_tradovate",
        return_value={"http_status": 200, "latency_ms": 105.0, "ok": True},
    ):
        payload = get_latency_monitor_status(control_tower=root, now=now)

    assert payload["status"] == "OK"
    assert payload["formatted_line"].startswith("TRADOVATE_LATENCY=105ms STATUS=200")
    assert "SCHEDULER_STATE=disabled" in payload["formatted_line"]


def test_get_latency_monitor_status_tool_dispatch(monkeypatch):
    monkeypatch.setenv("ALGOCHAINS_DEMO_MODE", "1")
    from algochains_mcp.server import call_tool

    async def _run():
        result = await call_tool("get_latency_monitor_status", {})
        text = result[0].text if hasattr(result[0], "text") else str(result[0])
        return json.loads(text)

    payload = asyncio.run(_run())
    assert payload.get("component") == "latency-monitor"
    assert "formatted_line" in payload
