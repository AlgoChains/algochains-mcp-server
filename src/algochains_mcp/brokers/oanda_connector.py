"""
Oanda connector — forex pairs via REST v20 API.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from ..config import OandaConfig
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

logger = logging.getLogger("algochains_mcp.brokers.oanda")

_BASE_URLS = {
    "practice": "https://api-fxpractice.oanda.com",
    "live": "https://api-fxtrade.oanda.com",
}


class OandaConnector(BrokerConnector):
    name = "oanda"
    supported_asset_classes = [AssetClass.FOREX]

    def __init__(self, config: OandaConfig):
        self.cfg = config
        self._client: Optional[httpx.AsyncClient] = None

    async def connect(self) -> bool:
        base = _BASE_URLS.get(self.cfg.environment, _BASE_URLS["practice"])
        self._client = httpx.AsyncClient(
            base_url=base,
            headers={
                "Authorization": f"Bearer {self.cfg.access_token}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        resp = await self._client.get(f"/v3/accounts/{self.cfg.account_id}")
        if resp.status_code == 200:
            logger.info("Oanda connected: %s", self.cfg.account_id)
            return True
        logger.error("Oanda connection failed: %s", resp.text)
        return False

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def get_account(self) -> AccountInfo:
        resp = await self._client.get(f"/v3/accounts/{self.cfg.account_id}/summary")
        resp.raise_for_status()
        a = resp.json()["account"]
        return AccountInfo(
            broker="oanda",
            account_id=a["id"],
            equity=float(a["NAV"]),
            cash=float(a["balance"]),
            buying_power=float(a["marginAvailable"]),
            currency=a.get("currency", "USD"),
            paper=self.cfg.environment == "practice",
            asset_classes=["forex"],
            raw=a,
        )

    async def get_positions(self) -> list[Position]:
        resp = await self._client.get(f"/v3/accounts/{self.cfg.account_id}/openPositions")
        resp.raise_for_status()
        positions = []
        for p in resp.json().get("positions", []):
            long_units = float(p.get("long", {}).get("units", 0))
            short_units = float(p.get("short", {}).get("units", 0))
            units = long_units + short_units
            if units == 0:
                continue
            pnl_long = float(p.get("long", {}).get("unrealizedPL", 0))
            pnl_short = float(p.get("short", {}).get("unrealizedPL", 0))
            avg_price = float(p.get("long", {}).get("averagePrice", 0) or
                              p.get("short", {}).get("averagePrice", 0))
            positions.append(Position(
                broker="oanda",
                symbol=p["instrument"],
                qty=units,
                avg_entry_price=avg_price,
                market_value=abs(units * avg_price),
                unrealized_pnl=pnl_long + pnl_short,
                asset_class=AssetClass.FOREX,
                side="long" if units > 0 else "short",
                raw=p,
            ))
        return positions

    async def get_orders(self, status: Optional[str] = None) -> list[Order]:
        endpoint = f"/v3/accounts/{self.cfg.account_id}/orders"
        if status == "pending":
            endpoint = f"/v3/accounts/{self.cfg.account_id}/pendingOrders"
        resp = await self._client.get(endpoint)
        resp.raise_for_status()
        return [
            Order(
                id=o["id"],
                broker="oanda",
                symbol=o.get("instrument", ""),
                side=OrderSide.BUY if float(o.get("units", 1)) > 0 else OrderSide.SELL,
                order_type=self._map_type(o.get("type", "MARKET")),
                qty=abs(float(o.get("units", 0))),
                status=self._map_status(o.get("state", "PENDING")),
                limit_price=float(o["price"]) if o.get("price") else None,
                raw=o,
            )
            for o in resp.json().get("orders", [])
        ]

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
        units = qty if side == OrderSide.BUY else -qty
        body: dict = {
            "order": {
                "instrument": symbol,
                "units": str(int(units)),
                "timeInForce": "FOK" if order_type == OrderType.MARKET else "GTC",
            }
        }
        if order_type == OrderType.MARKET:
            body["order"]["type"] = "MARKET"
        elif order_type == OrderType.LIMIT and limit_price:
            body["order"]["type"] = "LIMIT"
            body["order"]["price"] = str(limit_price)
        elif order_type == OrderType.STOP and stop_price:
            body["order"]["type"] = "STOP"
            body["order"]["price"] = str(stop_price)
        else:
            body["order"]["type"] = "MARKET"

        resp = await self._client.post(
            f"/v3/accounts/{self.cfg.account_id}/orders", json=body
        )
        resp.raise_for_status()
        data = resp.json()
        fill = data.get("orderFillTransaction", {})
        create = data.get("orderCreateTransaction", {})
        return Order(
            id=fill.get("id", create.get("id", "")),
            broker="oanda",
            symbol=symbol,
            side=side,
            order_type=order_type,
            qty=qty,
            status=OrderStatus.FILLED if fill else OrderStatus.PENDING,
            filled_qty=abs(float(fill.get("units", 0))) if fill else 0,
            filled_avg_price=float(fill.get("price", 0)) if fill else 0,
            raw=data,
        )

    async def cancel_order(self, order_id: str) -> bool:
        resp = await self._client.put(
            f"/v3/accounts/{self.cfg.account_id}/orders/{order_id}/cancel"
        )
        return resp.status_code in (200, 201)

    async def get_quote(self, symbol: str) -> Quote:
        resp = await self._client.get(
            f"/v3/accounts/{self.cfg.account_id}/pricing",
            params={"instruments": symbol},
        )
        resp.raise_for_status()
        prices = resp.json().get("prices", [])
        if not prices:
            return Quote(symbol=symbol, bid=0, ask=0, last=0)
        p = prices[0]
        bid = float(p.get("bids", [{}])[0].get("price", 0)) if p.get("bids") else 0
        ask = float(p.get("asks", [{}])[0].get("price", 0)) if p.get("asks") else 0
        return Quote(symbol=symbol, bid=bid, ask=ask, last=(bid + ask) / 2)

    @staticmethod
    def _map_type(oanda_type: str) -> OrderType:
        return {
            "MARKET": OrderType.MARKET,
            "LIMIT": OrderType.LIMIT,
            "STOP": OrderType.STOP,
            "MARKET_IF_TOUCHED": OrderType.STOP,
            "TRAILING_STOP_LOSS": OrderType.TRAILING_STOP,
        }.get(oanda_type, OrderType.MARKET)

    @staticmethod
    def _map_status(state: str) -> OrderStatus:
        return {
            "PENDING": OrderStatus.PENDING,
            "FILLED": OrderStatus.FILLED,
            "TRIGGERED": OrderStatus.FILLED,
            "CANCELLED": OrderStatus.CANCELLED,
        }.get(state, OrderStatus.PENDING)
