"""
Tests for subscriber_auth.py — key prefix detection, hashing, and RPC resolution.

Includes the BUG-19b regression: a Supabase row with a null/empty
`subscriber_id` must resolve to None (unauthenticated) WITHOUT raising.
The original guard used an undefined name (`logger` instead of `log`),
turning the auth failure into a NameError / HTTP 500.
"""
from unittest.mock import MagicMock, patch

from algochains_mcp.subscriber_auth import (
    DEFAULT_SUBSCRIBER_SCOPES,
    hash_subscriber_key,
    invalidate_cache,
    is_subscriber_key,
    resolve_subscriber_key,
)


class TestIsSubscriberKey:
    def test_live_key_prefix(self):
        assert is_subscriber_key("sub_live_abc123") is True

    def test_test_key_prefix(self):
        assert is_subscriber_key("sub_test_xyz987") is True

    def test_developer_key_not_subscriber(self):
        assert is_subscriber_key("ac_live_abc") is False

    def test_empty_and_none(self):
        assert is_subscriber_key("") is False
        assert is_subscriber_key(None) is False


class TestHashSubscriberKey:
    def test_deterministic_sha256_hex(self):
        digest = hash_subscriber_key("sub_live_x")
        assert digest == hash_subscriber_key("sub_live_x")
        assert len(digest) == 64
        assert all(c in "0123456789abcdef" for c in digest)


class TestResolveSubscriberKey:
    def setup_method(self):
        invalidate_cache()

    def _mock_sb(self, rows):
        sb = MagicMock()
        sb.rpc.return_value.execute.return_value = MagicMock(data=rows)
        return sb

    def test_non_subscriber_key_returns_none(self):
        assert resolve_subscriber_key("ac_live_notasub") is None

    def test_unknown_key_returns_none_and_negative_caches(self):
        sb = self._mock_sb([])
        with patch("algochains_mcp.subscriber_auth._service_client", return_value=sb):
            assert resolve_subscriber_key("sub_live_unknown") is None
            # second call within negative TTL must not hit the RPC again
            assert resolve_subscriber_key("sub_live_unknown") is None
        assert sb.rpc.call_count == 1

    def test_valid_row_resolves(self):
        sb = self._mock_sb([{"subscriber_id": "abc-123", "scopes": ["my_pnl"]}])
        with patch("algochains_mcp.subscriber_auth._service_client", return_value=sb):
            resolved = resolve_subscriber_key("sub_live_good")
        assert resolved is not None
        assert resolved.subscriber_id == "abc-123"
        assert resolved.scopes == ("my_pnl",)

    def test_null_scopes_fall_back_to_defaults(self):
        sb = self._mock_sb([{"subscriber_id": "abc-123", "scopes": None}])
        with patch("algochains_mcp.subscriber_auth._service_client", return_value=sb):
            resolved = resolve_subscriber_key("sub_live_defaultscopes")
        assert resolved is not None
        assert resolved.scopes == DEFAULT_SUBSCRIBER_SCOPES

    def test_supabase_unavailable_fails_closed(self):
        with patch("algochains_mcp.subscriber_auth._service_client", return_value=None):
            assert resolve_subscriber_key("sub_live_nodb") is None

    def test_null_subscriber_id_row_returns_none_without_raising(self):
        """BUG-19b regression: null subscriber_id must be a clean auth failure.

        Before the fix, this path raised NameError (`logger` undefined) and
        the bridge surfaced a 500 instead of a 401.
        """
        for bad_sid in (None, ""):
            invalidate_cache()
            sb = self._mock_sb([{"subscriber_id": bad_sid, "scopes": ["my_pnl"]}])
            with patch(
                "algochains_mcp.subscriber_auth._service_client", return_value=sb
            ):
                # Must not raise — that is the regression under test.
                assert resolve_subscriber_key("sub_live_nullsid") is None

    def test_null_subscriber_id_is_negative_cached(self):
        sb = self._mock_sb([{"subscriber_id": None, "scopes": []}])
        with patch("algochains_mcp.subscriber_auth._service_client", return_value=sb):
            assert resolve_subscriber_key("sub_live_nullcached") is None
            assert resolve_subscriber_key("sub_live_nullcached") is None
        assert sb.rpc.call_count == 1
