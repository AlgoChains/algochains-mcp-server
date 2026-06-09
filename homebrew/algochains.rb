class Algochains < Formula
  desc "AI-native algorithmic trading CLI — 482 MCP tools, interactive REPL, no IDE required"
  homepage "https://algochains.ai"
  url "https://github.com/AlgoChains/algochains-mcp-server/releases/download/v22.4.0/algochains-cli.js"
  sha256 "PENDING_FIRST_RELEASE"
  license "MIT"
  version "22.4.0"

  # This formula is auto-updated by CI on every tagged release.
  # To update manually: bump version, url, and sha256 above.
  # SHA256 is computed by CI: sha256sum algochains-cli.js | cut -d' ' -f1

  depends_on "node" => ">= 18"

  def install
    libexec.install "algochains-cli.js"

    # Main binary: full safety wrapper with trust-ladder flags
    (bin/"algochains").write <<~EOS
      #!/usr/bin/env bash
      # AlgoChains CLI — Safety wrapper v#{version}
      # Trust ladder: T0 (read) → T1 (compute) → T2 (paper) → T3 (live)
      set -euo pipefail

      BUNDLE="#{libexec}/algochains-cli.js"
      CMD="${1:-}"

      # Trust-tier classification
      TRADE_EXEC="place-order cancel-order close-position flatten-position close-all-positions deploy-strategy restart-bot"
      COMPUTE="run-backtest optimize-strategy validate-strategy dispatch-tower-job dispatch-gpu-task"
      KILLSWITCH_FILE="${HOME}/.algochains/KILLSWITCH"

      DRY_RUN=false; SAFE_ONLY=false; CONFIRM=false
      for arg in "$@"; do
        case "$arg" in
          --dry-run) DRY_RUN=true ;;
          --safe-only) SAFE_ONLY=true ;;
          --confirm) CONFIRM=true ;;
        esac
      done

      # Check kill switch
      if [ -f "$KILLSWITCH_FILE" ]; then
        is_in() { local t="$1" l="$2"; for i in $l; do [ "$i" = "$t" ] && return 0; done; return 1; }
        if is_in "$CMD" "$TRADE_EXEC"; then
          echo "🛑 KILL SWITCH ACTIVE — all T3/TRADE_EXEC operations blocked"
          echo "   Run: algochains killswitch off   to resume"
          exit 1
        fi
      fi

      exec node "$BUNDLE" "$@"
    EOS

    bash_completion.install "completions/algochains.bash" => "algochains" if File.exist?("completions/algochains.bash")
    zsh_completion.install "completions/algochains.zsh" => "_algochains" if File.exist?("completions/algochains.zsh")
    fish_completion.install "completions/algochains.fish" if File.exist?("completions/algochains.fish")
    man1.install "man/man1/algochains.1" if File.exist?("man/man1/algochains.1")
  end

  def caveats
    <<~EOS
      AlgoChains CLI v#{version} installed.

      Quick start:
        algochains doctor              # verify setup
        algochains detect-market-regime
        algochains                     # launch interactive REPL

      Authenticate brokers:
        algochains auth set tradovate
        algochains auth set alpaca

      Configure IDE (Cursor, Claude Desktop):
        algochains config generate cursor

      Kill switch (emergency stop all trades):
        algochains killswitch on
        algochains killswitch off

      Full docs: https://docs.algochains.ai/cli
    EOS
  end

  test do
    # Verify CLI starts and discovers tools
    output = shell_output("#{bin}/algochains discover-tools --query portfolio --json 2>&1")
    assert_match "portfolio", output

    # Verify version matches formula
    version_out = shell_output("node #{libexec}/algochains-cli.js --version 2>&1")
    assert_match version.to_s, version_out
  end
end
