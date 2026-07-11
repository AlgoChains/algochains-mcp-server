from algochains_mcp.config import OandaConfig, TradovateConfig


def test_oanda_uses_canonical_access_token_first(monkeypatch):
    monkeypatch.setenv("OANDA_ACCESS_TOKEN", "canonical")
    monkeypatch.setenv("OANDA_API_KEY", "legacy")

    assert OandaConfig().access_token == "canonical"


def test_oanda_accepts_django_api_token_alias(monkeypatch):
    monkeypatch.delenv("OANDA_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("OANDA_API_KEY", raising=False)
    monkeypatch.setenv("OANDA_API_TOKEN", "django-token")

    assert OandaConfig().access_token == "django-token"


def test_tradovate_accepts_client_id_alias(monkeypatch):
    monkeypatch.delenv("TRADOVATE_CID", raising=False)
    monkeypatch.setenv("TRADOVATE_CLIENT_ID", "client-id")

    assert TradovateConfig().cid == "client-id"
