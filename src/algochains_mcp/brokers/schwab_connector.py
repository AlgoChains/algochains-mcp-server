"""
schwab_connector.py — Charles Schwab Broker Connector
=======================================================

Schwab acquired TD Ameritrade; their API is now at api.schwabapi.com.

OAuth 2.0 flow (not PKCE — uses HTTP Basic Auth for client credentials):
  1. User authorizes at https://api.schwabapi.com/v1/oauth/authorize
  2. Exchange code at https://api.schwabapi.com/v1/oauth/token
  3. Access token valid 30 minutes; refresh token valid 7 days

Required env vars:
  SCHWAB_CLIENT_ID      — App key from developer.schwab.com
  SCHWAB_CLIENT_SECRET  — App secret from developer.schwab.com

API reference: https://developer.schwab.com/products/trader-api--individual-

No synthetic fills. No mock data. Every method calls the real Schwab API.
"""
from __future__ import annotations

import base64
import logging
import os
from typing import Any, Optional

import httpx

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

logger = logging.getLogger("algochains_mcp.brokers.schwab")

SCHWAB_API_BASE = "https://api.schwabapi.com/trader/v1"
SCHWAB_MARKET_DATA_BASE = "https://api.schwabapi.com/marketdata/v1"
_TIMEOUT = httpx.Timeout(20.0, connect=5.0)


class SchwabConnector(BrokerConnector):
    """
    Charles Schwab broker connector.

    Uses the Schwab Trader API (successor to TD Ameritrade).
    Supports equities, options, ETFs, and mutual funds.
    Does NOT support futures directly (Schwab futures are on a separate platform).
    """

    name = "schwab"
    supported_asset_classes = [
        AssetClass.STOCK,
        AssetClass.OPTIONS,
    ]

    def __init__(
        self,
        access_token: str = "",
        account_hash: str = "",
        paper: bool = False,
    ):
        self._access_token = access_token or os.getenv("SCHWAB_ACCESS_TOKEN", "")
        self._account_hash = account_hash or os.getenv("SCHWAB_ACCOUNT_HASH", "")
        self._paper = paper
        self._client: Optional[httpx.AsyncClient] = None

    def _auth_header(self) -> dict:
        return {"Authorization": f"Bearer {self._access_token}"}

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=_TIMEOUT)
        return self._client

    async def connect(self) -> bool:
        if not self._access_token:
            logger.error(
                "Schwab: no access token. Set SCHWAB_ACCESS_TOKEN or complete OAuth flow "
                "via generate_auth_url(broker='schwab')"
            )
            return False
        try:
            # Validate by fetching accounts list
            client = await self._ensure_client()
            resp = await client.get(
                f"{SCHWAB_API_BASE}/accounts",
                headers=self._auth_header(),
            )
            if resp.status_code == 200:
                accounts = resp.json()
                if accounts and not self._account_hash:
                    # Auto-select first account
                    self._account_hash = accounts[0].get("hashValue", "")
                    logger.info("Schwab: auto-selected account hash %s", self._account_hash[:8])
                logger.info("Schwab connected, %d account(s)", len(accounts))
                return True
            elif resp.status_code == 401:
                logger.error("Schwab: access token expired or invalid (401). Re-run OAuth flow.")
                return False
            else:
                logger.error("Schwab connect failed %s: %s", resp.status_code, resp.text[:200])
                return False
        except Exception as e:
            logger.error("Schwab connect error: %s", e)
            return False

    async def disconnect(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def get_account(self) -> AccountInfo:
        if not self._account_hash:
            raise RuntimeError("Schwab: no account hash — call connect() first")
        client = await self._ensure_client()
        resp = await client.get(
            f"{SCHWAB_API_BASE}/accounts/{self._account_hash}",
            headers=self._auth_header(),
            params={"fields": "positions"},
        )
        resp.raise_for_status()
        data = resp.json()
        acct = data.get("securitiesAccount", data)
        balances = acct.get("currentBalances", {})
        return AccountInfo(
            broker="schwab",
            account_id=self._account_hash,
            equity=float(balances.get("liquidationValue", 0)),
            cash=float(balances.get("cashBalance", 0)),
            buying_power=float(balances.get("buyingPower", 0)),
            currency="USD",
            paper=self._paper,
            asset_classes=["stock", "options", "etf"],
            raw=acct,
        )

    async def get_positions(self) -> list[Position]:
        if not self._account_hash:
            raise RuntimeError("Schwab: not connected")
        client = await self._ensure_client()
        resp = await client.get(
            f"{SCHWAB_API_BASE}/accounts/{self._account_hash}",
            headers=self._auth_header(),
            params={"fields": "positions"},
        )
        resp.raise_for_status()
        data = resp.json()
        raw_positions = data.get("securitiesAccount", {}).get("positions", [])
        positions = []
        for p in raw_positions:
            instr = p.get("instrument", {})
            qty = float(p.get("longQuantity", 0)) - float(p.get("shortQuantity", 0))
            positions.append(Position(
                symbol=instr.get("symbol", ""),
                qty=qty,
                avg_price=float(p.get("averagePrice", 0)),
                market_value=float(p.get("marketValue", 0)),
                unrealized_pnl=float(p.get("currentDayProfitLoss", 0)),
                asset_class=instr.get("assetType", "EQUITY").lower(),
                raw=p,
            ))
        return positions

    async def get_orders(self, status: Optional[str] = None) -> list[Order]:
        if not self._account_hash:
            raise RuntimeError("Schwab: not connected")
        client = await self._ensure_client()
        params: dict[str, str] = {"maxResults": "250"}
        if status:
            params["status"] = status.upper()
        resp = await client.get(
            f"{SCHWAB_API_BASE}/accounts/{self._account_hash}/orders",
            headers=self._auth_header(),
            params=params,
        )
        resp.raise_for_status()
        raw_orders = resp.json()
        orders = []
        for o in raw_orders:
            leg = o.get("orderLegCollection", [{}])[0] if o.get("orderLegCollection") else {}
            instr = leg.get("instrument", {})
            orders.append(Order(
                order_id=str(o.get("orderId", "")),
                symbol=instr.get("symbol", ""),
                side=OrderSide.BUY if leg.get("instruction", "").upper().startswith("BUY") else OrderSide.SELL,
                qty=float(o.get("quantity", 0)),
                filled_qty=float(o.get("filledQuantity", 0)),
                order_type=OrderType.MARKET if o.get("orderType") == "MARKET" else OrderType.LIMIT,
                limit_price=float(o.get("price", 0)) if o.get("price") else None,
                status=OrderStatus.FILLED if o.get("status") == "FILLED" else OrderStatus.PENDING,
                broker="schwab",
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
        if not self._account_hash:
            raise RuntimeError("Schwab: not connected")

        instruction = "BUY" if side == OrderSide.BUY else "SELL"
        order_type_str = {
            OrderType.MARKET: "MARKET",
            OrderType.LIMIT: "LIMIT",
            OrderType.STOP: "STOP",
            OrderType.STOP_LIMIT: "STOP_LIMIT",
        }.get(order_type, "MARKET")

        tif_map = {"day": "DAY", "gtc": "GOOD_TILL_CANCEL", "ioc": "FILL_OR_KILL"}
        tif = tif_map.get(time_in_force.lower(), "DAY")

        payload: dict[str, Any] = {
            "orderType": order_type_str,
            "session": "NORMAL",
            "duration": tif,
            "orderStrategyType": "SINGLE",
            "orderLegCollection": [
                {
                    "instruction": instruction,
                    "quantity": qty,
                    "instrument": {"symbol": symbol, "assetType": "EQUITY"},
                }
            ],
        }
        if limit_price and order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT):
            payload["price"] = str(round(limit_price, 2))
        if stop_price and order_type in (OrderType.STOP, OrderType.STOP_LIMIT):
            payload["stopPrice"] = str(round(stop_price, 2))

        client = await self._ensure_client()
        resp = await client.post(
            f"{SCHWAB_API_BASE}/accounts/{self._account_hash}/orders",
            headers={**self._auth_header(), "Content-Type": "application/json"},
            json=payload,
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Schwab place_order failed {resp.status_code}: {resp.text[:300]}")

        # Schwab returns the order ID in the Location header
        order_id = resp.headers.get("Location", "").split("/")[-1]
        return Order(
            order_id=order_id,
            symbol=symbol,
            side=side,
            qty=qty,
            filled_qty=0.0,
            order_type=order_type,
            limit_price=limit_price,
            status=OrderStatus.PENDING,
            broker="schwab",
        )

    async def cancel_order(self, order_id: str) -> bool:
        if not self._account_hash:
            raise RuntimeError("Schwab: not connected")
        client = await self._ensure_client()
        resp = await client.delete(
            f"{SCHWAB_API_BASE}/accounts/{self._account_hash}/orders/{order_id}",
            headers=self._auth_header(),
        )
        if resp.status_code in (200, 204):
            logger.info("Schwab order %s cancelled", order_id)
            return True
        logger.error("Schwab cancel failed %s: %s", resp.status_code, resp.text[:200])
        return False

    async def get_quote(self, symbol: str) -> Quote:
        client = await self._ensure_client()
        resp = await client.get(
            f"{SCHWAB_MARKET_DATA_BASE}/quotes",
            headers=self._auth_header(),
            params={"symbols": symbol, "fields": "quote,reference"},
        )
        resp.raise_for_status()
        data = resp.json()
        q = data.get(symbol, {}).get("quote", {})
        if not q:
            raise RuntimeError(f"Schwab: no quote data for {symbol}")
        return Quote(
            symbol=symbol,
            bid=float(q.get("bidPrice", 0)),
            ask=float(q.get("askPrice", 0)),
            last=float(q.get("lastPrice", 0)),
            volume=int(q.get("totalVolume", 0)),
            change=float(q.get("netChange", 0)),
            change_pct=float(q.get("netPercentChange", 0)),
            raw=q,
        )

    async def get_options_chain(
        self,
        symbol: str,
        expiration_date: Optional[str] = None,
        strike_count: int = 10,
    ) -> dict[str, Any]:
        """Get options chain for a symbol."""
        client = await self._ensure_client()
        params: dict[str, Any] = {
            "symbol": symbol,
            "strikeCount": strike_count,
            "includeUnderlyingQuote": True,
        }
        if expiration_date:
            params["expirationDate"] = expiration_date
        resp = await client.get(
            f"{SCHWAB_MARKET_DATA_BASE}/chains",
            headers=self._auth_header(),
            params=params,
        )
        resp.raise_for_status()
        return resp.json()
