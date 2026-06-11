from __future__ import annotations

from algochains_mcp.live_bot_intelligence import metrics_parser


def test_parse_errors_ignores_timestamped_errors_older_than_one_hour(monkeypatch):
    monkeypatch.setattr(metrics_parser.time, "time", lambda: 1_775_000_000.0)

    last_error, error_count = metrics_parser._parse_errors(
        [
            "2026-03-31T07:13:19+00:00 ERROR Runtime Exception in MNQ loop",
            "2026-03-31T07:59:59+00:00 Traceback (most recent call last)",
        ]
    )

    assert last_error == ""
    assert error_count == 0


def test_parse_errors_counts_fresh_timestamped_errors(monkeypatch):
    monkeypatch.setattr(metrics_parser.time, "time", lambda: 1_775_000_000.0)

    last_error, error_count = metrics_parser._parse_errors(
        [
            "2026-03-31T08:00:01+00:00 ERROR Runtime Exception in MNQ loop",
            "2026-03-31 08:43:20,500 WARNING recovered",
            "2026-03-31 08:45:20,500 Traceback (most recent call last)",
        ]
    )

    assert "Traceback" in last_error
    assert error_count == 2


def test_parse_errors_counts_untimestamped_errors_for_legacy_logs(monkeypatch):
    monkeypatch.setattr(metrics_parser.time, "time", lambda: 1_775_000_000.0)

    last_error, error_count = metrics_parser._parse_errors(
        [
            "ERROR legacy bot log without an ISO timestamp",
            "regular heartbeat",
        ]
    )

    assert "legacy bot log" in last_error
    assert error_count == 1
