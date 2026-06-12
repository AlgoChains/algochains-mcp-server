"""
Tests for subscriber_tools.py — the sub_live_ tool surface added at the
AlgoChains Paper public launch (June 2026).

Covers:
  - registry consistency: every handler has a scope, every scope has a handler
  - every required scope is grantable (subset of DEFAULT_SUBSCRIBER_SCOPES)
  - call_subscriber_tool never trusts a caller-supplied subscriber_id
  - place_paper_order argument validation (side / qty / order_type / limit_price)
  - paper-order tools fail closed when the paper account is missing
"""
from unittest.mock import MagicMock, patch

from algochains_mcp.subscriber_auth import DEFAULT_SUBSCRIBER_SCOPES
from algochains_mcp.subscriber_tools import (
    SUBSCRIBER_TOOL_HANDLERS,
    SUBSCRIBER_TOOL_SCOPES,
    SUBSCRIBER_TOOLS,
    call_subscriber_tool,
    get_my_pnl,
    get_my_portfolio,
    place_paper_order,
)

SUB_ID = "00000000-0000-0000-0000-000000000001"


class TestToolRegistryConsistency:
    def test_every_handler_has_a_scope(self):
        missing = SUBSCRIBER_TOOLS - set(SUBSCRIBER_TOOL_SCOPES)
        assert not missing, f"tools without a scope mapping: {missing}"

    def test_every_scope_entry_has_a_handler(self):
        orphaned = set(SUBSCRIBER_TOOL_SCOPES) - SUBSCRIBER_TOOLS
        assert not orphaned, f"scope entries without a handler: {orphaned}"

    def test_all_required_scopes_are_grantable(self):
        # A tool requiring a scope no key can ever hold would be dead code.
        ungrantable = set(SUBSCRIBER_TOOL_SCOPES.values()) - set(DEFAULT_SUBSCRIBER_SCOPES)
        assert not ungrantable, f"scopes not in DEFAULT_SUBSCRIBER_SCOPES: {ungrantable}"

    def test_paper_launch_tools_registered(self):
        for tool in (
            "get_my_portfolio",
            "get_marketplace_listings",
            "place_paper_order",
            "cancel_paper_order",
            "get_my_paper_positions",
        ):
            assert tool in SUBSCRIBER_TOOLS

    def test_paper_order_tools_require_paper_trade_scope(self):
        for tool in ("place_paper_order", "cancel_paper_order", "get_my_paper_positions"):
            assert SUBSCRIBER_TOOL_SCOPES[tool] == "paper_trade"


class TestCallSubscriberTool:
    def test_unknown_tool_errors(self):
        out = call_subscriber_tool("not_a_tool", SUB_ID, {})
        assert out["error"] == "unknown_subscriber_tool"

    def test_caller_supplied_subscriber_id_is_discarded(self):
        seen = {}

        def fake_handler(subscriber_id, **kwargs):
            seen["subscriber_id"] = subscriber_id
            seen["kwargs"] = kwargs
            return {"ok": True}

        with patch.dict(SUBSCRIBER_TOOL_HANDLERS, {"get_my_pnl": fake_handler}):
            out = call_subscriber_tool(
                "get_my_pnl", SUB_ID, {"subscriber_id": "attacker-id"}
            )
        assert out == {"ok": True}
        assert seen["subscriber_id"] == SUB_ID
        assert "subscriber_id" not in seen["kwargs"]

    def test_bad_arguments_surface_cleanly(self):
        out = call_subscriber_tool("get_my_pnl", SUB_ID, {"bogus_kwarg": 1})
        assert out["error"] == "bad_arguments"


def _mock_sb_with_account(account_row):
    """Supabase client stub whose paper-account lookup returns account_row."""
    sb = MagicMock()
    lookup = MagicMock()
    lookup.data = account_row
    (
        sb.table.return_value.select.return_value.eq.return_value
        .maybe_single.return_value.execute.return_value
    ) = lookup
    return sb


class _FakeResponse:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, data):
        self._data = data

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def gte(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def maybe_single(self, *_args, **_kwargs):
        return self

    def execute(self):
        return _FakeResponse(self._data)


class _FakeSubscriberClient:
    def __init__(self, *, paper_account, fill_pages=None, assignments=None):
        self.paper_account = paper_account
        self.fill_pages = list(fill_pages or [])
        self.assignments = assignments or []

    def table(self, name):
        if name == "subscriber_fills":
            return _FakeQuery(self.fill_pages.pop(0) if self.fill_pages else [])
        if name == "subscriber_paper_accounts":
            return _FakeQuery(self.paper_account)
        if name == "subscriber_bot_assignments":
            return _FakeQuery(self.assignments)
        raise AssertionError(f"unexpected table lookup: {name}")


class TestPaperPnlAliases:
    def test_get_my_pnl_exposes_account_level_paper_pnl_when_today_is_empty(self):
        sb = _FakeSubscriberClient(
            paper_account={
                "starting_balance_usd": "2500.00",
                "current_balance_usd": "2801.60",
                "realized_pnl_usd": "301.60",
            },
            fill_pages=[[], []],
        )

        with patch("algochains_mcp.subscriber_tools._service_client", return_value=sb):
            out = get_my_pnl(SUB_ID)

        assert out["pnl_today_usd"] == 0
        assert out["pnl_7d_usd"] == 0
        assert out["paper_pnl_usd"] == 301.6
        assert out["paper_pnl"] == 301.6
        assert out["paper_pnl_rollup_usd"] == 301.6

    def test_get_my_pnl_falls_back_to_balance_delta_for_paper_pnl(self):
        sb = _FakeSubscriberClient(
            paper_account={
                "starting_balance_usd": "2500.00",
                "current_balance_usd": "2801.605",
                "realized_pnl_usd": None,
            },
            fill_pages=[[], []],
        )

        with patch("algochains_mcp.subscriber_tools._service_client", return_value=sb):
            out = get_my_pnl(SUB_ID)

        assert out["paper_pnl_usd"] == 301.61
        assert out["paper_pnl"] == 301.61
        assert out["paper_pnl_rollup_usd"] == 301.61

    def test_get_my_portfolio_exposes_same_account_level_paper_pnl_aliases(self):
        sb = _FakeSubscriberClient(
            paper_account={
                "starting_balance_usd": "2500.00",
                "current_balance_usd": "2801.60",
                "realized_pnl_usd": "301.60",
            },
            fill_pages=[[], []],
            assignments=[],
        )

        with patch("algochains_mcp.subscriber_tools._service_client", return_value=sb):
            out = get_my_portfolio(SUB_ID)

        assert out["paper_account"]["realized_pnl_usd"] == "301.60"
        assert out["pnl_today_usd"] == 0
        assert out["paper_pnl_usd"] == 301.6
        assert out["paper_pnl"] == 301.6
        assert out["paper_pnl_rollup_usd"] == 301.6


class TestPlacePaperOrderValidation:
    def test_invalid_side_rejected(self):
        with patch(
            "algochains_mcp.subscriber_tools._service_client",
            return_value=_mock_sb_with_account({"subscriber_id": SUB_ID}),
        ):
            out = place_paper_order(SUB_ID, symbol="MNQ", side="HOLD", qty=1)
        assert out["error"] == "invalid_side"

    def test_zero_qty_rejected(self):
        with patch(
            "algochains_mcp.subscriber_tools._service_client",
            return_value=_mock_sb_with_account({"subscriber_id": SUB_ID}),
        ):
            out = place_paper_order(SUB_ID, symbol="MNQ", side="BUY", qty=0)
        assert out["error"] == "invalid_qty"

    def test_limit_order_requires_price(self):
        with patch(
            "algochains_mcp.subscriber_tools._service_client",
            return_value=_mock_sb_with_account({"subscriber_id": SUB_ID}),
        ):
            out = place_paper_order(
                SUB_ID, symbol="MNQ", side="BUY", qty=1, order_type="limit"
            )
        assert out["error"] == "limit_price_required"

    def test_missing_paper_account_fails_closed(self):
        with patch(
            "algochains_mcp.subscriber_tools._service_client",
            return_value=_mock_sb_with_account(None),
        ):
            out = place_paper_order(SUB_ID, symbol="MNQ", side="BUY", qty=1)
        assert out["error"] == "paper_account_missing"

    def test_supabase_down_fails_closed(self):
        with patch(
            "algochains_mcp.subscriber_tools._service_client", return_value=None
        ):
            out = place_paper_order(SUB_ID, symbol="MNQ", side="BUY", qty=1)
        assert out["error"] == "supabase_unavailable"
