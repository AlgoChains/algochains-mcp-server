"""SSRF regression tests for the public Learn Hub health tool."""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx


def _decode(result: list[Any]) -> dict[str, Any]:
    item = result[0]
    return json.loads(item.text if hasattr(item, "text") else str(item))


class _Response:
    def __init__(
        self,
        status_code: int,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}


def test_learn_hub_schema_has_no_caller_controlled_base_url() -> None:
    import algochains_mcp.server as srv

    tool = next(tool for tool in srv.TOOLS if tool.name == "get_learn_hub_health")

    assert tool.inputSchema == {"type": "object", "properties": {}, "required": []}


def test_learn_hub_ignores_legacy_base_url_and_uses_fixed_hosts(
    monkeypatch,
) -> None:
    import algochains_mcp.server as srv

    requested_urls: list[str] = []

    class _Client:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["follow_redirects"] is False

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def get(self, url: str) -> _Response:
            requested_urls.append(url)
            if url.endswith("/learn/feed.xml"):
                return _Response(200, headers={"content-type": "application/rss+xml"})
            if url == "https://learn.algochains.ai/":
                return _Response(
                    301,
                    headers={"location": "https://algochains.ai/learn/"},
                )
            return _Response(200)

    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    result = asyncio.run(
        srv.call_tool(
            "get_learn_hub_health",
            {"base_url": "http://169.254.169.254/latest/meta-data"},
        )
    )
    payload = _decode(result)

    assert payload["base_url"] == "https://algochains.ai"
    assert payload["healthy"] is True
    assert requested_urls == [
        "https://algochains.ai/learn/",
        "https://algochains.ai/learn/feed.xml",
        "https://learn.algochains.ai/",
    ]
    assert all("169.254.169.254" not in url for url in requested_urls)
