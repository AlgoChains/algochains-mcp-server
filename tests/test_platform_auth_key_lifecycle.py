"""
Tests for the developer-key lifecycle security fix in auth/platform_auth.py.

Covers:
  - validate_live_token(): live Supabase /auth/v1/user validation helper
  - create_developer_key / list_developer_keys / rotate_developer_key /
    revoke_developer_key now require a live-validated access_token and
    never trust the cached global session file's user_id for authorization.

The old exploit: any caller (zero credentials of their own) could call
create_developer_key() with no arguments and mint a key for whoever's
session happened to be cached in state/platform_session.json. These tests
prove that path is closed and that the legitimate path (valid AAL2 token)
still works.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from algochains_mcp.auth import platform_auth


AAL2_USER_ID = "11111111-1111-1111-1111-111111111111"
OTHER_USER_ID = "22222222-2222-2222-2222-222222222222"


def _mock_response(status_code, json_data):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.text = str(json_data)
    return resp


def _fake_async_client(*, post=None, get=None):
    """Build a stand-in for httpx.AsyncClient supporting `async with ... as client`."""
    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, headers=None, json=None):
            return await post(url, headers=headers, json=json)

        async def get(self, url, headers=None):
            return await get(url, headers=headers)

    return _FakeClient


@pytest.fixture(autouse=True)
def _configure_supabase(monkeypatch):
    monkeypatch.setattr(platform_auth, "_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(platform_auth, "_SUPABASE_ANON_KEY", "anon-key")
    monkeypatch.setattr(platform_auth, "_SUPABASE_SERVICE_KEY", "service-key")


@pytest.fixture(autouse=True)
def _isolate_session_file(tmp_path, monkeypatch):
    monkeypatch.setattr(platform_auth, "_SESSION_FILE", tmp_path / "platform_session.json")


class TestValidateLiveToken:
    async def test_empty_token_rejected_without_network_call(self):
        result = await platform_auth.validate_live_token("")
        assert "error" in result

    async def test_valid_token_returns_user_and_aal(self):
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get.return_value = _mock_response(
            200, {"id": AAL2_USER_ID, "email": "victim@example.com"}
        )
        with patch("httpx.AsyncClient", return_value=mock_client), \
             patch.object(platform_auth, "_aal_level", return_value="aal2"):
            result = await platform_auth.validate_live_token("live-token-for-victim")
        assert result["user_id"] == AAL2_USER_ID
        assert result["aal"] == "aal2"

    async def test_invalid_token_rejected(self):
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get.return_value = _mock_response(401, {"msg": "invalid token"})
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await platform_auth.validate_live_token("garbage-token")
        assert "error" in result


def _patch_validate(monkeypatch, *, user_id, aal):
    async def _fake_validate(token):
        if token == "valid-token":
            return {"user_id": user_id, "email": "u@example.com", "aal": aal}
        return {"error": "Token validation failed — token is invalid or expired."}
    monkeypatch.setattr(platform_auth, "validate_live_token", _fake_validate)


class TestCreateDeveloperKeyExploitClosed:
    async def test_no_token_argument_is_rejected(self):
        """The old exploit: calling with zero credentials must no longer mint a key."""
        with pytest.raises(TypeError):
            await platform_auth.create_developer_key()  # access_token now required

    async def test_invalid_token_cannot_mint_key(self, monkeypatch):
        _patch_validate(monkeypatch, user_id=AAL2_USER_ID, aal="aal2")
        result = await platform_auth.create_developer_key(access_token="not-a-real-token")
        assert "error" in result

    async def test_aal1_token_cannot_mint_key(self, monkeypatch):
        """A live, valid-but-unverified (AAL1) token must still be rejected."""
        _patch_validate(monkeypatch, user_id=AAL2_USER_ID, aal="aal1")
        result = await platform_auth.create_developer_key(access_token="valid-token")
        assert result.get("error") == "requires_mfa_challenge"

    async def test_cached_global_session_is_never_consulted(self, monkeypatch):
        """
        Even if the global session file caches a DIFFERENT (victim) user's
        AAL2 session, create_developer_key must mint under the live-validated
        caller's identity only — never the cached file's user_id.
        """
        platform_auth._save_session({
            "access_token": "victim-cached-token",
            "user_id": OTHER_USER_ID,
        })
        _patch_validate(monkeypatch, user_id=AAL2_USER_ID, aal="aal2")

        captured = {}

        async def _post(url, headers=None, json=None):
            captured["user_id"] = json["user_id"]
            return _mock_response(201, [{"id": "key-row-1"}])

        with patch("httpx.AsyncClient", _fake_async_client(post=_post)):
            result = await platform_auth.create_developer_key(access_token="valid-token")

        assert result["status"] == "ok"
        assert captured["user_id"] == AAL2_USER_ID
        assert captured["user_id"] != OTHER_USER_ID


class TestLegitimatePathStillWorks:
    async def test_valid_aal2_token_mints_key_for_self(self, monkeypatch):
        _patch_validate(monkeypatch, user_id=AAL2_USER_ID, aal="aal2")

        async def _post(url, headers=None, json=None):
            assert json["user_id"] == AAL2_USER_ID
            return _mock_response(201, [{"id": "key-row-2"}])

        with patch("httpx.AsyncClient", _fake_async_client(post=_post)):
            result = await platform_auth.create_developer_key(
                access_token="valid-token", name="my-key", env="test"
            )

        assert result["status"] == "ok"
        assert result["key"].startswith("ac_test_")
        assert result["key_id"] == "key-row-2"

    async def test_list_developer_keys_requires_valid_token(self, monkeypatch):
        _patch_validate(monkeypatch, user_id=AAL2_USER_ID, aal="aal1")

        async def _get(url, headers=None):
            return _mock_response(200, [])

        with patch("httpx.AsyncClient", _fake_async_client(get=_get)):
            result = await platform_auth.list_developer_keys(access_token="valid-token")

        # list does not require AAL2, only a valid live token
        assert result["status"] == "ok"

    async def test_list_developer_keys_rejects_bad_token(self, monkeypatch):
        _patch_validate(monkeypatch, user_id=AAL2_USER_ID, aal="aal2")
        result = await platform_auth.list_developer_keys(access_token="garbage")
        assert "error" in result


class TestRotateAndRevokeRequireLiveToken:
    async def test_rotate_requires_token_argument(self):
        with pytest.raises(TypeError):
            await platform_auth.rotate_developer_key(key_id="abc")

    async def test_rotate_rejects_invalid_token(self, monkeypatch):
        _patch_validate(monkeypatch, user_id=AAL2_USER_ID, aal="aal2")
        result = await platform_auth.rotate_developer_key(
            access_token="garbage", key_id="abc"
        )
        assert "error" in result

    async def test_revoke_requires_token_argument(self):
        with pytest.raises(TypeError):
            await platform_auth.revoke_developer_key(key_id="abc")

    async def test_revoke_rejects_aal1_token(self, monkeypatch):
        _patch_validate(monkeypatch, user_id=AAL2_USER_ID, aal="aal1")
        result = await platform_auth.revoke_developer_key(
            access_token="valid-token", key_id="abc"
        )
        assert result.get("error") == "requires_mfa_challenge"
