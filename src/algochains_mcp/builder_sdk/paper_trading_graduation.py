"""
Paper Trading Graduation Pipeline — AlgoChains Builder SDK

Monitors 30-day paper trading phases and auto-promotes qualifying strategies
to the marketplace validation queue. Implements decay watchdog for live listings.

Flow:
    Creator runs live_mode() for 30 days
        │
        ▼
    signal_to_api() sends HMAC-signed signals to Django
        │
        ▼
    PaperTradingMonitor polls Django API every 6 hours
        │ checks: signal count, win rate, Sharpe, max DD
        ▼
    If all gates pass after 30 days → submit_to_validation_queue()
        │
        ▼
    AlgoChains team reviews + approves → listing published

Decay Watchdog (for live listings):
    Runs every 24 hours against last 30 days of live signal events.
    If Sharpe drops below 0.5 or DD exceeds 25% → auto-pause.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("algochains_mcp.paper_trading_graduation")

DJANGO_BASE_URL = os.environ.get("ALGOCHAINS_DJANGO_URL", "https://algochains.ai")
METRICS_API_KEY = os.environ.get("METRICS_INGEST_API_KEY", "")
LISTING_API_KEY = os.environ.get("LISTING_API_KEY", "")

# Graduation gates — strategy must pass ALL of these after 30 days
GRADUATION_GATES = {
    "min_signals": 50,           # At least 50 paper trades
    "min_days_active": 25,       # Active for at least 25 of 30 days
    "min_sharpe": 0.8,           # Paper Sharpe >= 0.8 (relaxed vs backtest)
    "max_drawdown_pct": 30.0,    # Max drawdown <= 30%
    "min_win_rate": 35.0,        # Win rate >= 35%
    "max_anomaly_score": 0.3,    # Signal anomaly score (fake metric detector)
}

# Decay gates — live listing auto-paused if these trigger
DECAY_GATES = {
    "min_sharpe_30d": 0.5,
    "max_drawdown_30d_pct": 25.0,
    "max_consecutive_losses": 10,
    "min_signals_14d": 5,        # Must have at least 5 signals in last 14 days
}


@dataclass
class PaperMetrics:
    strategy_name: str
    start_date: datetime
    end_date: datetime
    total_signals: int = 0
    days_active: int = 0
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate: float = 0.0
    anomaly_score: float = 0.0
    raw_data: dict = field(default_factory=dict)

    def passes_graduation(self) -> dict[str, Any]:
        gates = {
            "min_signals": self.total_signals >= GRADUATION_GATES["min_signals"],
            "min_days_active": self.days_active >= GRADUATION_GATES["min_days_active"],
            "min_sharpe": self.sharpe_ratio >= GRADUATION_GATES["min_sharpe"],
            "max_drawdown_pct": self.max_drawdown_pct <= GRADUATION_GATES["max_drawdown_pct"],
            "min_win_rate": self.win_rate >= GRADUATION_GATES["min_win_rate"],
            "max_anomaly_score": self.anomaly_score <= GRADUATION_GATES["max_anomaly_score"],
        }
        passed_all = all(gates.values())
        failures = [k for k, v in gates.items() if not v]
        return {
            "passed": passed_all,
            "gates": gates,
            "failures": failures,
            "strategy_name": self.strategy_name,
            "summary": {
                "total_signals": self.total_signals,
                "days_active": self.days_active,
                "sharpe_ratio": round(self.sharpe_ratio, 3),
                "max_drawdown_pct": round(self.max_drawdown_pct, 2),
                "win_rate_pct": round(self.win_rate, 2),
            },
        }


@dataclass
class DecayStatus:
    strategy_name: str
    listing_id: int
    sharpe_30d: float
    max_drawdown_30d_pct: float
    consecutive_losses: int
    signals_14d: int
    is_paused: bool = False
    pause_reason: str = ""

    def needs_pause(self) -> bool:
        if self.sharpe_30d < DECAY_GATES["min_sharpe_30d"]:
            self.pause_reason = f"30d Sharpe {self.sharpe_30d:.2f} < {DECAY_GATES['min_sharpe_30d']}"
            return True
        if self.max_drawdown_30d_pct > DECAY_GATES["max_drawdown_30d_pct"]:
            self.pause_reason = f"30d MaxDD {self.max_drawdown_30d_pct:.1f}% > {DECAY_GATES['max_drawdown_30d_pct']}%"
            return True
        if self.consecutive_losses > DECAY_GATES["max_consecutive_losses"]:
            self.pause_reason = f"Consecutive losses {self.consecutive_losses} > {DECAY_GATES['max_consecutive_losses']}"
            return True
        if self.signals_14d < DECAY_GATES["min_signals_14d"]:
            self.pause_reason = f"Signals last 14d ({self.signals_14d}) < {DECAY_GATES['min_signals_14d']} — stale strategy"
            return True
        return False


class PaperTradingMonitor:
    """Polls Django for paper trading metrics and evaluates graduation eligibility."""

    def __init__(self, strategy_name: str, creator_key: str | None = None) -> None:
        self.strategy_name = strategy_name
        self.creator_key = creator_key or os.environ.get("ALGOCHAINS_BUILDER_KEY", "")
        self._http_client = None

    async def _get_client(self):
        if self._http_client is None:
            try:
                import httpx
                self._http_client = httpx.AsyncClient(timeout=30.0)
            except ImportError:
                raise ImportError("httpx is required. pip install httpx")
        return self._http_client

    async def fetch_metrics(self, days: int = 30) -> PaperMetrics:
        """Fetch live signal metrics from the AlgoChains Django API."""
        client = await self._get_client()
        end_dt = datetime.now(tz=timezone.utc)
        start_dt = end_dt - timedelta(days=days)

        url = f"{DJANGO_BASE_URL}/api/v1/creators/metrics/{self.strategy_name}/"
        headers = {
            "Authorization": f"Bearer {self.creator_key}",
            "X-Api-Key": METRICS_API_KEY,
        }
        params = {
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
        }

        try:
            resp = await client.get(url, headers=headers, params=params)
            if resp.status_code == 404:
                logger.warning("No paper trading data found for strategy '%s'", self.strategy_name)
                return PaperMetrics(
                    strategy_name=self.strategy_name,
                    start_date=start_dt,
                    end_date=end_dt,
                )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.error("Failed to fetch metrics: %s", exc)
            return PaperMetrics(
                strategy_name=self.strategy_name,
                start_date=start_dt,
                end_date=end_dt,
                raw_data={"error": str(exc)},
            )

        return PaperMetrics(
            strategy_name=self.strategy_name,
            start_date=start_dt,
            end_date=end_dt,
            total_signals=data.get("total_signals", 0),
            days_active=data.get("days_active", 0),
            sharpe_ratio=data.get("sharpe_ratio", 0.0),
            max_drawdown_pct=data.get("max_drawdown_pct", 100.0),
            win_rate=data.get("win_rate", 0.0),
            anomaly_score=data.get("anomaly_score", 0.0),
            raw_data=data,
        )

    async def check_graduation_eligibility(self) -> dict[str, Any]:
        """Fetch 30-day metrics and evaluate graduation gates."""
        metrics = await self.fetch_metrics(days=30)
        result = metrics.passes_graduation()
        elapsed_days = (metrics.end_date - metrics.start_date).days
        result["elapsed_days"] = elapsed_days
        result["days_remaining"] = max(0, 30 - elapsed_days)
        result["recommendation"] = (
            "READY_FOR_SUBMISSION"
            if result["passed"]
            else f"NOT_READY — failing: {', '.join(result['failures'])}"
        )
        return result

    async def submit_to_validation_queue(self, backtest_result: dict | None = None) -> dict[str, Any]:
        """Submit strategy to the AlgoChains validation queue after graduation."""
        client = await self._get_client()
        metrics = await self.fetch_metrics(days=30)
        graduation = metrics.passes_graduation()

        if not graduation["passed"]:
            return {
                "submitted": False,
                "reason": "Graduation gates not passed",
                "failures": graduation["failures"],
                "metrics": graduation["summary"],
            }

        payload = {
            "strategy_name": self.strategy_name,
            "paper_metrics": graduation["summary"],
            "backtest_result": backtest_result or {},
            "submitted_at": datetime.now(tz=timezone.utc).isoformat(),
        }

        url = f"{DJANGO_BASE_URL}/api/v1/creators/submit/"
        headers = {
            "Authorization": f"Bearer {self.creator_key}",
            "Content-Type": "application/json",
        }

        try:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            logger.info(
                "Strategy '%s' submitted for validation. submission_id=%s",
                self.strategy_name,
                data.get("submission_id"),
            )
            return {
                "submitted": True,
                "submission_id": data.get("submission_id"),
                "status": data.get("status", "pending_review"),
                "metrics": graduation["summary"],
                "next_steps": (
                    "AlgoChains will run independent backtest verification and MCPT validation. "
                    "Expect review within 3-5 business days. You'll receive a Slack/email notification."
                ),
            }
        except Exception as exc:
            return {"submitted": False, "error": str(exc)}

    async def close(self) -> None:
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None


class DecayWatchdog:
    """Monitors all live marketplace listings for performance decay.

    Runs periodically and auto-pauses listings that breach decay thresholds.
    """

    def __init__(self) -> None:
        self._http_client = None

    async def _get_client(self):
        if self._http_client is None:
            try:
                import httpx
                self._http_client = httpx.AsyncClient(timeout=30.0)
            except ImportError:
                raise ImportError("httpx is required. pip install httpx")
        return self._http_client

    async def check_all_listings(self) -> list[DecayStatus]:
        """Fetch all active listings and check for decay."""
        client = await self._get_client()
        url = f"{DJANGO_BASE_URL}/api/v1/marketplace/listings/active/"
        headers = {"X-Api-Key": LISTING_API_KEY}

        try:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            listings = resp.json().get("results", [])
        except Exception as exc:
            logger.error("Failed to fetch active listings: %s", exc)
            return []

        statuses: list[DecayStatus] = []
        for listing in listings:
            status = await self._check_listing_decay(listing)
            statuses.append(status)

        return statuses

    async def _check_listing_decay(self, listing: dict) -> DecayStatus:
        """Check decay for a single listing."""
        client = await self._get_client()
        listing_id = listing.get("id")
        strategy_name = listing.get("strategy_name", "unknown")

        url = f"{DJANGO_BASE_URL}/api/v1/marketplace/listings/{listing_id}/decay_metrics/"
        headers = {"X-Api-Key": LISTING_API_KEY}

        try:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("Could not fetch decay metrics for listing %s: %s", listing_id, exc)
            return DecayStatus(
                strategy_name=strategy_name,
                listing_id=listing_id,
                sharpe_30d=1.0,  # Assume OK on error
                max_drawdown_30d_pct=0.0,
                consecutive_losses=0,
                signals_14d=10,
            )

        status = DecayStatus(
            strategy_name=strategy_name,
            listing_id=listing_id,
            sharpe_30d=data.get("sharpe_30d", 0.0),
            max_drawdown_30d_pct=data.get("max_drawdown_30d_pct", 0.0),
            consecutive_losses=data.get("consecutive_losses", 0),
            signals_14d=data.get("signals_14d", 0),
        )

        if status.needs_pause():
            await self._pause_listing(listing_id, strategy_name, status.pause_reason)
            status.is_paused = True

        return status

    async def _pause_listing(self, listing_id: int, strategy_name: str, reason: str) -> None:
        """Auto-pause a listing that triggered decay thresholds."""
        client = await self._get_client()
        url = f"{DJANGO_BASE_URL}/api/v1/marketplace/listings/{listing_id}/pause/"
        headers = {
            "X-Api-Key": LISTING_API_KEY,
            "Content-Type": "application/json",
        }
        payload = {"reason": reason, "auto_paused": True}

        try:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            logger.warning(
                "AUTO-PAUSED listing %s (%s): %s",
                listing_id, strategy_name, reason
            )
        except Exception as exc:
            logger.error("Failed to pause listing %s: %s", listing_id, exc)

    async def run_decay_check(self) -> dict[str, Any]:
        """Run a complete decay check and return a summary."""
        statuses = await self.check_all_listings()
        paused = [s for s in statuses if s.is_paused]
        healthy = [s for s in statuses if not s.is_paused]

        return {
            "checked_at": datetime.now(tz=timezone.utc).isoformat(),
            "total_listings": len(statuses),
            "healthy": len(healthy),
            "auto_paused": len(paused),
            "paused_listings": [
                {
                    "listing_id": s.listing_id,
                    "strategy_name": s.strategy_name,
                    "pause_reason": s.pause_reason,
                    "sharpe_30d": s.sharpe_30d,
                    "max_dd_30d_pct": s.max_drawdown_30d_pct,
                }
                for s in paused
            ],
            "healthy_listings": [s.strategy_name for s in healthy],
        }

    async def run_forever(self, interval_hours: float = 24.0) -> None:
        """Run decay checks on a schedule (for daemon use)."""
        logger.info("Decay watchdog started — checking every %.0f hours", interval_hours)
        while True:
            try:
                summary = await self.run_decay_check()
                logger.info(
                    "Decay check: %d total, %d healthy, %d paused",
                    summary["total_listings"],
                    summary["healthy"],
                    summary["auto_paused"],
                )
            except Exception as exc:
                logger.error("Decay check failed: %s", exc)
            await asyncio.sleep(interval_hours * 3600)

    async def close(self) -> None:
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None


def create_signal_hmac(payload: dict, secret: str) -> str:
    """Create HMAC-SHA256 signature for a signal payload.

    This is used by live_mode() / signal_to_api() to sign each signal
    so Django can verify authenticity and detect injected signals.

    Args:
        payload: The signal dict (must include 'timestamp' and 'strategy_name')
        secret:  Per-creator secret (ALGOCHAINS_SIGNAL_SECRET env var)

    Returns:
        Hex-encoded HMAC-SHA256 signature string.
    """
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256)
    return sig.hexdigest()


def verify_signal_hmac(payload: dict, secret: str, expected_sig: str) -> bool:
    """Verify a signal's HMAC signature."""
    actual = create_signal_hmac(payload, secret)
    return hmac.compare_digest(actual, expected_sig)
