"""
TradersPost.io connector — meta-router to 15+ brokers via webhooks.

TradersPost routes signals to: Schwab, Robinhood, Tastytrade, TradeStation,
Tradier, Alpaca, Coinbase, Kraken, Bitget, ByBit, and more.

This connector sends webhook JSON signals that TradersPost routes to
whichever broker the user has connected on their TradersPost account.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from ..config import TradersPostConfig
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

logger = logging.getLogger("algochains_mcp.brokers.traderspost")


class TradersPostConnector(BrokerConnector):
    """
    TradersPost webhook-based order router.

    Supported downstream brokers (user connects on traderspost.io):
    - Schwab / TD Ameritrade
    - Robinhood
    - Tastytrade
    - TradeStation
    - Tradier
    - Alpaca
    - Coinbase
    - Kraken
    - Bitget
    - ByBit
    - Interactive Brokers (via TradersPost)
    """

    name = "traderspost"
    supported_asset_classes = [
        AssetClass.STOCK, AssetClass.CRYPTO, AssetClass.OPTIONS, AssetClass.FUTURES,
    ]

    def __init__(self, config: TradersPostConfig):
        self.cfg = config
        self._client: Optional[httpx.AsyncClient] = None

    async def connect(self) -> bool:
        if not self.cfg.webhook_url:
            logger.error("TRADERSPOST_WEBHOOK_URL not set")
            return False
        self._client = httpx.AsyncClient(timeout=30.0)
        logger.info("TradersPost connector ready (webhook mode)")
        return True

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def get_account(self) -> AccountInfo:
        return AccountInfo(
            broker="traderspost",
            account_id="webhook",
            equity=0.0,
            cash=0.0,
            buying_power=0.0,
            currency="USD",
            paper=False,
            asset_classes=["stock", "crypto", "options", "futures"],
        )

    async def get_positions(self) -> list[Position]:
        logger.warning("TradersPost is webhook-only; positions not available via this connector")
        return []

    async def get_orders(self, status: Optional[str] = None) -> list[Order]:
        logger.warning("TradersPost is webhook-only; order history not available via this connector")
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
        """
        Send a webhook signal to TradersPost.

        TradersPost JSON webhook format:
        {
            "ticker": "AAPL",
            "action": "buy",
            "orderType": "market",
            "quantity": 10,
            "limitPrice": null,
            "stopPrice": null
        }
        """
        payload = {
            "ticker": symbol,
            "action": side.value,
            "orderType": self._map_order_type(order_type),
            "quantity": qty,
        }
        if limit_price is not None:
            payload["limitPrice"] = limit_price
        if stop_price is not None:
            payload["stopPrice"] = stop_price
        if trail_pct is not None:
            payload["trailPercent"] = trail_pct

        resp = await self._client.post(self.cfg.webhook_url, json=payload)

        if resp.status_code in (200, 201, 202):
            logger.info("TradersPost signal sent: %s %s %s", side.value, qty, symbol)
            return Order(
                id=f"tp_{symbol}_{side.value}_{int(qty)}",
                broker="traderspost",
                symbol=symbol,
                side=side,
                order_type=order_type,
                qty=qty,
                status=OrderStatus.ACCEPTED,
                raw={"webhook_response": resp.text},
            )

        logger.error("TradersPost webhook failed: %s %s", resp.status_code, resp.text)
        return Order(
            id="error",
            broker="traderspost",
            symbol=symbol,
            side=side,
            order_type=order_type,
            qty=qty,
            status=OrderStatus.REJECTED,
            raw={"error": resp.text, "status_code": resp.status_code},
        )

    async def cancel_order(self, order_id: str) -> bool:
        logger.warning("TradersPost cancel not supported via webhook; cancel on downstream broker")
        return False

    async def get_quote(self, symbol: str) -> Quote:
        logger.warning("TradersPost does not provide market data; use a data provider")
        return Quote(symbol=symbol, bid=0, ask=0, last=0)

    async def close_position(self, symbol: str) -> Optional[Order]:
        """Send a flatten signal via TradersPost webhook."""
        payload = {
            "ticker": symbol,
            "action": "exit",
        }
        resp = await self._client.post(self.cfg.webhook_url, json=payload)
        if resp.status_code in (200, 201, 202):
            return Order(
                id=f"tp_{symbol}_exit",
                broker="traderspost",
                symbol=symbol,
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                qty=0,
                status=OrderStatus.ACCEPTED,
            )
        return None

    @staticmethod
    def _map_order_type(ot: OrderType) -> str:
        return {
            OrderType.MARKET: "market",
            OrderType.LIMIT: "limit",
            OrderType.STOP: "stop",
            OrderType.STOP_LIMIT: "stop_limit",
            OrderType.TRAILING_STOP: "trailing_stop",
        }.get(ot, "market")
