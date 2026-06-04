"""
Tests for developer_tools.py — allowlist, blocklist, scope enforcement.
"""
import pytest

from algochains_mcp.developer_tools import (
    DEVELOPER_BLOCKED_TOOLS,
    DEVELOPER_TOOL_SCOPES,
    DEVELOPER_TOOLS,
    check_developer_tool_access,
)


class TestDeveloperToolsAllowlist:
    def test_public_tools_included(self):
        """Public tools should all be in the developer surface."""
        public = {"detect_market_regime", "get_macro_signals", "onyx_search", "discover_tools"}
        for tool in public:
            assert tool in DEVELOPER_TOOLS, f"{tool} missing from DEVELOPER_TOOLS"

    def test_order_exec_not_included(self):
        assert "place_order" not in DEVELOPER_TOOLS
        assert "cancel_order" not in DEVELOPER_TOOLS
        assert "close_position" not in DEVELOPER_TOOLS

    def test_execute_dynamic_tool_not_included(self):
        assert "execute_dynamic_tool" not in DEVELOPER_TOOLS

    def test_owner_tools_not_included(self):
        owner = {"get_bot_health", "get_all_bot_metrics", "portfolio_summary", "run_marketplace_autopilot"}
        for tool in owner:
            assert tool not in DEVELOPER_TOOLS, f"{tool} should not be in DEVELOPER_TOOLS"

    def test_subscriber_tools_not_included(self):
        sub = {"get_signal_stream", "get_my_pnl", "report_fill", "heartbeat", "ack_signal"}
        for tool in sub:
            assert tool not in DEVELOPER_TOOLS


class TestCheckDeveloperToolAccess:
    BASE_SCOPES = ("read:market_data", "read:signals")

    def test_allowed_tool_no_scope_required(self):
        ok, reason = check_developer_tool_access("detect_market_regime", self.BASE_SCOPES)
        assert ok is True
        assert reason is None

    def test_allowed_tool_scope_present(self):
        scopes = self.BASE_SCOPES + ("read:market_data",)
        ok, reason = check_developer_tool_access("get_historical_bars", scopes)
        assert ok is True

    def test_allowed_tool_scope_missing(self):
        ok, reason = check_developer_tool_access(
            "submit_to_marketplace", self.BASE_SCOPES  # missing publish:listing
        )
        assert ok is False
        assert "missing_scope" in (reason or "")

    def test_blocked_tool_rejected(self):
        ok, reason = check_developer_tool_access("place_order", ("read:market_data",))
        assert ok is False
        assert reason == "tool_not_available_for_developer_tier"

    def test_execute_dynamic_blocked(self):
        ok, reason = check_developer_tool_access("execute_dynamic_tool", self.BASE_SCOPES)
        assert ok is False

    def test_unknown_tool_rejected(self):
        ok, reason = check_developer_tool_access("totally_unknown_tool_xyz", self.BASE_SCOPES)
        assert ok is False
        assert reason == "tool_not_in_developer_allowlist"

    def test_owner_tools_blocked(self):
        for tool in ["get_bot_health", "get_positions", "run_marketplace_autopilot"]:
            ok, _ = check_developer_tool_access(tool, self.BASE_SCOPES)
            assert ok is False, f"{tool} should be blocked for developer tier"

    def test_blocked_set_no_overlap_with_allowed(self):
        overlap = DEVELOPER_TOOLS & DEVELOPER_BLOCKED_TOOLS
        assert not overlap, f"Tools in both allowlist and blocklist: {overlap}"
