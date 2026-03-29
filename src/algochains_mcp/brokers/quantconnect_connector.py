"""
QuantConnect LEAN connector — cloud algorithm management and backtesting.

Uses QuantConnect REST API v2 to manage algorithms, run backtests,
and retrieve results. Does not execute live orders directly — instead
manages LEAN algorithm deployments.
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Optional

import httpx

from ..config import QuantConnectConfig
from .base import (
    AccountInfo,
    AssetClass,
    BrokerConnector,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    Quote,
)

logger = logging.getLogger("algochains_mcp.brokers.quantconnect")


class QuantConnectConnector(BrokerConnector):
    """
    QuantConnect LEAN integration.

    This is not a traditional broker — it manages algorithm deployments
    on QuantConnect's cloud. Use for:
    - Submitting LEAN algorithms
    - Running backtests
    - Deploying live algorithms
    - Retrieving backtest/live results
    """

    name = "quantconnect"
    supported_asset_classes = [
        AssetClass.STOCK, AssetClass.FUTURES, AssetClass.OPTIONS,
        AssetClass.FOREX, AssetClass.CRYPTO,
    ]

    def __init__(self, config: QuantConnectConfig):
        self.cfg = config
        self._client: Optional[httpx.AsyncClient] = None

    def _auth_headers(self) -> dict:
        ts = str(int(time.time()))
        hash_bytes = hashlib.sha256(f"{self.cfg.api_token}:{ts}".encode()).hexdigest()
        return {
            "Timestamp": ts,
            "Authorization": f"Basic {self.cfg.user_id}:{hash_bytes}",
        }

    async def connect(self) -> bool:
        self._client = httpx.AsyncClient(
            base_url=self.cfg.base_url,
            timeout=30.0,
        )
        resp = await self._client.get(
            "/authenticate", headers=self._auth_headers()
        )
        if resp.status_code == 200 and resp.json().get("success"):
            logger.info("QuantConnect authenticated: user %s", self.cfg.user_id)
            return True
        logger.error("QuantConnect auth failed: %s", resp.text)
        return False

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def get_account(self) -> AccountInfo:
        resp = await self._client.get("/account/read", headers=self._auth_headers())
        resp.raise_for_status()
        a = resp.json().get("account", {})
        return AccountInfo(
            broker="quantconnect",
            account_id=self.cfg.user_id,
            equity=float(a.get("balance", 0)),
            cash=float(a.get("balance", 0)),
            buying_power=float(a.get("balance", 0)),
            currency="USD",
            paper=True,
            asset_classes=["stock", "futures", "options", "forex", "crypto"],
            raw=a,
        )

    async def get_positions(self) -> list[Position]:
        return []

    async def get_orders(self, status: Optional[str] = None) -> list[Order]:
        return []

    async def place_order(
        self,
        symbol: str,
        side: OrderSide,
        qty: float,
        order_type: OrderType = OrderType.MARKET,
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        trail_pct: Optional[float] = None,
        time_in_force: str = "day",
    ) -> Order:
        raise NotImplementedError(
            "QuantConnect does not support direct order placement via this connector. "
            "Deploy a LEAN algorithm instead using create_algorithm() + deploy_live()."
        )

    async def cancel_order(self, order_id: str) -> bool:
        return False

    async def get_quote(self, symbol: str) -> Quote:
        return Quote(symbol=symbol, bid=0, ask=0, last=0)

    # ── QuantConnect-specific methods ───────────────────────────────

    async def list_projects(self) -> list[dict]:
        resp = await self._client.get("/projects/read", headers=self._auth_headers())
        resp.raise_for_status()
        return resp.json().get("projects", [])

    async def create_project(self, name: str, language: str = "Py") -> dict:
        resp = await self._client.post(
            "/projects/create",
            headers=self._auth_headers(),
            json={"name": name, "language": language},
        )
        resp.raise_for_status()
        return resp.json().get("projects", [{}])[0] if resp.json().get("projects") else resp.json()

    async def update_file(self, project_id: int, filename: str, content: str) -> bool:
        resp = await self._client.post(
            "/files/update",
            headers=self._auth_headers(),
            json={
                "projectId": project_id,
                "name": filename,
                "content": content,
            },
        )
        return resp.status_code == 200 and resp.json().get("success", False)

    async def run_backtest(self, project_id: int, name: str = "AlgoChains Backtest") -> dict:
        resp = await self._client.post(
            "/backtests/create",
            headers=self._auth_headers(),
            json={"projectId": project_id, "name": name, "compileId": ""},
        )
        resp.raise_for_status()
        return resp.json().get("backtest", {})

    async def get_backtest_results(self, project_id: int, backtest_id: str) -> dict:
        resp = await self._client.get(
            "/backtests/read",
            headers=self._auth_headers(),
            params={"projectId": project_id, "backtestId": backtest_id},
        )
        resp.raise_for_status()
        return resp.json().get("backtest", {})

    async def deploy_live(
        self,
        project_id: int,
        broker_name: str = "InteractiveBrokersBrokerage",
        environment: str = "paper",
    ) -> dict:
        resp = await self._client.post(
            "/live/create",
            headers=self._auth_headers(),
            json={
                "projectId": project_id,
                "compileId": "",
                "serverType": "L-MICRO",
                "baseLiveAlgorithmSettings": {
                    "id": broker_name,
                    "environment": environment,
                },
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def stop_live(self, project_id: int) -> bool:
        resp = await self._client.post(
            "/live/update/stop",
            headers=self._auth_headers(),
            json={"projectId": project_id},
        )
        return resp.status_code == 200
