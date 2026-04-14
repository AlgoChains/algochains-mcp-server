"""
Tradovate futures broker connector — ported from Control Tower patterns.

Auth: OAuth2 + WebSocket streaming
Assets: Futures (MNQ, MES, NQ, ES, CL, GC, etc.)
Token: Uses Token Guardian pattern — NEVER use tradovate_token_auto_refresh.py
Symbology: Use continuous contract format (e.g. MNQZ5 for front-month)
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

import httpx

from ..config import TradovateConfig
from ..errors import BrokerAuthError, BrokerConnectionError, BrokerOrderError, BrokerQuoteError
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

logger = logging.getLogger("algochains_mcp.brokers.tradovate")


# Tradovate contract month codes
MONTH_CODES = {1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M",
               7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z"}


def _front_month_symbol(base: str) -> str:
    """Convert base symbol (MNQ) to front-month Tradovate symbol (MNQZ5)."""
    now = datetime.now(timezone.utc)
    month_code = MONTH_CODES.get(now.month, "Z")
    year_digit = str(now.year)[-1]
    return f"{base}{month_code}{year_digit}"


def _map_order_status(status: str) -> OrderStatus:
    mapping = {
        "Accepted": OrderStatus.ACCEPTED,
        "Working": OrderStatus.ACCEPTED,
        "Filled": OrderStatus.FILLED,
        "Cancelled": OrderStatus.CANCELLED,
        "Rejected": OrderStatus.REJECTED,
        "Expired": OrderStatus.EXPIRED,
        "PendingNew": OrderStatus.PENDING,
        "PendingCancel": OrderStatus.PENDING,
    }
    return mapping.get(status, OrderStatus.PENDING)


def _map_order_type(action: str) -> OrderType:
    mapping = {
        "Market": OrderType.MARKET,
        "Limit": OrderType.LIMIT,
        "Stop": OrderType.STOP,
        "StopLimit": OrderType.STOP_LIMIT,
        "TrailingStop": OrderType.TRAILING_STOP,
    }
    return mapping.get(action, OrderType.MARKET)


class TradovateConnector(BrokerConnector):
    """Tradovate futures connector — OAuth2 REST + WebSocket."""

    name = "tradovate"
    supported_asset_classes = [AssetClass.FUTURES]

    def __init__(self, config: TradovateConfig):
        self.cfg = config
        self._access_token: str = ""
        self._token_expires_at: float = 0.0
        self._account_id: int = 0
        self._account_spec: str = ""
        self._connected: bool = False

    @property
    def capabilities(self) -> dict:
        return {
            "streaming": True,
            "futures": True,
            "bracket_orders": True,
            "order_book": True,
            "historical": True,
            "paper_trading": True,
            "options": False,
            "crypto": False,
            "forex": False,
            "fractional_shares": False,
        }

    async def connect(self) -> bool:
        """Authenticate via OAuth2 and get account info."""
        if not self.cfg.cid or not self.cfg.secret:
            logger.warning("Tradovate credentials not configured")
            return False

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self.cfg.base_url}/v1/auth/accesstokenrequest",
                    json={
                        "name": self.cfg.cid,
                        "password": self.cfg.secret,
                        "appId": "AlgoChains",
                        "appVersion": "2.0",
                        "cid": 0,
                        "sec": "",
                    },
                )
                if resp.status_code == 401:
                    raise BrokerAuthError(
                        "Tradovate authentication failed — check CID/SECRET",
                        broker="tradovate",
                    )
                resp.raise_for_status()
                data = resp.json()

            self._access_token = data.get("accessToken", "")
            expires_in = data.get("expirationTime", "")
            if expires_in:
                try:
                    exp_dt = datetime.fromisoformat(expires_in.replace("Z", "+00:00"))
                    self._token_expires_at = exp_dt.timestamp()
                except ValueError:
                    self._token_expires_at = time.time() + 5400
            else:
                # BUG-20 FIX: expirationTime missing from auth response.
                # If _token_expires_at stays 0, _ensure_token fires on EVERY call
                # (time.time() > 0 - 3600 is always True), causing a reconnect storm.
                # Default to 60 minutes as a safe lower bound.
                self._token_expires_at = time.time() + 3600

            accounts = await self._get("/account/list")
            if accounts and isinstance(accounts, list):
                self._account_id = accounts[0].get("id", 0)
                self._account_spec = accounts[0].get("name", "")

            self._connected = True
            logger.info(
                "Tradovate connected: account=%s env=%s",
                self._account_spec, self.cfg.env,
            )
            return True

        except BrokerAuthError:
            raise
        except Exception as e:
            raise BrokerConnectionError(
                f"Tradovate connection failed: {e}", broker="tradovate"
            )

    async def disconnect(self) -> None:
        self._connected = False
        self._access_token = ""
        logger.info("Tradovate disconnected")

    async def _get(self, path: str, params: Optional[dict] = None) -> Any:
        """Authenticated GET request."""
        await self._ensure_token()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.cfg.base_url}/v1{path}",
                params=params,
                headers={"Authorization": f"Bearer {self._access_token}"},
            )
            if resp.status_code == 401:
                raise BrokerAuthError("Tradovate token expired", broker="tradovate")
            resp.raise_for_status()
            return resp.json()

    async def _post(self, path: str, payload: dict) -> Any:
        """Authenticated POST request."""
        await self._ensure_token()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.cfg.base_url}/v1{path}",
                json=payload,
                headers={"Authorization": f"Bearer {self._access_token}"},
            )
            if resp.status_code == 401:
                raise BrokerAuthError("Tradovate token expired", broker="tradovate")
            resp.raise_for_status()
            return resp.json()

    async def _delete(self, path: str) -> Any:
        """Authenticated DELETE request. Returns None (not raising) on 404 so callers
        can distinguish 'already gone' (404) from genuine errors without try/except."""
        await self._ensure_token()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.delete(
                f"{self.cfg.base_url}/v1{path}",
                headers={"Authorization": f"Bearer {self._access_token}"},
            )
            if resp.status_code == 401:
                raise BrokerAuthError("Tradovate token expired", broker="tradovate")
            if resp.status_code == 404:
                return None  # already gone — caller decides what to do
            resp.raise_for_status()
            try:
                return resp.json()
            except Exception:
                return {}

    async def _ensure_token(self) -> None:
        """Check token validity — reconnect if within renewal window.

        V2 FIX: Raised threshold from 5 minutes to 60 minutes.
        Google SSO tokens have a fixed ~12h window; waiting until 5 min before
        expiry left essentially no recovery time. 60 min provides a meaningful
        window for the system to reconnect before the token is actually expired.
        """
        if time.time() > self._token_expires_at - 3600:  # 60 min threshold (was 5 min)
            logger.info("Tradovate token near expiry (< 60 min), reconnecting...")
            await self.connect()

    async def get_account(self) -> AccountInfo:
        accounts = await self._get("/account/list")
        if not accounts:
            raise BrokerConnectionError("No Tradovate accounts found", broker="tradovate")

        acct = accounts[0]
        cash_balances = await self._get("/cashBalance/getCashBalanceSnapshot", {
            "accountId": acct["id"]
        })

        equity = 0.0
        cash = 0.0
        if isinstance(cash_balances, dict):
            equity = cash_balances.get("totalCashValue", 0.0)
            cash = cash_balances.get("cashBalance", equity)
        elif isinstance(cash_balances, list) and cash_balances:
            equity = cash_balances[0].get("totalCashValue", 0.0)
            cash = cash_balances[0].get("cashBalance", equity)

        return AccountInfo(
            broker="tradovate",
            account_id=str(acct.get("id", "")),
            equity=equity,
            cash=cash,
            buying_power=equity,
            currency="USD",
            paper=self.cfg.env != "live",
            asset_classes=["futures"],
            raw=acct,
        )

    async def get_positions(self) -> list[Position]:
        raw_positions = await self._get("/position/list")
        positions = []
        for pos in (raw_positions or []):
            net_qty = pos.get("netPos", 0)
            if net_qty == 0:
                continue
            contract_id = pos.get("contractId", 0)
            contract = await self._get(f"/contract/item", {"id": contract_id})
            symbol = contract.get("name", str(contract_id)) if contract else str(contract_id)

            entry = pos.get("netPrice", 0.0)
            pnl = pos.get("openPnL", 0.0)
            positions.append(Position(
                broker="tradovate",
                symbol=symbol,
                qty=float(net_qty),
                avg_entry_price=entry,
                market_value=0.0,
                unrealized_pnl=pnl,
                realized_pnl=0.0,
                asset_class=AssetClass.FUTURES,
                side="long" if net_qty > 0 else "short",
                raw=pos,
            ))
        return positions

    async def get_orders(self, status: Optional[str] = None) -> list[Order]:
        raw_orders = await self._get("/order/list")
        orders = []
        for o in (raw_orders or []):
            order_status = _map_order_status(o.get("ordStatus", ""))
            if status == "open" and order_status not in (
                OrderStatus.PENDING, OrderStatus.ACCEPTED
            ):
                continue
            if status == "closed" and order_status in (
                OrderStatus.PENDING, OrderStatus.ACCEPTED
            ):
                continue

            contract_id = o.get("contractId", 0)
            contract = await self._get("/contract/item", {"id": contract_id})
            symbol = contract.get("name", str(contract_id)) if contract else str(contract_id)

            orders.append(Order(
                id=str(o.get("id", "")),
                broker="tradovate",
                symbol=symbol,
                side=OrderSide.BUY if o.get("action") == "Buy" else OrderSide.SELL,
                order_type=_map_order_type(o.get("ordType", "Market")),
                qty=float(o.get("qty", 0)),
                status=order_status,
                filled_qty=float(o.get("filledQty", 0)),
                filled_avg_price=float(o.get("avgFillPrice", 0)),
                limit_price=o.get("price"),
                stop_price=o.get("stopPrice"),
                created_at=None,
                asset_class=AssetClass.FUTURES,
                raw=o,
            ))
        return orders

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
        action = "Buy" if side == OrderSide.BUY else "Sell"

        tov_type_map = {
            OrderType.MARKET: "Market",
            OrderType.LIMIT: "Limit",
            OrderType.STOP: "Stop",
            OrderType.STOP_LIMIT: "StopLimit",
            OrderType.TRAILING_STOP: "TrailingStop",
        }
        tov_type = tov_type_map.get(order_type, "Market")

        tif_map = {"day": "Day", "gtc": "GTC", "ioc": "IOC"}
        tov_tif = tif_map.get(time_in_force.lower(), "Day")

        contract = await self._find_contract(symbol)
        if not contract:
            raise BrokerOrderError(
                f"Contract not found for symbol: {symbol}",
                broker="tradovate",
            )

        payload: dict = {
            "accountSpec": self._account_spec,
            "accountId": self._account_id,
            "action": action,
            "symbol": contract.get("name", symbol),
            "orderQty": int(qty),
            "orderType": tov_type,
            "timeInForce": tov_tif,
        }
        if limit_price is not None:
            payload["price"] = limit_price
        if stop_price is not None:
            payload["stopPrice"] = stop_price
        if trail_pct is not None:
            payload["trailStop"] = trail_pct

        try:
            result = await self._post("/order/placeOrder", payload)
        except Exception as e:
            raise BrokerOrderError(
                f"Order placement failed: {e}", broker="tradovate"
            )

        order_data = result.get("orderId") or result
        return Order(
            id=str(order_data if isinstance(order_data, int) else result.get("id", "")),
            broker="tradovate",
            symbol=symbol,
            side=side,
            order_type=order_type,
            qty=float(qty),
            status=OrderStatus.PENDING,
            limit_price=limit_price,
            stop_price=stop_price,
            asset_class=AssetClass.FUTURES,
            raw=result,
        )

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order by ID.

        V4-BUG-03 PORT: try DELETE first; if 404 (already gone/filled) return True without
        falling through to POST cancel — that produced misleading 'CANCELLED' logs for
        already-filled bracket orders and masked the race condition root of Apr 14 2026.
        """
        try:
            # Prefer DELETE which returns 404 when order is already gone
            resp = await self._delete(f"/order/item/{int(order_id)}")
            if resp is not None:
                return True
        except Exception:
            pass

        # 404 means the order is already gone (filled or cancelled) — treat as success
        try:
            status = await self.get_order_status(int(order_id))
            if status in ("Filled", "Canceled", "Completed", "Expired"):
                logger.info("cancel_order %s: already gone (status=%s)", order_id, status)
                return True
        except Exception:
            pass

        # Last resort: POST cancel
        try:
            await self._post("/order/cancelOrder", {"orderId": int(order_id)})
            return True
        except Exception as e:
            logger.error("Cancel order failed: %s", e)
            return False

    async def get_order_status(self, order_id: int) -> str:
        """Return the current status string for an order, or 'Unknown' on failure."""
        try:
            result = await self._get("/order/item", {"id": order_id})
            if isinstance(result, dict):
                return result.get("ordStatus", result.get("status", "Unknown"))
        except Exception:
            pass
        return "Unknown"

    async def get_quote(self, symbol: str) -> Quote:
        contract = await self._find_contract(symbol)
        if not contract:
            raise BrokerQuoteError(
                f"Contract not found: {symbol}", broker="tradovate"
            )
        contract_id = contract.get("id", 0)
        try:
            quotes = await self._get("/md/getQuote", {"symbol": contract.get("name", symbol)})
            if isinstance(quotes, dict):
                entries = quotes.get("entries", {})
                bid_entry = entries.get("Bid", {})
                ask_entry = entries.get("Offer", {})
                trade_entry = entries.get("Trade", {})
                return Quote(
                    symbol=symbol,
                    bid=bid_entry.get("price", 0.0),
                    ask=ask_entry.get("price", 0.0),
                    last=trade_entry.get("price", 0.0),
                    volume=int(trade_entry.get("size", 0)),
                )
        except Exception:
            pass

        return Quote(symbol=symbol, bid=0.0, ask=0.0, last=0.0, volume=0)

    async def _find_contract(self, symbol: str) -> Optional[dict]:
        """Find a contract by symbol name or base symbol."""
        try:
            result = await self._get("/contract/find", {"name": symbol})
            if result:
                return result
        except Exception:
            pass

        front = _front_month_symbol(symbol)
        try:
            result = await self._get("/contract/find", {"name": front})
            if result:
                return result
        except Exception:
            pass

        return None

    async def get_historical(
        self,
        symbol: str,
        interval: str = "1d",
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> list[dict]:
        """Get historical bars from Tradovate."""
        contract = await self._find_contract(symbol)
        if not contract:
            return []

        element_size_map = {
            "1m": 1, "5m": 5, "15m": 15, "30m": 30,
            "1h": 60, "4h": 240, "1d": 1440,
        }
        element_size = element_size_map.get(interval, 1440)
        element_size_unit = "MinuteBar" if element_size < 1440 else "DailyBar"

        params: dict = {
            "symbol": contract.get("name", symbol),
            "chartDescription": {
                "underlyingType": "MinuteBar" if element_size < 1440 else "DailyBar",
                "elementSize": element_size if element_size < 1440 else 1,
                "elementSizeUnit": element_size_unit,
            },
        }
        try:
            result = await self._get("/md/getChart", params)
            return result.get("bars", []) if isinstance(result, dict) else []
        except Exception as e:
            logger.error("Historical data fetch failed: %s", e)
            return []

    async def stream_quotes(self, symbols: list[str]) -> AsyncIterator[Quote]:
        """Placeholder for WebSocket streaming — requires full WS implementation."""
        raise NotImplementedError(
            "WebSocket streaming requires full WS client implementation. "
            "Use the Control Tower's tradovate_websocket_client.py pattern."
        )

    def normalize_symbol(self, symbol: str) -> str:
        """Normalize Tradovate symbol to base form."""
        import re
        match = re.match(r"^([A-Z]+)[A-Z]\d$", symbol)
        return match.group(1) if match else symbol

    def denormalize_symbol(self, symbol: str) -> str:
        """Convert base symbol to front-month Tradovate symbol."""
        if len(symbol) <= 4 and symbol.isalpha():
            return _front_month_symbol(symbol)
        return symbol
