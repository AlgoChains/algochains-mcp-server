"""
Interactive Brokers connector — stocks, futures, options, forex.
Uses ib_async when available, otherwise provides stub for config-only mode.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from ..config import IBKRConfig
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

logger = logging.getLogger("algochains_mcp.brokers.ibkr")


class IBKRConnector(BrokerConnector):
    name = "ibkr"
    supported_asset_classes = [
        AssetClass.STOCK, AssetClass.FUTURES, AssetClass.OPTIONS, AssetClass.FOREX,
    ]

    def __init__(self, config: IBKRConfig):
        self.cfg = config
        self._ib = None

    async def connect(self) -> bool:
        try:
            from ib_async import IB
            self._ib = IB()
            await self._ib.connectAsync(
                host=self.cfg.host,
                port=self.cfg.port,
                clientId=self.cfg.client_id,
            )
            logger.info("IBKR connected: %s:%s", self.cfg.host, self.cfg.port)
            return True
        except ImportError:
            logger.warning("ib_async not installed — IBKR connector in stub mode")
            return False
        except Exception as e:
            logger.error("IBKR connection failed: %s", e)
            return False

    async def disconnect(self) -> None:
        if self._ib:
            self._ib.disconnect()
            self._ib = None

    async def get_account(self) -> AccountInfo:
        if not self._ib:
            raise RuntimeError("IBKR not connected")
        summary = self._ib.accountSummary()
        values = {s.tag: s.value for s in summary}
        return AccountInfo(
            broker="ibkr",
            account_id=summary[0].account if summary else "unknown",
            equity=float(values.get("NetLiquidation", 0)),
            cash=float(values.get("TotalCashValue", 0)),
            buying_power=float(values.get("BuyingPower", 0)),
            currency=values.get("Currency", "USD"),
            paper=self.cfg.port == 7497,
            asset_classes=["stock", "futures", "options", "forex"],
        )

    async def get_positions(self) -> list[Position]:
        if not self._ib:
            raise RuntimeError("IBKR not connected")
        positions = self._ib.positions()
        return [
            Position(
                broker="ibkr",
                symbol=p.contract.symbol,
                qty=float(p.position),
                avg_entry_price=float(p.avgCost),
                market_value=float(p.position * p.avgCost),
                unrealized_pnl=0.0,
                side="long" if p.position > 0 else "short",
            )
            for p in positions
        ]

    async def get_orders(self, status: Optional[str] = None) -> list[Order]:
        if not self._ib:
            raise RuntimeError("IBKR not connected")
        trades = self._ib.openTrades() if not status else self._ib.trades()
        return [
            Order(
                id=str(t.order.orderId),
                broker="ibkr",
                symbol=t.contract.symbol,
                side=OrderSide.BUY if t.order.action == "BUY" else OrderSide.SELL,
                order_type=self._map_order_type(t.order.orderType),
                qty=float(t.order.totalQuantity),
                status=self._map_status(t.orderStatus.status),
                filled_qty=float(t.orderStatus.filled),
                filled_avg_price=float(t.orderStatus.avgFillPrice or 0),
            )
            for t in trades
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
        if not self._ib:
            raise RuntimeError("IBKR not connected")

        from ib_async import Stock, MarketOrder, LimitOrder, StopOrder

        contract = Stock(symbol, "SMART", "USD")
        action = "BUY" if side == OrderSide.BUY else "SELL"

        if order_type == OrderType.MARKET:
            ib_order = MarketOrder(action, qty)
        elif order_type == OrderType.LIMIT and limit_price:
            ib_order = LimitOrder(action, qty, limit_price)
        elif order_type == OrderType.STOP and stop_price:
            ib_order = StopOrder(action, qty, stop_price)
        else:
            ib_order = MarketOrder(action, qty)

        trade = self._ib.placeOrder(contract, ib_order)
        return Order(
            id=str(trade.order.orderId),
            broker="ibkr",
            symbol=symbol,
            side=side,
            order_type=order_type,
            qty=qty,
            status=OrderStatus.PENDING,
        )

    async def cancel_order(self, order_id: str) -> bool:
        if not self._ib:
            return False
        for trade in self._ib.openTrades():
            if str(trade.order.orderId) == order_id:
                self._ib.cancelOrder(trade.order)
                return True
        return False

    async def get_quote(self, symbol: str) -> Quote:
        if not self._ib:
            raise RuntimeError("IBKR not connected")
        from ib_async import Stock
        contract = Stock(symbol, "SMART", "USD")
        self._ib.qualifyContracts(contract)
        ticker = self._ib.reqMktData(contract)
        await self._ib.sleep(2)
        return Quote(
            symbol=symbol,
            bid=float(ticker.bid or 0),
            ask=float(ticker.ask or 0),
            last=float(ticker.last or 0),
            volume=int(ticker.volume or 0),
        )

    @staticmethod
    def _map_order_type(ib_type: str) -> OrderType:
        return {
            "MKT": OrderType.MARKET,
            "LMT": OrderType.LIMIT,
            "STP": OrderType.STOP,
            "STP LMT": OrderType.STOP_LIMIT,
            "TRAIL": OrderType.TRAILING_STOP,
        }.get(ib_type, OrderType.MARKET)

    @staticmethod
    def _map_status(ib_status: str) -> OrderStatus:
        return {
            "Submitted": OrderStatus.ACCEPTED,
            "Filled": OrderStatus.FILLED,
            "Cancelled": OrderStatus.CANCELLED,
            "Inactive": OrderStatus.REJECTED,
            "PendingSubmit": OrderStatus.PENDING,
            "PreSubmitted": OrderStatus.PENDING,
        }.get(ib_status, OrderStatus.PENDING)
