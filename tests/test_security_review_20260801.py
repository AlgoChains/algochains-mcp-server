"""Regressions for the 2026-08-01 application-security review (findings 1-4).

Each test asserts the property that was violated, not the shape of the fix, so a
future refactor that reopens the hole fails here rather than passing on a moved
line number.
"""
from __future__ import annotations

import ast
import collections
import json
import pathlib


# ── Finding 1 (High): /mcp bypassed the bridge role matrices ──────────────────

def test_http_transport_denies_owner_tools_to_transport_bearer():
    """`_dispatch_jsonrpc` called server.call_tool() directly, so anything holding
    the transport secret reached every registered tool."""
    from algochains_mcp.http_transport import _authorize_tool_call

    for tool in ("get_account", "get_positions", "get_orders",
                 "portfolio_summary", "execute_dynamic_tool"):
        allowed, denial = _authorize_tool_call(tool, {})
        assert allowed is False, f"{tool} reachable over /mcp without owner authorization"
        assert denial, f"{tool} denied without a reason payload"


def test_http_transport_still_serves_the_public_surface():
    """A gate that denies everything is not a fix."""
    from algochains_mcp.http_transport import _authorize_tool_call, _public_tool_names

    allowed, _ = _authorize_tool_call("browse_strategy_marketplace", {})
    assert allowed is True
    assert len(_public_tool_names()) > 0


def test_http_transport_tools_list_advertises_only_what_it_will_run():
    """Listing tools that tools/call refuses turns an authorization boundary into
    a confusing runtime error, and invites clients to build against them."""
    from algochains_mcp.http_transport import _authorize_tool_call, _public_tool_names

    for name in _public_tool_names():
        allowed, _ = _authorize_tool_call(name, {})
        assert allowed is True, f"tools/list advertises {name} but tools/call denies it"


# ── Finding 3 (Medium): PostgREST parameter injection ─────────────────────────

async def _summary(event_type):
    from algochains_mcp.platform_analytics import get_analytics_summary
    return await get_analytics_summary(days=7, event_type=event_type)


def test_analytics_rejects_query_parameter_injection():
    """`event_type` was concatenated into the query string, so an `&` opened a new
    parameter — widening `select` to `properties` and raising `limit`, under the
    service key, which bypasses RLS."""
    import asyncio

    payload = "page_view&select=*,properties&limit=5000&user_id=eq.someone-else"
    out = asyncio.run(_summary(payload))
    assert out.get("error") == "unknown event_type"
    assert "known_event_types" in out


def test_analytics_accepts_the_real_vocabulary():
    import asyncio
    from algochains_mcp.platform_analytics import FUNNEL_EVENTS

    out = asyncio.run(_summary("page_view"))
    assert out.get("error") != "unknown event_type"
    assert "page_view" in FUNNEL_EVENTS


def test_analytics_encodes_rather_than_interpolates():
    """Second layer: even a value that passed validation must not be able to
    terminate a parameter. httpx params= percent-encodes; f-strings do not."""
    import httpx

    req = httpx.Request("GET", "https://h/rest/v1/t",
                        params={"event_type": "eq.x&select=*&limit=5000"})
    assert "&select" not in str(req.url).split("event_type=")[1]
    assert "%26" in str(req.url)


# ── Finding 2 (High): Kalshi account readers reachable without owner auth ─────

def test_kalshi_account_readers_require_owner_authorization():
    """Every one of these calls kalshi_signed_get against /trade-api/v2/portfolio/*
    with the server's signing key. Two matched the ("get_", READ_ONLY) prefix rule;
    get_kalshi_settlements was explicitly READ_ONLY, so a prefix-only fix misses it."""
    from algochains_mcp.tool_danger_tiers import TIER_ORDER_EXEC, get_danger_tier
    from algochains_mcp.tool_policy import evaluate_dynamic_tool

    for tool in ("get_kalshi_account", "get_kalshi_pnl_summary", "get_kalshi_settlements"):
        assert get_danger_tier(tool) >= TIER_ORDER_EXEC, f"{tool} below ORDER_EXEC"
        decision = evaluate_dynamic_tool(tool, {}, expected_owner_token="owner-token")
        assert decision.allow is False, f"{tool} callable without owner_token"
        assert decision.required_secret == "OWNER_API_TOKEN"


def test_kalshi_market_data_stays_reachable():
    """Owner-gating account state must not take public market data with it."""
    from algochains_mcp.tool_policy import evaluate_dynamic_tool

    for tool in ("get_kalshi_orderbook_depth", "scan_kalshi_wide_spreads", "scan_kalshi_edges"):
        assert evaluate_dynamic_tool(tool, {}, expected_owner_token="owner-token").allow is True


def test_kalshi_owner_gated_readers_are_not_advertised_in_smart_mode():
    from algochains_mcp.server import TIER1_TOOL_NAMES

    for tool in ("get_kalshi_account", "get_kalshi_pnl_summary", "get_kalshi_settlements"):
        assert tool not in TIER1_TOOL_NAMES


def test_scan_kalshi_edges_does_not_leak_account_positions():
    """Adjacent to finding 2: run_full_scan() reads account state for Kelly sizing
    and returned `positions` alongside. `scan_` has no prefix rule, so the tool
    defaults to WRITE_LOCAL — exactly the autonomous/research scope ceiling."""
    src = pathlib.Path("src/algochains_mcp/server.py").read_text(encoding="utf-8")
    marker = 'elif name == "scan_kalshi_edges":'
    body = src[src.index(marker): src.index(marker) + 1400]
    assert '"positions"' in body and '"positions_open"' in body, \
        "scan_kalshi_edges no longer strips account fields"
    assert "account_fields_withheld" in body


# ── Finding 4 (Medium): forgeable marketplace promotion evidence ──────────────

def test_promotion_metric_writer_requires_owner_authorization():
    """bot_id and market_id are caller-supplied; the only validation was that
    platform is one of two literals. The evidence a promotion rests on must not be
    writable by whoever benefits from the promotion."""
    from algochains_mcp.tool_danger_tiers import TIER_ORDER_EXEC, get_danger_tier
    from algochains_mcp.tool_policy import evaluate_dynamic_tool

    tool = "record_prediction_market_bot_metric"
    assert get_danger_tier(tool) >= TIER_ORDER_EXEC
    forged = {"bot_id": "bot-that-never-traded", "platform": "kalshi",
              "market_id": "X", "edge_vs_entry": 0.42}
    assert evaluate_dynamic_tool(tool, forged, expected_owner_token="owner-token").allow is False


def test_promotion_metric_reader_reports_unattested_rows(tmp_path, monkeypatch):
    """Gating the writer protects future rows and does nothing for the rows already
    in the file. A reader that returns those silently reports an audit trail over
    data whose origin nobody established."""
    from algochains_mcp import prediction_market_metrics as pm

    path = tmp_path / "m.jsonl"
    monkeypatch.setattr(pm, "_METRICS_PATH", path)
    path.write_text(json.dumps({"bot_id": "b1", "platform": "kalshi",
                                "market_id": "X", "edge_vs_entry": 0.42}) + "\n",
                    encoding="utf-8")
    pm.record_bot_metric_snapshot("b1", "kalshi", "X", written_by="mcp_owner_authorized")

    out = pm.read_recent_metrics("b1")
    assert out["count"] == 2
    assert out["attested"] == 1
    assert out["unattested"] == 1
    assert "provenance_warning" in out


def test_metric_row_without_explicit_provenance_is_unattested(tmp_path, monkeypatch):
    """Defaulting to trusted would let a forgetful caller inherit credibility."""
    from algochains_mcp import prediction_market_metrics as pm

    monkeypatch.setattr(pm, "_METRICS_PATH", tmp_path / "m.jsonl")
    pm.record_bot_metric_snapshot("b2", "kalshi", "Y")
    assert pm.read_recent_metrics("b2")["entries"][0]["written_by"] == "unattested"


# ── Control-plane integrity ───────────────────────────────────────────────────

def test_tool_tier_table_has_no_duplicate_keys():
    """Found while fixing finding 4: an existing WRITE_LOCAL entry sat two lines
    below the ORDER_EXEC one being added, and the later key silently won — the fix
    verified as applied and had no effect. In the file that IS the security control
    plane, a duplicate key is how a fix gets quietly reverted."""
    src = pathlib.Path("src/algochains_mcp/tool_danger_tiers.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "_TOOL_TIERS":
            counts = collections.Counter(k.value for k in node.value.keys)
            dupes = {n: c for n, c in counts.items() if c > 1}
            assert not dupes, f"duplicate keys silently override earlier tiers: {dupes}"
            return
    raise AssertionError("_TOOL_TIERS not found")
