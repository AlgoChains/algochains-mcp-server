from __future__ import annotations

def test_connect_onyx_docs_rejects_secret_like_direct_file(tmp_path, monkeypatch):
    from algochains_mcp import data_ingestion

    monkeypatch.setattr(data_ingestion, "_STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(data_ingestion, "_INGESTION_REGISTRY", tmp_path / "state" / "ingestion_registry.json")
    monkeypatch.setattr(data_ingestion, "_CUSTOM_DATA_DIR", tmp_path / "state" / "custom_data")
    monkeypatch.setattr(data_ingestion, "_CUSTOM_STRATEGIES_DIR", tmp_path / "state" / "custom_strategies")

    secret_file = tmp_path / "operator.env"
    secret_file.write_text("ALGOCHAINS_SECRET_REVIEW_PROOF=super-sensitive-value\n", encoding="utf-8")

    result = data_ingestion.connect_onyx_docs(
        doc_paths=[str(secret_file)],
        doc_type="general",
        onyx_url="http://onyx.internal:8085",
        onyx_key="proof-key",
    )

    assert result["success"] is False
    assert "No indexable files" in result["error"]
    assert result["rejected"][0]["path"] == str(secret_file)


def test_broker_oauth_and_prop_monitor_tools_require_owner_authorization():
    from algochains_mcp.tool_danger_tiers import TIER_ORDER_EXEC, get_danger_tier
    from algochains_mcp.tool_policy import evaluate_dynamic_tool

    protected_tools = [
        "generate_broker_auth_url",
        "exchange_broker_oauth_code",
        "get_connected_brokers",
        "revoke_broker_connection",
        "register_prop_fund_account",
        "run_prop_fund_check",
    ]

    for tool_name in protected_tools:
        assert get_danger_tier(tool_name) >= TIER_ORDER_EXEC
        decision = evaluate_dynamic_tool(tool_name, {}, expected_owner_token="owner-token")
        assert decision.allow is False
        assert decision.required_secret == "OWNER_API_TOKEN"


def test_onyx_doc_filter_skips_sensitive_directory_candidates(tmp_path, monkeypatch):
    from algochains_mcp import data_ingestion

    monkeypatch.setattr(data_ingestion, "_STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(data_ingestion, "_INGESTION_REGISTRY", tmp_path / "state" / "ingestion_registry.json")
    monkeypatch.setattr(data_ingestion, "_CUSTOM_DATA_DIR", tmp_path / "state" / "custom_data")
    monkeypatch.setattr(data_ingestion, "_CUSTOM_STRATEGIES_DIR", tmp_path / "state" / "custom_strategies")

    docs_dir = tmp_path / "docs"
    hidden_dir = docs_dir / ".config"
    hidden_dir.mkdir(parents=True)
    (hidden_dir / "secrets.json").write_text('{"token":"proof"}', encoding="utf-8")

    result = data_ingestion.connect_onyx_docs(
        doc_paths=[str(docs_dir)],
        doc_type="general",
        onyx_url="http://onyx.internal:8085",
        onyx_key="proof-key",
    )

    assert result["success"] is False
    assert result["rejected"][0]["path"] == str(hidden_dir / "secrets.json")


def test_connect_onyx_docs_rejects_path_outside_ingest_root(tmp_path, monkeypatch):
    """2026-07-13: Tier-1 callers must not ingest arbitrary host paths into shared RAG."""
    from algochains_mcp import data_ingestion

    jail = tmp_path / "onyx_ingest"
    jail.mkdir()
    outside = tmp_path / "research_report.md"
    outside.write_text("secret research body", encoding="utf-8")
    monkeypatch.setenv("ONYX_INGEST_ROOT", str(jail))
    monkeypatch.setattr(data_ingestion, "_STATE_DIR", tmp_path)

    result = data_ingestion.connect_onyx_docs([str(outside)], "general")
    assert result.get("success") is False
    rejected = result.get("rejected") or []
    assert any("outside allowed Onyx ingest root" in (r.get("reason") or "") for r in rejected) or (
        "outside allowed Onyx ingest root" in str(result.get("error", ""))
        or "No indexable files" in str(result.get("error", ""))
    )
