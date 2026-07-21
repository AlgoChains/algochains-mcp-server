"""Regression tests for SEC-2026 medium appsec remediations (2026-07-21)."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from algochains_mcp.daemon_callback_auth import verify_daemon_callback_key
from algochains_mcp.marketplace.supabase_tools import get_subscriber_bots
from algochains_mcp.notifications.push import NotificationChannel, NotificationDispatcher
from algochains_mcp.security.internal_auth_context import (
    attach_trusted_developer_context,
    strip_untrusted_internal_auth,
)
from algochains_mcp.security.ssrf_guard import is_ssrf_target, validate_webhook_url
from algochains_mcp.subscriber_tools import report_fill


class TestSsrfGuard:
    def test_blocks_loopback(self):
        assert is_ssrf_target("http://127.0.0.1/hook")
        assert validate_webhook_url("http://127.0.0.1/hook")

    def test_allows_public_https(self):
        assert not is_ssrf_target("https://hooks.slack.com/services/abc")
        assert validate_webhook_url("https://hooks.slack.com/services/abc") is None


class TestInternalAuthContext:
    def test_unsigned_scopes_stripped(self, monkeypatch):
        monkeypatch.setenv("BRIDGE_API_KEY", "bridge-secret")
        raw = {
            "_developer_scopes": ["agent:sandbox"],
            "_clerk_user_id": "user_attacker",
        }
        cleaned = strip_untrusted_internal_auth(raw)
        assert "_developer_scopes" not in cleaned
        assert "_clerk_user_id" not in cleaned

    def test_signed_scopes_preserved(self, monkeypatch):
        monkeypatch.setenv("BRIDGE_API_KEY", "bridge-secret")
        signed = attach_trusted_developer_context(
            {},
            scopes=("agent:sandbox",),
            clerk_user_id="user_real",
        )
        cleaned = strip_untrusted_internal_auth(signed)
        assert cleaned["_developer_scopes"] == ["agent:sandbox"]
        assert cleaned["_clerk_user_id"] == "user_real"


class TestGetSubscriberBotsOwnerGate:
    def test_rejects_without_owner(self):
        out = get_subscriber_bots("sub-1", owner_authorized=False)
        assert "error" in out
        assert out["subscriptions"] == []

    @patch("algochains_mcp.marketplace.supabase_tools._get_sb_client")
    def test_allows_owner_lookup(self, mock_client):
        sb = MagicMock()
        sb.table.return_value.select.return_value.eq.return_value.order.return_value.execute.return_value = MagicMock(
            data=[{"bot": "MNQ", "paused": False}]
        )
        mock_client.return_value = sb
        out = get_subscriber_bots("sub-1", owner_authorized=True)
        assert out["total"] == 1
        assert out["subscriptions"][0]["bot"] == "MNQ"


class TestNotificationSsrf:
    def test_configure_slack_rejects_private_webhook(self):
        dispatcher = NotificationDispatcher()
        with pytest.raises(ValueError, match="Blocked"):
            dispatcher.configure_slack("http://169.254.169.254/latest/meta-data/")


class TestReportFillDaemonAuth:
    def _mock_sb(self, *, signal_row=None, assignments=None):
        fills_insert = MagicMock()
        fills_insert.execute.return_value = MagicMock(data=[{"id": "fill-1"}])
        fills_table = MagicMock()
        fills_table.insert = fills_insert

        sb = MagicMock()

        def table(name):
            if name == "subscriber_fills":
                return fills_table
            t = MagicMock()
            if name == "copy_trade_signals":
                t.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = MagicMock(
                    data=signal_row
                )
            elif name == "subscriber_bot_assignments":
                t.select.return_value.eq.return_value.execute.return_value = MagicMock(
                    data=assignments or [{"bot": "MNQ", "paused": False}]
                )
            elif name == "copy_trade_signal_audit":
                t.insert.return_value.execute.return_value = MagicMock(data=[])
            return t

        sb.table.side_effect = table
        sb._fills_insert = fills_insert
        return sb

    def test_subscriber_key_cannot_store_pnl(self):
        sb = self._mock_sb(
            signal_row={"id": "sig-1", "bot": "MNQ", "symbol": "MNQ", "side": "BUY", "qty": 1},
        )
        with patch("algochains_mcp.subscriber_tools._service_client", return_value=sb):
            out = report_fill(
                "sub-1",
                signal_id="sig-1",
                bot="MNQ",
                symbol="MNQ",
                side="BUY",
                qty=1,
                pnl_usd=999.0,
                tradovate_order_id="fake-order",
                daemon_authorized=False,
            )
        assert out.get("pnl_stored") is False
        insert_payload = sb._fills_insert.call_args[0][0]
        assert insert_payload["pnl_usd"] is None
        assert insert_payload["tradovate_order_id"] is None

    def test_daemon_auth_stores_verified_pnl(self):
        sb = self._mock_sb(
            signal_row={"id": "sig-1", "bot": "MNQ", "symbol": "MNQ", "side": "BUY", "qty": 1},
        )
        with patch("algochains_mcp.subscriber_tools._service_client", return_value=sb):
            out = report_fill(
                "sub-1",
                signal_id="sig-1",
                bot="MNQ",
                symbol="MNQ",
                side="BUY",
                qty=1,
                pnl_usd=12.5,
                tradovate_order_id="tv-123",
                daemon_authorized=True,
            )
        assert out.get("pnl_stored") is True
        insert_payload = sb._fills_insert.call_args[0][0]
        assert insert_payload["pnl_usd"] == 12.5
        assert insert_payload["tradovate_order_id"] == "tv-123"


class TestDaemonCallbackToken:
    def test_verify_requires_env(self, monkeypatch):
        monkeypatch.delenv("ALGOCHAINS_DAEMON_CALLBACK_TOKEN", raising=False)
        assert verify_daemon_callback_key("anything") is False

    def test_verify_accepts_matching_token(self, monkeypatch):
        monkeypatch.setenv("ALGOCHAINS_DAEMON_CALLBACK_TOKEN", "daemon-secret")
        assert verify_daemon_callback_key("daemon-secret") is True
        assert verify_daemon_callback_key("sub_daemon_daemon-secret") is True
        assert verify_daemon_callback_key("wrong") is False
