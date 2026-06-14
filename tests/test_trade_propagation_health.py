from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from algochains_mcp.trade_propagation import get_copy_trade_fanout_health


class _FakeQuery:
    def __init__(self, rows):
        self._rows = list(rows)

    def select(self, *_args, **_kwargs):
        return self

    def gt(self, field, value):
        threshold = _parse(value)
        self._rows = [row for row in self._rows if _parse(row.get(field)) > threshold]
        return self

    def eq(self, field, value):
        self._rows = [row for row in self._rows if row.get(field) == value]
        return self

    def order(self, field, desc=False):
        self._rows.sort(key=lambda row: _parse(row.get(field)), reverse=desc)
        return self

    def limit(self, count):
        self._rows = self._rows[:count]
        return self

    def execute(self):
        return SimpleNamespace(data=self._rows)


class _FakeSupabase:
    def __init__(self, tables):
        self._tables = tables

    def table(self, name):
        return _FakeQuery(self._tables.get(name, []))


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse(value) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _health(tables, max_lag_seconds=30.0):
    with patch("algochains_mcp.trade_propagation._service_client", return_value=_FakeSupabase(tables)):
        return get_copy_trade_fanout_health(max_lag_seconds=max_lag_seconds)


def test_idle_since_last_signal_is_not_active_fanout_lag():
    now = datetime.now(timezone.utc)
    out = _health({
        "copy_trade_signals": [{
            "id": "expired-signal",
            "bot": "MNQ",
            "symbol": "MNQ",
            "side": "BUY",
            "emitted_at": _iso(now - timedelta(hours=4)),
            "expires_at": _iso(now - timedelta(hours=3, minutes=59)),
        }],
        "copy_trade_signal_audit": [{"occurred_at": _iso(now - timedelta(hours=4))}],
        "subscriber_fills": [{"is_paper": True, "filled_at": _iso(now - timedelta(minutes=5))}],
        "subscriber_paper_accounts": [{"subscriber_id": "sub-1", "updated_at": _iso(now - timedelta(minutes=5))}],
    })

    assert out["status"] == "healthy"
    assert out["reason"] == "idle_no_active_signals"
    assert out["active_signal_count"] == 0
    assert out["active_lag_seconds"] == 0.0
    assert out["idle_since_last_signal_seconds"] > 30.0


def test_unexpired_signal_past_slo_is_degraded_active_lag():
    now = datetime.now(timezone.utc)
    out = _health({
        "copy_trade_signals": [{
            "id": "active-signal",
            "bot": "MNQ",
            "symbol": "MNQ",
            "side": "BUY",
            "emitted_at": _iso(now - timedelta(seconds=45)),
            "expires_at": _iso(now + timedelta(minutes=5)),
        }],
        "copy_trade_signal_audit": [],
        "subscriber_fills": [],
        "subscriber_paper_accounts": [],
    })

    assert out["status"] == "degraded"
    assert out["reason"] == "active_signal_lag_high"
    assert out["active_signal_count"] == 1
    assert out["active_lag_seconds"] >= 40.0
