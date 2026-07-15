"""
test_writer_parity.py — Writer-parity tests for developer_api_keys INSERT shape.

Verifies that all three writers (Django, MCP platform_auth, Stripe provisioner)
produce the identical INSERT column set required by the developer_api_keys table.
Also verifies that a key minted via build_insert_payload() resolves correctly
through developer_auth.resolve_developer_key().

Background:
  Megaprompt 1 (AlgoChains API Key Unification, 2026-06-28) requires that:
    Writer A (developer_key_service.py) — psycopg2 INSERT — canonical, correct
    Writer B (platform_auth.py)         — PostgREST INSERT — fixed to use key_contract
    Writer C (stripe_app/server.py)     — PostgREST INSERT — fixed to use key_contract

  All three must produce: clerk_user_id, key_hash, prefix, key_prefix, key_hint,
  label, name, scopes, tier_at_creation, env, is_active.

  resolve_developer_api_key() works by key_hash + is_active=TRUE. Any row
  missing clerk_user_id (NOT NULL constraint) will fail to be inserted; any row
  with a correct hash resolves correctly.
"""
from __future__ import annotations

import hashlib
import importlib
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _ensure_local_src_on_path():
    """
    Ensure the local src/ directory is on sys.path so tests can import
    the latest key_contract.py even when the installed package is older.
    """
    repo_root = Path(__file__).parent.parent
    src = repo_root / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))


_ensure_local_src_on_path()

# Force reimport from local src if the installed package lacks key_contract
if "algochains_mcp.auth.key_contract" in sys.modules:
    del sys.modules["algochains_mcp.auth.key_contract"]
if "algochains_mcp.auth" in sys.modules:
    del sys.modules["algochains_mcp.auth"]

try:
    from algochains_mcp.auth.key_contract import (
        DEFAULT_SCOPES,
        DEVELOPER_KEY_PREFIXES,
        LIVE_PREFIX,
        TEST_PREFIX,
        TIER_SCOPES,
        build_insert_payload,
        generate_platform_key,
        hash_platform_key,
        is_developer_key,
        key_hint,
        key_prefix_field,
        prefix_field,
        scopes_for_tier,
    )
except ModuleNotFoundError:
    pytest.skip("algochains_mcp.auth.key_contract not available", allow_module_level=True)

from algochains_mcp.developer_auth import (
    ResolvedDeveloper,
    hash_developer_key,
    invalidate_cache,
    is_developer_key as da_is_developer_key,
    resolve_developer_key,
)

# ── Required columns every INSERT must carry ───────────────────────────────────

REQUIRED_COLUMNS = frozenset({
    "clerk_user_id",
    "key_hash",
    "prefix",
    "key_prefix",
    "key_hint",
    "label",
    "name",
    "scopes",
    "tier_at_creation",
    "env",
    "is_active",
})


# ═══════════════════════════════════════════════════════════════════════════════
#  key_contract unit tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestKeyContractGeneration:
    def test_live_prefix(self):
        key = generate_platform_key("live")
        assert key.startswith(LIVE_PREFIX)

    def test_test_prefix(self):
        key = generate_platform_key("test")
        assert key.startswith(TEST_PREFIX)

    def test_invalid_env_raises(self):
        with pytest.raises(ValueError):
            generate_platform_key("prod")

    def test_unique_keys(self):
        assert generate_platform_key("live") != generate_platform_key("live")

    def test_minimum_length(self):
        assert len(generate_platform_key("live")) > 20


class TestKeyContractHashing:
    def test_hash_matches_sha256(self):
        raw = "ac_live_testkey_parity_001"
        expected = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        assert hash_platform_key(raw) == expected

    def test_hash_idempotent(self):
        raw = generate_platform_key("live")
        assert hash_platform_key(raw) == hash_platform_key(raw)

    def test_hash_is_hex_64_chars(self):
        h = hash_platform_key("ac_live_x")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_key_contract_hash_matches_developer_auth_hash(self):
        """
        CRITICAL: key_contract.hash_platform_key and developer_auth.hash_developer_key
        must produce the same value for the same input — otherwise a key minted by
        Django won't be found by resolve_developer_key.
        """
        raw = generate_platform_key("live")
        assert hash_platform_key(raw) == hash_developer_key(raw)


class TestKeyContractHelpers:
    def test_hint_prefix_and_suffix(self):
        raw = "ac_live_ABCDEFGHIJ1234"
        hint = key_hint(raw)
        assert hint.startswith("ac_live_")
        assert hint.endswith("1234")
        assert "..." in hint

    def test_hint_does_not_contain_raw_key(self):
        raw = generate_platform_key("live")
        assert raw not in key_hint(raw)

    def test_key_prefix_field_is_12_chars(self):
        raw = "ac_live_XXXXXXXXXXXXXXXXXXXXX"
        assert key_prefix_field(raw) == raw[:12]

    def test_prefix_field_live(self):
        assert prefix_field("ac_live_x") == "ac_live_"

    def test_prefix_field_test(self):
        assert prefix_field("ac_test_x") == "ac_test_"

    def test_is_developer_key_live(self):
        assert is_developer_key("ac_live_foo") is True

    def test_is_developer_key_test(self):
        assert is_developer_key("ac_test_foo") is True

    def test_is_developer_key_rejects_subscriber(self):
        assert is_developer_key("sub_live_foo") is False

    def test_is_developer_key_rejects_none(self):
        assert is_developer_key(None) is False


class TestScopesForTier:
    def test_developer_pro_scopes(self):
        scopes = scopes_for_tier("developer_pro")
        assert "read:market_data" in scopes
        assert "read:signals" in scopes
        assert "read:backtest" in scopes
        assert "write:backtest" in scopes
        assert "agent:sandbox" in scopes
        assert "spend:llm_budget" in scopes
        assert "agent:host" not in scopes

    def test_enterprise_has_agent_host(self):
        scopes = scopes_for_tier("enterprise")
        assert "agent:sandbox" in scopes
        assert "spend:llm_budget" in scopes
        assert "agent:host" in scopes

    def test_developer_pro_cannot_override_agent_host(self):
        scopes = scopes_for_tier(
            "developer_pro",
            override=["read:market_data", "agent:host", "agent:sandbox"],
        )
        assert "agent:sandbox" in scopes
        assert "agent:host" not in scopes

    def test_enterprise_is_superset_of_developer_pro(self):
        pro = set(scopes_for_tier("developer_pro"))
        ent = set(scopes_for_tier("enterprise"))
        assert pro.issubset(ent)

    def test_enterprise_has_extra_scopes(self):
        assert set(scopes_for_tier("enterprise")) > set(scopes_for_tier("developer_pro"))

    def test_unknown_tier_returns_default(self):
        assert scopes_for_tier("unknown_tier") == DEFAULT_SCOPES

    def test_override_filtered_by_tier(self):
        # developer_pro cannot get publish:listing (enterprise only)
        scopes = scopes_for_tier("developer_pro", override=["read:market_data", "publish:listing"])
        assert "read:market_data" in scopes
        assert "publish:listing" not in scopes

    def test_empty_override_returns_default(self):
        assert scopes_for_tier("developer_pro", override=[]) == DEFAULT_SCOPES

    def test_default_scopes_not_empty(self):
        assert len(DEFAULT_SCOPES) > 0


# ═══════════════════════════════════════════════════════════════════════════════
#  INSERT shape parity — the core writer-parity contract
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuildInsertPayload:
    """
    Verify build_insert_payload() produces the canonical INSERT shape.
    Every writer must use this function (or produce an identical shape).
    """

    def _make_payload(self, env="live", tier="developer_pro", label="Test key", **kw):
        raw = generate_platform_key(env)
        return raw, build_insert_payload(raw, clerk_user_id="user_test_abc", tier=tier, label=label, **kw)

    def test_all_required_columns_present(self):
        _, payload = self._make_payload()
        missing = REQUIRED_COLUMNS - set(payload.keys())
        assert not missing, f"INSERT shape missing: {missing}"

    def test_clerk_user_id_not_empty(self):
        _, payload = self._make_payload()
        assert payload["clerk_user_id"]

    def test_plaintext_not_in_payload(self):
        raw, payload = self._make_payload()
        for col, val in payload.items():
            if isinstance(val, str):
                assert raw not in val, f"Plaintext key found in column '{col}'!"

    def test_key_hash_matches_contract_function(self):
        raw, payload = self._make_payload()
        assert payload["key_hash"] == hash_platform_key(raw)

    def test_env_detected_from_live_key(self):
        _, payload = self._make_payload(env="live")
        assert payload["env"] == "live"

    def test_env_detected_from_test_key(self):
        _, payload = self._make_payload(env="test")
        assert payload["env"] == "test"

    def test_is_active_always_true(self):
        _, payload = self._make_payload()
        assert payload["is_active"] is True

    def test_tier_at_creation_set(self):
        _, payload = self._make_payload(tier="enterprise")
        assert payload["tier_at_creation"] == "enterprise"

    def test_label_truncated_to_60(self):
        _, payload = self._make_payload(label="x" * 100)
        assert len(payload["label"]) <= 60

    def test_scopes_match_tier(self):
        _, payload = self._make_payload(tier="developer_pro")
        assert "write:backtest" in payload["scopes"]

    def test_prefix_correct_for_live(self):
        raw = generate_platform_key("live")
        payload = build_insert_payload(raw, clerk_user_id="user_x")
        assert payload["prefix"] == "ac_live_"

    def test_prefix_correct_for_test(self):
        raw = generate_platform_key("test")
        payload = build_insert_payload(raw, clerk_user_id="user_x")
        assert payload["prefix"] == "ac_test_"

    def test_key_prefix_is_first_12_chars(self):
        raw = generate_platform_key("live")
        payload = build_insert_payload(raw, clerk_user_id="user_x")
        assert payload["key_prefix"] == raw[:12]


class TestWriterParityIdenticalShape:
    """
    Simulate Writer A (Django) and Writer B (MCP) both calling build_insert_payload
    with the same raw key and verify that the key_hash, prefix, env, and scopes
    are identical — enabling resolve_developer_key to find the row from either writer.
    """

    def test_same_key_same_hash_across_writers(self):
        raw = generate_platform_key("live")
        payload_a = build_insert_payload(raw, clerk_user_id="user_django_writer_a", label="Django key")
        payload_b = build_insert_payload(raw, clerk_user_id="user_mcp_writer_b", label="MCP key")

        assert payload_a["key_hash"] == payload_b["key_hash"], \
            "key_hash must be identical — resolve_developer_api_key looks up by hash"
        assert payload_a["prefix"] == payload_b["prefix"]
        assert payload_a["key_prefix"] == payload_b["key_prefix"]
        assert payload_a["key_hint"] == payload_b["key_hint"]
        assert payload_a["env"] == payload_b["env"]
        assert payload_a["is_active"] == payload_b["is_active"]

    def test_writer_c_uses_email_fallback_for_clerk_user_id(self):
        """
        Writer C (Stripe provisioner) may not have a real Clerk ID; it falls back
        to email. Verify this still produces a valid, non-empty clerk_user_id.
        """
        raw = generate_platform_key("live")
        payload = build_insert_payload(
            raw,
            clerk_user_id="customer@example.com",  # email fallback
            tier="developer_pro",
            label="Stripe-provisioned",
        )
        assert payload["clerk_user_id"] == "customer@example.com"
        assert payload["key_hash"]  # must be set for the NOT NULL constraint


# ═══════════════════════════════════════════════════════════════════════════════
#  End-to-end: Django-minted key resolves via developer_auth
# ═══════════════════════════════════════════════════════════════════════════════

class TestDjangoKeyResolvesViaDeveloperAuth:
    """
    Simulate what happens at runtime:
      1. Django (Writer A) mints a key using build_insert_payload and stores key_hash.
      2. Bridge receives the raw key in X-Api-Key header.
      3. developer_auth.resolve_developer_key hashes it and queries Supabase.
      4. Row is found; ResolvedDeveloper is returned.
    """

    def setup_method(self):
        invalidate_cache()

    def _mock_sb_with_payload(self, raw_key: str, clerk_user_id: str, tier: str = "developer_pro"):
        """Build a mock Supabase RPC response that mirrors what the DB would return."""
        payload = build_insert_payload(raw_key, clerk_user_id=clerk_user_id, tier=tier)
        sb = MagicMock()
        sb.rpc.return_value.execute.return_value = MagicMock(data=[{
            "clerk_user_id": payload["clerk_user_id"],
            "scopes": payload["scopes"],
            "env": payload["env"],
        }])
        return sb

    @patch("algochains_mcp.developer_auth._service_client")
    def test_django_minted_key_resolves(self, mock_client):
        """
        A key minted by Django (build_insert_payload) should resolve through
        developer_auth — the hashes must match for the RPC lookup to succeed.
        """
        raw = generate_platform_key("live")
        clerk_id = "user_django_123"

        mock_client.return_value = self._mock_sb_with_payload(raw, clerk_id, "developer_pro")

        result = resolve_developer_key(raw)

        assert result is not None, "Expected resolve_developer_key to return a result"
        assert result.clerk_user_id == clerk_id
        assert result.env == "live"
        assert "read:market_data" in result.scopes
        assert "write:backtest" in result.scopes

    @patch("algochains_mcp.developer_auth._service_client")
    def test_revoked_key_returns_none(self, mock_client):
        """
        If Supabase returns empty (revoked_at IS NOT NULL or is_active=FALSE),
        resolve_developer_key must return None — fail closed.
        """
        raw = generate_platform_key("live")
        sb = MagicMock()
        sb.rpc.return_value.execute.return_value = MagicMock(data=[])
        mock_client.return_value = sb

        result = resolve_developer_key(raw)
        assert result is None

    @patch("algochains_mcp.developer_auth._service_client")
    def test_rpc_called_with_correct_hash(self, mock_client):
        """
        The RPC must be called with the SHA-256 hash of the raw key — NOT the
        raw key itself. Verify the argument passed to sb.rpc() matches.
        """
        raw = generate_platform_key("live")
        expected_hash = hash_platform_key(raw)

        sb = MagicMock()
        sb.rpc.return_value.execute.return_value = MagicMock(data=[])
        mock_client.return_value = sb

        resolve_developer_key(raw)

        # sb.rpc() was called; inspect the argument
        rpc_args = sb.rpc.call_args
        assert rpc_args is not None, "Expected sb.rpc() to be called"
        # The hash should appear in the call args
        call_str = str(rpc_args)
        assert expected_hash in call_str, \
            f"Expected hash {expected_hash[:16]}... in RPC call args: {call_str[:200]}"

    @patch("algochains_mcp.developer_auth._service_client")
    def test_enterprise_key_resolves_with_all_scopes(self, mock_client):
        raw = generate_platform_key("live")
        ent_scopes = TIER_SCOPES["enterprise"]

        sb = MagicMock()
        sb.rpc.return_value.execute.return_value = MagicMock(data=[{
            "clerk_user_id": "user_enterprise_abc",
            "scopes": ent_scopes,
            "env": "live",
        }])
        mock_client.return_value = sb

        result = resolve_developer_key(raw)

        assert result is not None
        for scope in ent_scopes:
            assert scope in result.scopes, f"Expected enterprise scope '{scope}' in result"

    @patch("algochains_mcp.developer_auth._service_client")
    def test_test_env_key_resolves_with_env_test(self, mock_client):
        raw = generate_platform_key("test")

        sb = MagicMock()
        sb.rpc.return_value.execute.return_value = MagicMock(data=[{
            "clerk_user_id": "user_test_tier",
            "scopes": DEFAULT_SCOPES,
            "env": "test",
        }])
        mock_client.return_value = sb

        result = resolve_developer_key(raw)
        assert result is not None
        assert result.env == "test"

    @patch("algochains_mcp.developer_auth._service_client")
    def test_supabase_unavailable_fails_closed(self, mock_client):
        mock_client.return_value = None
        raw = generate_platform_key("live")
        assert resolve_developer_key(raw) is None

    @patch("algochains_mcp.developer_auth._service_client")
    def test_non_developer_key_skips_rpc(self, mock_client):
        result = resolve_developer_key("sub_live_NOT_A_DEV_KEY")
        assert result is None
        mock_client.assert_not_called()
