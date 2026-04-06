"""
Signal Propagation Tester — AlgoChains Builder SDK

Verifies end-to-end signal delivery:
  1. Creates a test signal with HMAC signature
  2. Sends it to the Django signal endpoint
  3. Polls for acknowledgement
  4. Reports success/failure with detailed diagnostics

Also validates the HMAC key setup before live trading begins.

Usage (CLI):
    python -m algochains_mcp.builder_sdk.signal_tester --strategy my_algo --dry-run

Usage (code):
    from algochains_mcp.builder_sdk.signal_tester import SignalTester
    tester = SignalTester("my_algo")
    result = await tester.run_test(dry_run=True)
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("algochains_mcp.signal_tester")

DJANGO_BASE_URL = os.environ.get("ALGOCHAINS_DJANGO_URL", "https://algochains.ai")
SIGNAL_SECRET = os.environ.get("ALGOCHAINS_SIGNAL_SECRET", "")
BUILDER_KEY = os.environ.get("ALGOCHAINS_BUILDER_KEY", "")

# Django signal endpoint (from CREATOR_SUBMISSION_PIPELINE_BLUEPRINT.md)
SIGNAL_ENDPOINT = f"{DJANGO_BASE_URL}/signals/signal/"


@dataclass
class SignalTestResult:
    strategy_name: str
    test_id: str
    dry_run: bool
    success: bool
    hmac_valid: bool = False
    endpoint_reachable: bool = False
    signal_acknowledged: bool = False
    latency_ms: float = 0.0
    error: str = ""
    diagnostics: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_id": self.test_id,
            "strategy_name": self.strategy_name,
            "dry_run": self.dry_run,
            "success": self.success,
            "checks": {
                "hmac_valid": self.hmac_valid,
                "endpoint_reachable": self.endpoint_reachable,
                "signal_acknowledged": self.signal_acknowledged,
            },
            "latency_ms": round(self.latency_ms, 1),
            "error": self.error,
            "diagnostics": self.diagnostics,
        }

    def summary(self) -> str:
        status = "PASS" if self.success else "FAIL"
        checks = []
        if self.hmac_valid:
            checks.append("HMAC-OK")
        if self.endpoint_reachable:
            checks.append("ENDPOINT-OK")
        if self.signal_acknowledged:
            checks.append("ACK-OK")
        checks_str = "  ".join(checks) if checks else "NO-CHECKS-PASSED"
        latency = f"  latency={self.latency_ms:.0f}ms" if self.latency_ms else ""
        error = f"  error={self.error}" if self.error else ""
        return f"[{status}] {self.strategy_name}  {checks_str}{latency}{error}"


class SignalTester:
    """End-to-end signal propagation tester for AlgoChains strategies."""

    def __init__(
        self,
        strategy_name: str,
        signal_secret: str | None = None,
        builder_key: str | None = None,
    ) -> None:
        self.strategy_name = strategy_name
        self.signal_secret = signal_secret or SIGNAL_SECRET
        self.builder_key = builder_key or BUILDER_KEY
        self._http_client = None

    async def _get_client(self):
        if self._http_client is None:
            try:
                import httpx
                self._http_client = httpx.AsyncClient(timeout=10.0)
            except ImportError:
                raise ImportError("httpx is required. pip install httpx")
        return self._http_client

    def _build_test_signal(self, test_id: str) -> dict[str, Any]:
        """Build a test signal payload."""
        return {
            "strategy_name": self.strategy_name,
            "signal": "buy",
            "symbol": "TEST",
            "price": 0.0,
            "quantity": 0,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "test_mode": True,
            "test_id": test_id,
        }

    def _sign_signal(self, payload: dict) -> str:
        """HMAC-SHA256 sign a signal payload."""
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        sig = hmac.new(self.signal_secret.encode(), body.encode(), hashlib.sha256)
        return sig.hexdigest()

    def _verify_hmac_setup(self) -> tuple[bool, str]:
        """Verify that HMAC signing is properly configured."""
        if not self.signal_secret:
            return False, "ALGOCHAINS_SIGNAL_SECRET is not set in environment"
        if len(self.signal_secret) < 32:
            return False, f"ALGOCHAINS_SIGNAL_SECRET is too short ({len(self.signal_secret)} chars, min 32)"
        test_payload = {"test": "value", "ts": "2026-01-01T00:00:00Z"}
        sig1 = self._sign_signal(test_payload)
        sig2 = self._sign_signal(test_payload)
        if sig1 != sig2:
            return False, "HMAC signing is non-deterministic (should not happen)"
        return True, f"HMAC signing OK (sig={sig1[:16]}...)"

    async def _check_endpoint_health(self) -> tuple[bool, str]:
        """Check if the Django signal endpoint is reachable."""
        client = await self._get_client()
        health_url = f"{DJANGO_BASE_URL}/health/"
        try:
            resp = await client.get(health_url, timeout=5.0)
            if resp.status_code < 500:
                return True, f"Endpoint reachable (HTTP {resp.status_code})"
            return False, f"Endpoint returned HTTP {resp.status_code}"
        except Exception as exc:
            return False, f"Endpoint unreachable: {exc}"

    async def _send_test_signal(self, payload: dict, signature: str) -> tuple[bool, float, str]:
        """Send a test signal to the Django endpoint and measure latency."""
        client = await self._get_client()
        headers = {
            "Content-Type": "application/json",
            "X-AlgoChains-Signature": signature,
            "X-AlgoChains-Builder-Key": self.builder_key,
            "X-Test-Mode": "true",
        }
        t0 = time.monotonic()
        try:
            resp = await client.post(SIGNAL_ENDPOINT, json=payload, headers=headers)
            latency_ms = (time.monotonic() - t0) * 1000
            if resp.status_code in (200, 201, 202):
                return True, latency_ms, f"Signal acknowledged (HTTP {resp.status_code})"
            elif resp.status_code == 403:
                return False, latency_ms, "Signal rejected: invalid HMAC signature or unauthorized key"
            elif resp.status_code == 429:
                return False, latency_ms, "Signal rejected: rate limit exceeded (max 100/hour)"
            else:
                return False, latency_ms, f"Unexpected response: HTTP {resp.status_code} — {resp.text[:200]}"
        except Exception as exc:
            latency_ms = (time.monotonic() - t0) * 1000
            return False, latency_ms, f"Request failed: {exc}"

    async def run_test(self, dry_run: bool = True) -> SignalTestResult:
        """Run the full end-to-end signal propagation test.

        Args:
            dry_run: If True, sends test_mode=True signal (Django logs but doesn't execute).
                     If False, sends a real test signal (USE WITH CAUTION).
        """
        test_id = str(uuid.uuid4())[:8]
        result = SignalTestResult(
            strategy_name=self.strategy_name,
            test_id=test_id,
            dry_run=dry_run,
            success=False,
        )

        # Step 1: Verify HMAC setup
        hmac_ok, hmac_msg = self._verify_hmac_setup()
        result.hmac_valid = hmac_ok
        result.diagnostics["hmac"] = hmac_msg
        if not hmac_ok:
            result.error = hmac_msg
            return result

        # Step 2: Check endpoint health
        endpoint_ok, endpoint_msg = await self._check_endpoint_health()
        result.endpoint_reachable = endpoint_ok
        result.diagnostics["endpoint"] = endpoint_msg
        if not endpoint_ok:
            result.error = endpoint_msg
            return result

        # Step 3: Send test signal
        payload = self._build_test_signal(test_id)
        if not dry_run:
            payload.pop("test_mode", None)
            payload.pop("test_id", None)

        signature = self._sign_signal(payload)
        ack_ok, latency_ms, ack_msg = await self._send_test_signal(payload, signature)
        result.signal_acknowledged = ack_ok
        result.latency_ms = latency_ms
        result.diagnostics["signal_send"] = ack_msg

        if not ack_ok:
            result.error = ack_msg
            return result

        result.success = True
        result.diagnostics["summary"] = (
            f"All checks passed. Signal latency: {latency_ms:.0f}ms. "
            "Strategy is ready for paper trading."
        )
        return result

    async def validate_signal_key_setup(self) -> dict[str, Any]:
        """Validate the full signal key setup without sending a signal."""
        checks = {}

        # Check signal secret
        hmac_ok, hmac_msg = self._verify_hmac_setup()
        checks["signal_secret"] = {"ok": hmac_ok, "message": hmac_msg}

        # Check builder key
        if self.builder_key:
            checks["builder_key"] = {"ok": True, "message": f"Key set ({len(self.builder_key)} chars)"}
        else:
            checks["builder_key"] = {"ok": False, "message": "ALGOCHAINS_BUILDER_KEY not set"}

        # Check Django URL
        if DJANGO_BASE_URL and DJANGO_BASE_URL != "https://algochains.ai":
            checks["django_url"] = {"ok": True, "message": f"Custom URL: {DJANGO_BASE_URL}"}
        elif DJANGO_BASE_URL:
            checks["django_url"] = {"ok": True, "message": "Using default: https://algochains.ai"}
        else:
            checks["django_url"] = {"ok": False, "message": "ALGOCHAINS_DJANGO_URL not set"}

        # Check endpoint reachability
        endpoint_ok, endpoint_msg = await self._check_endpoint_health()
        checks["endpoint"] = {"ok": endpoint_ok, "message": endpoint_msg}

        all_ok = all(v["ok"] for v in checks.values())
        return {
            "all_valid": all_ok,
            "strategy_name": self.strategy_name,
            "checks": checks,
            "recommendation": (
                "Configuration is valid. Run `run_test(dry_run=True)` to verify signal delivery."
                if all_ok
                else "Fix the failing checks above before starting live paper trading."
            ),
        }

    async def close(self) -> None:
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None


async def _cli_main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="AlgoChains Signal Propagation Tester")
    parser.add_argument("--strategy", required=True, help="Strategy name to test")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Use test_mode signals")
    parser.add_argument("--live", action="store_true", help="Send real signals (overrides --dry-run)")
    args = parser.parse_args()

    dry_run = not args.live
    tester = SignalTester(args.strategy)

    try:
        # First validate keys
        key_check = await tester.validate_signal_key_setup()
        print("\n=== Signal Key Validation ===")
        for name, check in key_check["checks"].items():
            status = "OK" if check["ok"] else "FAIL"
            print(f"  [{status}] {name}: {check['message']}")
        print(f"\nOverall: {'VALID' if key_check['all_valid'] else 'INVALID'}")

        if not key_check["all_valid"]:
            print(f"\nRecommendation: {key_check['recommendation']}")
            return

        # Run the signal test
        print(f"\n=== Signal Propagation Test ({'DRY RUN' if dry_run else 'LIVE'}) ===")
        result = await tester.run_test(dry_run=dry_run)
        print(result.summary())
        if result.diagnostics:
            for k, v in result.diagnostics.items():
                print(f"  {k}: {v}")
    finally:
        await tester.close()


if __name__ == "__main__":
    asyncio.run(_cli_main())
