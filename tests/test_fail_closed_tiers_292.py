"""
Follow-up to PR #292: developer-key tiers and scopes must fail closed.

Covers:
  1. Writer refuses unknown/empty/non-key tiers; reader denies empty scopes.
  2. Legacy developer_pro / pro match Django #930 (-> free, no Developer key).
  3. create_developer_key never mints without an authoritative server-side tier,
     and the MCP handler never forwards a caller-supplied tier.
  4. Stripe provisioning maps only known products; unknown -> no key.
  5. Rotation preserves existing scopes/tier and denies (before revoking) when empty.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from algochains_mcp.auth import key_contract, platform_auth
from algochains_mcp.auth.key_contract import (
    TIER_SCOPES,
    UnknownKeyTierError,
    build_insert_payload,
    canonical_key_tier,
    generate_platform_key,
    scopes_for_tier,
)
from algochains_mcp.developer_auth import invalidate_cache, resolve_developer_key

REPO = Path(__file__).resolve().parent.parent
USER_ID = "11111111-1111-1111-1111-111111111111"


# ── 1 + 2: writer contract ────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "tier",
    ["", None, "   ", "unknown", "free", "trader", "developer_pro", "pro", "starter", "Developer-Pro"],
)
def test_writer_refuses_non_key_tiers(tier):
    with pytest.raises(UnknownKeyTierError):
        build_insert_payload(generate_platform_key("live"), clerk_user_id="u", tier=tier)


def test_writer_requires_explicit_tier():
    with pytest.raises(TypeError):
        build_insert_payload(generate_platform_key("live"), clerk_user_id="u")


def test_writer_refuses_when_override_leaves_no_scopes():
    with pytest.raises(UnknownKeyTierError):
        build_insert_payload(
            generate_platform_key("live"), clerk_user_id="u", tier="developer",
            override_scopes=["agent:host"],  # enterprise-only -> filtered to []
        )
    with pytest.raises(UnknownKeyTierError):
        build_insert_payload(
            generate_platform_key("live"), clerk_user_id="u", tier="developer", override_scopes=[]
        )


@pytest.mark.parametrize("tier", ["developer_pro", "pro"])
def test_legacy_tiers_match_django_free(tier):
    # Django developer_entitlements._canonical_tier maps these to "free".
    assert canonical_key_tier(tier) == ""
    assert scopes_for_tier(tier) == []
    assert tier in key_contract.LEGACY_FREE_TIER_TOKENS


@pytest.mark.parametrize("tier", ["developer", "employee", "enterprise", " Enterprise "])
def test_key_tiers_keep_expected_scopes(tier):
    canonical = canonical_key_tier(tier)
    payload = build_insert_payload(generate_platform_key("live"), clerk_user_id="u", tier=tier)
    assert payload["tier_at_creation"] == canonical
    assert payload["scopes"] == TIER_SCOPES[canonical]
    assert {"read:market_data", "read:signals", "write:backtest"} <= set(payload["scopes"])
    assert ("agent:host" in payload["scopes"]) is (canonical == "enterprise")


def test_developer_scopes_grant_no_trading_or_ops_rights():
    # Owner/OWNER_API_TOKEN-gated trading & ops tools are outside every key scope.
    for scopes in TIER_SCOPES.values():
        for scope in scopes:
            assert not re.search(r"order|trade|exec|owner|admin|ops", scope), scope


# ── 1: reader denies empty scopes ─────────────────────────────────────────────
class TestReaderDeniesEmptyScopes:
    def setup_method(self):
        invalidate_cache()

    @staticmethod
    def _sb(rows):
        sb = MagicMock()
        sb.rpc.return_value.execute.return_value = MagicMock(data=rows)
        return sb

    @pytest.mark.parametrize("scopes", [[], None, "read:market_data", [None, ""], {}])
    @patch("algochains_mcp.developer_auth._service_client")
    def test_empty_or_malformed_scopes_denied(self, mock_client, scopes):
        row = {"clerk_user_id": "clerk_x", "env": "live"}
        if scopes is not None:
            row["scopes"] = scopes
        mock_client.return_value = self._sb([row])
        assert resolve_developer_key("ac_live_emptyscopes") is None

    @patch("algochains_mcp.developer_auth._service_client")
    def test_real_scopes_still_resolve(self, mock_client):
        mock_client.return_value = self._sb([{"clerk_user_id": "c", "scopes": ["read:signals"], "env": "live"}])
        resolved = resolve_developer_key("ac_live_realscopes")
        assert resolved is not None and resolved.scopes == ("read:signals",)


# ── 3: create_developer_key needs an authoritative tier ───────────────────────
@pytest.fixture
def supabase_env(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_auth, "_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(platform_auth, "_SUPABASE_ANON_KEY", "anon-key")
    monkeypatch.setattr(platform_auth, "_SUPABASE_SERVICE_KEY", "service-key")
    monkeypatch.setattr(platform_auth, "_SESSION_FILE", tmp_path / "platform_session.json")

    async def _validate(token):
        return {"user_id": USER_ID, "email": "dev@example.com", "aal": "aal2", "user_meta": {}}

    monkeypatch.setattr(platform_auth, "validate_live_token", _validate)
    monkeypatch.setattr(platform_auth, "_sync_algochains_core_insert", AsyncMock(), raising=False)


class _RecordingClient:
    """httpx.AsyncClient stand-in recording every request."""

    calls: list = []
    get_rows: list = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def _resp(self, status, data):
        r = MagicMock()
        r.status_code = status
        r.json.return_value = data
        r.text = json.dumps(data)
        return r

    async def get(self, url, headers=None):
        type(self).calls.append(("GET", url, None))
        return self._resp(200, type(self).get_rows)

    async def patch(self, url, headers=None, json=None):
        type(self).calls.append(("PATCH", url, json))
        return self._resp(204, {})

    async def post(self, url, headers=None, json=None):
        type(self).calls.append(("POST", url, json))
        return self._resp(201, [{"id": "new-row"}])


@pytest.fixture
def client():
    _RecordingClient.calls = []
    _RecordingClient.get_rows = []
    with patch("httpx.AsyncClient", _RecordingClient):
        yield _RecordingClient


@pytest.mark.parametrize("tier", [None, "", "developer_pro", "pro", "free"])
async def test_create_without_authoritative_tier_mints_nothing(supabase_env, client, tier):
    kwargs = {} if tier is None else {"tier": tier}
    result = await platform_auth.create_developer_key(access_token="t", **kwargs)
    assert result.get("error") == "developer_tier_unresolved"
    assert "key" not in result
    assert client.calls == []


async def test_create_with_trusted_tier_mints(supabase_env, client):
    result = await platform_auth.create_developer_key(access_token="t", tier="developer")
    assert result["status"] == "ok" and result["tier"] == "developer"
    posted = [c for c in client.calls if c[0] == "POST"]
    assert posted and posted[0][2]["tier_at_creation"] == "developer"


def test_mcp_handler_does_not_forward_caller_tier():
    src = (REPO / "src" / "algochains_mcp" / "server.py").read_text(encoding="utf-8")
    start = src.index('elif name == "create_developer_key":')
    block = src[start:src.index("elif name ==", start + 10)]
    assert "_create_key(" in block
    assert "tier" not in block.split("_create_key(", 1)[1].split("))", 1)[0]


# ── 5: rotation ───────────────────────────────────────────────────────────────
async def test_rotation_preserves_scopes_and_tier(supabase_env, client):
    client.get_rows = [{
        "id": "old", "env": "live", "name": "k", "tier_at_creation": "enterprise",
        "scopes": ["read:signals", "agent:host"],
    }]
    result = await platform_auth.rotate_developer_key(access_token="t", key_id="old")
    assert result["status"] == "ok"
    post = [c for c in client.calls if c[0] == "POST"][0][2]
    assert post["scopes"] == ["read:signals", "agent:host"]
    assert post["tier_at_creation"] == "enterprise"


@pytest.mark.parametrize("row", [
    {"scopes": [], "tier_at_creation": "developer"},
    {"tier_at_creation": "developer"},
    {"scopes": ["read:signals"], "tier_at_creation": ""},
    {"scopes": ["read:signals"], "tier_at_creation": "developer_pro"},
    {"scopes": ["agent:host"], "tier_at_creation": "developer"},
])
async def test_rotation_denies_without_scopes_or_tier_and_keeps_old_key(supabase_env, client, row):
    client.get_rows = [{"id": "old", "env": "live", "name": "k", **row}]
    result = await platform_auth.rotate_developer_key(access_token="t", key_id="old")
    assert result.get("error") == "rotation_denied"
    assert [c[0] for c in client.calls] == ["GET"]  # never revoked, never minted


# ── 4: Stripe product mapping ─────────────────────────────────────────────────
stripe_server = pytest.importorskip("stripe_app.server")


@pytest.mark.parametrize("product,tier", [
    ("developer-tier", "developer"), ("enterprise-tier", "enterprise"),
    ("paper-tier", None), ("live-tier", None), ("", None), ("developer_pro", None), ("pro", None),
])
def test_stripe_product_mapping(product, tier):
    assert stripe_server.tier_for_stripe_product(product) == tier


@pytest.mark.parametrize("body", [{"product_id": "paper-tier"}, {"product_id": "mystery"}, {}])
def test_stripe_unknown_product_provisions_no_key(monkeypatch, body):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(stripe_server, "_verify_request", AsyncMock(return_value=json.dumps(body).encode()))
    gen = MagicMock(side_effect=AssertionError("must not mint"))
    monkeypatch.setattr(key_contract, "generate_platform_key", gen)
    resp = TestClient(stripe_server.app).post("/app/provision", content=b"{}")
    assert resp.status_code == 403
    assert resp.json()["error"] == "product_not_entitled"
    assert "credentials" not in resp.json()
    gen.assert_not_called()


def test_stripe_known_product_still_provisions(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setattr(
        stripe_server, "_verify_request",
        AsyncMock(return_value=json.dumps({"product_id": "developer-tier", "email": "a@b.c"}).encode()),
    )
    resp = TestClient(stripe_server.app).post("/app/provision", content=b"{}")
    assert resp.status_code == 200
    assert resp.json()["credentials"]["ALGOCHAINS_API_KEY"].startswith("ac_live_")
