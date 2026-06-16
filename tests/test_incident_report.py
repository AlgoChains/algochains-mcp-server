from __future__ import annotations

import json

from algochains_mcp.incident_report import get_incident_report


def test_get_incident_report_reads_latest_incident(tmp_path):
    incidents_dir = tmp_path / "logs" / "incidents"
    incidents_dir.mkdir(parents=True)
    older = incidents_dir / "incident_20260616_120000.json"
    latest = incidents_dir / "incident_20260616_124514.json"
    older.write_text(
        json.dumps({"title": "Older incident", "issues": ["old"]}),
        encoding="utf-8",
    )
    latest.write_text(
        json.dumps(
            {
                "title": "Critical Path Failure: 2 Issues",
                "captured_at": "2026-06-16 12:45:14",
                "issues": ["Bot Processes: 5/4 running", "alpha_loop sqlite read failed"],
                "bot_processes": {"running": 5, "expected": 4},
                "recent_deploy": "0bc90216 alpha_loop WAL fix",
            }
        ),
        encoding="utf-8",
    )

    result = get_incident_report(control_tower=tmp_path)

    assert result["status"] == "ok"
    assert result["incidents_available"] == 2
    assert len(result["reports"]) == 1
    report = result["reports"][0]
    assert report["incident_file"] == "incident_20260616_124514.json"
    assert report["title"] == "Critical Path Failure: 2 Issues"
    assert report["issue_count"] == 2
    assert report["issues"][0] == "Bot Processes: 5/4 running"


def test_get_incident_report_missing_dir(tmp_path):
    result = get_incident_report(control_tower=tmp_path)

    assert result["status"] == "missing"
    assert result["reports"] == []
