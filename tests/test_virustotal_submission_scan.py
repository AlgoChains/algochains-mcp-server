"""VirusTotal hash gate + strategy-spec path jail."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from algochains_mcp.builder_sdk.submission_pipeline import SubmissionPipeline
from algochains_mcp import data_ingestion


def test_virustotal_hash_allows_clean_response():
    pipeline = SubmissionPipeline(api_key="k", django_url="https://algochains.ai")

    async def _run():
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"allowed": True, "verdict": "clean"}
        client = AsyncMock()
        client.post = AsyncMock(return_value=response)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=client):
            ok, reason = await pipeline._virustotal_hash("a" * 64, "clerk:jeremy")
        assert ok is True
        assert reason == ""
        client.post.assert_awaited()
        args, kwargs = client.post.await_args
        assert args[0].endswith("/api/v1/security/scan-hash/")
        assert kwargs["json"]["subject"] == "clerk:jeremy"

    asyncio.run(_run())


def test_virustotal_hash_fails_closed_on_quota():
    pipeline = SubmissionPipeline(api_key="k", django_url="https://algochains.ai")

    async def _run():
        response = MagicMock()
        response.status_code = 400
        response.json.return_value = {"reason": "Daily file-scan limit reached (5/5 per person)."}
        client = AsyncMock()
        client.post = AsyncMock(return_value=response)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=client):
            ok, reason = await pipeline._virustotal_hash("b" * 64, "clerk:jeremy")
        assert ok is False
        assert "5/5" in reason

    asyncio.run(_run())


def test_register_strategy_rejects_path_outside_jail(tmp_path, monkeypatch):
    jail = tmp_path / "custom_strategies"
    jail.mkdir()
    secret = tmp_path / "outside" / "secrets.json"
    secret.parent.mkdir()
    secret.write_text('{"entry_rules": [], "exit_rules": []}', encoding="utf-8")
    monkeypatch.setattr(data_ingestion, "_CUSTOM_STRATEGIES_DIR", jail)
    monkeypatch.setattr(data_ingestion, "_STATE_DIR", tmp_path)
    monkeypatch.delenv("ALGOCHAINS_STRATEGY_SPEC_ROOTS", raising=False)
    monkeypatch.delenv("ALGOCHAINS_VERIFIED_ARTIFACT_DIR", raising=False)

    result = data_ingestion.register_strategy(
        name="sneaky",
        asset_class="equities",
        timeframe="1d",
        symbols=["AAPL"],
        spec_path=str(secret),
    )
    assert result["success"] is False
    assert "jail" in result["error"]


def test_register_strategy_accepts_path_inside_jail(tmp_path, monkeypatch):
    jail = tmp_path / "custom_strategies"
    jail.mkdir()
    spec = jail / "ok.json"
    spec.write_text('{"entry_rules": ["x"], "exit_rules": ["y"]}', encoding="utf-8")
    monkeypatch.setattr(data_ingestion, "_CUSTOM_STRATEGIES_DIR", jail)
    monkeypatch.setattr(data_ingestion, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(data_ingestion, "_INGESTION_REGISTRY", tmp_path / "registry.json")
    monkeypatch.delenv("ALGOCHAINS_STRATEGY_SPEC_ROOTS", raising=False)
    monkeypatch.delenv("ALGOCHAINS_VERIFIED_ARTIFACT_DIR", raising=False)

    result = data_ingestion.register_strategy(
        name="ok spec",
        asset_class="equities",
        timeframe="1d",
        symbols=["AAPL"],
        spec_path=str(spec),
    )
    assert result.get("success") is True, result
