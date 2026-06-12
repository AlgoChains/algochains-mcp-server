from __future__ import annotations

import asyncio
import base64
import json

from algochains_mcp.tradovate_token_status import summarize_tradovate_token_state


def _jwt_with_exp(exp: int) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"{header}.{payload}.sig"


def test_summarizes_state_token_json_without_exposing_token(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    token = _jwt_with_exp(2_000)
    (state_dir / "tradovate_token.json").write_text(
        json.dumps(
            {
                "access_token": token,
                "expirationTime": "1970-01-01T00:33:20Z",
            }
        )
    )

    result = summarize_tradovate_token_state(tmp_path, now=1_000)

    assert result["present"] is True
    assert result["status"] == "expiring_soon"
    assert result["expires_in_seconds"] == 1_000
    assert result["primary_source"] == "state/tradovate_token.json"
    assert "state/tradovate_token.json" in {source["source"] for source in result["sources"]}
    assert token not in json.dumps(result)


def test_uses_best_available_expiry_when_legacy_file_is_stale(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (tmp_path / "tradovate_token_live.txt").write_text(_jwt_with_exp(900))
    (state_dir / "tradovate_token.json").write_text(
        json.dumps({"accessToken": _jwt_with_exp(5_000), "expires_at_epoch": 5_000})
    )

    result = summarize_tradovate_token_state(tmp_path, now=1_000)

    assert result["present"] is True
    assert result["status"] == "ok"
    assert result["expires_in_seconds"] == 4_000
    sources = {source["source"]: source for source in result["sources"]}
    assert sources["tradovate_token_live.txt"]["expired"] is True
    assert sources["state/tradovate_token.json"]["expired"] is False


def test_includes_sanitized_guardian_failure_state(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "tradovate_token_guardian_state.json").write_text(
        json.dumps(
            {
                "status": "failed",
                "last_error": "All tiers failed",
                "failed_tiers": ["primary", "desktop", "cloud"],
                "access_token": "secret-token-value",
                "refresh_secret": "secret-refresh-value",
            }
        )
    )

    result = summarize_tradovate_token_state(tmp_path, now=1_000)

    assert result["status"] == "missing"
    assert result["guardian"]["status"] == "failed"
    assert result["guardian"]["last_error"] == "All tiers failed"
    assert result["guardian"]["failed_tiers"] == ["primary", "desktop", "cloud"]
    serialized = json.dumps(result)
    assert "secret-token-value" not in serialized
    assert "secret-refresh-value" not in serialized


def test_get_bot_health_uses_multi_source_tradovate_token_summary(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "tradovate_token.json").write_text(
        json.dumps({"accessToken": _jwt_with_exp(5_000), "expires_at_epoch": 5_000})
    )
    (state_dir / "tradovate_token_guardian_state.json").write_text(
        json.dumps({"status": "ok", "last_success": "2026-06-12T16:00:00Z"})
    )
    monkeypatch.setenv("ALGOCHAINS_CONTROL_TOWER", str(tmp_path))

    import algochains_mcp.server as srv

    result = asyncio.run(srv.call_tool("get_bot_health", {}))
    payload = json.loads(result[0].text)

    token = payload["tradovate_token"]
    assert token["present"] is True
    assert token["status"] == "ok"
    assert token["expires_in_seconds"] == 4_000
    assert token["primary_source"] == "state/tradovate_token.json"
    assert token["guardian"]["status"] == "ok"
