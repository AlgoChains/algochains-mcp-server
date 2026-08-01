"""
Tests for the OAuth user_id binding security fix in brokers/oauth_manager.py.

Old exploit: generate_auth_url(broker, user_id=<victim>) accepted a free-form
user_id argument with no proof the caller was that user. The attacker could
complete the OAuth flow with their own broker account and have the victim's
user_id bound to the attacker's broker tokens.

Fix: user_id is now derived exclusively from a live-validated access_token
(the same helper used for the developer-key fix in platform_auth.py).
"""
import os
from unittest.mock import AsyncMock, patch

import pytest

from algochains_mcp.brokers import oauth_manager


VICTIM_USER_ID = "victim-user-id"
ATTACKER_USER_ID = "attacker-user-id"


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    monkeypatch.setattr(oauth_manager, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(oauth_manager, "_OAUTH_TOKENS_FILE", tmp_path / "oauth_tokens.json")
    monkeypatch.setattr(oauth_manager, "_OAUTH_STATES_FILE", tmp_path / "oauth_states.json")
    monkeypatch.setenv("SCHWAB_CLIENT_ID", "test-client-id")


def _patch_validate(*, user_id=None, error=None):
    async def _fake_validate(token):
        if error:
            return {"error": error}
        return {"user_id": user_id, "email": "u@example.com", "aal": "aal2"}
    return patch(
        "algochains_mcp.auth.platform_auth.validate_live_token",
        new=AsyncMock(side_effect=_fake_validate),
    )


class TestGenerateAuthUrlNoLongerAcceptsRawUserId:
    async def test_caller_supplied_victim_user_id_is_ignored(self):
        """
        The exploit path: an attacker who only has THEIR OWN access_token
        must never be able to bind the resulting OAuth state to a victim's
        user_id, no matter what they pass.
        """
        with _patch_validate(user_id=ATTACKER_USER_ID):
            result = await oauth_manager.generate_auth_url(
                broker="schwab", access_token="attacker-own-token"
            )
        assert result["success"] is True
        state = result["state"]
        states = oauth_manager._load_states()
        assert states[state]["user_id"] == ATTACKER_USER_ID
        assert states[state]["user_id"] != VICTIM_USER_ID

    async def test_invalid_token_rejected(self):
        with _patch_validate(error="Token validation failed — token is invalid or expired."):
            result = await oauth_manager.generate_auth_url(
                broker="schwab", access_token="garbage"
            )
        assert result["success"] is False
        assert "error" in result

    async def test_missing_token_argument_raises(self):
        with pytest.raises(TypeError):
            await oauth_manager.generate_auth_url(broker="schwab", user_id=VICTIM_USER_ID)


class TestLegitimatePathStillWorks:
    async def test_valid_token_generates_url_bound_to_caller(self):
        with _patch_validate(user_id=VICTIM_USER_ID):
            result = await oauth_manager.generate_auth_url(
                broker="schwab", access_token="victim-own-token"
            )
        assert result["success"] is True
        assert "auth_url" in result
        state = result["state"]
        states = oauth_manager._load_states()
        assert states[state]["user_id"] == VICTIM_USER_ID
        assert states[state]["broker"] == "schwab"

    async def test_unsupported_broker_still_rejected_after_validation(self):
        with _patch_validate(user_id=VICTIM_USER_ID):
            result = await oauth_manager.generate_auth_url(
                broker="not-a-broker", access_token="victim-own-token"
            )
        assert result["success"] is False
        assert "not supported" in result["error"]


class TestExchangeCodeTrustsOnlyValidatedState:
    async def test_exchange_uses_state_user_id_not_attacker_supplied(self, monkeypatch):
        """
        exchange_code() has no user_id parameter at all — it must keep
        reading user_id from the validated state set up at generation time.
        """
        with _patch_validate(user_id=VICTIM_USER_ID):
            gen_result = await oauth_manager.generate_auth_url(
                broker="schwab", access_token="victim-own-token"
            )
        state = gen_result["state"]

        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_resp.json = lambda: {
            "access_token": "broker-access-token",
            "refresh_token": "broker-refresh-token",
            "expires_in": 1800,
        }

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_resp

        with patch("httpx.AsyncClient", return_value=mock_client):
            exchange_result = await oauth_manager.exchange_code(
                state=state, code="auth-code-from-broker"
            )

        assert exchange_result["success"] is True
        assert exchange_result["user_id"] == VICTIM_USER_ID
