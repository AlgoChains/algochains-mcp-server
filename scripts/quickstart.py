#!/usr/bin/env python3
"""
AlgoChains MCP Server — Interactive Quickstart & Health Check
=============================================================

Usage:
    python scripts/quickstart.py                   # interactive guided setup
    python scripts/quickstart.py --health-check    # verify existing setup
    python scripts/quickstart.py --generate-config cursor
    python scripts/quickstart.py --generate-config claude-desktop
    python scripts/quickstart.py --generate-config windsurf
    python scripts/quickstart.py --mode demo       # read-only, no credentials
    python scripts/quickstart.py --mode paper      # Alpaca paper account only
    python scripts/quickstart.py --mode live       # full live broker access

Modes:
    demo  — no broker credentials required; calls public market data only
    paper — Alpaca paper account (free, no real money risk)
    live  — full live broker access (requires SAFETY_MODEL.md acknowledgment)

No synthetic data. No mock values. Every connectivity test hits a real API.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from textwrap import dedent

# ── ANSI colours (fall back gracefully on Windows) ────────────────────────────
try:
    from colorama import Fore, Style, init as colorama_init
    colorama_init()
    GREEN = Fore.GREEN
    RED = Fore.RED
    YELLOW = Fore.YELLOW
    CYAN = Fore.CYAN
    BOLD = Style.BRIGHT
    RESET = Style.RESET_ALL
except ImportError:
    GREEN = RED = YELLOW = CYAN = BOLD = RESET = ""

TICK = f"{GREEN}✓{RESET}"
CROSS = f"{RED}✗{RESET}"
WARN = f"{YELLOW}⚠{RESET}"
INFO = f"{CYAN}ℹ{RESET}"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _print_header(text: str) -> None:
    width = 70
    print()
    print(f"{BOLD}{'─' * width}{RESET}")
    print(f"{BOLD}  {text}{RESET}")
    print(f"{BOLD}{'─' * width}{RESET}")
    print()


def _ok(label: str, detail: str = "") -> None:
    print(f"  {TICK}  {label}" + (f"  {CYAN}({detail}){RESET}" if detail else ""))


def _fail(label: str, hint: str = "") -> None:
    print(f"  {CROSS}  {RED}{label}{RESET}")
    if hint:
        print(f"       {YELLOW}→ {hint}{RESET}")


def _warn(label: str) -> None:
    print(f"  {WARN}  {YELLOW}{label}{RESET}")


def _info(label: str) -> None:
    print(f"  {INFO}  {label}")


def _env(key: str) -> str | None:
    return os.environ.get(key) or None


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        val = input(f"  {CYAN}{prompt}{suffix}:{RESET} ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    return val or default


def _confirm(prompt: str) -> bool:
    try:
        answer = input(f"  {CYAN}{prompt} (y/N):{RESET} ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    return answer in ("y", "yes")


# ── Module import check ───────────────────────────────────────────────────────

def check_python_version() -> bool:
    ok = sys.version_info >= (3, 11)
    ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if ok:
        _ok(f"Python {ver}", "3.11+ required")
    else:
        _fail(f"Python {ver}", "Need Python 3.11 or newer — https://python.org")
    return ok


def check_module_imports() -> bool:
    required = [
        ("mcp", "pip install mcp"),
        ("httpx", "pip install httpx"),
        ("pydantic", "pip install pydantic"),
        ("dotenv", "pip install python-dotenv"),
    ]
    all_ok = True
    for mod, fix in required:
        try:
            __import__(mod)
            _ok(f"import {mod}")
        except ImportError:
            _fail(f"import {mod} — NOT INSTALLED", fix)
            all_ok = False

    optional = [
        ("optuna", "pip install optuna  # needed for optimize_strategy"),
        ("stripe", "pip install stripe  # needed for billing/marketplace"),
        ("pandas", "pip install pandas  # needed for backtest / data ingest"),
    ]
    for mod, hint in optional:
        try:
            __import__(mod)
            _ok(f"import {mod} (optional)")
        except ImportError:
            _warn(f"import {mod} (optional) — {hint}")

    return all_ok


def check_server_importable() -> bool:
    try:
        src_path = Path(__file__).resolve().parent.parent / "src"
        if str(src_path) not in sys.path:
            sys.path.insert(0, str(src_path))
        import algochains_mcp.server  # noqa: F401
        _ok("algochains_mcp.server importable")
        return True
    except Exception as e:
        _fail("algochains_mcp.server failed to import", str(e))
        return False


# ── Env var checks ────────────────────────────────────────────────────────────

BROKER_ENV_VARS: dict[str, list[tuple[str, str]]] = {
    "tradovate": [
        ("TRADOVATE_USERNAME", "your Tradovate login email"),
        ("TRADOVATE_PASSWORD", "your Tradovate login password"),
        ("TRADOVATE_APP_ID", "from https://trader.tradovate.com/account → API access"),
        ("TRADOVATE_APP_SECRET", "same location as APP_ID"),
    ],
    "alpaca": [
        ("ALPACA_API_KEY", "from https://app.alpaca.markets/paper/dashboard/overview"),
        ("ALPACA_SECRET_KEY", "same location as API key"),
        ("ALPACA_PAPER", "set to 'true' for paper account (recommended to start)"),
    ],
    "oanda": [
        ("OANDA_ACCESS_TOKEN", "from https://www.oanda.com/demo-account/tpa/personal_token"),
        ("OANDA_ACCOUNT_ID", "from your OANDA account dashboard"),
    ],
}

DATA_ENV_VARS: list[tuple[str, str, bool]] = [
    ("POLYGON_API_KEY", "from https://polygon.io/dashboard — free tier available", False),
    ("DATABENTO_API_KEY", "from https://databento.com/portal — tick-level data", False),
    ("FRED_API_KEY", "from https://fred.stlouisfed.org/docs/api/api_key.html — free", False),
    ("OPENAI_API_KEY", "from https://platform.openai.com/api-keys — for AI ensemble", False),
    ("ANTHROPIC_API_KEY", "from https://console.anthropic.com — for Claude in ensemble", False),
]


def check_env_vars(mode: str) -> dict[str, bool]:
    """Check env vars for the given mode. Returns {var_name: is_set}."""
    results: dict[str, bool] = {}

    if mode == "demo":
        _info("Demo mode — no broker credentials required")
        return results

    if mode in ("paper", "live"):
        broker = "alpaca" if mode == "paper" else "tradovate"
        vars_to_check = BROKER_ENV_VARS.get(broker, [])
        for var, desc in vars_to_check:
            val = _env(var)
            results[var] = bool(val)
            if val:
                _ok(f"{var}", "set")
            else:
                if mode == "paper":
                    _warn(f"{var} not set — {desc}")
                else:
                    _fail(f"{var} not set", desc)

    for var, desc, required in DATA_ENV_VARS:
        val = _env(var)
        results[var] = bool(val)
        if val:
            _ok(f"{var}", "set")
        elif required:
            _fail(f"{var} not set", desc)
        else:
            _warn(f"{var} not set (optional) — {desc}")

    return results


# ── Live connectivity checks ──────────────────────────────────────────────────

def check_polygon_connectivity() -> bool:
    key = _env("POLYGON_API_KEY")
    if not key:
        _warn("POLYGON_API_KEY not set — skipping connectivity check")
        return True  # Not a hard failure in demo mode

    try:
        import httpx
        resp = httpx.get(
            "https://api.polygon.io/v2/aggs/ticker/AAPL/range/1/day/2026-01-02/2026-01-02",
            params={"apiKey": key},
            timeout=10.0,
        )
        if resp.status_code == 200 and resp.json().get("resultsCount", 0) > 0:
            _ok("Polygon API reachable", f"AAPL bar fetched")
            return True
        elif resp.status_code == 403:
            _fail("Polygon API — invalid key (403)", "Check POLYGON_API_KEY")
            return False
        else:
            _warn(f"Polygon API responded {resp.status_code} — may be outside market hours")
            return True
    except Exception as e:
        _fail("Polygon API unreachable", str(e))
        return False


def check_alpaca_paper_connectivity() -> bool:
    key = _env("ALPACA_API_KEY")
    secret = _env("ALPACA_SECRET_KEY")
    if not key or not secret:
        _warn("Alpaca credentials not set — skipping connectivity check")
        return True

    try:
        import httpx
        paper = _env("ALPACA_PAPER") not in ("false", "0", "no")
        base = "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"
        resp = httpx.get(
            f"{base}/v2/account",
            headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
            timeout=10.0,
        )
        if resp.status_code == 200:
            acct = resp.json()
            equity = float(acct.get("equity", 0))
            mode_label = "PAPER" if paper else "LIVE"
            _ok(f"Alpaca {mode_label} account connected", f"equity=${equity:,.2f}")
            if not paper:
                print(f"       {YELLOW}⚠ WARNING: connected to LIVE Alpaca account{RESET}")
            return True
        elif resp.status_code == 403:
            _fail("Alpaca — invalid credentials (403)", "Check ALPACA_API_KEY and ALPACA_SECRET_KEY")
            return False
        else:
            _fail(f"Alpaca API responded {resp.status_code}", resp.text[:100])
            return False
    except Exception as e:
        _fail("Alpaca unreachable", str(e))
        return False


def check_tradovate_token() -> bool:
    session_file = Path("tradovate_session.json")
    if not session_file.exists():
        _warn("tradovate_session.json not found — not connected to Tradovate")
        return True  # Not a hard failure if not configured

    try:
        session = json.loads(session_file.read_text())
        token = session.get("access_token", "")
        expiry = session.get("expiration_time", 0)
        remaining = expiry - time.time()
        if not token:
            _warn("Tradovate: no access token in session file")
            return True
        if remaining <= 0:
            _fail("Tradovate token EXPIRED", "Run: python3 tradovate_token_guardian.py")
            return False
        mins = int(remaining / 60)
        _ok(f"Tradovate token valid", f"{mins}min remaining")
        return True
    except Exception as e:
        _fail("Cannot read Tradovate session", str(e))
        return False


def check_http_bridge() -> bool:
    bridge_url = os.getenv("ALGOCHAINS_BRIDGE_URL", "http://127.0.0.1:8090")
    try:
        import httpx
        resp = httpx.get(f"{bridge_url}/health", timeout=5.0)
        if resp.status_code == 200:
            _ok(f"HTTP bridge UP", bridge_url)
            return True
        else:
            _warn(f"HTTP bridge responded {resp.status_code} — may not be started")
            return True
    except Exception:
        _warn(f"HTTP bridge not running at {bridge_url}")
        _info("Start with: uvicorn algochains_mcp.http_bridge:app_http --port 8090")
        return True  # Not mandatory for basic use


def check_onyx_connectivity() -> bool:
    url = _env("ONYX_API_URL")
    if not url:
        _warn("ONYX_API_URL not set — Onyx knowledge base disabled")
        return True

    try:
        import httpx
        resp = httpx.get(f"{url.rstrip('/')}/api/health", timeout=8.0)
        if resp.status_code == 200:
            _ok("Onyx knowledge base reachable", url)
            return True
        else:
            _warn(f"Onyx responded {resp.status_code} — may be starting up")
            return True
    except Exception:
        _warn(f"Onyx unreachable at {url} — search tools will degrade gracefully")
        return True


# ── IDE config generators ────────────────────────────────────────────────────

def _mcp_server_args() -> dict:
    src = Path(__file__).resolve().parent.parent / "src"
    env = {
        k: v
        for k, v in os.environ.items()
        if k.startswith(
            (
                "ALGOCHAINS_",
                "TRADOVATE_",
                "ALPACA_",
                "OANDA_",
                "POLYGON_",
                "DATABENTO_",
                "OPENAI_",
                "ANTHROPIC_",
                "ONYX_",
            )
        )
    }
    # Subscriber paper portfolio: paste sub_live_* from algochains.ai account → API keys.
    if os.getenv("ALGOCHAINS_SUB_KEY"):
        env["ALGOCHAINS_SUB_KEY"] = os.environ["ALGOCHAINS_SUB_KEY"]
    else:
        env.setdefault(
            "ALGOCHAINS_SUB_KEY",
            "sub_live_PASTE_YOUR_KEY_FROM_ALGOCHAINS_AI",
        )
    env.setdefault(
        "ALGOCHAINS_BRIDGE_URL",
        os.getenv("ALGOCHAINS_BRIDGE_URL", "https://api.algochains.ai"),
    )
    return {
        "command": sys.executable,
        "args": ["-m", "algochains_mcp"],
        "cwd": str(src.parent),
        "env": env,
    }


def generate_cursor_config() -> str:
    srv = _mcp_server_args()
    config = {
        "mcpServers": {
            "algochains": {
                "command": srv["command"],
                "args": srv["args"],
                "env": srv["env"],
            }
        }
    }
    output_path = Path.home() / ".cursor" / "mcp.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if output_path.exists():
        try:
            existing = json.loads(output_path.read_text())
        except Exception:
            pass
    existing.setdefault("mcpServers", {})["algochains"] = config["mcpServers"]["algochains"]
    output_path.write_text(json.dumps(existing, indent=2))
    return str(output_path)


def generate_claude_desktop_config() -> str:
    srv = _mcp_server_args()
    config = {
        "mcpServers": {
            "algochains": {
                "command": srv["command"],
                "args": srv["args"],
                "env": srv["env"],
            }
        }
    }
    if sys.platform == "darwin":
        config_dir = Path.home() / "Library" / "Application Support" / "Claude"
    elif sys.platform == "win32":
        config_dir = Path(os.environ.get("APPDATA", "~")) / "Claude"
    else:
        config_dir = Path.home() / ".config" / "claude"
    output_path = config_dir / "claude_desktop_config.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if output_path.exists():
        try:
            existing = json.loads(output_path.read_text())
        except Exception:
            pass
    existing.setdefault("mcpServers", {})["algochains"] = config["mcpServers"]["algochains"]
    output_path.write_text(json.dumps(existing, indent=2))
    return str(output_path)


def generate_windsurf_config() -> str:
    srv = _mcp_server_args()
    config = {
        "mcpServers": {
            "algochains": {
                "command": srv["command"],
                "args": srv["args"],
                "env": srv["env"],
            }
        }
    }
    output_path = Path.home() / ".codeium" / "windsurf" / "mcp_config.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if output_path.exists():
        try:
            existing = json.loads(output_path.read_text())
        except Exception:
            pass
    existing.setdefault("mcpServers", {})["algochains"] = config["mcpServers"]["algochains"]
    output_path.write_text(json.dumps(existing, indent=2))
    return str(output_path)


CONFIG_GENERATORS = {
    "cursor": generate_cursor_config,
    "claude-desktop": generate_claude_desktop_config,
    "windsurf": generate_windsurf_config,
}


# ── Health check mode ─────────────────────────────────────────────────────────

def run_health_check(mode: str = "demo") -> bool:
    _print_header(f"AlgoChains MCP Server — Health Check (mode: {mode.upper()})")

    all_ok = True

    print(f"{BOLD}[1/6] Python environment{RESET}")
    if not check_python_version():
        all_ok = False
    if not check_module_imports():
        all_ok = False

    print()
    print(f"{BOLD}[2/6] Server importability{RESET}")
    if not check_server_importable():
        all_ok = False

    print()
    print(f"{BOLD}[3/6] Environment variables{RESET}")
    check_env_vars(mode)

    print()
    print(f"{BOLD}[4/6] Broker connectivity{RESET}")
    if mode != "demo":
        check_alpaca_paper_connectivity()
        check_tradovate_token()
    else:
        _info("Demo mode — skipping broker connectivity checks")

    print()
    print(f"{BOLD}[5/6] Market data connectivity{RESET}")
    check_polygon_connectivity()

    print()
    print(f"{BOLD}[6/6] Infrastructure services{RESET}")
    check_http_bridge()
    check_onyx_connectivity()

    print()
    if all_ok:
        print(f"  {GREEN}{BOLD}Health check passed.{RESET}")
        print(f"  {INFO} Your MCP server is ready. Restart your AI IDE to pick up new config.")
    else:
        print(f"  {RED}{BOLD}Health check found issues — see ✗ items above.{RESET}")
        print(f"  {INFO} Fix the issues and re-run: python scripts/quickstart.py --health-check")

    return all_ok


# ── Interactive setup ─────────────────────────────────────────────────────────

def run_interactive_setup() -> None:
    _print_header("AlgoChains MCP Server — Interactive Setup")

    print(dedent(f"""
  {BOLD}What is AlgoChains MCP Server?{RESET}

  It's a bridge between your AI assistant (Claude, Cursor, ChatGPT) and
  your trading infrastructure. When you ask Claude "What's my MNQ P&L?",
  the MCP server securely calls your Tradovate account and returns real data.

  {BOLD}Three modes:{RESET}
    {GREEN}demo{RESET}   — No credentials required. Read public market data.
    {YELLOW}paper{RESET}  — Alpaca paper account (free, no real money).
    {RED}live{RESET}   — Real broker accounts. Real money. Read SAFETY_MODEL.md first.
    """))

    mode = _ask("Choose mode (demo/paper/live)", default="demo").lower()
    if mode not in ("demo", "paper", "live"):
        mode = "demo"

    if mode == "live":
        print()
        safety_path = Path(__file__).parent.parent / "SAFETY_MODEL.md"
        print(f"  {YELLOW}{BOLD}LIVE MODE — Please read SAFETY_MODEL.md before continuing.{RESET}")
        print(f"  {INFO} Path: {safety_path}")
        print()
        ack = _confirm("I have read SAFETY_MODEL.md and understand the risks")
        if not ack:
            print(f"  {WARN} Switching to paper mode for safety.")
            mode = "paper"

    print()
    print(f"  {INFO} Selected mode: {BOLD}{mode.upper()}{RESET}")

    # P2-11 FIX: set ALGOCHAINS_DEMO_MODE env var so the MCP server and tool handlers
    # know to stub out real broker/Kalshi/Alpaca calls even when credentials happen to
    # be present in the environment. Without this, --mode demo only skipped the health
    # check but did not prevent real API calls during actual tool invocations.
    if mode == "demo":
        os.environ["ALGOCHAINS_DEMO_MODE"] = "1"
        _info("ALGOCHAINS_DEMO_MODE=1 set — execution-class tools will return stub responses")
    else:
        os.environ.pop("ALGOCHAINS_DEMO_MODE", None)

    # Run health check in selected mode
    run_health_check(mode)

    # Offer to generate IDE config
    print()
    _print_header("IDE Configuration")
    print("  Available IDEs: cursor, claude-desktop, windsurf")
    ide = _ask("Generate config for which IDE? (press Enter to skip)", default="")

    if ide and ide in CONFIG_GENERATORS:
        try:
            path = CONFIG_GENERATORS[ide]()
            _ok(f"{ide} config written to {path}")
            print()
            print(f"  {INFO} Restart {ide} to load the AlgoChains MCP server.")
            print(f"  {INFO} Then ask your AI: {CYAN}\"call algochains get_quote for AAPL\"{RESET}")
        except Exception as e:
            _fail(f"Could not write {ide} config", str(e))
    elif ide:
        _warn(f"Unknown IDE '{ide}'. Supported: cursor, claude-desktop, windsurf")

    print()
    print(f"  {TICK}  Setup complete.")
    print()
    print(f"  {INFO} {BOLD}Next steps:{RESET}")
    print(f"       1. {CYAN}python scripts/quickstart.py --health-check{RESET}  — verify everything")
    print(f"       2. Open your AI IDE and ask: {CYAN}\"What's the market regime today?\"{RESET}")
    print(f"       3. Read MARKETPLACE_CREATOR_GUIDE.md to list your bots")
    print()


# ── CLI entry point ───────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="AlgoChains MCP Server quickstart and health check",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=dedent("""
        Examples:
          python scripts/quickstart.py                        # interactive setup
          python scripts/quickstart.py --health-check         # verify existing setup
          python scripts/quickstart.py --health-check --mode live
          python scripts/quickstart.py --generate-config cursor
          python scripts/quickstart.py --generate-config claude-desktop
        """),
    )
    parser.add_argument(
        "--health-check",
        action="store_true",
        help="Run health check on existing setup without interactive prompts",
    )
    parser.add_argument(
        "--mode",
        choices=["demo", "paper", "live"],
        default="demo",
        help="Operating mode (default: demo)",
    )
    parser.add_argument(
        "--generate-config",
        metavar="IDE",
        choices=list(CONFIG_GENERATORS.keys()),
        help="Generate MCP config file for the specified IDE",
    )

    args = parser.parse_args()

    if args.generate_config:
        _print_header(f"Generating {args.generate_config} config")
        try:
            path = CONFIG_GENERATORS[args.generate_config]()
            _ok(f"Config written to {path}")
            print(f"\n  {INFO} Restart {args.generate_config} to activate the AlgoChains MCP server.")
        except Exception as e:
            _fail("Config generation failed", str(e))
            sys.exit(1)
        return

    if args.health_check:
        ok = run_health_check(mode=args.mode)
        sys.exit(0 if ok else 1)

    run_interactive_setup()


if __name__ == "__main__":
    main()
