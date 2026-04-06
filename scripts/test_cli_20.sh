#!/bin/bash
# test_cli_20.sh — Test 20 most useful CLI commands (Phase 2.3)
set -uo pipefail
CLI="node /Users/treycsa/CascadeProjects/algochains-mcp-server/dist/algochains-cli.js"
PASS=0
FAIL=0
TOTAL=20

test_cmd() {
  local name="$1"
  shift
  echo -n "  [$((PASS+FAIL+1))/$TOTAL] $name ... "
  OUTPUT=$($CLI "$@" 2>&1)
  if [ $? -eq 0 ] && echo "$OUTPUT" | python3 -c "import json,sys; json.load(sys.stdin)" 2>/dev/null; then
    echo "✅ PASS"
    PASS=$((PASS+1))
  else
    echo "❌ FAIL"
    echo "    Output: $(echo "$OUTPUT" | head -3)"
    FAIL=$((FAIL+1))
  fi
}

echo "🧪 AlgoChains CLI Test Suite — 20 Commands"
echo "============================================"
echo ""

echo "── Smart Discovery ──"
test_cmd "discover_tools (portfolio)" discover_tools --query "portfolio positions"
test_cmd "discover_tools (sentiment)" discover_tools --query "sentiment analysis SEC filings"
test_cmd "discover_tools (options)" discover_tools --query "options flow dark pool"
test_cmd "discover_tools (backtest)" discover_tools --query "walk-forward optimization"

echo ""
echo "── Marketplace ──"
test_cmd "browse_strategy_marketplace" browse_strategy_marketplace

echo ""
echo "── V18 Intent Engine ──"
test_cmd "execute_intent (dry-run)" execute_intent --intent "Get me 10K AI exposure max 2% per stock" --dry-run true
test_cmd "detect_market_regime" detect_market_regime
test_cmd "detect_arbitrage" detect_arbitrage
test_cmd "create_shadow_portfolio" create_shadow_portfolio --name "test_cli" --capital 100000
test_cmd "evolve_strategies" evolve_strategies
test_cmd "get_intent_history" get_intent_history

echo ""
echo "── V17 Massive White-Label ──"
test_cmd "massive_search_endpoints" massive_search_endpoints --query "stock aggregates"

echo ""
echo "── V10 ML Engine ──"
test_cmd "dispatch_gpu_job" dispatch_gpu_job --script "test.py" --gpu-target "mac"

echo ""
echo "── V12 Analytics ──"
test_cmd "detect_regime" detect_regime --symbol "SPY"

echo ""
echo "── V13 Alt Data ──"
test_cmd "analyze_sentiment" analyze_sentiment --symbol "NVDA"

echo ""
echo "── V14 Agent Swarm ──"
test_cmd "get_swarm_status" get_swarm_status --swarm-id "test"

echo ""
echo "── V16 Cloud SaaS ──"
test_cmd "get_platform_health" get_platform_health

echo ""
echo "── Strategy Pipeline ──"
test_cmd "validate_strategy" validate_strategy --strategy-id "rsi_bb_mnq"

echo ""
echo "── Connectivity ──"
test_cmd "get_supported_brokers" get_supported_brokers

echo ""
echo "── Risk ──"
test_cmd "check_risk_alerts" check_risk_alerts

echo ""
echo "============================================"
echo "📊 Results: $PASS/$TOTAL passed, $FAIL/$TOTAL failed"
if [ $FAIL -eq 0 ]; then
  echo "🎉 ALL TESTS PASSED!"
else
  echo "⚠️  $FAIL tests need attention"
fi
