# AlgoChains MCP Server — Makefile
# Usage: make <target>

PYTHON     ?= python3
PIP        ?= $(PYTHON) -m pip
BUN        ?= bun
EXTRAS     ?= dev,http,supabase,auth
CLI_DIR    := src/cli
CLI_SRC    := src/cli/src
DIST       := dist

.PHONY: all install demo health config-cursor config-claude config-windsurf \
        test lint clean build-cli build-binary release-dry-run \
        stripe-app doctor version

# ── Default ───────────────────────────────────────────────────────────────────
all: install

# ── Python MCP server install ─────────────────────────────────────────────────
install:
	@echo "→ Installing AlgoChains MCP server (extras: $(EXTRAS))"
	$(PIP) install -e "[$(EXTRAS)]"
	@echo "✓ Install complete. Run: make demo"

# ── Demo mode — no credentials needed ─────────────────────────────────────────
demo: install
	@echo "→ Starting AlgoChains in demo mode (no credentials required)"
	$(PYTHON) scripts/quickstart.py --mode demo

# ── Health check ──────────────────────────────────────────────────────────────
health:
	@echo "→ Running health checks"
	$(PYTHON) scripts/startup_health_check.py

# ── IDE config generation ──────────────────────────────────────────────────────
config-cursor:
	$(PYTHON) scripts/quickstart.py --generate-config cursor

config-claude:
	$(PYTHON) scripts/quickstart.py --generate-config claude-desktop

config-windsurf:
	$(PYTHON) scripts/quickstart.py --generate-config windsurf

# ── Test suite ────────────────────────────────────────────────────────────────
test:
	$(PYTHON) -m pytest tests/ -x -q --tb=short

test-live:
	$(PYTHON) -m pytest tests/live/ -x -q --tb=short

# ── Lint ──────────────────────────────────────────────────────────────────────
lint:
	ruff check src/ tests/
	ruff format --check src/ tests/

lint-fix:
	ruff check --fix src/ tests/
	ruff format src/ tests/

# ── CLI: build TypeScript package ─────────────────────────────────────────────
cli-install:
	@echo "→ Installing CLI TypeScript dependencies"
	cd $(CLI_DIR) && npm install

cli-build: cli-install
	@echo "→ Building AlgoChains CLI"
	cd $(CLI_DIR) && $(BUN) build $(CLI_SRC)/index.ts --outdir $(DIST) --target bun

# ── Standalone binaries ────────────────────────────────────────────────────────
binary-linux: cli-install
	cd $(CLI_DIR) && $(BUN) build $(CLI_SRC)/index.ts \
	  --compile --outfile ../../$(DIST)/algochains-linux-x64 --target bun-linux-x64
	@echo "✓ $(DIST)/algochains-linux-x64"

binary-darwin: cli-install
	cd $(CLI_DIR) && $(BUN) build $(CLI_SRC)/index.ts \
	  --compile --outfile ../../$(DIST)/algochains-darwin-arm64 --target bun-darwin-arm64
	@echo "✓ $(DIST)/algochains-darwin-arm64"

binary-win: cli-install
	cd $(CLI_DIR) && $(BUN) build $(CLI_SRC)/index.ts \
	  --compile --outfile ../../$(DIST)/algochains-win-x64.exe --target bun-windows-x64
	@echo "✓ $(DIST)/algochains-win-x64.exe"

binaries: binary-darwin binary-linux binary-win
	@echo "✓ All binaries built in $(DIST)/"

# ── Shell completions ─────────────────────────────────────────────────────────
completions:
	@mkdir -p completions
	node $(DIST)/algochains-linux-x64 completion bash > completions/algochains.bash 2>/dev/null || \
	  $(PYTHON) -c "from src.cli.src.commands.completion import generateBashCompletion; print(generateBashCompletion())" > completions/algochains.bash
	node $(DIST)/algochains-linux-x64 completion zsh  > completions/algochains.zsh  2>/dev/null || true
	node $(DIST)/algochains-linux-x64 completion fish > completions/algochains.fish 2>/dev/null || true
	@echo "✓ Completions in completions/"

# ── Stripe APP ────────────────────────────────────────────────────────────────
stripe-app:
	@echo "→ Starting Stripe APP server on port 8091"
	uvicorn src.stripe_app.server:app --port 8091 --reload

# ── Doctor ────────────────────────────────────────────────────────────────────
doctor:
	$(PYTHON) scripts/startup_health_check.py

# ── Version ───────────────────────────────────────────────────────────────────
version:
	@$(PYTHON) -c "from src.algochains_mcp import __version__; print('algochains-mcp-server', __version__)"

# ── Release dry run ───────────────────────────────────────────────────────────
release-dry-run:
	@echo "→ Release dry run (no publish)"
	$(PIP) install build
	$(PYTHON) -m build --outdir dist/release-dry
	@echo "✓ Would publish: dist/release-dry/"
	ls -lh dist/release-dry/ 2>/dev/null || true

# ── Clean ─────────────────────────────────────────────────────────────────────
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	rm -rf dist/release-dry build/ .ruff_cache/
	@echo "✓ Clean complete"
