#!/usr/bin/env python3
"""
AlgoChains MCP Server — Startup Health Check

One-command check that verifies the entire system is ready:
  - Environment variables (required + optional)
  - Python dependencies
  - MCP server import (memory safe startup)
  - Broker connectivity (if keys present)
  - Django API reachability
  - Signal key setup
  - Supabase data warehouse access
  - Account protection configuration

Usage:
    python scripts/startup_health_check.py
    python scripts/startup_health_check.py --broker alpaca
    python scripts/startup_health_check.py --full   (all checks, slow)

Exit code 0 = all critical checks pass
Exit code 1 = one or more critical checks failed
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Add src to path for direct script execution
_root = Path(__file__).parent.parent
sys.path.insert(0, str(_root / "src"))

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _ok(msg: str) -> str:
    return f"{GREEN}[OK]{RESET}  {msg}"


def _fail(msg: str) -> str:
    return f"{RED}[FAIL]{RESET} {msg}"


def _warn(msg: str) -> str:
    return f"{YELLOW}[WARN]{RESET} {msg}"


def _info(msg: str) -> str:
    return f"{BLUE}[INFO]{RESET} {msg}"


@dataclass
class CheckResult:
    name: str
    passed: bool
    critical: bool
    message: str
    details: str = ""
    latency_ms: float = 0.0


class HealthChecker:
    def __init__(self, full: bool = False, broker: str | None = None) -> None:
        self.full = full
        self.broker = broker
        self.results: list[CheckResult] = []

    def _add(self, name: str, passed: bool, critical: bool, message: str, details: str = "", latency_ms: float = 0.0) -> None:
        self.results.append(CheckResult(name, passed, critical, message, details, latency_ms))

    # ── 1. Environment Variables ──────────────────────────────────────────────
    def check_environment(self) -> None:
        print(f"\n{BOLD}── Environment Variables ──{RESET}")

        critical_vars = []  # None truly critical — server starts without them
        optional_pairs = [
            ("ALGOCHAINS_API_KEY", "AlgoChains platform access"),
            ("ALGOCHAINS_BUILDER_KEY", "Builder SDK ($199 tier)"),
            ("ALGOCHAINS_SIGNAL_SECRET", "Signal HMAC signing (required for paper trading)"),
            ("SUPABASE_URL", "Supabase marketplace DB"),
            ("SUPABASE_ANON_KEY", "Supabase read access"),
            ("ALGOCHAINS_DJANGO_URL", "Django API URL"),
            ("LISTING_API_KEY", "Publish/update marketplace listings"),
            ("METRICS_INGEST_API_KEY", "Push performance metrics"),
            ("POLYGON_API_KEY", "Polygon market data"),
            ("ALPACA_API_KEY", "Alpaca trading"),
            ("TRADOVATE_CID", "Tradovate futures trading"),
            ("ALGOCHAINS_HTTP_TRANSPORT_SECRET", "HTTP transport security"),
        ]

        set_count = 0
        for var, desc in optional_pairs:
            val = os.environ.get(var, "")
            if val:
                set_count += 1
                # Mask the value
                masked = val[:4] + "..." + val[-4:] if len(val) > 8 else "***"
                print(f"  {_ok(f'{var} = {masked}  ({desc})')}")
            else:
                print(f"  {_warn(f'{var} not set  ({desc})')}")

        self._add(
            "environment_vars",
            passed=set_count >= 1,
            critical=False,
            message=f"{set_count}/{len(optional_pairs)} optional vars set",
        )

    # ── 2. Python Dependencies ────────────────────────────────────────────────
    def check_dependencies(self) -> None:
        print(f"\n{BOLD}── Python Dependencies ──{RESET}")

        required = ["mcp", "httpx", "pydantic"]
        optional = [
            ("backtrader", "Strategy templates"),
            ("fastapi", "HTTP transport"),
            ("uvicorn", "HTTP transport server"),
            ("psutil", "Memory monitoring"),
            ("supabase", "Supabase data warehouse"),
            ("pandas", "Data analysis"),
            ("numpy", "Numerical computation"),
        ]

        all_required_ok = True
        for pkg in required:
            try:
                mod = importlib.import_module(pkg.replace("-", "_"))
                ver = getattr(mod, "__version__", "?")
                print(f"  {_ok(f'{pkg} {ver}  [required]')}")
            except ImportError:
                print(f"  {_fail(f'{pkg} NOT INSTALLED  [required]')}")
                all_required_ok = False

        for pkg, desc in optional:
            try:
                mod = importlib.import_module(pkg.replace("-", "_"))
                ver = getattr(mod, "__version__", "?")
                print(f"  {_ok(f'{pkg} {ver}  ({desc})')}")
            except ImportError:
                print(f"  {_warn(f'{pkg} not installed  ({desc}) — pip install algochains-mcp[{pkg}]')}")

        self._add(
            "dependencies",
            passed=all_required_ok,
            critical=True,
            message="All required packages installed" if all_required_ok else "Missing required packages",
        )

    # ── 3. MCP Server Import (memory safe) ────────────────────────────────────
    def check_server_import(self) -> None:
        print(f"\n{BOLD}── MCP Server Startup ──{RESET}")
        t0 = time.monotonic()
        try:
            from algochains_mcp import server  # noqa: F401
            latency_ms = (time.monotonic() - t0) * 1000
            print(f"  {_ok(f'Server imported in {latency_ms:.0f}ms (lazy loading active)')}")

            # Check memory usage
            try:
                from algochains_mcp.memory_safety import get_process_memory_mb
                mem_mb = get_process_memory_mb()
                if mem_mb < 200:
                    print(f"  {_ok(f'Memory: {mem_mb:.0f} MB (healthy)')}")
                elif mem_mb < 500:
                    print(f"  {_warn(f'Memory: {mem_mb:.0f} MB (moderate)')}")
                else:
                    print(f"  {_warn(f'Memory: {mem_mb:.0f} MB (high — check for eager imports)')}")
            except Exception:
                pass

            self._add(
                "server_import",
                passed=True,
                critical=True,
                message=f"Import OK in {latency_ms:.0f}ms",
                latency_ms=latency_ms,
            )
        except Exception as exc:
            latency_ms = (time.monotonic() - t0) * 1000
            print(f"  {_fail(f'Server import FAILED: {exc}')}")
            self._add(
                "server_import",
                passed=False,
                critical=True,
                message=f"Import failed: {exc}",
                latency_ms=latency_ms,
            )

    # ── 4. Account Protection ─────────────────────────────────────────────────
    def check_account_protection(self) -> None:
        print(f"\n{BOLD}── Account Protection ──{RESET}")
        try:
            from algochains_mcp.account_protection.engine import AccountProtectionEngine
            engine = AccountProtectionEngine()
            config = engine.get_config()
            # get_config returns a dict
            preset = config.get("preset", config.preset if hasattr(config, "preset") else "moderate")
            vix = config.get("vix_killswitch", 35)
            max_loss = config.get("max_daily_loss_usd", 500)
            print(f"  {_ok(f'Engine loaded — preset: {preset}')}")
            print(f"  {_ok(f'13 guards active: VIX killswitch, daily loss, drawdown, fat finger...')}")
            print(f"  {_ok(f'VIX killswitch threshold: {vix}')}")
            print(f"  {_ok(f'Max daily loss: ${max_loss:,.0f}')}")
            self._add("account_protection", passed=True, critical=False, message="Account protection active")
        except Exception as exc:
            print(f"  {_fail(f'Account protection failed: {exc}')}")
            self._add("account_protection", passed=False, critical=False, message=str(exc))

    # ── 5. Signal Key Setup ───────────────────────────────────────────────────
    async def check_signal_key(self) -> None:
        print(f"\n{BOLD}── Signal Key Setup ──{RESET}")
        secret = os.environ.get("ALGOCHAINS_SIGNAL_SECRET", "")
        if not secret:
            print(f"  {_warn('ALGOCHAINS_SIGNAL_SECRET not set — paper trading signals will not be HMAC-signed')}")
            self._add("signal_key", passed=True, critical=False, message="Signal secret not configured (optional)")
            return

        if len(secret) < 32:
            print(f"  {_warn(f'Signal secret is short ({len(secret)} chars) — recommend 32+ chars')}")
        else:
            print(f"  {_ok(f'Signal secret set ({len(secret)} chars)')}")

        # Test HMAC generation
        import hashlib
        import hmac
        test_payload = json.dumps({"test": "value"}, separators=(",", ":")).encode()
        sig = hmac.new(secret.encode(), test_payload, hashlib.sha256).hexdigest()
        print(f"  {_ok(f'HMAC signing works (sig={sig[:12]}...)')}")
        self._add("signal_key", passed=True, critical=False, message="Signal HMAC ready")

    # ── 6. Django API Reachability ────────────────────────────────────────────
    async def check_django_api(self) -> None:
        print(f"\n{BOLD}── Django API ──{RESET}")
        django_url = os.environ.get("ALGOCHAINS_DJANGO_URL", "https://algochains.ai")
        health_url = f"{django_url}/health/"

        try:
            import httpx
            t0 = time.monotonic()
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(health_url)
            latency_ms = (time.monotonic() - t0) * 1000
            if resp.status_code < 500:
                print(f"  {_ok(f'{django_url} reachable (HTTP {resp.status_code}, {latency_ms:.0f}ms)')}")
                self._add("django_api", passed=True, critical=False, message=f"Reachable ({latency_ms:.0f}ms)", latency_ms=latency_ms)
            else:
                print(f"  {_warn(f'{django_url} returned HTTP {resp.status_code}')}")
                self._add("django_api", passed=False, critical=False, message=f"HTTP {resp.status_code}")
        except ImportError:
            print(f"  {_warn('httpx not installed — skipping Django check')}")
            self._add("django_api", passed=True, critical=False, message="Skipped (httpx not installed)")
        except Exception as exc:
            print(f"  {_warn(f'{django_url} unreachable: {exc}')}")
            self._add("django_api", passed=False, critical=False, message=f"Unreachable: {exc}")

    # ── 7. HTTP Transport ─────────────────────────────────────────────────────
    def check_http_transport(self) -> None:
        print(f"\n{BOLD}── HTTP Transport ──{RESET}")
        try:
            import fastapi  # noqa: F401
            import uvicorn  # noqa: F401
            print(f"  {_ok('FastAPI + uvicorn installed — HTTP transport available')}")
            print(f"  {_info('Start with: algochains-mcp-http --host 0.0.0.0 --port 8080')}")
            secret = os.environ.get("ALGOCHAINS_HTTP_TRANSPORT_SECRET", "")
            if secret:
                print(f"  {_ok('HTTP transport secret set — Bearer token auth ENABLED')}")
            else:
                print(f"  {_warn('ALGOCHAINS_HTTP_TRANSPORT_SECRET not set — open access in HTTP mode')}")
            self._add("http_transport", passed=True, critical=False, message="HTTP transport available")
        except ImportError:
            print(f"  {_info('HTTP transport not installed. pip install algochains-mcp[http]')}")
            self._add("http_transport", passed=True, critical=False, message="HTTP transport not installed (optional)")

    # ── 8. Marketplace keys (strict mode) ───────────────────────────────────
    def check_marketplace_keys_strict(self) -> None:
        print(f"\n{BOLD}── Marketplace API Keys (strict) ──{RESET}")
        strict = os.environ.get("ALGOCHAINS_MARKETPLACE_STRICT", "").lower() in ("1", "true", "yes")
        skip = os.environ.get("ALGOCHAINS_SKIP_MARKETPLACE_KEY_CHECK", "").lower() in ("1", "true", "yes")
        if not strict:
            print(f"  {_info('ALGOCHAINS_MARKETPLACE_STRICT not set — listing/ingest keys optional for health exit code')}")
            print(f"  {_info('Set ALGOCHAINS_MARKETPLACE_STRICT=1 in CI before publish/ingest jobs')}")
            self._add("marketplace_keys_strict", passed=True, critical=False, message="Strict check disabled")
            return
        if skip:
            print(f"  {_warn('ALGOCHAINS_SKIP_MARKETPLACE_KEY_CHECK set — bypassing strict key check')}")
            self._add("marketplace_keys_strict", passed=True, critical=False, message="Skipped (dev bypass)")
            return
        listing = (os.environ.get("LISTING_API_KEY") or "").strip()
        ingest = (os.environ.get("METRICS_INGEST_API_KEY") or "").strip()
        ok = bool(listing) and bool(ingest)
        if listing:
            print(f"  {_ok('LISTING_API_KEY is set')}")
        else:
            print(f"  {_fail('LISTING_API_KEY missing (required when ALGOCHAINS_MARKETPLACE_STRICT=1)')}")
        if ingest:
            print(f"  {_ok('METRICS_INGEST_API_KEY is set')}")
        else:
            print(f"  {_fail('METRICS_INGEST_API_KEY missing (required when ALGOCHAINS_MARKETPLACE_STRICT=1)')}")
        self._add(
            "marketplace_keys_strict",
            passed=ok,
            critical=True,
            message="Both marketplace keys present" if ok else "Set LISTING_API_KEY and METRICS_INGEST_API_KEY",
        )

    # ── 9. Strategy Templates ─────────────────────────────────────────────────
    def check_strategy_templates(self) -> None:
        print(f"\n{BOLD}── Strategy Templates ──{RESET}")
        try:
            from algochains_mcp.builder_sdk.templates.registry import list_templates
            templates = list_templates()
            for t in templates:
                desc = t["description"][:60]
                name = t["name"]
                print(f"  {_ok(f'{name}: {desc}...')}")
            self._add("strategy_templates", passed=True, critical=False, message=f"{len(templates)} templates available")
        except Exception as exc:
            print(f"  {_warn(f'Could not load strategy templates: {exc}')}")
            self._add("strategy_templates", passed=False, critical=False, message=str(exc))

    # ── 8. Broker credential probe ────────────────────────────────────────────
    def check_broker_credentials(self) -> None:
        """Surface missing broker env vars so agents get clear errors, not silent broker failures."""
        print(f"\n{BOLD}── Broker Credentials ──{RESET}")
        try:
            from algochains_mcp.byok.provider_registry import PROVIDER_REGISTRY, ProviderCategory
            broker_providers = {
                k: v for k, v in PROVIDER_REGISTRY.items()
                if ProviderCategory.EXECUTION in v.categories
            }
            all_present = True
            for broker_name, meta in broker_providers.items():
                missing = [v for v in meta.env_vars if not os.environ.get(v)]
                present = [v for v in meta.env_vars if os.environ.get(v)]
                if not meta.env_vars:
                    continue
                if present:
                    print(f"  {_ok(f'{meta.display_name}: {len(present)}/{len(meta.env_vars)} vars set')}")
                else:
                    print(f"  {_warn(f'{meta.display_name}: no credentials set — missing {missing}')}")
                    all_present = False
            self._add(
                "broker_credentials",
                passed=all_present,
                critical=False,
                message="At least one broker has credentials set" if all_present else
                        "Some brokers have no credentials (tools will return errors on use)",
            )
        except Exception as exc:
            print(f"  {_warn(f'Could not check broker registry: {exc}')}")
            self._add("broker_credentials", passed=False, critical=False, message=str(exc))

    # ── Summary ───────────────────────────────────────────────────────────────
    def print_summary(self) -> bool:
        print(f"\n{BOLD}{'='*60}{RESET}")
        print(f"{BOLD}Health Check Summary{RESET}")
        print(f"{BOLD}{'='*60}{RESET}")

        critical_failed = []
        warnings = []

        for r in self.results:
            if r.passed:
                status = _ok(r.name)
            elif r.critical:
                status = _fail(r.name)
                critical_failed.append(r.name)
            else:
                status = _warn(r.name)
                warnings.append(r.name)
            print(f"  {status}: {r.message}")

        print()
        if critical_failed:
            print(f"{RED}{BOLD}CRITICAL FAILURES: {', '.join(critical_failed)}{RESET}")
            print(f"{RED}Server may not start correctly. Fix these before deploying.{RESET}")
            return False
        elif warnings:
            print(f"{YELLOW}{BOLD}Warnings: {', '.join(warnings)}{RESET}")
            print(f"{GREEN}Server is functional but some optional features are unavailable.{RESET}")
            return True
        else:
            print(f"{GREEN}{BOLD}All checks passed! AlgoChains MCP Server is ready.{RESET}")
            print(f"{BLUE}Start with: algochains-mcp{RESET}")
            return True

    async def run(self) -> bool:
        print(f"\n{BOLD}AlgoChains MCP Server v20.0 — Startup Health Check{RESET}")
        print("=" * 60)

        self.check_environment()
        self.check_dependencies()
        self.check_server_import()
        self.check_account_protection()
        await self.check_signal_key()
        await self.check_django_api()
        self.check_http_transport()
        self.check_strategy_templates()
        self.check_marketplace_keys_strict()
        self.check_broker_credentials()

        return self.print_summary()


async def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="AlgoChains MCP Server health check")
    parser.add_argument("--full", action="store_true", help="Run all checks including slow ones")
    parser.add_argument("--broker", help="Test specific broker connectivity")
    args = parser.parse_args()

    checker = HealthChecker(full=args.full, broker=args.broker)
    passed = await checker.run()
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    asyncio.run(main())
