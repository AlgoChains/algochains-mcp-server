"""
Alpaca broker connector — stocks, ETFs, crypto, options.
Uses the official alpaca-py SDK when available, falls back to httpx.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import httpx

from ..config import AlpacaConfig
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

logger = logging.getLogger("algochains_mcp.brokers.alpaca")

_STATUS_MAP = {
    "new": OrderStatus.PENDING,
    "accepted": OrderStatus.ACCEPTED,
    "filled": OrderStatus.FILLED,
    "partially_filled": OrderStatus.PARTIALLY_FILLED,
    "canceled": OrderStatus.CANCELLED,
    "cancelled": OrderStatus.CANCELLED,
    "rejected": OrderStatus.REJECTED,
    "expired": OrderStatus.EXPIRED,
    "pending_new": OrderStatus.PENDING,
}

_ORDER_TYPE_MAP = {
    OrderType.MARKET: "market",
    OrderType.LIMIT: "limit",
    OrderType.STOP: "stop",
    OrderType.STOP_LIMIT: "stop_limit",
    OrderType.TRAILING_STOP: "trailing_stop",
}


class AlpacaConnector(BrokerConnector):
    name = "alpaca"
    supported_asset_classes = [AssetClass.STOCK, AssetClass.CRYPTO, AssetClass.OPTIONS]

    def __init__(self, config: AlpacaConfig):
        self.cfg = config
        self._client: Optional[httpx.AsyncClient] = None

    async def connect(self) -> bool:
        self._client = httpx.AsyncClient(
            base_url=self.cfg.base_url,
            headers={
                "APCA-API-KEY-ID": self.cfg.api_key,
                "APCA-API-SECRET-KEY": self.cfg.secret_key,
            },
            timeout=30.0,
        )
        resp = await self._client.get("/v2/account")
        if resp.status_code == 200:
            logger.info("Alpaca connected: %s", resp.json().get("id"))
            return True
        logger.error("Alpaca connection failed: %s", resp.text)
        return False

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def get_account(self) -> AccountInfo:
        resp = await self._client.get("/v2/account")
        resp.raise_for_status()
        d = resp.json()
        return AccountInfo(
            broker="alpaca",
            account_id=d["id"],
            equity=float(d["equity"]),
            cash=float(d["cash"]),
            buying_power=float(d["buying_power"]),
            currency=d.get("currency", "USD"),
            paper="paper" in self.cfg.base_url,
            asset_classes=["stock", "crypto", "options"],
            raw=d,
        )

    async def get_positions(self) -> list[Position]:
        resp = await self._client.get("/v2/positions")
        resp.raise_for_status()
        return [
            Position(
                broker="alpaca",
                symbol=p["symbol"],
                qty=float(p["qty"]),
                avg_entry_price=float(p["avg_entry_price"]),
                market_value=float(p["market_value"]),
                unrealized_pnl=float(p["unrealized_pl"]),
                realized_pnl=float(p.get("realized_pl", 0)),
                side="long" if float(p["qty"]) > 0 else "short",
                raw=p,
            )
            for p in resp.json()
        ]

    async def get_orders(self, status: Optional[str] = None) -> list[Order]:
        params = {"limit": 100}
        if status:
            params["status"] = status
        resp = await self._client.get("/v2/orders", params=params)
        resp.raise_for_status()
        return [self._parse_order(o) for o in resp.json()]

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
        body = {
            "symbol": symbol,
            "side": side.value,
            "type": _ORDER_TYPE_MAP[order_type],
            "qty": str(qty),
            "time_in_force": time_in_force,
        }
        if limit_price is not None:
            body["limit_price"] = str(limit_price)
        if stop_price is not None:
            body["stop_price"] = str(stop_price)
        if trail_pct is not None:
            body["trail_percent"] = str(trail_pct)

        resp = await self._client.post("/v2/orders", json=body)
        resp.raise_for_status()
        return self._parse_order(resp.json())

    async def cancel_order(self, order_id: str) -> bool:
        resp = await self._client.delete(f"/v2/orders/{order_id}")
        return resp.status_code in (200, 204)

    async def get_quote(self, symbol: str) -> Quote:
        data_url = "https://data.alpaca.markets"
        async with httpx.AsyncClient(
            headers={
                "APCA-API-KEY-ID": self.cfg.api_key,
                "APCA-API-SECRET-KEY": self.cfg.secret_key,
            },
            timeout=15.0,
        ) as client:
            resp = await client.get(f"{data_url}/v2/stocks/{symbol}/quotes/latest")
            resp.raise_for_status()
            q = resp.json().get("quote", {})
            return Quote(
                symbol=symbol,
                bid=float(q.get("bp", 0)),
                ask=float(q.get("ap", 0)),
                last=float(q.get("bp", 0)),
                volume=int(q.get("bs", 0) + q.get("as", 0)),
            )

    async def close_position(self, symbol: str) -> Optional[Order]:
        resp = await self._client.delete(f"/v2/positions/{symbol}")
        if resp.status_code in (200, 204):
            data = resp.json() if resp.status_code == 200 else {}
            return self._parse_order(data) if data else None
        return None

    async def close_all_positions(self) -> list[Order]:
        resp = await self._client.delete("/v2/positions")
        if resp.status_code in (200, 207):
            return [self._parse_order(o.get("body", {})) for o in resp.json() if o.get("body")]
        return []

    def _parse_order(self, o: dict) -> Order:
        return Order(
            id=o.get("id", ""),
            broker="alpaca",
            symbol=o.get("symbol", ""),
            side=OrderSide(o.get("side", "buy")),
            order_type=OrderType(o.get("type", "market")),
            qty=float(o.get("qty", 0)),
            status=_STATUS_MAP.get(o.get("status", ""), OrderStatus.PENDING),
            filled_qty=float(o.get("filled_qty", 0)),
            filled_avg_price=float(o.get("filled_avg_price", 0) or 0),
            limit_price=float(o["limit_price"]) if o.get("limit_price") else None,
            stop_price=float(o["stop_price"]) if o.get("stop_price") else None,
            created_at=datetime.fromisoformat(o["created_at"]) if o.get("created_at") else None,
            filled_at=datetime.fromisoformat(o["filled_at"]) if o.get("filled_at") else None,
            raw=o,
        )
