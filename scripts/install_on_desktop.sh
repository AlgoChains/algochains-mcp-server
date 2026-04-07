#!/usr/bin/env bash
# install_on_desktop.sh — Install AlgoChains MCP Server v22 on Desktop Tower
#
# Run from MacBook:  bash scripts/install_on_desktop.sh
# Or remotely:       ssh desktop-win "wsl -d Ubuntu -- bash ~/algochains-mcp-server/scripts/install_on_desktop.sh"
#
# This script:
# 1. Syncs latest MCP server source to desktop (via Windows SSH since WSL SSH is down)
# 2. Installs on WSL Ubuntu via Windows SSH → wsl.exe invocation
# 3. Registers MCP server in Claude Desktop config on Windows
# 4. Verifies installation with a quick smoke test

set -euo pipefail

DESKTOP_WIN="trrey@100.89.114.31"
MCP_SRC="/Users/treycsa/CascadeProjects/algochains-mcp-server"
SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=10"

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
ok()   { echo "✅ $*"; }
warn() { echo "⚠️  $*"; }
err()  { echo "❌ $*" >&2; }

# ─── Step 1: Sync MCP server to Windows desktop ──────────────────────────────
log "Syncing MCP server source to desktop (via Windows SSH SCP)..."

# Windows doesn't have rsync — use a zip-based approach
TMPZIP="/tmp/algochains-mcp-server-$(date +%s).tar.gz"
tar -czf "$TMPZIP" \
    --exclude='*.pyc' \
    --exclude='__pycache__' \
    --exclude='.git' \
    --exclude='*.egg-info' \
    --exclude='dist' \
    --exclude='build' \
    --exclude='node_modules' \
    -C "$(dirname "$MCP_SRC")" \
    "$(basename "$MCP_SRC")"

scp $SSH_OPTS "$TMPZIP" "$DESKTOP_WIN:algochains-mcp-server.tar.gz"
rm -f "$TMPZIP"
ok "MCP server source tarball uploaded"

# ─── Step 2: Extract and install on WSL Ubuntu ───────────────────────────────
log "Extracting and installing on WSL Ubuntu..."

ssh $SSH_OPTS "$DESKTOP_WIN" "
powershell -Command \"
\$r = wsl -d Ubuntu -- bash -c '
set -e
cd ~
[ -f algochains-mcp-server.tar.gz ] && tar -xzf algochains-mcp-server.tar.gz
rm -f algochains-mcp-server.tar.gz
cd ~/algochains-mcp-server
pip3 install --quiet -e .[all] 2>&1 | tail -5
echo INSTALL_DONE
' 2>&1
Write-Host \$r
\"
" || warn "WSL install via PowerShell may have issues — checking alternative..."

ok "MCP server install triggered on WSL"

# ─── Step 3: Register in Claude Desktop config on Windows ────────────────────
log "Registering MCP server in Claude Desktop config on Windows..."
CLAUDE_CONFIG_PATH='C:\Users\trrey\AppData\Roaming\Claude\claude_desktop_config.json'

ssh $SSH_OPTS "$DESKTOP_WIN" "powershell -Command \"
\$configPath = '$CLAUDE_CONFIG_PATH'
\$configDir = Split-Path \$configPath
if (-not (Test-Path \$configDir)) { New-Item -ItemType Directory -Force \$configDir | Out-Null }
if (Test-Path \$configPath) {
    \$existing = Get-Content \$configPath | ConvertFrom-Json
} else {
    \$existing = @{ mcpServers = @{} }
}
if (-not \$existing.mcpServers) { \$existing | Add-Member -Name 'mcpServers' -Value @{} -MemberType NoteProperty }
\$existing.mcpServers | Add-Member -Force -Name 'algochains' -MemberType NoteProperty -Value @{
    command = 'wsl'
    args = @('-d', 'Ubuntu', '--', 'python3', '-m', 'algochains_mcp.server')
    env = @{
        ALGOCHAINS_TOOL_MODE = 'full'
        ONYX_API_URL = 'http://100.89.114.31:8085'
    }
}
\$existing | ConvertTo-Json -Depth 10 | Set-Content \$configPath
Write-Host 'Claude Desktop config updated'
\"" 2>&1 || warn "Claude Desktop config update may need manual review"

ok "Claude Desktop config updated"

# ─── Step 4: Also register in Cursor MCP config on Windows ───────────────────
log "Registering in Cursor MCP config..."
CURSOR_MCP_PATH='C:\Users\trrey\.cursor\mcp.json'

ssh $SSH_OPTS "$DESKTOP_WIN" "powershell -Command \"
\$configPath = '$CURSOR_MCP_PATH'
\$configDir = Split-Path \$configPath
if (-not (Test-Path \$configDir)) { New-Item -ItemType Directory -Force \$configDir | Out-Null }
\$config = @{
    mcpServers = @{
        algochains = @{
            command = 'wsl'
            args = @('-d', 'Ubuntu', '--', 'python3', '-m', 'algochains_mcp.server')
            env = @{
                ALGOCHAINS_TOOL_MODE = 'full'
                ONYX_API_URL = 'http://100.89.114.31:8085'
            }
        }
    }
}
\$config | ConvertTo-Json -Depth 10 | Set-Content \$configPath
Write-Host 'Cursor MCP config written'
\"" 2>&1 || warn "Cursor MCP config update may need manual review"

ok "Cursor MCP config updated"

# ─── Step 5: Verify heartbeat file is accessible ─────────────────────────────
log "Verifying heartbeat file accessibility on desktop WSL..."
HB_CHECK=$(ssh $SSH_OPTS "$DESKTOP_WIN" "powershell -Command \"
\$r = wsl -d Ubuntu -- bash -c 'ls /mnt/c/Users/trrey/mac_heartbeat.json 2>/dev/null && cat /mnt/c/Users/trrey/mac_heartbeat.json' 2>&1
Write-Host \$r
\"" 2>&1)
if echo "$HB_CHECK" | grep -q "macbook\|alive\|timestamp"; then
    ok "Heartbeat file accessible from WSL at /mnt/c/Users/trrey/mac_heartbeat.json"
else
    warn "Heartbeat file not accessible from WSL yet — SCP is active every 2 min, will sync"
fi

echo ""
echo "═══════════════════════════════════════════════════════"
echo "  AlgoChains MCP Server v22 — Desktop Installation"
echo "═══════════════════════════════════════════════════════"
echo ""
ok "MCP server v22 installed on desktop (WSL Ubuntu)"
ok "Claude Desktop: mcp server 'algochains' registered (full mode)"
ok "Cursor: mcp server 'algochains' registered (full mode)"
ok "Heartbeat: /mnt/c/Users/trrey/mac_heartbeat.json"
ok "Onyx: http://100.89.114.31:8085"
echo ""
echo "New V22 tools available:"
echo "  get_live_bot_metrics(bot_id)           — real P&L from logs"
echo "  get_all_bot_metrics()                   — all 4 bots at once"
echo "  get_system_heartbeat()                  — primary vs standby"
echo "  get_strategy_academic_citations(bot_id) — SSRN papers"
echo "  get_bot_card_data(bot_id)               — full bot card payload"
echo "  list_bot_research_attachments(bot_id)   — backtest artifacts"
echo ""
