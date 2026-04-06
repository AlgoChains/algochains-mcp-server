#!/bin/bash
# demo_algochains.sh — 60-second investor demo. No IDE required.
# Usage: ./scripts/demo_algochains.sh
set -euo pipefail

CLI="node $(dirname "$0")/../dist/algochains-cli.js"

echo "🏗️  AlgoChains — AI-Native Algorithmic Trading Platform"
echo "======================================================="
echo ""

echo "📊 Step 1: Browse validated bot marketplace (172 bots, MCPT-validated)"
$CLI browse_strategy_marketplace
echo ""

echo "🔍 Step 2: Smart Tool Discovery — find any capability by description"
$CLI discover_tools --query "options flow dark pool institutional"
echo ""

echo "📈 Step 3: Real-time market data via Massive white-label"
$CLI massive_search_endpoints --query "stock aggregates daily"
echo ""

echo "🧠 Step 4: V18 Intent-Based Trading — natural language → execution plan"
$CLI execute_intent --intent "Show me the top 5 AI stocks by momentum, max 2% risk each" --dry-run true
echo ""

echo "🔬 Step 5: Market regime detection — bull, bear, or sideways?"
$CLI detect_market_regime
echo ""

echo "✅ All from CLI — no IDE required. 242 tools, 5 brokers, 10+ data providers."
echo "   Visit https://algochains.ai for more."
