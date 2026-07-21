from unittest.mock import MagicMock, patch

from algochains_mcp.marketplace.supabase_tools import get_subscriber_bots


class _QueryRecorder:
    def __init__(self, rows):
        self.rows = rows
        self.selected = None
        self.filters = []
        self.orders = []

    def select(self, columns):
        self.selected = columns
        return self

    def eq(self, field, value):
        self.filters.append((field, value))
        return self

    def order(self, field, desc=False):
        self.orders.append((field, desc))
        return self

    def execute(self):
        return MagicMock(data=self.rows)


class _SupabaseRecorder:
    def __init__(self, rows):
        self.query = _QueryRecorder(rows)
        self.tables = []

    def table(self, name):
        self.tables.append(name)
        return self.query


def test_get_subscriber_bots_reads_subscriber_bot_assignments():
    sb = _SupabaseRecorder([
        {
            "bot": "MNQ",
            "mode": "paper",
            "paused": False,
            "size_multiplier": 1,
            "max_contracts": 1,
            "daily_loss_cap_usd": 500,
        }
    ])

    with patch("algochains_mcp.marketplace.supabase_tools._get_sb_client", return_value=sb):
        out = get_subscriber_bots("sub-uuid", owner_authorized=True)

    assert sb.tables == ["subscriber_bot_assignments"]
    assert "mode" in sb.query.selected
    assert ("subscriber_id", "sub-uuid") in sb.query.filters
    assert out["subscriptions"][0]["mode"] == "paper"
