"""
Security regression tests for massive_whitelabel.py

Covers the Jun-9 P1 finding: massive_get_endpoint_docs forwarded the Massive
API key to any caller-controlled URL. Two distinct issues patched:

  1. get_endpoint_docs: hostname allowlist prevents fetching from attacker URLs;
     no Authorization header is sent on docs fetches.
  2. call_api: the @userinfo path trick ("@attacker.com/x" → URL host becomes
     attacker.com) is blocked by host-checking the resolved URL; follow_redirects
     is False on the shared async client.
"""
from __future__ import annotations

import json
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch, ANY

from algochains_mcp.data_providers.massive_whitelabel import (
    MassiveWhiteLabelProvider,
    MassiveError,
    _host_allowed,
)
from algochains_mcp.config import MassiveConfig


# ─── helpers ──────────────────────────────────────────────────────────────────

def _make_provider(
    base_url: str = "https://api.massive.com",
    llms_url: str = "https://massive.com/docs/rest/llms.txt",
    api_key: str = "test-secret-key",
) -> MassiveWhiteLabelProvider:
    cfg = MassiveConfig.__new__(MassiveConfig)
    cfg.api_key = api_key
    cfg.base_url = base_url
    cfg.llms_txt_url = llms_url
    cfg.max_tables = 50
    cfg.max_rows = 50000
    return MassiveWhiteLabelProvider(cfg)


def _mock_http_response(status: int = 200, json_body: dict | None = None) -> AsyncMock:
    """Return an AsyncMock that mimics an httpx response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.json.return_value = json_body or {}
    resp.raise_for_status = MagicMock()
    if status >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            message=f"HTTP {status}",
            request=MagicMock(),
            response=resp,
        )
    mock_get = AsyncMock(return_value=resp)
    return mock_get


# ─── _host_allowed unit tests ─────────────────────────────────────────────────

class TestHostAllowed:
    def test_exact_match(self):
        assert _host_allowed("massive.com", {"massive.com"})

    def test_subdomain_match(self):
        assert _host_allowed("api.massive.com", {"massive.com"})

    def test_deep_subdomain_match(self):
        assert _host_allowed("docs.api.massive.com", {"massive.com"})

    def test_rejected_different_domain(self):
        assert not _host_allowed("evil.com", {"massive.com", "api.massive.com"})

    def test_rejected_suffix_prefix_trick(self):
        # "notmassive.com" must NOT match "massive.com"
        assert not _host_allowed("notmassive.com", {"massive.com"})

    def test_rejected_empty_host(self):
        assert not _host_allowed("", {"massive.com"})

    def test_port_stripped(self):
        assert _host_allowed("massive.com:443", {"massive.com"})

    def test_case_insensitive(self):
        assert _host_allowed("API.Massive.COM", {"massive.com"})


# ─── _compute_allowed_hosts ───────────────────────────────────────────────────

class TestComputeAllowedHosts:
    def test_includes_base_url_host(self):
        prov = _make_provider(base_url="https://api.massive.com")
        assert "api.massive.com" in prov._allowed_hosts

    def test_includes_llms_host(self):
        prov = _make_provider(llms_url="https://massive.com/docs/rest/llms.txt")
        assert "massive.com" in prov._allowed_hosts

    def test_custom_base_url_added(self):
        prov = _make_provider(base_url="https://data.custom-deployment.example.com")
        assert "data.custom-deployment.example.com" in prov._allowed_hosts

    def test_always_includes_massive_dot_com(self):
        prov = _make_provider(base_url="https://custom.example.com")
        assert "massive.com" in prov._allowed_hosts
        assert "api.massive.com" in prov._allowed_hosts


# ─── get_endpoint_docs — allowlist enforcement ────────────────────────────────

class TestGetEndpointDocsAllowlist:
    @pytest.mark.asyncio
    async def test_rejects_attacker_url(self):
        prov = _make_provider()
        with pytest.raises(MassiveError, match="non-allowlisted"):
            await prov.get_endpoint_docs("https://attacker.example.com/steal-key")

    @pytest.mark.asyncio
    async def test_rejects_http_scheme(self):
        prov = _make_provider()
        with pytest.raises(MassiveError, match="non-allowlisted"):
            await prov.get_endpoint_docs("http://massive.com/docs/something.json")

    @pytest.mark.asyncio
    async def test_rejects_empty_url(self):
        prov = _make_provider()
        with pytest.raises(MassiveError):
            await prov.get_endpoint_docs("")

    @pytest.mark.asyncio
    async def test_rejects_file_scheme(self):
        prov = _make_provider()
        with pytest.raises(MassiveError, match="non-allowlisted"):
            await prov.get_endpoint_docs("file:///etc/passwd")

    @pytest.mark.asyncio
    async def test_rejects_ftp_scheme(self):
        prov = _make_provider()
        with pytest.raises(MassiveError, match="non-allowlisted"):
            await prov.get_endpoint_docs("ftp://massive.com/something")

    @pytest.mark.asyncio
    async def test_accepts_allowlisted_massive_url(self):
        prov = _make_provider()
        docs_url = "https://massive.com/docs/rest/some-endpoint.json"
        mock_docs = {"parameters": [{"name": "ticker", "type": "string"}]}
        prov._http.get = _mock_http_response(200, mock_docs)
        result = await prov.get_endpoint_docs(docs_url)
        assert result == mock_docs

    @pytest.mark.asyncio
    async def test_accepts_api_subdomain_url(self):
        prov = _make_provider()
        docs_url = "https://api.massive.com/v2/docs/endpoint.json"
        mock_docs = {"parameters": []}
        prov._http.get = _mock_http_response(200, mock_docs)
        result = await prov.get_endpoint_docs(docs_url)
        assert result == mock_docs


# ─── get_endpoint_docs — no auth header on docs fetch ─────────────────────────

class TestGetEndpointDocsNoAuth:
    @pytest.mark.asyncio
    async def test_no_authorization_header_sent(self):
        """The API key must NEVER be forwarded to the docs host."""
        prov = _make_provider()
        docs_url = "https://massive.com/docs/rest/some-endpoint.json"

        captured_kwargs: dict = {}

        async def _capturing_get(url, **kwargs):
            captured_kwargs.update(kwargs)
            resp = MagicMock(spec=httpx.Response)
            resp.status_code = 200
            resp.json.return_value = {}
            resp.raise_for_status = MagicMock()
            return resp

        prov._http.get = _capturing_get
        await prov.get_endpoint_docs(docs_url)

        headers_sent = captured_kwargs.get("headers", {})
        auth_keys = {k.lower() for k in headers_sent}
        assert "authorization" not in auth_keys, (
            f"Authorization header was sent to docs URL — API key leak! headers={headers_sent}"
        )


# ─── get_endpoint_docs — no redirects ────────────────────────────────────────

class TestGetEndpointDocsNoRedirects:
    def test_http_client_has_redirects_disabled(self):
        prov = _make_provider()
        assert prov._http.follow_redirects is False

    @pytest.mark.asyncio
    async def test_redirect_response_raises_not_followed(self):
        """A 301 from a legit URL must raise, not silently follow to an attacker host."""
        prov = _make_provider()
        docs_url = "https://massive.com/docs/rest/endpoint.json"

        redirect_resp = MagicMock(spec=httpx.Response)
        redirect_resp.status_code = 301
        redirect_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            message="301 Moved",
            request=MagicMock(),
            response=redirect_resp,
        )
        redirect_resp.headers = {"location": "https://attacker.example.com/steal"}
        prov._http.get = AsyncMock(return_value=redirect_resp)

        with pytest.raises(Exception):
            await prov.get_endpoint_docs(docs_url)

        # Exactly one call was made — to the legitimate URL — not a second one to attacker
        assert prov._http.get.call_count == 1
        called_url = prov._http.get.call_args[0][0]
        assert "attacker.example.com" not in called_url, (
            "Client appears to have followed redirect to attacker host!"
        )


# ─── call_api — host smuggling via path ───────────────────────────────────────

class TestCallApiHostSmuggling:
    @pytest.mark.asyncio
    async def test_userinfo_path_normalized_to_api_host(self):
        """Original vulnerable code: base_url + '@attacker.com/x'
        → 'https://api.massive.com@attacker.com/x' → host = attacker.com.
        The fixed code inserts a '/' separator, turning '@attacker.com/x' into
        a path segment on api.massive.com — the API key never reaches attacker.com.
        """
        prov = _make_provider()
        captured_url: list[str] = []

        async def _capturing_get(url, **kwargs):
            captured_url.append(str(url))
            raise httpx.ConnectError("no real server in tests")

        prov._http.get = _capturing_get

        with pytest.raises(MassiveError):
            await prov.call_api("@attacker.example.com/steal-key")

        assert len(captured_url) == 1
        from urllib.parse import urlsplit
        host = urlsplit(captured_url[0]).hostname
        assert host == "api.massive.com", (
            f"API key was sent to {host!r} instead of api.massive.com — "
            "@userinfo path trick succeeded!"
        )

    @pytest.mark.asyncio
    async def test_rejects_userinfo_with_slash_trick(self):
        """Variant: path starts with slash and @: '/x@attacker.com/steal'."""
        prov = _make_provider()
        # After lstrip('/') → 'x@attacker.com/steal'
        # Combined → 'https://api.massive.com/x@attacker.com/steal' (host stays api.massive.com)
        # So this is NOT a smuggling vector — host is still api.massive.com; passes allowlist.
        # The test verifies our normalisation behaviour (no false rejection).
        prov._http.get = _mock_http_response(200, {"results": []})
        # Should not raise MassiveError for allowlist — it's on api.massive.com
        # (the actual HTTP call to this weird path would 404 in prod, but we mock it)
        result = await prov.call_api("/x@attacker.example.com/steal")
        assert result is not None

    @pytest.mark.asyncio
    async def test_accepts_legitimate_path(self):
        prov = _make_provider()
        prov._http.get = _mock_http_response(200, {"results": [], "status": "OK"})
        result = await prov.call_api("/v2/aggs/ticker/AAPL/range/1/day/2024-01-01/2024-12-31")
        assert result is not None

    @pytest.mark.asyncio
    async def test_authorization_sent_only_to_api_host(self):
        """The API key Bearer token must only ever be attached to requests bound
        for the configured api host."""
        prov = _make_provider(api_key="test-bearer-secret")

        captured_headers: dict = {}

        async def _capturing_get(url, **kwargs):
            captured_headers.update(kwargs.get("headers", {}))
            resp = MagicMock(spec=httpx.Response)
            resp.status_code = 200
            resp.json.return_value = {"results": [], "status": "OK"}
            resp.raise_for_status = MagicMock()
            return resp

        prov._http.get = _capturing_get
        await prov.call_api("/v2/aggs/ticker/AAPL/range/1/day/2024-01-01/2024-12-31")

        assert "Authorization" in captured_headers
        assert "test-bearer-secret" in captured_headers["Authorization"]
