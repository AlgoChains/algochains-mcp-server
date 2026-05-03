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

Run local offline checks:
    pytest tests/test_live_audit.py -v

Run live broker/data checks explicitly:
    source .env && PYTEST_LIVE=1 pytest tests/live/test_live_audit.py -v
"""
from __future__ import annotations

import ast
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# ── environment setup (env vars only, no hardcoded values) ─────────────────────
os.environ.setdefault("ALGOCHAINS_TOOL_MODE", "full")


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
