from __future__ import annotations

import asyncio
import json
from pathlib import Path

from algochains_mcp.live_bot_intelligence.bot_ops import (
    check_orphan_bracket_orders,
    get_bracket_guardian_status,
)
from algochains_mcp.orphan_bracket_scanner_status import (
    format_orphan_bracket_scanner_line,
    get_orphan_bracket_scanner_status,
    is_orphan_bracket_scanner_command,
)


def _ps_line(pid: int, command: str) -> str:
    return f"trey {pid} 0.0 0.1 123 456 ?? S 04:00 0:00 {command}"


def _make_control_tower(tmp_path: Path) -> Path:
    root = tmp_path / "algochains-control-tower"
    (root / "autonomous").mkdir(parents=True)
    (root / "logs").mkdir()
    (root / "state").mkdir()
    (root / "autonomous" / "orphan_bracket_scanner.py").write_text("# daemon\n", encoding="utf-8")
    (root / "state" / "orphan_bracket_scanner_state.json").write_text(
        json.dumps(
            {
                "last_check": "2026-06-16T16:50:00Z",
                "MNQ_SWING_PROTECT": "YES",
                "gate": "PROCEED",
                "SWING": "YES",
                "orphans_found": 0,
                "orphans_cancelled": 0,
            }
        ),
        encoding="utf-8",
    )
    (root / "state" / "bracket_guardian_state.json").write_text(
        json.dumps(
            {
                "last_check": "2026-06-16T16:45:00Z",
                "unknown_flat_orders": [],
                "positions_count": 0,
                "working_orders_count": 0,
            }
        ),
        encoding="utf-8",
    )
    return root


def test_orphan_scanner_process_matching_rejects_shell_false_positives():
    assert is_orphan_bracket_scanner_command(
        "/usr/bin/python3 -B -u autonomous/orphan_bracket_scanner.py"
    )
    assert not is_orphan_bracket_scanner_command("bash -lc 'python autonomous/orphan_bracket_scanner.py'")
    assert not is_orphan_bracket_scanner_command("rg orphan_bracket_scanner.py /workspace")


def test_orphan_scanner_status_reports_gate_proceed(tmp_path):
    root = _make_control_tower(tmp_path)
    script = root / "autonomous" / "orphan_bracket_scanner.py"
    ps_output = "\n".join(
        [
            "USER PID %CPU %MEM VSZ RSS TTY STAT START TIME COMMAND",
            _ps_line(333, f"/usr/bin/python3 -B -u {script}"),
        ]
    )

    status = get_orphan_bracket_scanner_status(
        control_tower=root,
        ps_output=ps_output,
        now=1_780_000_000,
    )

    assert status["scan_status"] == "OK"
    assert status["gate"] == "PROCEED"
    assert status["swing"] == "YES"
    assert status["mnq_swing_protect"] == "YES"
    assert status["unknown_flat_order_count"] == 0
    assert status["running"] is True
    assert status["formatted_line"].startswith("[OK]")


def test_format_orphan_scanner_line_for_orphan_orders():
    line = format_orphan_bracket_scanner_line(
        {
            "scan_status": "ORPHAN_ORDERS",
            "unknown_flat_order_count": 2,
            "orphans_cancelled": 1,
            "gate": "PROCEED",
        }
    )
    assert line.startswith("[ORPHAN_ORDERS]")
    assert "2 orphan working order" in line


def test_bracket_guardian_status_includes_unknown_flat_orders(tmp_path, monkeypatch):
    import algochains_mcp.live_bot_intelligence.bot_ops as bot_ops

    root = _make_control_tower(tmp_path)
    guardian_state = root / "state" / "bracket_guardian_state.json"
    guardian_state.write_text(
        json.dumps(
            {
                "last_check": "2026-06-16T16:45:00Z",
                "unknown_flat_orders": [{"orderId": 123, "contractName": "MNQU5"}],
                "unprotected_since": {},
                "positions_count": 0,
                "working_orders_count": 1,
                "MNQ_SWING_PROTECT": "YES",
                "GATE": "PROCEED",
                "SWING": "YES",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(bot_ops, "CONTROL_TOWER", root)
    monkeypatch.setattr(
        bot_ops,
        "bracket_integrity_check",
        lambda: {"status": "OK", "checked_count": 0, "message": "flat"},
    )

    status = get_bracket_guardian_status()

    assert status["status"] == "ALERT"
    assert status["unknown_flat_order_count"] == 1
    assert status["gate"] == "PROCEED"
    assert status["mnq_swing_protect"] == "YES"


def test_check_orphan_bracket_orders_detects_flat_contract_working_orders(monkeypatch):
    import algochains_mcp.live_bot_intelligence.bot_ops as bot_ops

    monkeypatch.setattr(
        bot_ops,
        "_fetch_tradovate_book",
        lambda: {
            "status": "OK",
            "positions": [{"contractId": 10, "contractName": "MNQ", "netPos": 1}],
            "working_orders": [
                {"id": 1, "contractId": 10, "orderType": "Stop", "action": "Sell"},
                {"id": 2, "contractId": 99, "orderType": "Stop", "action": "Buy", "contractName": "MNQ"},
            ],
            "environment": "DEMO",
        },
    )

    result = check_orphan_bracket_orders()

    assert result["status"] == "ORPHAN_ORDERS"
    assert result["orphan_order_count"] == 1
    assert result["orphan_orders"][0]["orderId"] == 2
    assert result["formatted_line"].startswith("[ORPHAN_ORDERS]")


def test_orphan_bracket_tools_registered_and_callable(monkeypatch, tmp_path):
    import algochains_mcp.live_bot_intelligence.bot_ops as bot_ops
    import algochains_mcp.orphan_bracket_scanner_status as orphan_status
    import algochains_mcp.server as srv

    root = _make_control_tower(tmp_path)
    monkeypatch.setattr(bot_ops, "CONTROL_TOWER", root)
    monkeypatch.setattr(orphan_status, "default_control_tower", lambda: root)
    monkeypatch.setattr(
        bot_ops,
        "_fetch_tradovate_book",
        lambda: {
            "status": "OK",
            "positions": [],
            "working_orders": [],
            "environment": "DEMO",
        },
    )

    tool_names = {tool.name for tool in srv.TOOLS_ANNOTATED}
    assert "check_orphan_bracket_orders" in tool_names
    assert "get_orphan_bracket_scanner_status" in tool_names
    assert "check_orphan_bracket_orders" in srv.TIER1_TOOL_NAMES
    assert "get_orphan_bracket_scanner_status" in srv.TIER1_TOOL_NAMES

    orphan_check = asyncio.run(srv.call_tool("check_orphan_bracket_orders", {}))
    scanner_status = asyncio.run(srv.call_tool("get_orphan_bracket_scanner_status", {}))

    orphan_payload = json.loads(orphan_check[0].text)
    scanner_payload = json.loads(scanner_status[0].text)

    assert orphan_payload["status"] == "OK"
    assert scanner_payload["scan_status"] == "OK"
