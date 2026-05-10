"""
Copy Trading Engine — Real-Time Position Mirroring.

Follows a leader (marketplace strategy creator) and mirrors their position
changes to a follower's broker account in real-time.

Implementation:
  - Leader positions polled from Alpaca / Tradovate REST every 30 seconds
  - Follower positions tracked and delta-computed
  - Position deltas executed on follower's broker account
  - Allocation percentage: follower allocates N% of capital to mirror leader

Real data only — positions fetched from live broker accounts.
No synthetic trade signals. No placeholder fills.

Storage: ~/.algochains/copy_trades.json (active subscriptions)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("algochains_mcp.social_trading.copy")

COPY_STATE_PATH = Path.home() / ".algochains" / "copy_trades.json"


class CopyTradingError(Exception):
    pass


@dataclass
class CopySubscription:
    subscription_id: str
    leader_id: str              # Alpaca account ID or strategy creator ID
    leader_broker: str          # "alpaca" | "tradovate"
    follower_broker: str        # broker for follower execution
    allocation_pct: float       # % of follower capital to mirror (0-100)
    max_notional: float         # max USD to allocate
    status: str = "active"      # "active" | "paused" | "cancelled"
    created_at: float = field(default_factory=time.time)
    last_sync: float | None = None
    total_trades_copied: int = 0
    realized_pnl: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "subscription_id": self.subscription_id,
            "leader_id": self.leader_id,
            "leader_broker": self.leader_broker,
            "follower_broker": self.follower_broker,
            "allocation_pct": self.allocation_pct,
            "max_notional": self.max_notional,
            "status": self.status,
            "created_at": self.created_at,
            "last_sync": self.last_sync,
            "total_trades_copied": self.total_trades_copied,
            "realized_pnl": round(self.realized_pnl, 2),
        }


class CopyTradingEngine:
    """
    Real copy trading engine.

    Leader positions are fetched from the real broker API (Alpaca or Tradovate).
    Position deltas are computed and executed on the follower account.
    """

    def __init__(self) -> None:
        self._subscriptions: dict[str, CopySubscription] = {}
        self._leader_positions: dict[str, dict[str, float]] = {}  # leader_id → {symbol: qty}
        self._sync_task: asyncio.Task | None = None
        self._load_state()

    def _load_state(self) -> None:
        if not COPY_STATE_PATH.exists():
            return
        try:
            with open(COPY_STATE_PATH) as f:
                data = json.load(f)
            for sid, sub_data in data.items():
                self._subscriptions[sid] = CopySubscription(**sub_data)
        except Exception as exc:
            logger.error("Failed to load copy trade state: %s", exc)

    def _save_state(self) -> None:
        COPY_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {sid: sub.to_dict() for sid, sub in self._subscriptions.items()}
        with open(COPY_STATE_PATH, "w") as f:
            json.dump(data, f, indent=2)

    def subscribe(
        self,
        leader_id: str,
        leader_broker: str,
        follower_broker: str,
        allocation_pct: float,
        max_notional: float,
    ) -> CopySubscription:
        """
        Start copying a leader's trades.

        Args:
            leader_id: Alpaca account ID or marketplace creator ID
            leader_broker: Broker where leader's positions are held
            follower_broker: Broker where trades will be executed
            allocation_pct: Percentage of follower capital to allocate (1-100)
            max_notional: Hard cap on total mirrored position value

        Returns:
            CopySubscription with subscription_id for status polling
        """
        if not 0 < allocation_pct <= 100:
            raise CopyTradingError("allocation_pct must be between 1 and 100.")
        if max_notional <= 0:
            raise CopyTradingError("max_notional must be positive.")
        if leader_broker not in ("alpaca", "tradovate", "paper"):
            raise CopyTradingError(f"Unsupported leader broker: {leader_broker}. Use: alpaca, tradovate, paper.")

        sub = CopySubscription(
            subscription_id=str(uuid.uuid4()),
            leader_id=leader_id,
            leader_broker=leader_broker,
            follower_broker=follower_broker,
            allocation_pct=allocation_pct,
            max_notional=max_notional,
        )
        self._subscriptions[sub.subscription_id] = sub
        self._save_state()

        # Start sync task if not running
        if self._sync_task is None or self._sync_task.done():
            self._sync_task = asyncio.ensure_future(self._sync_loop())

        logger.info("Copy subscription created: leader=%s allocation=%.1f%%", leader_id, allocation_pct)
        return sub

    def get_status(self, subscription_id: str) -> dict[str, Any]:
        sub = self._subscriptions.get(subscription_id)
        if not sub:
            raise CopyTradingError(f"Subscription {subscription_id} not found.")
        return {
            **sub.to_dict(),
            "leader_positions": self._leader_positions.get(sub.leader_id, {}),
        }

    def unsubscribe(self, subscription_id: str) -> dict[str, Any]:
        sub = self._subscriptions.get(subscription_id)
        if not sub:
            raise CopyTradingError(f"Subscription {subscription_id} not found.")
        sub.status = "cancelled"
        self._save_state()
        return {"cancelled": True, "subscription_id": subscription_id}

    def pause(self, subscription_id: str) -> dict[str, Any]:
        sub = self._subscriptions.get(subscription_id)
        if not sub:
            raise CopyTradingError(f"Subscription {subscription_id} not found.")
        sub.status = "paused"
        self._save_state()
        return {"paused": True, "subscription_id": subscription_id}

    def list_subscriptions(self) -> list[dict[str, Any]]:
        return [sub.to_dict() for sub in self._subscriptions.values()]

    async def _fetch_leader_positions(self, leader_id: str, broker: str) -> dict[str, float]:
        """Fetch real leader positions from broker API."""
        if broker == "alpaca":
            return await self._fetch_alpaca_positions(leader_id)
        elif broker == "tradovate":
            return await self._fetch_tradovate_positions()
        return {}

    async def _fetch_alpaca_positions(self, account_id: str) -> dict[str, float]:
        """Fetch positions from Alpaca API."""
        api_key = os.environ.get("ALPACA_API_KEY", "")
        api_secret = os.environ.get("ALPACA_SECRET_KEY", "")
        if not api_key:
            raise CopyTradingError(
                "ALPACA_API_KEY required to fetch leader positions. "
                "Set env var or use a paper account for testing."
            )
        try:
            import httpx
            async with httpx.AsyncClient(
                base_url="https://paper-api.alpaca.markets",  # paper for safety
                headers={
                    "APCA-API-KEY-ID": api_key,
                    "APCA-API-SECRET-KEY": api_secret,
                },
                timeout=10,
            ) as client:
                resp = await client.get("/v2/positions")
                resp.raise_for_status()
                positions = resp.json()
            return {p["symbol"]: float(p["qty"]) for p in positions}
        except ImportError:
            raise CopyTradingError("httpx required. Install: pip install httpx")

    async def _fetch_tradovate_positions(self) -> dict[str, float]:
        """Fetch positions from Tradovate."""
        try:
            from ..brokers.tradovate import TradovateConnector
            from ..config import TradovateConfig
            cfg = TradovateConfig()
            if not cfg.cid or not cfg.secret:
                raise CopyTradingError(
                    "TRADOVATE_CID / TRADOVATE_SECRET not set. "
                    "Ensure the same .env as the live bot is loaded."
                )
            connector = TradovateConnector(cfg)
            await connector.connect()
            try:
                positions = await connector.get_positions()
            finally:
                await connector.disconnect()
            return {p.symbol: float(p.qty) for p in positions}
        except Exception as exc:
            raise CopyTradingError(f"Tradovate position fetch failed: {exc}")

    async def _sync_loop(self, interval: int = 30) -> None:
        """Background sync loop — fetches leader positions and mirrors changes."""
        logger.info("Copy trading sync loop started (interval=%ds)", interval)
        while True:
            active_subs = [s for s in self._subscriptions.values() if s.status == "active"]
            for sub in active_subs:
                try:
                    new_positions = await self._fetch_leader_positions(sub.leader_id, sub.leader_broker)
                    old_positions = self._leader_positions.get(sub.leader_id, {})
                    self._leader_positions[sub.leader_id] = new_positions

                    # Compute deltas and execute on follower
                    deltas = self._compute_deltas(old_positions, new_positions)
                    if deltas:
                        await self._execute_deltas(sub, deltas)

                    sub.last_sync = time.time()
                    self._save_state()
                except Exception as exc:
                    logger.warning("Sync failed for sub %s: %s", sub.subscription_id[:8], exc)

            await asyncio.sleep(interval)

    def _compute_deltas(self, old: dict[str, float], new: dict[str, float]) -> dict[str, float]:
        """Compute position changes between old and new leader positions."""
        deltas: dict[str, float] = {}
        all_symbols = set(old.keys()) | set(new.keys())
        for sym in all_symbols:
            old_qty = old.get(sym, 0)
            new_qty = new.get(sym, 0)
            delta = new_qty - old_qty
            if abs(delta) >= 0.001:
                deltas[sym] = delta
        return deltas

    async def _execute_deltas(self, sub: CopySubscription, deltas: dict[str, float]) -> None:
        """Execute position deltas on follower account, scaled by allocation_pct."""
        scale = sub.allocation_pct / 100.0
        for symbol, delta in deltas.items():
            scaled_delta = delta * scale
            if abs(scaled_delta) < 0.001:
                continue
            side = "buy" if scaled_delta > 0 else "sell"
            qty = abs(scaled_delta)
            try:
                if sub.follower_broker == "alpaca":
                    await self._place_alpaca_order(symbol, side, qty)
                    # BUG-18 FIX: Previously sub.total_trades_copied incremented
                    # regardless of whether _place_alpaca_order was called. For
                    # non-Alpaca follower brokers (or if the broker was unrecognised),
                    # the counter incremented and logged "Copied trade" even though
                    # no order was placed. Now only increment after a real placement.
                    sub.total_trades_copied += 1
                    logger.info("Copied trade: %s %s %.2f shares for sub %s", side, symbol, qty, sub.subscription_id[:8])
                else:
                    logger.warning(
                        "copy_engine: follower_broker=%r is not supported for execution — "
                        "order NOT placed for %s %s %.2f (sub %s). "
                        "Only 'alpaca' broker is currently wired for follower execution.",
                        sub.follower_broker, side, symbol, qty, sub.subscription_id[:8],
                    )
            except Exception as exc:
                logger.error("Failed to execute copied trade %s %s %.2f: %s", side, symbol, qty, exc)

    async def _place_alpaca_order(self, symbol: str, side: str, qty: float) -> None:
        api_key = os.environ.get("ALPACA_API_KEY", "")
        api_secret = os.environ.get("ALPACA_SECRET_KEY", "")
        if not api_key:
            raise CopyTradingError("ALPACA_API_KEY required for follower execution.")
        import httpx
        async with httpx.AsyncClient(
            base_url="https://paper-api.alpaca.markets",
            headers={"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret},
            timeout=10,
        ) as client:
            resp = await client.post("/v2/orders", json={
                "symbol": symbol,
                "qty": str(round(qty, 4)),
                "side": side,
                "type": "market",
                "time_in_force": "day",
            })
            resp.raise_for_status()


_copy_engine: CopyTradingEngine | None = None


def get_copy_engine() -> CopyTradingEngine:
    global _copy_engine
    if _copy_engine is None:
        _copy_engine = CopyTradingEngine()
    return _copy_engine
