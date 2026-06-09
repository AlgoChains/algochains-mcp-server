#!/usr/bin/env bash
# AlgoChains Universal Installer
# Usage: curl -fsSL https://algochains.ai/install.sh | sh
#        Or: bash scripts/install.sh [--version v22.4.0] [--method binary|npm|brew|pip]
set -euo pipefail

REPO="AlgoChains/algochains-mcp-server"
INSTALL_DIR="${ALGOCHAINS_INSTALL_DIR:-${HOME}/.local/bin}"
CONFIG_DIR="${HOME}/.algochains"
VERSION="${ALGOCHAINS_VERSION:-latest}"
METHOD="${ALGOCHAINS_METHOD:-auto}"

# ── Colors ─────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${CYAN}ℹ${NC}  $*"; }
success() { echo -e "${GREEN}✓${NC}  $*"; }
warn()    { echo -e "${YELLOW}⚠${NC}  $*"; }
error()   { echo -e "${RED}✗${NC}  $*" >&2; exit 1; }
step()    { echo -e "\n${BOLD}$*${NC}"; }

# ── Detect OS + arch ───────────────────────────────────────────────────────────
detect_platform() {
    OS="$(uname -s)"
    ARCH="$(uname -m)"

    case "$OS" in
        Darwin)
            case "$ARCH" in
                arm64) BINARY="algochains-darwin-arm64" ;;
                x86_64) BINARY="algochains-darwin-x64" ;;
                *) error "Unsupported macOS architecture: $ARCH" ;;
            esac
            ;;
        Linux)
            case "$ARCH" in
                x86_64) BINARY="algochains-linux-x64" ;;
                aarch64|arm64) BINARY="algochains-linux-arm64" ;;
                *) error "Unsupported Linux architecture: $ARCH" ;;
            esac
            ;;
        *)
            error "Unsupported OS: $OS. Use Windows installer: iwr https://algochains.ai/install.ps1 | iex"
            ;;
    esac
}

# ── Resolve latest version ──────────────────────────────────────────────────────
resolve_version() {
    if [ "$VERSION" = "latest" ]; then
        VERSION=$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest" \
            | grep '"tag_name"' | sed -E 's/.*"([^"]+)".*/\1/')
        [ -n "$VERSION" ] || error "Could not resolve latest version from GitHub API"
    fi
    info "Installing AlgoChains ${VERSION}"
}

# ── Check dependencies ──────────────────────────────────────────────────────────
check_deps() {
    if ! command -v curl &>/dev/null; then
        error "curl is required. Install it with: apt install curl  or  brew install curl"
    fi
}

# ── Install methods ─────────────────────────────────────────────────────────────
install_binary() {
    step "Installing standalone binary (no runtime dependencies)"
    detect_platform

    URL="https://github.com/${REPO}/releases/download/${VERSION}/${BINARY}"
    DEST="${INSTALL_DIR}/algochains"

    mkdir -p "$INSTALL_DIR"
    info "Downloading ${BINARY} from ${URL}"
    curl -fsSL --progress-bar "$URL" -o "$DEST"
    chmod +x "$DEST"

    success "Binary installed to ${DEST}"
    _post_install
}

install_brew() {
    step "Installing via Homebrew"
    if ! command -v brew &>/dev/null; then
        error "Homebrew not found. Install it: https://brew.sh"
    fi
    brew tap algochains/algochains 2>/dev/null || true
    brew install algochains
    success "Installed via Homebrew"
}

install_npm() {
    step "Installing via npm (Node.js CLI bundle)"
    if ! command -v node &>/dev/null || ! command -v npm &>/dev/null; then
        error "Node.js and npm are required. Install: https://nodejs.org"
    fi

    URL="https://github.com/${REPO}/releases/download/${VERSION}/algochains-cli.js"
    DEST="${INSTALL_DIR}/algochains-cli.js"
    WRAPPER="${INSTALL_DIR}/algochains"

    mkdir -p "$INSTALL_DIR"
    info "Downloading algochains-cli.js"
    curl -fsSL --progress-bar "$URL" -o "$DEST"

    # Write wrapper script
    cat > "$WRAPPER" << 'WRAPPER_EOF'
#!/usr/bin/env bash
exec node "$(dirname "$(realpath "$0")")/algochains-cli.js" "$@"
WRAPPER_EOF
    chmod +x "$WRAPPER"
    success "CLI installed to ${WRAPPER}"
    _post_install
}

install_pip() {
    step "Installing MCP server via pip"
    if ! command -v pip3 &>/dev/null && ! command -v pip &>/dev/null; then
        error "pip is required. Install Python: https://python.org"
    fi
    PIP="${PIP:-$(command -v pip3 || command -v pip)}"
    "$PIP" install "algochains-mcp-server==${VERSION#v}"
    success "MCP server installed. Run: algochains-mcp"
}

_post_install() {
    # Ensure install dir is in PATH
    if [[ ":$PATH:" != *":${INSTALL_DIR}:"* ]]; then
        warn "${INSTALL_DIR} is not in your PATH"
        echo ""
        echo "  Add to your shell profile (~/.zshrc, ~/.bashrc):"
        echo "  ${CYAN}export PATH=\"${INSTALL_DIR}:\$PATH\"${NC}"
        echo ""
    fi

    # Create config directory
    mkdir -p "$CONFIG_DIR"

    # Run doctor
    step "Running post-install health check"
    if command -v algochains &>/dev/null; then
        algochains doctor --quick 2>/dev/null || warn "Doctor check incomplete — run 'algochains doctor' after setting up auth"
        echo ""
        success "AlgoChains ${VERSION} installed successfully!"
        echo ""
        echo "  Next steps:"
        echo "  ${CYAN}algochains auth set alpaca${NC}     # Connect Alpaca (free paper trading)"
        echo "  ${CYAN}algochains auth set tradovate${NC}  # Connect Tradovate (futures)"
        echo "  ${CYAN}algochains doctor${NC}              # Verify full setup"
        echo "  ${CYAN}algochains${NC}                     # Launch interactive REPL"
        echo ""
        echo "  Docs: ${CYAN}https://docs.algochains.ai/cli${NC}"
    fi
}

# ── Method selection ────────────────────────────────────────────────────────────
main() {
    echo -e "${BOLD}AlgoChains CLI Installer${NC}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    check_deps
    resolve_version

    case "$METHOD" in
        binary) install_binary ;;
        brew)   install_brew ;;
        npm)    install_npm ;;
        pip)    install_pip ;;
        auto)
            # Auto-select: binary if GitHub releases exist, brew on macOS, npm fallback
            if command -v brew &>/dev/null && [[ "$(uname)" == "Darwin" ]]; then
                install_brew
            else
                install_binary
            fi
            ;;
        *) error "Unknown install method: $METHOD. Use: binary|brew|npm|pip" ;;
    esac
}

# ── Argument parsing ────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --version) VERSION="$2"; shift 2 ;;
        --method)  METHOD="$2"; shift 2 ;;
        --dir)     INSTALL_DIR="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: install.sh [--version v22.4.0] [--method binary|npm|brew|pip] [--dir /path/to/bin]"
            exit 0
            ;;
        *) error "Unknown argument: $1" ;;
    esac
done

main
