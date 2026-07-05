from __future__ import annotations

import asyncio
import os


def test_http_transport_requires_auth_secret_by_default(monkeypatch):
    from algochains_mcp import http_transport

    monkeypatch.delenv("ALGOCHAINS_HTTP_TRANSPORT_SECRET", raising=False)
    monkeypatch.delenv("ALGOCHAINS_HTTP_ALLOW_UNAUTHENTICATED", raising=False)

    assert http_transport._verify_bearer_token(None) is False
    assert http_transport.run_http_server.__defaults__ == ("127.0.0.1", 8080)


def test_http_transport_explicit_dev_open_mode(monkeypatch):
    from algochains_mcp import http_transport

    monkeypatch.delenv("ALGOCHAINS_HTTP_TRANSPORT_SECRET", raising=False)
    monkeypatch.setenv("ALGOCHAINS_HTTP_ALLOW_UNAUTHENTICATED", "1")

    assert http_transport._verify_bearer_token(None) is True


def test_sensitive_dynamic_tools_require_owner_token(monkeypatch):
    from algochains_mcp.tool_policy import evaluate_dynamic_tool

    monkeypatch.setenv("OWNER_API_TOKEN", "owner-secret")

    for tool_name in (
        "get_subscriber_bots",
        "get_user_bot_metrics",
        "get_all_user_bots",
        "revoke_broker_connection",
        "submit_to_marketplace",
    ):
        denied = evaluate_dynamic_tool(
            tool_name,
            {},
            expected_owner_token=os.environ["OWNER_API_TOKEN"],
        )
        assert denied.allow is False
        assert denied.required_secret == "OWNER_API_TOKEN"

        allowed = evaluate_dynamic_tool(
            tool_name,
            {"owner_token": os.environ["OWNER_API_TOKEN"]},
            expected_owner_token=os.environ["OWNER_API_TOKEN"],
        )
        assert allowed.allow is True


def test_user_bot_metrics_do_not_parse_operator_logs_without_subscription(monkeypatch):
    from algochains_mcp.live_bot_intelligence import multi_account_metrics as metrics

    async def no_subscription(*_args, **_kwargs):
        return None

    def fail_parse(*_args, **_kwargs):
        raise AssertionError("parse_bot_metrics should not run without a subscription")

    monkeypatch.setattr(metrics, "_fetch_subscription", no_subscription)
    monkeypatch.setattr(metrics, "parse_bot_metrics", fail_parse)

    result = asyncio.run(
        metrics.get_user_bot_metrics(
            user_id="attacker-user",
            bot_id="mnq",
            subscription_id="fake-subscription",
        )
    )

    assert result.state == metrics.BotDataState.BROKER_NOT_CONNECTED
    assert result.daily_pnl is None


def test_all_user_bots_no_subscription_no_owner_fallback(monkeypatch):
    from algochains_mcp.live_bot_intelligence import multi_account_metrics as metrics

    class EmptyResponse:
        status_code = 200

        def json(self):
            return []

    class FakeAsyncClient:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def get(self, *_args, **_kwargs):
            return EmptyResponse()

    def fail_parse(*_args, **_kwargs):
        raise AssertionError("owner bot logs should not be parsed as fallback")

    monkeypatch.setattr(metrics.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(metrics, "parse_bot_metrics", fail_parse)
    monkeypatch.setattr(metrics, "SUPABASE_URL", "https://supabase.example")
    monkeypatch.setattr(metrics, "SUPABASE_SERVICE_KEY", "service-role-proof")

    result = asyncio.run(metrics.get_all_user_bots("attacker-user"))

    assert result["success"] is True
    assert result["bots"] == []
    assert result["total"] == 0
