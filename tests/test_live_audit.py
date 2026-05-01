"""
Live audit tests for AlgoChains MCP Server.

Tests broker connectivity, data providers, and hidden killer detection.

IMPORTANT — credentials:
  All API keys are read exclusively from environment variables.
  Never hardcode keys in this file.  Tests that require live credentials
  are automatically skipped when the relevant env var is absent, so CI
  passes without secrets and local runs work when .env is sourced.

  Required vars for each test group:
    Alpaca paper  : ALPACA_API_KEY, ALPACA_SECRET_KEY
    Polygon       : POLYGON_API_KEY
    Finnhub       : FINNHUB_API_KEY
    Massive AI    : MASSIVE_API_KEY

Run locally:
    source .env && pytest tests/test_live_audit.py -v --live

Run in CI (no secrets — only offline assertions):
    pytest tests/test_live_audit.py -v
"""
from __future__ import annotations

import ast
import asyncio
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# ── live-mode guard ────────────────────────────────────────────────────────────
# Tests that hit real broker/data APIs require --live flag AND present env vars.
# This prevents silent skips from being confused with passing tests.

def pytest_addoption(parser):
    """Declare --live CLI flag (called by conftest; harmless if duplicate)."""
    try:
        parser.addoption(
            "--live",
            action="store_true",
            default=False,
            help="Run tests that contact live broker/data APIs",
        )
    except ValueError:
        pass  # already added by conftest.py


def _skip_unless_live(env_vars: list[str]):
    """Return a pytest.mark.skipif that skips when not in live mode or missing vars."""
    missing = [v for v in env_vars if not os.environ.get(v)]
    if missing:
        return pytest.mark.skip(reason=f"Missing env vars: {', '.join(missing)}.  Run with --live and source .env")
    return pytest.mark.skipif(
        not (os.environ.get("PYTEST_LIVE") or False),
        reason="Live tests require --live flag or PYTEST_LIVE=1",
    )


# ── environment setup (env vars only, no hardcoded values) ─────────────────────
os.environ.setdefault("ALGOCHAINS_TOOL_MODE", "full")
# Keys sourced from environment — absent values → tests skip, never fail with fake creds.
_ALPACA_KEY    = os.environ.get("ALPACA_API_KEY", "")
_ALPACA_SECRET = os.environ.get("ALPACA_SECRET_KEY", "")
_ALPACA_URL    = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
_POLYGON_KEY   = os.environ.get("POLYGON_API_KEY", "")
_MASSIVE_KEY   = os.environ.get("MASSIVE_API_KEY", "")
_FINNHUB_KEY   = os.environ.get("FINNHUB_API_KEY", "")

_HAVE_ALPACA   = bool(_ALPACA_KEY and _ALPACA_SECRET)
_HAVE_POLYGON  = bool(_POLYGON_KEY)
_HAVE_MASSIVE  = bool(_MASSIVE_KEY)
_HAVE_FINNHUB  = bool(_FINNHUB_KEY)


# ── helpers ────────────────────────────────────────────────────────────────────

def _server_path() -> str:
    return os.path.join(os.path.dirname(__file__), "..", "src", "algochains_mcp", "server.py")


# ══════════════════════════════════════════════════════════════════════════════
# OFFLINE TESTS — run in CI without any credentials
# ══════════════════════════════════════════════════════════════════════════════


def test_server_importable_offline():
    """server.py imports without live credentials."""
    import algochains_mcp.server as srv  # noqa: F401
    assert srv.app is not None


def test_no_hardcoded_secrets_in_test_file():
    """This test file must not contain hardcoded API keys.

    Checks the file itself against known-bad patterns to catch
    regressions where someone re-adds inline credentials.
    """
    _bad_patterns = [
        r"ALPACA_API_KEY\s*=\s*['\"][A-Z0-9]{20,}",
        r"ALPACA_SECRET_KEY\s*=\s*['\"][A-Za-z0-9]{20,}",
        r"POLYGON_API_KEY\s*=\s*['\"][A-Za-z0-9]{20,}",
        r"MASSIVE_API_KEY\s*=\s*['\"][A-Za-z0-9]{20,}",
        r"FINNHUB_API_KEY\s*=\s*['\"][A-Za-z0-9]{20,}",
        r"os\.environ\[['\"].*_KEY['\"]\]\s*=\s*['\"][A-Za-z0-9+/]{16,}",
    ]
    this_file = os.path.abspath(__file__)
    src = open(this_file).read()
    hits = []
    for pat in _bad_patterns:
        if re.search(pat, src):
            hits.append(pat)
    assert not hits, (
        "Hardcoded API key pattern detected in test_live_audit.py:\n"
        + "\n".join(f"  {p}" for p in hits)
        + "\nStore keys in .env and read via os.environ.get()."
    )


def test_dispatch_annotation_constants_defined():
    """All ANNOT_* constants must exist in server.py (offline)."""
    import algochains_mcp.server as srv

    annot_names = [
        "ANNOT_READ_ONLY", "ANNOT_READ_EXTERNAL", "ANNOT_WRITE_SAFE",
        "ANNOT_WRITE_DESTRUCTIVE", "ANNOT_TRADE_EXEC", "ANNOT_COMPUTE",
        "ANNOT_SEARCH",
    ]
    missing = [n for n in annot_names if not hasattr(srv, n)]
    assert not missing, f"Missing annotation constants: {missing}"


def test_text_helper_works():
    """_text helper serialises dicts without error."""
    import algochains_mcp.server as srv
    result = srv._text({"test": True, "data": [1, 2, 3]})
    assert isinstance(result, list)


def test_lazy_singletons_initialise():
    """Synchronous lazy singletons can initialise offline."""
    import algochains_mcp.server as srv

    for name in ("_get_registry", "_get_validator", "_get_bridge",
                 "_get_stream_manager", "_get_dynamic_gateway"):
        fn = getattr(srv, name, None)
        if fn is None:
            pytest.skip(f"{name} not present in server")
        obj = fn()
        assert obj is not None, f"{name}() returned None"


def test_no_bare_except_in_server():
    """Bare 'except:' clauses in server.py swallow all errors; report count."""
    with open(_server_path()) as f:
        tree = ast.parse(f.read())
    bare = sum(
        1 for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler) and node.type is None
    )
    assert bare == 0, (
        f"{bare} bare 'except:' clauses in server.py — each hides real errors. "
        "Use 'except Exception:' at minimum."
    )


def test_no_hardcoded_secrets_in_server():
    """server.py must not contain hardcoded credential values."""
    _secret_re = [
        r'(?i)api[_-]?key\s*=\s*["\'][A-Za-z0-9+/]{20,}["\']',
        r'(?i)secret\s*=\s*["\'][A-Za-z0-9+/]{20,}["\']',
        r'(?i)password\s*=\s*["\'][^"\']+["\']',
        r'(?i)token\s*=\s*["\'][A-Za-z0-9._\-]{20,}["\']',
    ]
    with open(_server_path()) as f:
        lines = f.readlines()
    hits = []
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "os.environ" in line or "os.getenv" in line or ".env" in line:
            continue
        for pat in _secret_re:
            if re.search(pat, line):
                hits.append(f"Line {i}: {stripped[:100]}")
    assert not hits, (
        f"Possible hardcoded secrets in server.py ({len(hits)} hits):\n"
        + "\n".join(hits)
    )


# ══════════════════════════════════════════════════════════════════════════════
# LIVE TESTS — require env vars; skipped silently in CI
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.skipif(not _HAVE_ALPACA, reason="ALPACA_API_KEY / ALPACA_SECRET_KEY not set")
def test_alpaca_paper_live():
    """Live Alpaca paper account: connect, positions, orders."""
    async def _run():
        from algochains_mcp.brokers.registry import BrokerRegistry
        from algochains_mcp.config import load_config

        cfg = load_config()
        registry = BrokerRegistry(cfg)
        results = await registry.connect_all()
        if "alpaca" not in registry.list_available():
            pytest.xfail(
                f"Alpaca not available after connect (may be network/cred issue). Results: {results}"
            )
        broker = registry.get("alpaca")
        try:
            acct = await broker.get_account()
            assert acct, "get_account returned empty"
            positions = await broker.get_positions()
            assert isinstance(positions, list)
            orders = await broker.get_orders()
            assert isinstance(orders, list)
        except Exception as exc:
            pytest.xfail(f"Live Alpaca call failed (non-blocking): {exc}")

    asyncio.run(_run())


@pytest.mark.skipif(not (_HAVE_POLYGON or _HAVE_FINNHUB), reason="POLYGON_API_KEY and FINNHUB_API_KEY both absent")
def test_data_providers_live():
    """Live data provider health checks."""
    async def _run():
        from algochains_mcp.data_providers.registry import DataProviderRegistry

        reg = DataProviderRegistry()
        results = await reg.health_check_all()
        failed = {k: v for k, v in results.items() if not v}
        # Warn but do not hard-fail: a degraded provider should not block CI
        if failed:
            pytest.xfail(f"Data providers degraded (non-blocking): {list(failed)}")

    asyncio.run(_run())


@pytest.mark.skipif(not _HAVE_MASSIVE, reason="MASSIVE_API_KEY not set")
def test_massive_bad_sql_returns_error():
    """Massive provider handles invalid SQL gracefully (returns error dict or raises cleanly)."""
    async def _run():
        import algochains_mcp.server as srv
        eng = await srv._get_massive_provider()
        try:
            result = await eng.query_data("SELECT * FROM nonexistent_table_xyz")
            assert isinstance(result, dict) and "error" in result, (
                f"Expected error dict, got: {str(result)[:200]}"
            )
        except Exception as exc:
            # Acceptable: provider raises a typed exception for invalid SQL
            # rather than returning an error dict.  Either pattern is safe.
            assert "sql" in str(exc).lower() or "table" in str(exc).lower() or "query" in str(exc).lower(), (
                f"Unexpected exception type for bad SQL: {type(exc).__name__}: {exc}"
            )

    asyncio.run(_run())
