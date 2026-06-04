"""
Tests for developer_auth.py — key prefix detection, hashing, cache, and RPC resolution.
"""
from unittest.mock import MagicMock, patch

import pytest

from algochains_mcp.developer_auth import (
    DEVELOPER_KEY_PREFIXES,
    ResolvedDeveloper,
    hash_developer_key,
    invalidate_cache,
    is_developer_key,
    resolve_developer_key,
)


class TestIsDeveloperKey:
    def test_live_key_prefix(self):
        assert is_developer_key("ac_live_abc123") is True

    def test_test_key_prefix(self):
        assert is_developer_key("ac_test_xyz987") is True

    def test_subscriber_key_not_developer(self):
        assert is_developer_key("sub_live_abc") is False

    def test_owner_bridge_key_not_developer(self):
        assert is_developer_key("bridge-secret-key") is False

    def test_empty_string(self):
        assert is_developer_key("") is False

    def test_none(self):
        assert is_developer_key(None) is False

    def test_partial_prefix(self):
        assert is_developer_key("ac_") is False


class TestHashDeveloperKey:
    def test_deterministic(self):
        k = "ac_live_testkey"
        assert hash_developer_key(k) == hash_developer_key(k)

    def test_different_keys_different_hashes(self):
        assert hash_developer_key("ac_live_aaa") != hash_developer_key("ac_live_bbb")

    def test_returns_hex_string(self):
        result = hash_developer_key("ac_live_x")
        assert all(c in "0123456789abcdef" for c in result)
        assert len(result) == 64  # SHA-256


class TestResolveDeveloperKey:
    def setup_method(self):
        invalidate_cache()

    def _mock_sb(self, rows):
        sb = MagicMock()
        sb.rpc.return_value.execute.return_value = MagicMock(data=rows)
        return sb

    @patch("algochains_mcp.developer_auth._service_client")
    def test_valid_key_resolves(self, mock_client):
        mock_client.return_value = self._mock_sb([{
            "clerk_user_id": "clerk_abc",
            "scopes": ["read:market_data", "read:signals"],
            "env": "live",
        }])
        result = resolve_developer_key("ac_live_validkey")
        assert result is not None
        assert result.clerk_user_id == "clerk_abc"
        assert "read:market_data" in result.scopes
        assert result.env == "live"

    @patch("algochains_mcp.developer_auth._service_client")
    def test_unknown_key_returns_none(self, mock_client):
        mock_client.return_value = self._mock_sb([])
        assert resolve_developer_key("ac_live_unknownkey") is None

    @patch("algochains_mcp.developer_auth._service_client")
    def test_null_clerk_user_id_returns_none(self, mock_client):
        mock_client.return_value = self._mock_sb([{
            "clerk_user_id": None,
            "scopes": ["read:market_data"],
            "env": "live",
        }])
        assert resolve_developer_key("ac_live_nullclerk") is None

    @patch("algochains_mcp.developer_auth._service_client")
    def test_supabase_unavailable_fails_closed(self, mock_client):
        mock_client.return_value = None
        assert resolve_developer_key("ac_live_anykey") is None

    @patch("algochains_mcp.developer_auth._service_client")
    def test_non_developer_key_returns_none_without_rpc(self, mock_client):
        result = resolve_developer_key("sub_live_subscriberkey")
        assert result is None
        mock_client.assert_not_called()

    @patch("algochains_mcp.developer_auth._service_client")
    def test_caches_positive_result(self, mock_client):
        mock_client.return_value = self._mock_sb([{
            "clerk_user_id": "cached_clerk",
            "scopes": ["read:signals"],
            "env": "test",
        }])
        result1 = resolve_developer_key("ac_test_cachekey")
        result2 = resolve_developer_key("ac_test_cachekey")
        assert result1 is result2
        # RPC called exactly once
        assert mock_client.return_value.rpc.call_count == 1

    @patch("algochains_mcp.developer_auth._service_client")
    def test_empty_scopes_uses_defaults(self, mock_client):
        mock_client.return_value = self._mock_sb([{
            "clerk_user_id": "clerk_noscopes",
            "scopes": [],
            "env": "live",
        }])
        result = resolve_developer_key("ac_live_noscopes")
        assert result is not None
        assert len(result.scopes) > 0  # defaults applied

    @patch("algochains_mcp.developer_auth._service_client")
    def test_invalid_env_normalised_to_live(self, mock_client):
        mock_client.return_value = self._mock_sb([{
            "clerk_user_id": "clerk_badenv",
            "scopes": ["read:market_data"],
            "env": "badvalue",
        }])
        result = resolve_developer_key("ac_live_badenv")
        assert result is not None
        assert result.env == "live"
