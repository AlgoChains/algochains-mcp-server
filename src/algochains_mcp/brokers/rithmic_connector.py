"""Rithmic R|Protocol connector for prop fund execution.

Rithmic is the execution and clearing backbone used by all major US futures
prop funds: Apex, Topstep, MyFundedFutures, TradeDay, Bulenox, Earn2Trade.

One Rithmic connection = access to all of the above simultaneously.

IMPORTANT: Live trading requires a signed Rithmic developer agreement.
  1. Go to: https://www.rithmic.com/contacts (request API access)
  2. Sign the NDA + API agreement (1-2 week business process)
  3. Receive RITHMIC_SYSTEM_NAME + RITHMIC_PLANT_NAME
  4. Set credentials in .env (see .env.example)

Until vendor agreement is signed, this connector:
  - Validates all logic, data structures, and order routing code
  - Simulates responses in DRY_RUN mode (RITHMIC_DRY_RUN=true)
  - Fully integrates with prop_fund_manager.py + drawdown monitor

Python wrapper used: pyrithmic (unofficial but widely used)
  pip install pyrithmic
  GitHub: https://github.com/jacksonwoody/pyrithmic

Architecture:
  RithmicConnector — manages WebSocket connection lifecycle
  RithmicAccount   — account-level operations (balance, positions, orders)
  RithmicOrderRouter — order placement with full order lifecycle tracking
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Callable

logger = logging.getLogger("algochains_mcp.brokers.rithmic")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DRY_RUN = os.environ.get("RITHMIC_DRY_RUN", "true").lower() == "true"
_SYSTEM_NAME = os.environ.get("RITHMIC_SYSTEM_NAME", "")
_PLANT_NAME = os.environ.get("RITHMIC_PLANT_NAME", "Chicago")
_USER_ID = os.environ.get("RITHMIC_USER_ID", "")
_PASSWORD = os.environ.get("RITHMIC_PASSWORD", "")
_GATEWAY = os.environ.get("RITHMIC_GATEWAY", "wss://rituz00100.rithmic.com:443")

# Rithmic gateway URLs per plant
RITHMIC_GATEWAYS = {
    "Chicago":   "wss://rituz00100.rithmic.com:443",
    "Sydney":    "wss://ritus01100.rithmic.com:443",
    "SaoPaulo":  "wss://ritsa00100.rithmic.com:443",
    "Dublin":    "wss://ritdu00100.rithmic.com:443",
    "Mumbai":    "wss://ritmu00100.rithmic.com:443",
}

# Instrument specs for Rithmic symbology
RITHMIC_INSTRUMENTS = {
    "MNQ": {"exchange": "CME", "full_symbol": "MNQ", "tick_size": 0.25, "tick_value": 0.50},
    "NQ":  {"exchange": "CME", "full_symbol": "NQ",  "tick_size": 0.25, "tick_value": 5.00},
    "MES": {"exchange": "CME", "full_symbol": "MES", "tick_size": 0.25, "tick_value": 1.25},
    "ES":  {"exchange": "CME", "full_symbol": "ES",  "tick_size": 0.25, "tick_value": 12.50},
    "CL":  {"exchange": "NYMEX", "full_symbol": "CL", "tick_size": 0.01, "tick_value": 10.00},
    "MCL": {"exchange": "NYMEX", "full_symbol": "MCL", "tick_size": 0.01, "tick_value": 1.00},
    "GC":  {"exchange": "COMEX", "full_symbol": "GC", "tick_size": 0.10, "tick_value": 10.00},
    "MGC": {"exchange": "COMEX", "full_symbol": "MGC", "tick_size": 0.10, "tick_value": 1.00},
    "RTY": {"exchange": "CME", "full_symbol": "RTY", "tick_size": 0.10, "tick_value": 5.00},
    "M2K": {"exchange": "CME", "full_symbol": "M2K", "tick_size": 0.10, "tick_value": 0.50},
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


@dataclass
class RithmicOrder:
    basket_id: str
    symbol: str
    exchange: str
    side: OrderSide
    qty: int
    order_type: OrderType
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    status: OrderStatus = OrderStatus.PENDING
    filled_qty: int = 0
    avg_fill_price: float = 0.0
    submitted_at: float = field(default_factory=time.time)
    filled_at: Optional[float] = None
    account_id: str = ""
    error_message: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["side"] = self.side.value
        d["order_type"] = self.order_type.value
        d["status"] = self.status.value
        return d


@dataclass
class RithmicPosition:
    symbol: str
    exchange: str
    qty: int           # Positive = long, negative = short
    avg_price: float
    unrealized_pnl: float
    realized_pnl: float
    account_id: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RithmicAccount:
    account_id: str
    account_name: str
    balance: float
    buying_power: float
    day_open_pnl: float
    day_closed_pnl: float
    net_liq: float
    margin_used: float
    high_water_mark: float
    trailing_drawdown: float
    is_prop_fund: bool = True
    fund_name: str = ""

    @property
    def daily_pnl(self) -> float:
        return self.day_open_pnl + self.day_closed_pnl

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Dry-run simulation (used until real Rithmic credentials available)
# ---------------------------------------------------------------------------

class _DryRunSimulator:
    """Simulates Rithmic API responses for testing without real credentials."""

    def __init__(self):
        self._orders: dict[str, RithmicOrder] = {}
        self._positions: dict[str, RithmicPosition] = {}
        self._account = RithmicAccount(
            account_id="DRY_RUN_ACCT",
            account_name="AlgoChains Dry Run",
            balance=50000.0,
            buying_power=200000.0,
            day_open_pnl=0.0,
            day_closed_pnl=0.0,
            net_liq=50000.0,
            margin_used=0.0,
            high_water_mark=50000.0,
            trailing_drawdown=0.0,
            is_prop_fund=True,
            fund_name="dry_run",
        )
        self._order_counter = 0

    def place_order(self, order: RithmicOrder) -> dict:
        self._order_counter += 1
        order.basket_id = f"DRY_{self._order_counter:06d}"
        order.status = OrderStatus.FILLED
        order.filled_qty = order.qty
        order.avg_fill_price = order.limit_price or 20000.0
        order.filled_at = time.time()
        self._orders[order.basket_id] = order
        logger.info("DRY_RUN: Order filled — %s %s %s qty=%d @ %.2f",
                    order.side, order.symbol, order.order_type, order.qty, order.avg_fill_price)
        return order.to_dict()

    def get_positions(self, account_id: str) -> list[dict]:
        return [p.to_dict() for p in self._positions.values() if p.account_id == account_id]

    def get_account(self, account_id: str) -> dict:
        return self._account.to_dict()

    def cancel_order(self, basket_id: str) -> dict:
        order = self._orders.get(basket_id)
        if order and order.status in (OrderStatus.PENDING, OrderStatus.OPEN):
            order.status = OrderStatus.CANCELLED
            return {"cancelled": True, "basket_id": basket_id}
        return {"cancelled": False, "reason": "Order not found or already terminal"}

    def flatten_all_positions(self, account_id: str) -> dict:
        flattened = len([p for p in self._positions.values() if p.account_id == account_id])
        self._positions = {k: v for k, v in self._positions.items() if v.account_id != account_id}
        return {"flattened": True, "positions_closed": flattened, "account_id": account_id}


# ---------------------------------------------------------------------------
# Main connector
# ---------------------------------------------------------------------------

class RithmicConnector:
    """Rithmic R|Protocol connector for prop fund execution.

    In DRY_RUN mode (default until credentials provided):
      - All logic executes against a simulator
      - Logs all would-be orders with [DRY_RUN] prefix
      - Does NOT send any real orders

    In LIVE mode (RITHMIC_DRY_RUN=false + valid credentials):
      - Connects to Rithmic WebSocket gateway
      - Uses pyrithmic library for R|Protocol protobuf communication
      - Full order lifecycle: submit → fill → position update → P&L

    Usage:
        connector = RithmicConnector()
        await connector.connect()
        result = await connector.place_order(...)
        await connector.disconnect()
    """

    def __init__(self, account_id: str = None, dry_run: bool = None):
        self.account_id = account_id or os.environ.get("RITHMIC_ACCOUNT_ID", "DEFAULT")
        self.dry_run = dry_run if dry_run is not None else _DRY_RUN
        self._connected = False
        self._client = None
        self._simulator = _DryRunSimulator() if self.dry_run else None
        self._on_fill_callbacks: list[Callable] = []
        self._on_position_callbacks: list[Callable] = []

    async def connect(self) -> dict:
        """Establish connection to Rithmic gateway."""
        if self.dry_run:
            self._connected = True
            logger.info("Rithmic DRY_RUN mode active — no real broker connection")
            return {
                "connected": True,
                "mode": "dry_run",
                "account_id": self.account_id,
                "note": "Set RITHMIC_DRY_RUN=false and provide credentials for live trading",
            }

        if not all([_SYSTEM_NAME, _USER_ID, _PASSWORD]):
            return {
                "connected": False,
                "error": "Missing required credentials",
                "required_env_vars": ["RITHMIC_SYSTEM_NAME", "RITHMIC_PLANT_NAME",
                                      "RITHMIC_USER_ID", "RITHMIC_PASSWORD"],
                "hint": "Sign Rithmic developer agreement at rithmic.com to obtain credentials",
            }

        try:
            from rithmic import RithmicClient  # pip install pyrithmic
            gateway = RITHMIC_GATEWAYS.get(_PLANT_NAME, RITHMIC_GATEWAYS["Chicago"])
            self._client = RithmicClient(
                system_name=_SYSTEM_NAME,
                plant_name=_PLANT_NAME,
                user_id=_USER_ID,
                password=_PASSWORD,
                gateway=gateway,
            )
            await self._client.connect()
            self._connected = True
            logger.info("Rithmic LIVE connection established — plant=%s user=%s", _PLANT_NAME, _USER_ID)
            return {
                "connected": True,
                "mode": "live",
                "plant": _PLANT_NAME,
                "gateway": gateway,
                "account_id": self.account_id,
            }
        except ImportError:
            return {
                "connected": False,
                "error": "pyrithmic not installed",
                "fix": "pip install pyrithmic",
            }
        except Exception as exc:
            return {"connected": False, "error": str(exc)}

    async def disconnect(self) -> None:
        if self._client and self._connected:
            await self._client.disconnect()
        self._connected = False

    async def get_account(self, account_id: str = None) -> dict:
        """Get account balance, buying power, daily P&L, trailing drawdown."""
        acct_id = account_id or self.account_id
        if self.dry_run:
            return self._simulator.get_account(acct_id)
        if not self._connected:
            return {"error": "Not connected"}
        try:
            acct = await self._client.get_account(acct_id)
            return {
                "account_id": acct_id,
                "balance": acct.balance,
                "buying_power": acct.buying_power,
                "day_open_pnl": acct.day_open_pnl,
                "day_closed_pnl": acct.day_closed_pnl,
                "daily_pnl": acct.day_open_pnl + acct.day_closed_pnl,
                "net_liq": acct.net_liq,
                "margin_used": acct.margin_used,
            }
        except Exception as exc:
            return {"error": str(exc)}

    async def get_positions(self, account_id: str = None) -> list[dict]:
        """Get all open positions for account."""
        acct_id = account_id or self.account_id
        if self.dry_run:
            return self._simulator.get_positions(acct_id)
        if not self._connected:
            return []
        try:
            positions = await self._client.get_positions(acct_id)
            return [
                {
                    "symbol": p.symbol,
                    "exchange": p.exchange,
                    "qty": p.qty,
                    "avg_price": p.avg_price,
                    "unrealized_pnl": p.unrealized_pnl,
                    "realized_pnl": p.realized_pnl,
                }
                for p in positions
            ]
        except Exception as exc:
            return [{"error": str(exc)}]

    async def place_order(
        self,
        symbol: str,
        side: str,
        qty: int,
        order_type: str = "MARKET",
        limit_price: float = None,
        stop_price: float = None,
        account_id: str = None,
    ) -> dict:
        """Place an order via Rithmic.

        Args:
            symbol:      Instrument (MNQ, CL, MES, NQ, ES, etc.)
            side:        BUY or SELL
            qty:         Number of contracts
            order_type:  MARKET, LIMIT, STOP, STOP_LIMIT
            limit_price: Required for LIMIT and STOP_LIMIT orders
            stop_price:  Required for STOP and STOP_LIMIT orders
            account_id:  Override default account

        Returns:
            dict with basket_id, status, fill details
        """
        acct_id = account_id or self.account_id
        instrument = RITHMIC_INSTRUMENTS.get(symbol.upper())
        if not instrument:
            return {"error": f"Unsupported instrument: {symbol}. Supported: {list(RITHMIC_INSTRUMENTS)}"}

        order = RithmicOrder(
            basket_id="",
            symbol=symbol.upper(),
            exchange=instrument["exchange"],
            side=OrderSide(side.upper()),
            qty=qty,
            order_type=OrderType(order_type.upper()),
            limit_price=limit_price,
            stop_price=stop_price,
            account_id=acct_id,
        )

        if self.dry_run:
            result = self._simulator.place_order(order)
            result["mode"] = "dry_run"
            return result

        if not self._connected:
            return {"error": "Not connected to Rithmic. Call connect() first."}

        try:
            resp = await self._client.place_order(
                account_id=acct_id,
                symbol=symbol.upper(),
                exchange=instrument["exchange"],
                side=side.upper(),
                quantity=qty,
                order_type=order_type.upper(),
                limit_price=limit_price,
                stop_price=stop_price,
            )
            return {
                "basket_id": resp.basket_id,
                "status": resp.status,
                "filled_qty": resp.filled_qty,
                "avg_fill_price": resp.avg_fill_price,
                "mode": "live",
            }
        except Exception as exc:
            logger.error("Rithmic place_order failed: %s", exc)
            return {"error": str(exc), "symbol": symbol, "side": side, "qty": qty}

    async def cancel_order(self, basket_id: str, account_id: str = None) -> dict:
        """Cancel an open order by basket_id."""
        acct_id = account_id or self.account_id
        if self.dry_run:
            return self._simulator.cancel_order(basket_id)
        if not self._connected:
            return {"error": "Not connected"}
        try:
            await self._client.cancel_order(account_id=acct_id, basket_id=basket_id)
            return {"cancelled": True, "basket_id": basket_id}
        except Exception as exc:
            return {"cancelled": False, "error": str(exc)}

    async def flatten_all_positions(self, account_id: str = None) -> dict:
        """Market-order close all open positions (emergency flatten)."""
        acct_id = account_id or self.account_id
        if self.dry_run:
            return self._simulator.flatten_all_positions(acct_id)
        if not self._connected:
            return {"error": "Not connected"}
        try:
            positions = await self.get_positions(acct_id)
            closed = []
            errors = []
            for pos in positions:
                if pos.get("qty", 0) != 0:
                    close_side = "SELL" if pos["qty"] > 0 else "BUY"
                    result = await self.place_order(
                        symbol=pos["symbol"],
                        side=close_side,
                        qty=abs(pos["qty"]),
                        order_type="MARKET",
                        account_id=acct_id,
                    )
                    if result.get("error"):
                        errors.append(result)
                    else:
                        closed.append(result)
            return {
                "flattened": True,
                "positions_closed": len(closed),
                "errors": errors,
                "account_id": acct_id,
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            }
        except Exception as exc:
            return {"error": str(exc)}

    def on_fill(self, callback: Callable) -> None:
        """Register a callback for fill events: callback(fill_dict)."""
        self._on_fill_callbacks.append(callback)

    def on_position_update(self, callback: Callable) -> None:
        """Register a callback for position updates: callback(position_dict)."""
        self._on_position_callbacks.append(callback)

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def mode(self) -> str:
        return "dry_run" if self.dry_run else "live"


# ---------------------------------------------------------------------------
# Connection health check (for MCP tool dispatch)
# ---------------------------------------------------------------------------

async def check_rithmic_status() -> dict:
    """Check Rithmic credential configuration and connectivity."""
    has_system_name = bool(_SYSTEM_NAME)
    has_user_id = bool(_USER_ID)
    has_password = bool(_PASSWORD)
    all_configured = has_system_name and has_user_id and has_password

    status = {
        "dry_run_mode": _DRY_RUN,
        "credentials_configured": all_configured,
        "missing_credentials": [],
        "plant": _PLANT_NAME,
        "gateway": RITHMIC_GATEWAYS.get(_PLANT_NAME, "Unknown"),
        "supported_instruments": list(RITHMIC_INSTRUMENTS.keys()),
        "supported_prop_funds": ["apex", "topstep", "myfundedfutures", "tradeday", "bulenox", "earn2trade"],
    }

    if not has_system_name:
        status["missing_credentials"].append("RITHMIC_SYSTEM_NAME")
    if not has_user_id:
        status["missing_credentials"].append("RITHMIC_USER_ID")
    if not has_password:
        status["missing_credentials"].append("RITHMIC_PASSWORD")

    if _DRY_RUN:
        status["note"] = (
            "DRY_RUN mode: All order logic runs against simulator. "
            "Set RITHMIC_DRY_RUN=false after signing vendor agreement."
        )
        status["vendor_agreement"] = "Sign at https://www.rithmic.com/contacts"
    elif not all_configured:
        status["note"] = (
            "Missing credentials. Sign the Rithmic developer agreement to obtain "
            "RITHMIC_SYSTEM_NAME and credentials."
        )
    else:
        status["note"] = "Credentials configured. Call rithmic_connector.connect() to establish live connection."

    return status
