#Requires -Version 5.1
<#
.SYNOPSIS
  AlgoChains CLI — Safety wrapper for Windows PowerShell
  Trust ladder: T0 (read) → T1 (compute) → T2 (paper) → T3 (live)

.DESCRIPTION
  Wraps the AlgoChains CLI bundle with trust-tier enforcement,
  kill switch support, and dry-run mode for safe exploration.

.PARAMETER Command
  The CLI command to run (e.g. detect-market-regime, discover-tools)

.PARAMETER DryRun
  Preview TRADE_EXEC actions without executing them

.PARAMETER SafeOnly
  Block all TRADE_EXEC and WRITE_DESTRUCTIVE tools

.PARAMETER Confirm
  Required flag for TRADE_EXEC operations

.EXAMPLE
  algochains detect-market-regime
  algochains discover-tools --query portfolio
  algochains place-order --broker alpaca --symbol AAPL --side buy --qty 10 --confirm
  algochains place-order --broker tradovate --symbol MNQ --side buy --qty 1 --dry-run
#>

param(
    [Parameter(Position=0)]
    [string]$Command = "",
    [switch]$DryRun,
    [switch]$SafeOnly,
    [switch]$Confirm
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Tool classification ────────────────────────────────────────────────────────
$TradeExec = @(
    "place-order", "cancel-order", "close-position", "flatten-position",
    "close-all-positions", "deploy-strategy", "restart-bot"
)
$WriteDestructive = @("close-all-positions", "cancel-all-orders")
$SafeTools = @(
    "discover-tools", "get-tool-details", "detect-market-regime", "get-bot-health",
    "browse-strategy-marketplace", "portfolio-summary", "get-positions", "get-account",
    "get-orders", "detect-arbitrage", "analyze-sentiment", "onyx-ask", "onyx-search"
)

# ── Locate CLI bundle ──────────────────────────────────────────────────────────
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$CliBundleOptions = @(
    (Join-Path $ScriptDir "..\dist\algochains-cli.js"),
    (Join-Path $env:APPDATA "AlgoChains\algochains-cli.js"),
    "algochains-cli.js"
)

$CliBundle = $null
foreach ($opt in $CliBundleOptions) {
    if (Test-Path $opt) { $CliBundle = (Resolve-Path $opt).Path; break }
}

if (-not $CliBundle) {
    Write-Error "algochains-cli.js not found. Run: iwr https://algochains.ai/install.ps1 | iex"
    exit 1
}

# ── Kill switch check ──────────────────────────────────────────────────────────
$KillSwitchFile = Join-Path $env:USERPROFILE ".algochains\KILLSWITCH"
if ((Test-Path $KillSwitchFile) -and ($TradeExec -contains $Command)) {
    Write-Host "🛑 KILL SWITCH ACTIVE — all T3/TRADE_EXEC operations blocked" -ForegroundColor Red
    Write-Host "   Run: algochains killswitch off   to resume" -ForegroundColor Yellow
    exit 1
}

# ── Safety enforcement ─────────────────────────────────────────────────────────
if ($SafeOnly -and ($TradeExec -contains $Command)) {
    Write-Host "🛑 BLOCKED by -SafeOnly: '$Command' is a TRADE_EXEC tool" -ForegroundColor Red
    exit 1
}

if ($DryRun -and ($TradeExec -contains $Command)) {
    Write-Host "⏸️  DRY-RUN: Would execute '$Command'" -ForegroundColor Yellow
    Write-Host "   Tool classification: TRADE_EXEC" -ForegroundColor Yellow
    Write-Host "   Remove -DryRun to execute" -ForegroundColor Yellow
    exit 0
}

if (($TradeExec -contains $Command) -and -not $Confirm) {
    Write-Host "⚠️  '$Command' is a TRADE_EXEC tool — requires -Confirm flag" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "   This tool can place/modify real trades. Add -Confirm to proceed:" -ForegroundColor White
    Write-Host "   algochains $Command $($args -join ' ') -Confirm" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "   Or use -DryRun to preview without executing:" -ForegroundColor White
    Write-Host "   algochains $Command $($args -join ' ') -DryRun" -ForegroundColor Cyan
    exit 1
}

if ($WriteDestructive -contains $Command) {
    Write-Host "🚨 DESTRUCTIVE: '$Command' will modify ALL positions/orders" -ForegroundColor Red
    $answer = Read-Host "   Type 'YES' to proceed"
    if ($answer -ne "YES") { Write-Host "   Aborted."; exit 1 }
}

# ── Execute ────────────────────────────────────────────────────────────────────
$NodeArgs = @($CliBundle) + $args
node @NodeArgs
