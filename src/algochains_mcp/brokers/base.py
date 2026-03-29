"""
Abstract broker interface — every connector implements this contract.

This is the normalization layer. No matter which broker (Alpaca, IBKR, Oanda,
Schwab via TradersPost, Robinhood via TradersPost, etc.), the AI agent sees
the same interface.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"
    TRAILING_STOP = "trailing_stop"


class OrderStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class AssetClass(str, Enum):
    STOCK = "stock"
    CRYPTO = "crypto"
    FOREX = "forex"
    FUTURES = "futures"
    OPTIONS = "options"


@dataclass
class Order:
    id: str
    broker: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    qty: float
    status: OrderStatus
    filled_qty: float = 0.0
    filled_avg_price: float = 0.0
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    trail_pct: Optional[float] = None
    created_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None
    asset_class: AssetClass = AssetClass.STOCK
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "broker": self.broker,
            "symbol": self.symbol,
            "side": self.side.value,
            "order_type": self.order_type.value,
            "qty": self.qty,
            "status": self.status.value,
            "filled_qty": self.filled_qty,
            "filled_avg_price": self.filled_avg_price,
            "limit_price": self.limit_price,
            "stop_price": self.stop_price,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "filled_at": self.filled_at.isoformat() if self.filled_at else None,
            "asset_class": self.asset_class.value,
        }


@dataclass
class Position:
    broker: str
    symbol: str
    qty: float
    avg_entry_price: float
    market_value: float
    unrealized_pnl: float
    realized_pnl: float = 0.0
    asset_class: AssetClass = AssetClass.STOCK
    side: str = "long"
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "broker": self.broker,
            "symbol": self.symbol,
            "qty": self.qty,
            "avg_entry_price": self.avg_entry_price,
            "market_value": self.market_value,
            "unrealized_pnl": self.unrealized_pnl,
            "realized_pnl": self.realized_pnl,
            "asset_class": self.asset_class.value,
            "side": self.side,
        }


@dataclass
class AccountInfo:
    broker: str
    account_id: str
    equity: float
    cash: float
    buying_power: float
    currency: str = "USD"
    paper: bool = True
    asset_classes: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "broker": self.broker,
            "account_id": self.account_id,
            "equity": self.equity,
            "cash": self.cash,
            "buying_power": self.buying_power,
            "currency": self.currency,
            "paper": self.paper,
            "asset_classes": self.asset_classes,
        }


@dataclass
class Quote:
    symbol: str
    bid: float
    ask: float
    last: float
    volume: int = 0
    timestamp: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "bid": self.bid,
            "ask": self.ask,
            "last": self.last,
            "volume": self.volume,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


class BrokerConnector(abc.ABC):
    """Abstract interface that every broker connector must implement."""

    name: str = "base"
    supported_asset_classes: list[AssetClass] = []

    @abc.abstractmethod
    async def connect(self) -> bool:
        """Establish connection to the broker. Returns True if successful."""
        ...

    @abc.abstractmethod
    async def disconnect(self) -> None:
        """Clean disconnect from the broker."""
        ...

    @abc.abstractmethod
    async def get_account(self) -> AccountInfo:
        """Get account information."""
        ...

    @abc.abstractmethod
    async def get_positions(self) -> list[Position]:
        """Get all open positions."""
        ...

    @abc.abstractmethod
    async def get_orders(self, status: Optional[str] = None) -> list[Order]:
        """Get orders, optionally filtered by status."""
        ...

    @abc.abstractmethod
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
        """Place an order. Returns the order object."""
        ...

    @abc.abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order by ID. Returns True if successful."""
        ...

    @abc.abstractmethod
    async def get_quote(self, symbol: str) -> Quote:
        """Get current quote for a symbol."""
        ...

    async def close_position(self, symbol: str) -> Optional[Order]:
        """Close an entire position. Default implementation uses place_order."""
        positions = await self.get_positions()
        for pos in positions:
            if pos.symbol == symbol:
                side = OrderSide.SELL if pos.qty > 0 else OrderSide.BUY
                return await self.place_order(
                    symbol=symbol,
                    side=side,
                    qty=abs(pos.qty),
                    order_type=OrderType.MARKET,
                )
        return None

    async def close_all_positions(self) -> list[Order]:
        """Close all open positions."""
        positions = await self.get_positions()
        orders = []
        for pos in positions:
            order = await self.close_position(pos.symbol)
            if order:
                orders.append(order)
        return orders

    async def health_check(self) -> dict:
        """Check broker connectivity. Returns status dict."""
        try:
            acct = await self.get_account()
            return {
                "broker": self.name,
                "status": "healthy",
                "account_id": acct.account_id,
                "equity": acct.equity,
                "paper": acct.paper,
            }
        except Exception as e:
            return {
                "broker": self.name,
                "status": "unhealthy",
                "error": str(e),
            }
