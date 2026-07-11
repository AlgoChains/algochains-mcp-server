from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from algochains_mcp.subscriber_tools import (
    PAPER_CONTRACT_VERSION,
    PAPER_STARTING_BALANCE_USD,
    _err,
    get_paper_route_health,
    get_signal_stream,
)

SUBSCRIBER_ID = "00000000-0000-0000-0000-000000000001"


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, client, table):
        self.client = client
        self.table = table

    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: self

    def execute(self):
        value = self.client.results[self.table]
        if isinstance(value, Exception):
            raise value
        return _Result(value)


class _Client:
    def __init__(self, **results):
        self.results = results

    def table(self, name):
        return _Query(self, name)


def test_contract_constants_match_web_contract():
    assert PAPER_CONTRACT_VERSION == "paper-subscriber.v1"
    assert PAPER_STARTING_BALANCE_USD == 50_000


def test_error_shape_is_structured_and_backward_compatible():
    result = _err("supabase_unavailable")

    assert result["ok"] is False
    assert result["error"] == "supabase_unavailable"
    assert result["error_code"] == "supabase_unavailable"
    assert result["environment"] == "paper"
    assert result["source"] == "supabase"


def test_signal_stream_fails_closed_when_assignment_scope_cannot_be_queried():
    client = _Client(subscriber_bot_assignments=RuntimeError("database unavailable"))

    with patch("algochains_mcp.subscriber_tools._service_client", return_value=client):
        result = get_signal_stream(SUBSCRIBER_ID)

    assert result["ok"] is False
    assert result["error"] == "assignments_unavailable"
    assert "signals" not in result


def test_paper_route_health_reports_pending_order_sla_breach():
    now = datetime.now(timezone.utc)
    client = _Client(
        subscriber_paper_accounts={
            "starting_balance_usd": 50_000,
            "current_balance_usd": 50_000,
        },
        subscriber_heartbeats={"last_seen": now.isoformat(), "daemon_version": "test"},
        subscriber_paper_orders=[
            {
                "id": "order-1",
                "status": "pending",
                "created_at": (now - timedelta(minutes=5)).isoformat(),
            }
        ],
    )

    with patch("algochains_mcp.subscriber_tools._service_client", return_value=client):
        result = get_paper_route_health(SUBSCRIBER_ID)

    assert result["ok"] is True
    assert result["health"] == "degraded"
    assert result["reason"] == "pending_order_sla_breached"
    assert result["pending_order_count"] == 1
    assert result["oldest_pending_age_seconds"] >= 299


def test_paper_route_health_never_calls_missing_heartbeat_healthy():
    client = _Client(
        subscriber_paper_accounts={"starting_balance_usd": 50_000},
        subscriber_heartbeats=None,
        subscriber_paper_orders=[],
    )

    with patch("algochains_mcp.subscriber_tools._service_client", return_value=client):
        result = get_paper_route_health(SUBSCRIBER_ID)

    assert result["health"] == "unavailable"
    assert result["reason"] == "executor_heartbeat_missing"
