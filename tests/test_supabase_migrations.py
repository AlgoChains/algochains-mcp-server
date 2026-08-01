from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _sql(path: str) -> str:
    return (ROOT / path).read_text()


def test_subscriber_api_keys_repair_migration_preserves_auth_contract():
    sql = _sql("supabase/migrations/20260614000400_subscriber_api_keys.sql")

    required_columns = (
        r"subscriber_id\s+TEXT\s+NOT\s+NULL",
        r"tier\s+TEXT\s+NOT\s+NULL\s+DEFAULT\s+'paper'",
        r"active\s+BOOLEAN\s+NOT\s+NULL\s+DEFAULT\s+TRUE",
        r"expires_at\s+TIMESTAMPTZ",
        r"bot_slug\s+TEXT",
        r"paper_account_id\s+TEXT",
    )
    for pattern in required_columns:
        assert re.search(pattern, sql), pattern

    assert "COALESCE(NULLIF(k.subscriber_id, ''), k.user_id::TEXT) AS subscriber_id" in sql
    assert "AND k.active = TRUE" in sql
    assert "AND k.revoked_at IS NULL" in sql
    assert "AND (k.expires_at IS NULL OR k.expires_at > now())" in sql
    assert "SET search_path = ''" in sql


def test_subscriber_auth_docs_reference_current_migrations():
    auth_py = _sql("src/algochains_mcp/subscriber_auth.py")

    assert "20260523_subscriber_copytrade.sql" in auth_py
    assert "20260614000400_subscriber_api_keys.sql" in auth_py
    assert "20260420_subscriber_copytrade.sql" not in auth_py
