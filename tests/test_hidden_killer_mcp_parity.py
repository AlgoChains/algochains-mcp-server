from __future__ import annotations

from pathlib import Path

from algochains_mcp import paths
from algochains_mcp.server import _default_control_tower


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "src" / "algochains_mcp" / "server.py"


def test_tower_tools_use_defined_control_tower_resolver():
    source = SERVER.read_text(encoding="utf-8")
    tower_block = source[
        source.index('elif name in ("dispatch_tower_job"') :
        source.index('elif name == "get_signal_conflict_stats"')
    ]

    assert "_resolve_control_tower_root" not in tower_block
    assert "_default_control_tower()" in tower_block or "default_control_tower()" in tower_block


def test_tower_health_and_status_are_tier1_tools():
    source = SERVER.read_text(encoding="utf-8")
    tier1_block = source[source.index("TIER1_TOOL_NAMES = {") : source.index("TOOLS_TIER1 =")]

    assert '"get_tower_health"' in tier1_block
    assert '"get_tower_job_status"' in tier1_block


def test_cc_health_maps_current_state_file_shape():
    source = SERVER.read_text(encoding="utf-8")
    cc_block = source[
        source.index("# ── Command Center watchdog state") :
        source.index("# ── E2E Execution Sentinel state")
    ]

    assert 'get("last_status")' in cc_block
    assert "last_alerted_issues_key" in cc_block
    assert "last_unhandled_error" in cc_block


def test_server_control_tower_resolver_delegates_to_shared_paths(monkeypatch, tmp_path):
    desktop_tower = tmp_path / "desktop-control-tower"
    desktop_tower.mkdir()

    monkeypatch.delenv("ALGOCHAINS_CONTROL_TOWER", raising=False)
    monkeypatch.delenv("ALGOCHAINS_CONTROL_TOWER_PATH", raising=False)
    monkeypatch.setattr(paths, "_LEGACY_POSSIBLE_ROOTS", (desktop_tower,))

    assert _default_control_tower() == str(desktop_tower)
