class Algochains < Formula
  desc "AI-Native Algorithmic Trading CLI — 242 MCP tools, no IDE required"
  homepage "https://github.com/AlgoChains/algochains-mcp-server"
  url "https://github.com/AlgoChains/algochains-mcp-server/releases/download/v18.0.0/algochains-cli.js"
  sha256 "PLACEHOLDER_SHA256"
  license "MIT"
  version "18.0.0"

  depends_on "node" => ">= 18"

  def install
    libexec.install "algochains-cli.js"

    # Safety wrapper
    (bin/"algochains").write <<~EOS
      #!/bin/bash
      exec node "#{libexec}/algochains-cli.js" "$@"
    EOS
  end

  test do
    output = shell_output("#{bin}/algochains discover-tools --query portfolio 2>&1")
    assert_match "get_factor_exposure", output
  end
end
