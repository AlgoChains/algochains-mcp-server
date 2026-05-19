from __future__ import annotations

import importlib


def test_waitlist_accepts_legacy_supabase_service_key(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "service-key-from-existing-env")

    import algochains_mcp.waitlist as waitlist

    waitlist = importlib.reload(waitlist)

    assert waitlist.SUPABASE_SERVICE_ROLE_KEY == "service-key-from-existing-env"
    assert waitlist._sb_available()
