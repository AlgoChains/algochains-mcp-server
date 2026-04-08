#!/bin/bash
# AlgoChains Command Center — Cloudflare Tunnel Setup
# Provisions a named tunnel and maps it to your chosen subdomain.
#
# Usage: bash scripts/setup_cc_tunnel.sh
#
# After running:
#   - Command Center will be live at https://<SUBDOMAIN>.<YOUR_DOMAIN>
#   - The tunnel persists across restarts
#   - No inbound firewall rules needed (Cloudflare handles TLS)

set -euo pipefail

TUNNEL_NAME="${TUNNEL_NAME:-algochains-cc}"
SUBDOMAIN="${SUBDOMAIN:-cc}"
DOMAIN="${DOMAIN:-algochains.io}"
CC_PORT="${CC_PORT:-3333}"
FULL_HOSTNAME="${SUBDOMAIN}.${DOMAIN}"

# Detect cloudflared
if ! command -v cloudflared &>/dev/null; then
  echo "Installing cloudflared..."
  if [[ "$(uname)" == "Darwin" ]]; then
    brew install cloudflare/cloudflare/cloudflared
  elif [[ "$(uname)" == "Linux" ]]; then
    curl -L https://pkg.cloudflare.com/cloudflare-main.gpg | sudo apt-key add -
    echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" \
      | sudo tee /etc/apt/sources.list.d/cloudflared.list
    sudo apt-get update && sudo apt-get install -y cloudflared
  else
    echo "ERROR: Unsupported OS. Install cloudflared manually: https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/"
    exit 1
  fi
fi

echo ""
echo "=== AlgoChains Command Center Tunnel Setup ==="
echo "Tunnel name: $TUNNEL_NAME"
echo "Target URL:  https://$FULL_HOSTNAME"
echo "Local port:  $CC_PORT"
echo ""

# Step 1: Authenticate
echo "Step 1: Authenticating with Cloudflare (opens browser)..."
cloudflared tunnel login

# Step 2: Create tunnel
echo "Step 2: Creating tunnel '$TUNNEL_NAME'..."
cloudflared tunnel create "$TUNNEL_NAME" 2>/dev/null || echo "Tunnel may already exist, continuing..."

# Step 3: Get tunnel ID
TUNNEL_ID=$(cloudflared tunnel list --output json 2>/dev/null | python3 -c "
import json, sys
tunnels = json.load(sys.stdin)
for t in tunnels:
    if t.get('name') == '$TUNNEL_NAME':
        print(t['id'])
        break
" 2>/dev/null || echo "")

if [[ -z "$TUNNEL_ID" ]]; then
  echo "ERROR: Could not determine tunnel ID. Run 'cloudflared tunnel list' to check."
  exit 1
fi
echo "Tunnel ID: $TUNNEL_ID"

# Step 4: Write config
CONFIG_DIR="$HOME/.cloudflared"
mkdir -p "$CONFIG_DIR"
CREDS_FILE="$CONFIG_DIR/${TUNNEL_ID}.json"

cat > "$CONFIG_DIR/config.yml" << EOF
tunnel: $TUNNEL_ID
credentials-file: $CREDS_FILE

ingress:
  # AlgoChains Command Center
  - hostname: $FULL_HOSTNAME
    service: http://localhost:$CC_PORT
    originRequest:
      noTLSVerify: true
      connectTimeout: 15s
      keepAliveTimeout: 90s
      keepAliveConnections: 100

  # Catch-all
  - service: http_status:404
EOF

echo "Config written to $CONFIG_DIR/config.yml"

# Step 5: Route DNS
echo "Step 5: Setting up DNS CNAME $FULL_HOSTNAME → $TUNNEL_ID.cfargotunnel.com..."
cloudflared tunnel route dns "$TUNNEL_NAME" "$FULL_HOSTNAME" 2>&1 || \
  echo "DNS may need manual setup: Add CNAME '$SUBDOMAIN' → '${TUNNEL_ID}.cfargotunnel.com' in Cloudflare dashboard"

# Step 6: Start tunnel
echo ""
echo "=== Setup Complete ==="
echo ""
echo "To start the tunnel:"
echo "  cloudflared tunnel run $TUNNEL_NAME"
echo ""
echo "To run as a background service:"
if [[ "$(uname)" == "Darwin" ]]; then
  echo "  sudo cloudflared service install"
  echo "  sudo launchctl start com.cloudflare.cloudflared"
elif [[ "$(uname)" == "Linux" ]]; then
  echo "  sudo cloudflared service install"
  echo "  sudo systemctl start cloudflared"
fi
echo ""
echo "Command Center will be at: https://$FULL_HOSTNAME"
echo "(Make sure the Command Center is running: cd algochains-command-center && npm run dev)"
