# AlgoChains MCP Server — Fix Megaprompt

**Generated:** 2026-06-08 · **Node:** mac · **Authority:** agent_memory  
**Purpose:** Step-by-step implementation guide for the 6-fix plan. Execute in order. Verify after each fix.

---

## Context

The MCP server has four concrete bugs found by live testing on 2026-06-08:

1. `pip install algochains-mcp-server` returns `No matching distribution found` — package was never published to PyPI. README lies.
2. `_ROO_DEFAULT_SECRET = "1234"` was in public git history. Django HMAC secret must be rotated and the code fallback removed.
3. Version strings drift: `pyproject.toml=22.4.0`, `__init__.py=22.2.0`, `server.py=v22.5`.
4. No GitHub push protection in place to prevent future credential commits.

Roo Fernando has requested a demo. These fixes must be merged before that call.

---

## Pre-flight (read before touching any file)

```bash
cd /Users/treycsa/CascadeProjects/algochains-mcp-server
git status          # confirm clean working tree
git log --oneline -3
python3 -m pytest tests/ -x -q 2>&1 | tail -20   # baseline test pass
```

---

## Fix 1 — README: remove false pip install claim

**File:** `README.md`

There are exactly two occurrences on lines 31 and 255. Both replace the same block.

### Old text (line 31 block)
```
pip install algochains-mcp-server
```

### New text (replace BOTH occurrences)
```bash
# Clone the private repo (requires GitHub access)
git clone https://github.com/AlgoChains/algochains-mcp-server.git
cd algochains-mcp-server
pip install -e ".[http,supabase,auth]"

# Try it immediately — no credentials needed
python scripts/quickstart.py --mode demo
```

**Verification:** `grep -n "pip install algochains-mcp-server" README.md` → should return nothing.

---

## Fix 2 — Add Makefile for one-command demo experience

**File to create:** `Makefile` (repo root)

```makefile
.PHONY: install demo health config-cursor config-claude lint test clean

PYTHON ?= python3
EXTRAS  ?= dev,http,supabase,auth

install:
	$(PYTHON) -m pip install -e ".[$(EXTRAS)]"

demo: install
	$(PYTHON) scripts/quickstart.py --mode demo

health:
	$(PYTHON) scripts/startup_health_check.py

config-cursor:
	$(PYTHON) scripts/quickstart.py --generate-config cursor

config-claude:
	$(PYTHON) scripts/quickstart.py --generate-config claude-desktop

lint:
	ruff check src/ tests/

test:
	$(PYTHON) -m pytest tests/ -x -q

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf dist/ build/ *.egg-info/
```

**Verification:** `make demo` completes without error and prints tool count.

---

## Fix 3 — HMAC fallback: fail closed (remove `_ROO_DEFAULT_SECRET`)

**File:** `src/algochains_mcp/trade_propagation.py`

### Exact old_string to replace (lines 45–82):
```python
# ⚠️  SECURITY: This is the dev-only fallback secret. It MUST be overridden via
# ALGOCHAINS_SIGNAL_SECRET or SIGNAL_SECRET in production. Any signal sent with
# the default secret will be rejected by a correctly-configured backend, and the
# propagate_signal() function will log a WARNING so operators know to fix it.
_ROO_DEFAULT_SECRET = "1234"


def _resolve_url() -> str:
    """Return signal endpoint URL — env override required; fails closed if unset."""
    url = (
        os.getenv("ALGOCHAINS_SIGNAL_URL", "").strip()
        or os.getenv("SIGNAL_URL", "").strip()
    )
    if not url:
        raise RuntimeError(
            "trade_propagation: ALGOCHAINS_SIGNAL_URL (or SIGNAL_URL) is not set. "
            "Refusing to propagate signal over an unverified endpoint. "
            "Set ALGOCHAINS_SIGNAL_URL=https://... to enable signal propagation."
        )
    if url.startswith("http://"):
        logger.warning(
            "trade_propagation: ALGOCHAINS_SIGNAL_URL uses plain HTTP (%s). "
            "HMAC secret and trade signals are transmitted in cleartext. "
            "Use HTTPS to protect signal integrity.",
            url,
        )
    return url


def _resolve_secret() -> bytes:
    """Return HMAC secret bytes — env override takes priority over Roo default."""
    raw = (
        os.getenv("ALGOCHAINS_SIGNAL_SECRET", "").strip()
        or os.getenv("SIGNAL_SECRET", "").strip()
        or _ROO_DEFAULT_SECRET
    )
    return raw.encode("utf-8")
```

### Exact new_string:
```python
def _resolve_url() -> str:
    """Return signal endpoint URL — fails closed when env var unset."""
    url = (
        os.getenv("ALGOCHAINS_SIGNAL_URL", "").strip()
        or os.getenv("SIGNAL_URL", "").strip()
    )
    if not url:
        raise RuntimeError(
            "trade_propagation: ALGOCHAINS_SIGNAL_URL (or SIGNAL_URL) is not set. "
            "Refusing to propagate signal over an unverified endpoint. "
            "Set ALGOCHAINS_SIGNAL_URL=https://... to enable signal propagation."
        )
    if url.startswith("http://"):
        logger.warning(
            "trade_propagation: ALGOCHAINS_SIGNAL_URL uses plain HTTP (%s). "
            "HMAC secret and trade signals are transmitted in cleartext. "
            "Use HTTPS to protect signal integrity.",
            url,
        )
    return url


def _resolve_secret() -> bytes:
    """Return HMAC secret bytes — fails closed when ALGOCHAINS_SIGNAL_SECRET is unset.

    The fallback default was removed after the repo was briefly public (2026-06-08).
    Rotate the Django signal endpoint's HMAC secret if it was using the '1234' default.
    """
    raw = (
        os.getenv("ALGOCHAINS_SIGNAL_SECRET", "").strip()
        or os.getenv("SIGNAL_SECRET", "").strip()
    )
    if not raw:
        raise RuntimeError(
            "trade_propagation: ALGOCHAINS_SIGNAL_SECRET (or SIGNAL_SECRET) is not set. "
            "Set it in .env to match the Django signal ingest endpoint HMAC secret. "
            "Contact Roo to confirm the current server-side secret after rotating it."
        )
    return raw.encode("utf-8")
```

**Verification:**
```bash
python3 -c "
from src.algochains_mcp.trade_propagation import _resolve_secret
import os
os.environ.pop('ALGOCHAINS_SIGNAL_SECRET', None)
os.environ.pop('SIGNAL_SECRET', None)
try:
    _resolve_secret()
    print('FAIL — should have raised')
except RuntimeError as e:
    print('PASS — fails closed:', str(e)[:60])
"
grep "_ROO_DEFAULT_SECRET" src/algochains_mcp/trade_propagation.py && echo "FAIL - still present" || echo "PASS - removed"
```

### Required manual action (Tyler tells Roo):
> The `"1234"` HMAC default was visible in the public repo. Roo: please rotate the HMAC secret on the Django `signal/` endpoint and share the new value via a secure channel (not Slack). Tyler will update `ALGOCHAINS_SIGNAL_SECRET` in `.env` on Mac and Desktop.

---

## Fix 4 — Version strings: use `importlib.metadata` as single source of truth

**File:** `src/algochains_mcp/__init__.py`

### Exact old_string:
```python
"""
AlgoChains MCP Server — Universal broker connectors and marketplace integration.

Exposes trading, market data, portfolio management, and strategy validation
tools via the Model Context Protocol (MCP) for any AI agent.
"""
__version__ = "22.2.0"
```

### Exact new_string:
```python
"""
AlgoChains MCP Server — Universal broker connectors and marketplace integration.

Exposes trading, market data, portfolio management, and strategy validation
tools via the Model Context Protocol (MCP) for any AI agent.
"""
from importlib.metadata import version as _pkg_version, PackageNotFoundError as _PkgNotFound

try:
    __version__ = _pkg_version("algochains-mcp-server")
except _PkgNotFound:
    # Running directly from source without pip install -e .
    __version__ = "dev"
```

**File:** `src/algochains_mcp/server.py`

### Exact old_string (line 406 area):
```python
SERVER_INSTRUCTIONS = (
    "AlgoChains MCP Server v22.5 — The Ultimate Algo Quant Stack. "
```

### Exact new_string:
```python
SERVER_INSTRUCTIONS = (
    f"AlgoChains MCP Server v{__version__} — The Ultimate Algo Quant Stack. "
```

Also add at the top of `server.py` (after the existing imports, find the import block and add if not already present):
```python
from algochains_mcp import __version__
```

**Verification:**
```bash
pip install -e . -q
python3 -c "import algochains_mcp; print('version:', algochains_mcp.__version__)"
# Expected: version: 22.4.0  (reads from pyproject.toml via installed metadata)
```

---

## Fix 5 — PyPI Trusted Publishing via OIDC (no stored API token)

**Research basis:** PyPI Trusted Publishing is the current best practice per [docs.pypi.org/trusted-publishers](https://docs.pypi.org/trusted-publishers/using-a-publisher/) and [pypa/gh-action-pypi-publish](https://github.com/pypa/gh-action-pypi-publish). API tokens are now legacy. OIDC tokens are short-lived, auto-generated, and require no stored secrets.

### Tyler must do this manually FIRST (one-time on pypi.org):
1. Go to `https://pypi.org/manage/account/publishing/`
2. Click "Add a new pending publisher"
3. Fill in:
   - PyPI project name: `algochains-mcp-server`
   - Owner: `AlgoChains`
   - Repository name: `algochains-mcp-server`
   - Workflow filename: `release.yml`
   - Environment name: `pypi`
4. In GitHub repo Settings → Environments → create environment named `pypi`
5. Add a required reviewer (yourself) so every PyPI release needs your approval

### Changes to `.github/workflows/release.yml`:

Add two new jobs. Insert after the closing `}` of the `build-cli` job, before the `publish-sdk` job:

```yaml
  release-build-python:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Build Python distributions
        run: |
          pip install build
          python -m build
      - name: Upload distributions
        uses: actions/upload-artifact@v4
        with:
          name: python-dists
          path: dist/

  publish-pypi:
    runs-on: ubuntu-latest
    needs: [build-cli, release-build-python]
    environment:
      name: pypi
      url: https://pypi.org/p/algochains-mcp-server
    permissions:
      id-token: write
    steps:
      - name: Download distributions
        uses: actions/download-artifact@v4
        with:
          name: python-dists
          path: dist/
      - name: Publish to PyPI
        uses: pypa/gh-action-pypi-publish@release/v1
```

**Key rules from research (do not violate):**
- `id-token: write` is on the `publish-pypi` job ONLY — not the whole workflow
- Build (`release-build-python`) and publish (`publish-pypi`) must be separate jobs
- No `username` or `password` in the publish step — OIDC provides them automatically
- The `environment: pypi` must match exactly what you entered on pypi.org

**Verification after merge + tag push:**
```bash
pip install algochains-mcp-server   # should now succeed
python -c "import algochains_mcp; print(algochains_mcp.__version__)"
```

---

## Fix 6 — GitHub push protection (correct mechanism)

**Research finding:** `.github/secret_scanning.yml` configures **exclusions only**. Push protection for private repos requires GitHub Secret Protection (paid) enabled in Settings.

### Steps Tyler does in GitHub UI:
1. Repo Settings → Code security and analysis
2. "Secret scanning" → Enable
3. "Push protection" → Enable
4. Optional: configure "Delegated bypass" so you can override false positives

### If not on paid plan — free alternative (`git-secrets`):
```bash
brew install git-secrets
cd /Users/treycsa/CascadeProjects/algochains-mcp-server
git secrets --install        # installs pre-commit hook into .git/hooks/
git secrets --register-aws   # catches AWS key patterns
# Add custom patterns for Tradovate, Slack tokens:
git secrets --add 'TRADOVATE_[A-Z_]+=.+'
git secrets --add 'xoxb-[0-9]+-[0-9A-Za-z-]+'
git secrets --add 'xapp-[0-9]+-[0-9A-Za-z-]+'
```

---

## Commit sequence

```bash
cd /Users/treycsa/CascadeProjects/algochains-mcp-server

# After all edits:
git add README.md Makefile \
  src/algochains_mcp/__init__.py \
  src/algochains_mcp/server.py \
  src/algochains_mcp/trade_propagation.py \
  .github/workflows/release.yml

git diff --staged   # review everything one more time

git commit -m "$(cat <<'EOF'
fix: pip install path, HMAC fallback removal, version drift, PyPI OIDC publishing

- README: replace false PyPI install claim with working git clone + pip install -e
- Makefile: add make install / demo / health / config-cursor targets
- trade_propagation: remove _ROO_DEFAULT_SECRET='1234' fallback; fail closed when
  ALGOCHAINS_SIGNAL_SECRET unset (was visible in public repo — Django secret must rotate)
- __init__.py: replace hardcoded __version__ with importlib.metadata single source of truth
- server.py: derive version string from __version__ import instead of hardcoded v22.5
- release.yml: add OIDC Trusted Publishing jobs (release-build-python + publish-pypi);
  no PYPI_API_TOKEN secret needed — pending publisher must be configured on pypi.org first
EOF
)"

git push origin main
```

---

## Post-implementation checklist

- [ ] `grep -n "pip install algochains-mcp-server" README.md` → zero results
- [ ] `make demo` completes and prints tool count
- [ ] `python3 -c "from algochains_mcp.trade_propagation import _resolve_secret; _resolve_secret()"` → raises RuntimeError (no env set)
- [ ] `grep "_ROO_DEFAULT_SECRET" src/algochains_mcp/trade_propagation.py` → zero results
- [ ] `python3 -c "import algochains_mcp; print(algochains_mcp.__version__)"` → `22.4.0` (not `22.2.0`)
- [ ] `grep "v22.5" src/algochains_mcp/server.py` → zero results (now uses `__version__`)
- [ ] Tyler has configured pending publisher on pypi.org before next tag push
- [ ] Roo has been told to rotate Django HMAC secret and share new value securely
- [ ] `make test` passes (run `python3 -m pytest tests/ -x -q`)
