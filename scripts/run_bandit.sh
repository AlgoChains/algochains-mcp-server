#!/usr/bin/env bash
# AlgoChains CI — bandit security scan
# Catches: B602/B603 (shell=True), B105/B106 (hardcoded passwords), B324 (insecure hash)
# Run: bash scripts/run_bandit.sh [--fail-fast]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONTROL_TOWER="$(cd "$REPO_ROOT/../algochains-control-tower" 2>/dev/null && pwd || echo "")"

echo "=== AlgoChains bandit security scan ==="
pip install bandit --quiet 2>/dev/null || true

EXIT_CODE=0

scan() {
  local label="$1"; local path="$2"
  if [ ! -d "$path" ]; then
    echo "[SKIP] $label — path not found: $path"
    return
  fi
  echo ""
  echo "--- Scanning: $label ---"
  bandit -r "$path" \
    -t B602,B603,B105,B106,B324,B110,B112 \
    --skip B101 \
    -f txt \
    --severity-level medium \
    --confidence-level medium \
    || { echo "[WARN] bandit found issues in $label"; EXIT_CODE=1; }
}

scan "algochains-mcp-server" "$REPO_ROOT/src"
scan "algochains-control-tower" "$CONTROL_TOWER"

echo ""
if [ "$EXIT_CODE" -eq 0 ]; then
  echo "=== bandit: CLEAN ==="
else
  echo "=== bandit: ISSUES FOUND — review above output ==="
fi
exit "$EXIT_CODE"
