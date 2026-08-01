#!/bin/zsh
# AlgoChains MCP launcher — sources .env and starts the stdio MCP server
set -a
source "$(dirname "$0")/../.env" 2>/dev/null || true
set +a
exec "$(dirname "$0")/../.venv/bin/algochains-mcp" "$@"
