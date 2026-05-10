from __future__ import annotations

from algochains_mcp.tool_policy import (
    evaluate_bridge_tool,
    evaluate_dynamic_tool,
    explain_decision,
    visible_tools_for_bridge,
)


PUBLIC = {"detect_market_regime"}
OWNER = {"get_positions", "place_order", "flatten_all_positions"}


def test_bridge_policy_blocks_owner_tool_for_public_caller():
    decision = evaluate_bridge_tool(
        "place_order",
        {},
        is_owner=False,
        caller_scope=None,
        public_tools=PUBLIC,
        owner_tools=OWNER,
    )
    assert decision.allow is False
    assert decision.required_secret == "ALGOCHAINS_BRIDGE_API_KEY"


def test_bridge_policy_requires_confirm_for_order_exec():
    decision = evaluate_bridge_tool(
        "place_order",
        {},
        is_owner=True,
        caller_scope="interactive",
        public_tools=PUBLIC,
        owner_tools=OWNER,
    )
    assert decision.allow is False
    assert decision.required_arg == "confirm=true"


def test_bridge_policy_accepts_legacy_confirmed_alias():
    decision = evaluate_bridge_tool(
        "place_order",
        {"confirmed": True},
        is_owner=True,
        caller_scope="interactive",
        public_tools=PUBLIC,
        owner_tools=OWNER,
    )
    assert decision.allow is True


def test_dynamic_policy_requires_owner_token_and_confirm():
    decision = evaluate_dynamic_tool(
        "place_order",
        {"owner_token": "secret"},
        expected_owner_token="secret",
    )
    assert decision.allow is False
    assert decision.required_arg == "confirm=true"


def test_dynamic_policy_allows_read_only_without_token():
    decision = evaluate_dynamic_tool(
        "get_positions",
        {},
        expected_owner_token="",
    )
    assert decision.allow is True


def test_visible_tools_respects_scope_ceiling():
    visible = visible_tools_for_bridge(
        public_tools=PUBLIC,
        owner_tools=OWNER,
        is_owner=True,
        caller_scope="autonomous",
    )
    assert "detect_market_regime" in visible
    assert "get_positions" in visible
    assert "place_order" not in visible
    assert "flatten_all_positions" not in visible


def test_explain_decision_redacts_arguments_and_keeps_hash_only():
    decision = evaluate_dynamic_tool(
        "place_order",
        {"owner_token": "secret", "confirm": True},
        expected_owner_token="secret",
    )
    payload = explain_decision(
        decision,
        arguments={"owner_token": "secret", "confirm": True, "symbol": "MNQ"},
        transports_allowed={"dynamic": True},
    )
    rendered = str(payload)
    assert payload["decision"] == "allow"
    assert payload["arguments_redacted"] is True
    assert "argument_hash" in payload
    assert "'secret'" not in rendered
    assert "owner_token" not in rendered
