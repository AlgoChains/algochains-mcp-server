#Requires -Version 5.1
<#
.SYNOPSIS
  AlgoChains Universal Windows Installer
  Usage: iwr https://algochains.ai/install.ps1 | iex
         Or: .\scripts\install.ps1 [--version 22.4.0] [--method pipx|venv|pip]

.DESCRIPTION
  Installs algochains-mcp-server on Windows by:
  1. Detecting Python (python, python3, py in order)
  2. Installing Python via winget if not found
  3. Installing pipx if not on PATH
  4. Installing algochains-mcp-server via pipx
  5. Verifying and printing next steps
#>

param(
    [string]$Version   = "latest",
    [string]$Method    = "pipx",     # pipx | venv | pip
    [string]$VenvPath  = "$env:USERPROFILE\algochains-venv",
    [switch]$Help
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Colors / helpers ─────────────────────────────────────────────────────────
function Write-Step  { param($msg) Write-Host "`n  $msg" -ForegroundColor Cyan }
function Write-OK    { param($msg) Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn  { param($msg) Write-Host "  [!]  $msg" -ForegroundColor Yellow }
function Write-Fail  { param($msg) Write-Host "  [X]  $msg" -ForegroundColor Red; exit 1 }

if ($Help) {
    Get-Help $MyInvocation.MyCommand.Definition -Detailed
    exit 0
}

Write-Host ""
Write-Host "  AlgoChains Installer for Windows" -ForegroundColor Cyan -NoNewline
Write-Host " (method: $Method)" -ForegroundColor Gray
Write-Host "  ─────────────────────────────────────────────" -ForegroundColor DarkGray

# ── Step 1: Detect Python ─────────────────────────────────────────────────────
Write-Step "Detecting Python..."

$PythonCmd = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $ver = & $cmd --version 2>&1
        if ($ver -match "Python (\d+)\.(\d+)") {
            $major = [int]$Matches[1]
            $minor = [int]$Matches[2]
            if ($major -ge 3 -and $minor -ge 11) {
                $PythonCmd = $cmd
                Write-OK "$cmd $($Matches[0]) found"
                break
            } elseif ($major -ge 3) {
                Write-Warn "$cmd $($Matches[0]) found but Python 3.11+ recommended (continuing anyway)"
                $PythonCmd = $cmd
                break
            }
        }
    } catch { }
}

if (-not $PythonCmd) {
    Write-Warn "Python not found on PATH. Attempting to install via winget..."
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install --id Python.Python.3.12 --source winget --accept-package-agreements --accept-source-agreements
        Write-OK "Python installed. Please RESTART PowerShell and run this installer again."
        exit 0
    } else {
        Write-Fail "Python not found and winget is not available.`n  Install Python manually: https://www.python.org/downloads/`n  Check 'Add Python to PATH' during install, then re-run this script."
    }
}

# ── Step 2: Install based on method ──────────────────────────────────────────

if ($Method -eq "venv") {
    # ── venv method (no pipx required) ───────────────────────────────────────
    Write-Step "Creating virtual environment at $VenvPath..."
    & $PythonCmd -m venv $VenvPath
    Write-OK "Virtual environment created"

    $pip   = Join-Path $VenvPath "Scripts\pip.exe"
    $mcp   = Join-Path $VenvPath "Scripts\algochains-mcp.exe"

    Write-Step "Installing algochains-mcp-server in venv..."
    if ($Version -eq "latest") {
        & $pip install algochains-mcp-server --quiet
    } else {
        & $pip install "algochains-mcp-server==$Version" --quiet
    }

    Write-OK "algochains-mcp-server installed"

    Write-Host ""
    Write-Host "  Installation complete!" -ForegroundColor Green
    Write-Host ""
    Write-Host "  To use AlgoChains, activate the venv first:" -ForegroundColor White
    Write-Host "    $VenvPath\Scripts\Activate.ps1" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Or run directly without activating:" -ForegroundColor White
    Write-Host "    $mcp --help" -ForegroundColor Cyan
    Write-Host ""

} else {
    # ── pipx method (recommended) ─────────────────────────────────────────────
    Write-Step "Checking for pipx..."

    $pipxFound = $false
    try {
        $null = & pipx --version 2>&1
        $pipxFound = $true
        Write-OK "pipx already installed"
    } catch { }

    if (-not $pipxFound) {
        Write-Step "Installing pipx..."
        & $PythonCmd -m pip install pipx --quiet
        Write-OK "pipx installed"

        Write-Step "Adding pipx to PATH..."
        & $PythonCmd -m pipx ensurepath
        Write-Warn "PATH updated. You may need to RESTART PowerShell for 'pipx' to be recognized."
        Write-Warn "If the next step fails, close and reopen PowerShell, then run:"
        Write-Warn "  pipx install algochains-mcp-server"

        # Refresh PATH in current session (best-effort)
        $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "User") + ";" +
                    [System.Environment]::GetEnvironmentVariable("PATH", "Machine")
    }

    Write-Step "Installing algochains-mcp-server via pipx..."
    try {
        if ($Version -eq "latest") {
            & pipx install algochains-mcp-server
        } else {
            & pipx install "algochains-mcp-server==$Version"
        }
        Write-OK "algochains-mcp-server installed"
    } catch {
        # pipx might not be on PATH yet in this session — try via python -m
        Write-Warn "pipx not yet on PATH in this session. Trying python -m pipx..."
        if ($Version -eq "latest") {
            & $PythonCmd -m pipx install algochains-mcp-server
        } else {
            & $PythonCmd -m pipx install "algochains-mcp-server==$Version"
        }
        Write-OK "algochains-mcp-server installed (via python -m pipx)"
    }

    Write-Host ""
    Write-Host "  Installation complete!" -ForegroundColor Green
    Write-Host ""
    Write-Host "  Quick start:" -ForegroundColor White
    Write-Host "    algochains-mcp --help" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Configure your IDE (Cursor, Claude Desktop, Windsurf):" -ForegroundColor White
    Write-Host "    Add to MCP config: { `"algochains`": { `"command`": `"algochains-mcp`" } }" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Demo mode (no credentials needed):" -ForegroundColor White
    Write-Host "    algochains-mcp  # then ask Claude: discover tools for backtesting" -ForegroundColor Cyan
    Write-Host ""
}

# ── Verify install ────────────────────────────────────────────────────────────
Write-Step "Verifying install..."
try {
    $ver = & algochains-mcp --version 2>&1
    Write-OK "algochains-mcp $ver is ready"
} catch {
    Write-Warn "Could not verify algochains-mcp version — PATH may need a restart."
    Write-Warn "Run 'algochains-mcp --version' in a new PowerShell window to confirm."
}
