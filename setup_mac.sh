#!/bin/zsh
# AlgoChains setup for macOS — no Homebrew/sudo required
set -e

echo "=== Step 1: Ensure ~/.local/bin is on PATH ==="
grep -q 'HOME/.local/bin' ~/.zshrc 2>/dev/null || echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
grep -q 'HOME/.local/bin' ~/.zprofile 2>/dev/null || echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zprofile
export PATH="$HOME/.local/bin:$PATH"

echo ""
echo "=== Step 2: Install uv (user-level Python manager) ==="
if ! command -v uv &>/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
echo "uv: $(uv --version)"

echo ""
echo "=== Step 3: Install Python 3.11 + algochains-mcp-server ==="
cd ~/Documents/CursorProjects/algochains-mcp-server
uv python install 3.11
uv venv .venv --python 3.11
uv pip install --python .venv/bin/python algochains-mcp-server

echo ""
echo "=== Step 4: Health check (demo mode) ==="
source .venv/bin/activate
python scripts/quickstart.py --health-check --mode demo

echo ""
echo "=== Step 5: Generate Cursor MCP config ==="
python scripts/quickstart.py --generate-config cursor

echo ""
echo "=== Step 6: Install Claude Code (if missing) ==="
if ! command -v claude &>/dev/null; then
  curl -fsSL https://claude.ai/install.sh | bash
fi
claude --version

echo ""
echo "==========================================="
echo "Done! Run these in a NEW terminal window:"
echo ""
echo "  claude                          # Claude Code CLI"
echo "  source ~/Documents/CursorProjects/algochains-mcp-server/.venv/bin/activate"
echo "  algochains-mcp --mode demo        # MCP server (demo, no keys)"
echo ""
echo "Restart Cursor to load AlgoChains MCP tools."
echo "==========================================="
