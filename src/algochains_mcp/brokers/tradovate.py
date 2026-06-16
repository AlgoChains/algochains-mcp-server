"""
Tradovate futures broker connector — ported from Control Tower patterns.

Auth: OAuth2 REST only (Token Guardian pattern)
WebSocket: NOT implemented here — live bots own the WS via control tower.
Assets: Futures (MNQ, MES, NQ, ES, CL, GC, etc.)
Token: Uses Token Guardian pattern — NEVER use tradovate_token_auto_refresh.py
Symbology: Use continuous contract format (e.g. MNQZ5 for front-month)
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional

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


def _jwt_expiry_epoch(access_token: str) -> float | None:
    """Return the JWT exp claim as an epoch timestamp, if present and parseable."""
    token = access_token.strip()
    if token.startswith("Bearer "):
        token = token.removeprefix("Bearer ").strip()

    parts = token.split(".")
    if len(parts) != 3:
        return None

    try:
        import base64
        import binascii
        import json

        payload_segment = parts[1] + "=" * (-len(parts[1]) % 4)
        payload_bytes = base64.urlsafe_b64decode(payload_segment.encode("ascii"))
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (ValueError, TypeError, UnicodeDecodeError, binascii.Error):
        return None

    exp = payload.get("exp")
    if isinstance(exp, bool):
        return None
    if isinstance(exp, (int, float)):
        return float(exp)
    if isinstance(exp, str):
        try:
            return float(exp)
        except ValueError:
            return None
    return None


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


def _optional_float(value: object) -> float | None:
    """Convert broker numeric fields without treating JSON null as a real zero."""
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _first_number(row: dict, keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _optional_float(row.get(key))
        if value is not None:
            return value
    return None


def _positive_float(value: object) -> float | None:
    parsed = _optional_float(value)
    if parsed is None or parsed <= 0:
        return None
    return parsed


def _quote_rows(payload: object) -> list[dict]:
    """Extract quote objects from REST/WebSocket-shaped Tradovate payloads."""
    rows: list[dict] = []

    def _collect(value: object) -> None:
        if isinstance(value, list):
            for item in value:
                _collect(item)
            return
        if not isinstance(value, dict):
            return
        if isinstance(value.get("entries"), dict):
            rows.append(value)
        for key in ("d", "quotes", "data"):
            nested = value.get(key)
            if isinstance(nested, (dict, list)):
                _collect(nested)

    _collect(payload)
    return rows


def _quote_entry_price(entries: dict, name: str) -> float | None:
    entry = entries.get(name)
    if not isinstance(entry, dict):
        return None
    return _positive_float(entry.get("price"))


def _quote_entry_size(entries: dict, name: str) -> int | None:
    entry = entries.get(name)
    if not isinstance(entry, dict):
        return None
    size = _optional_float(entry.get("size"))
    if size is None or size < 0:
        return None
    return int(size)


def _quote_timestamp(row: dict) -> datetime | None:
    timestamp = row.get("timestamp")
    if not isinstance(timestamp, str) or not timestamp:
        return None
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_tradovate_quote(symbol: str, payload: object) -> Quote | None:
    for row in _quote_rows(payload):
        entries = row.get("entries")
        if not isinstance(entries, dict):
            continue
        bid = _quote_entry_price(entries, "Bid")
        ask = _quote_entry_price(entries, "Offer")
        trade = _quote_entry_price(entries, "Trade")
        if trade is not None:
            last = trade
        elif bid is not None and ask is not None:
            last = (bid + ask) / 2
        else:
            last = bid or ask
        if last is None:
            continue
        return Quote(
            symbol=symbol,
            bid=bid or last,
            ask=ask or last,
            last=last,
            volume=_quote_entry_size(entries, "Trade")
            or _quote_entry_size(entries, "TotalTradeVolume")
            or 0,
            timestamp=_quote_timestamp(row),
        )
    return None


class TradovateConnector(BrokerConnector):
    """Tradovate futures connector — REST-only (OAuth2 via Token Guardian pattern).

    WebSocket streaming is intentionally not implemented here.  Live bots own
    the persistent WebSocket connection via tradovate_websocket_client.py in the
    control tower.  This MCP connector provides read-only market data and
    execution via Tradovate REST endpoints, using a pre-existing Token Guardian
    access token when available to avoid creating a second OAuth session that
    would race with the guardian.

    Token priority (see connect()):
      1. TRADOVATE_ACCESS_TOKEN env var (written by tradovate_token_guardian.py)
      2. Full OAuth username/password flow (fallback when no guardian token)
      3. Legacy CID+secret fallback (backwards compat)

    NEVER run github.com/0xjmp/mcp-tradovate alongside this connector for live
    trading — it creates a competing OAuth session.  See docs/TRADOVATE_PARITY.md.
    """

    name = "tradovate"
    supported_asset_classes = [AssetClass.FUTURES]

    def __init__(self, config: TradovateConfig):
        self.cfg = config
        self._access_token: str = ""
        self._token_expires_at: float = 0.0
        self._account_id: int = 0
        self._account_spec: str = ""
        self._connected: bool = False
        # Persistent HTTP client — created once, reused for all REST calls.
        # Avoids a new TCP/TLS handshake on every _get/_post/_delete invocation.
        # Closed explicitly in disconnect().
        self._http: httpx.AsyncClient = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )
        # _find_contract TTL cache: {symbol: (expires_at_epoch, contract_dict)}
        # Prevents 2x /contract/find HTTPs per get_quote / place_order / get_historical.
        self._contract_cache: dict[str, tuple[float, dict]] = {}
        self._contract_cache_ttl: float = 300.0  # 5 min

    @property
    def capabilities(self) -> dict:
        return {
            # streaming=False: WebSocket is intentionally NOT implemented in the MCP
            # connector.  Live bots own the WS connection via the control tower.
            # stream_quotes() raises NotImplementedError to enforce this boundary.
            "streaming": False,
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
        """Authenticate via OAuth2 and get account info.

        Auth priority:
          1. Pre-existing TRADOVATE_ACCESS_TOKEN (written by token guardian, shared
             with the live bots). Validated by calling /account/list. If valid, skips
             re-auth entirely — avoids a second OAuth round-trip that would fail when
             the guardian token is the only available credential.
          2. Full username+password OAuth (TRADOVATE_USERNAME / TRADOVATE_PASSWORD /
             TRADOVATE_OAUTH_CLIENT_ID / TRADOVATE_OAUTH_CLIENT_SECRET).
          3. Legacy CID+secret fallback (original MCP auth format — kept for backwards
             compatibility but will fail for Google-SSO accounts).
        """
        # ── Priority 1: use pre-existing access token ────────────────────────
        pretoken = self.cfg.access_token
        if pretoken:
            try:
                r = await self._http.get(
                    f"{self.cfg.base_url}/v1/account/list",
                    headers={"Authorization": f"Bearer {pretoken}"},
                )
                if r.status_code == 200:
                    accounts = r.json()
                    now = time.time()
                    expires_at = _jwt_expiry_epoch(pretoken)
                    self._access_token = pretoken
                    self._token_expires_at = (
                        expires_at if expires_at and expires_at > now else now + 3600
                    )
                    if accounts and isinstance(accounts, list):
                        self._account_id = accounts[0].get("id", 0)
                        self._account_spec = accounts[0].get("name", "")
                    self._connected = True
                    logger.info(
                        "Tradovate connected via pre-existing token: account=%s env=%s",
                        self._account_spec, self.cfg.env,
                    )
                    return True
                else:
                    logger.debug(
                        "Pre-existing token rejected (%s) — falling back to OAuth",
                        r.status_code,
                    )
            except Exception as _pre_err:
                logger.debug("Pre-existing token check failed (%s) — trying OAuth", _pre_err)

        # ── Priority 2: username + password OAuth ────────────────────────────
        username = self.cfg.username
        password = self.cfg.password
        if not username or not password:
            # Legacy: treat CID as username, secret as password
            username = self.cfg.cid
            password = self.cfg.secret

        if not username or not password:
            logger.warning("Tradovate credentials not configured")
            return False

        try:
            resp = await self._http.post(
                f"{self.cfg.base_url}/v1/auth/accesstokenrequest",
                json={
                    "name": username,
                    "password": password,
                    "appId": "AlgoChains",
                    "appVersion": "2.0",
                    "cid": int(self.cfg.oauth_cid) if self.cfg.oauth_cid.isdigit() else 0,
                    "sec": self.cfg.oauth_sec or "",
                },
            )
            if resp.status_code == 401:
                raise BrokerAuthError(
                    "Tradovate authentication failed — check USERNAME/PASSWORD",
                    broker="tradovate",
                )
            resp.raise_for_status()
            data = resp.json()

            self._access_token = data.get("accessToken", "")
            if not self._access_token:
                raise BrokerAuthError(
                    "Tradovate auth response did not include accessToken — "
                    "check USERNAME/PASSWORD and OAUTH_CLIENT_ID/SECRET",
                    broker="tradovate",
                )
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
                "Tradovate connected via OAuth: account=%s env=%s",
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
        await self._http.aclose()
        # Re-instantiate the client so a subsequent connect() doesn't use a
        # closed httpx.AsyncClient (which would crash on the first request).
        self._http = httpx.AsyncClient(timeout=self.cfg.timeout)
        logger.info("Tradovate disconnected")

    async def _request_with_retry(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        payload: Optional[dict] = None,
        max_attempts: int = 3,
    ) -> Any:
        """Authenticated HTTP with exponential backoff on 429/5xx.

        - 401: raise BrokerAuthError immediately (caller reconnects).
        - 404 (DELETE only): return None so callers can treat as 'already gone'.
        - 429: honor Retry-After header (capped at 10s), else expo backoff.
        - 5xx: exponential backoff with jitter, up to max_attempts.
        - network errors: treat like 5xx.
        """
        import random

        await self._ensure_token()
        url = f"{self.cfg.base_url}/v1{path}"
        headers = {"Authorization": f"Bearer {self._access_token}"}
        attempt = 0
        last_exc: Optional[Exception] = None
        while attempt < max_attempts:
            attempt += 1
            try:
                if method == "GET":
                    resp = await self._http.get(url, params=params, headers=headers)
                elif method == "POST":
                    resp = await self._http.post(url, json=payload, headers=headers)
                elif method == "DELETE":
                    resp = await self._http.delete(url, headers=headers)
                else:
                    raise ValueError(f"unsupported method: {method}")
            except (httpx.TransportError, httpx.TimeoutException) as e:
                last_exc = e
                if attempt >= max_attempts:
                    raise BrokerConnectionError(
                        f"Tradovate {method} {path} failed after {attempt} attempts: {e}",
                        broker="tradovate",
                    )
                backoff = min(2 ** attempt + random.uniform(0, 0.25), 8.0)
                logger.warning("tradovate transport error (%s), retry in %.2fs", e, backoff)
                await asyncio.sleep(backoff)
                continue

            if resp.status_code == 401:
                raise BrokerAuthError("Tradovate token expired", broker="tradovate")
            if resp.status_code == 404 and method == "DELETE":
                return None

            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                if attempt >= max_attempts:
                    resp.raise_for_status()
                # Respect Retry-After when present
                retry_after = resp.headers.get("Retry-After")
                try:
                    wait_s = min(float(retry_after), 10.0) if retry_after else 0.0
                except (TypeError, ValueError):
                    wait_s = 0.0
                if wait_s <= 0:
                    wait_s = min(2 ** attempt + random.uniform(0, 0.5), 8.0)
                logger.warning(
                    "tradovate %s %s returned %s; backoff %.2fs (attempt %d/%d)",
                    method, path, resp.status_code, wait_s, attempt, max_attempts,
                )
                await asyncio.sleep(wait_s)
                continue

            resp.raise_for_status()
            try:
                return resp.json()
            except Exception:
                return {}

        # Should not reach here, but defensively
        if last_exc:
            raise BrokerConnectionError(
                f"Tradovate {method} {path} exhausted retries: {last_exc}",
                broker="tradovate",
            )
        return {}

    async def _get(self, path: str, params: Optional[dict] = None) -> Any:
        """Authenticated GET — reuses persistent connection pool with backoff."""
        return await self._request_with_retry("GET", path, params=params)

    async def _post(self, path: str, payload: dict) -> Any:
        """Authenticated POST — reuses persistent connection pool with backoff."""
        return await self._request_with_retry("POST", path, payload=payload)

    async def _delete(self, path: str) -> Any:
        """Authenticated DELETE. Returns None on 404 so callers can distinguish
        'already gone' from genuine errors without try/except."""
        return await self._request_with_retry("DELETE", path)

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

        snapshot: dict = {}
        if isinstance(cash_balances, dict):
            snapshot = cash_balances
        elif isinstance(cash_balances, list) and cash_balances:
            snapshot = cash_balances[0] if isinstance(cash_balances[0], dict) else {}

        equity = _first_number(
            snapshot,
            ("totalCashValue", "netLiq", "netLiquidation", "cashBalance"),
        )
        cash = _first_number(snapshot, ("cashBalance", "totalCashValue", "netLiq", "netLiquidation"))
        if equity is None and cash is None:
            raise BrokerConnectionError(
                "Tradovate cash balance snapshot did not include a numeric balance",
                broker="tradovate",
                details={"account_id": acct.get("id"), "snapshot_keys": sorted(snapshot.keys())},
            )
        if equity is None:
            equity = cash
        if cash is None:
            cash = equity

        return AccountInfo(
            broker="tradovate",
            account_id=str(acct.get("id", "")),
            equity=equity,
            cash=cash,
            buying_power=equity,
            currency="USD",
            paper=self.cfg.env != "live",
            asset_classes=["futures"],
            raw={**acct, "cash_balance_snapshot": snapshot},
        )

    async def get_positions(self) -> list[Position]:
        raw_positions = await self._get("/position/list")
        open_positions = [p for p in (raw_positions or []) if p.get("netPos", 0) != 0]
        if not open_positions:
            return []

        # Batch-resolve contract IDs in parallel — eliminates N+1 HTTP serial calls
        import asyncio as _asyncio
        unique_ids = list({p["contractId"] for p in open_positions if p.get("contractId")})
        contract_tasks = [self._get("/contract/item", {"id": cid}) for cid in unique_ids]
        contract_results = await _asyncio.gather(*contract_tasks, return_exceptions=True)
        contract_map = {
            cid: (res.get("name", str(cid)) if isinstance(res, dict) else str(cid))
            for cid, res in zip(unique_ids, contract_results)
        }

        positions = []
        for pos in open_positions:
            net_qty = pos.get("netPos", 0)
            contract_id = pos.get("contractId", 0)
            symbol = contract_map.get(contract_id, str(contract_id))
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

    async def get_orders(self, status: Optional[str] = None, limit: int = 200) -> list[Order]:
        """Get orders, optionally filtered by status.

        Args:
            status: 'open', 'closed', or None for all
            limit: cap on returned orders (default 200); prevents unbounded
                   payloads for accounts with large order histories
        """
        import asyncio as _asyncio
        raw_orders = await self._get("/order/list")

        # Pre-filter before contract resolution to reduce unnecessary HTTP calls
        filtered_raw = []
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
            filtered_raw.append((o, order_status))

        if not filtered_raw:
            return []

        # Batch-resolve contract IDs in parallel — eliminates N+1 serial HTTP calls
        unique_ids = list({o.get("contractId", 0) for o, _ in filtered_raw if o.get("contractId")})
        contract_tasks = [self._get("/contract/item", {"id": cid}) for cid in unique_ids]
        contract_results = await _asyncio.gather(*contract_tasks, return_exceptions=True)
        contract_map = {
            cid: (res.get("name", str(cid)) if isinstance(res, dict) else str(cid))
            for cid, res in zip(unique_ids, contract_results)
        }

        orders = []
        for o, order_status in filtered_raw:
            contract_id = o.get("contractId", 0)
            symbol = contract_map.get(contract_id, str(contract_id))

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
        # Cap payload size — caller can pass higher limit for historical queries
        if limit and len(orders) > limit:
            logger.warning("get_orders: returning first %d of %d orders (limit=%d)", limit, len(orders), limit)
            orders = orders[:limit]
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

        # H-8 FIX: `result.get("orderId") or result` treats orderId=0 as falsy.
        # Use explicit None checks so a valid id=0 is preserved.
        _oid = result.get("orderId")
        if _oid is None:
            _oid = result.get("id", "")
        return Order(
            id=str(_oid),
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
        except Exception as e:
            logger.warning("cancel_order DELETE /order/item/%s failed: %s", order_id, e)

        # 404 means the order is already gone (filled or cancelled) — treat as success
        try:
            status = await self.get_order_status(int(order_id))
            if status in ("Filled", "Canceled", "Completed", "Expired"):
                logger.info("cancel_order %s: already gone (status=%s)", order_id, status)
                return True
        except Exception as e:
            logger.warning("cancel_order status lookup for %s failed: %s", order_id, e)

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
        except Exception as e:
            logger.warning("get_order_status(%s) failed: %s", order_id, e)
        return "Unknown"

    async def get_fills(
        self,
        order_id: Optional[int] = None,
        symbol: Optional[str] = None,
        since_seconds: int = 60,
    ) -> list[dict]:
        """Get fills, filtered by symbol+timestamp (OSO-safe).

        V8-BUG-04 PORT: OSO orders create parent + child IDs — filtering by parent
        order_id always returns empty because fills are recorded under child IDs.
        Use symbol + recent timestamp window to catch actual fills.

        Args:
            order_id: ignored for OSO orders but kept for API compat
            symbol: contract symbol filter (e.g. "MNQM6")
            since_seconds: how far back to look (default 60s)
        """
        try:
            all_fills = await self._get("/fill/list", {})
            if not isinstance(all_fills, list):
                return []
            if symbol:
                from datetime import datetime, timezone, timedelta  # noqa: PLC0415
                cutoff = datetime.now(timezone.utc) - timedelta(seconds=since_seconds)
                contract = await self._find_contract(symbol)
                target_contract_id = contract.get("id") if contract else None
                filtered = []
                for f in all_fills:
                    if target_contract_id and f.get("contractId") != target_contract_id:
                        continue
                    ts_str = f.get("timestamp", "")
                    if ts_str:
                        try:
                            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                            if ts < cutoff:
                                continue
                        except Exception:
                            # M-3 FIX: unparseable timestamp means we can't verify recency
                            # — exclude the fill rather than including it without a time check.
                            continue
                    filtered.append(f)
                return filtered
            if order_id:
                return [f for f in all_fills if f.get("orderId") == order_id]
            # H-9 FIX: returning all fills when no filter is provided is dangerous
            # (callers expecting a narrow window get the entire fill history).
            # Return empty list; callers must supply symbol or order_id.
            logger.warning("get_fills called with no filters — returning [] to prevent full-history dump")
            return []
        except Exception as e:
            logger.error("get_fills failed: %s", e)
            return []

    async def get_quote(self, symbol: str) -> Quote:
        contract = await self._find_contract(symbol)
        if not contract:
            raise BrokerQuoteError(
                f"Contract not found: {symbol}", broker="tradovate"
            )
        try:
            quotes = await self._get("/md/getQuote", {"symbol": contract.get("name", symbol)})
            quote = _parse_tradovate_quote(symbol, quotes)
            if quote is not None:
                return quote
        except Exception as e:
            logger.warning("get_quote failed for %s: %s", symbol, e)

        # Fail closed when REST does not provide a usable live price. Do not
        # collapse missing/null quote fields to 0.0; futures cannot trade at 0.
        raise BrokerQuoteError(
            f"Quote unavailable for {symbol} — API returned no data",
            broker="tradovate",
        )

    async def _find_contract(self, symbol: str) -> Optional[dict]:
        """Find a contract by symbol name or base symbol.

        Cached for 5 minutes keyed on the raw symbol to avoid a 2x
        /contract/find HTTP round-trip on every get_quote / place_order /
        get_historical call.
        """
        now = time.time()
        cached = self._contract_cache.get(symbol)
        if cached and cached[0] > now:
            return cached[1]

        try:
            result = await self._get("/contract/find", {"name": symbol})
            if result:
                self._contract_cache[symbol] = (now + self._contract_cache_ttl, result)
                return result
        except Exception as e:
            logger.warning("tradovate _find_contract(%s) direct lookup failed: %s", symbol, e)

        front = _front_month_symbol(symbol)
        try:
            result = await self._get("/contract/find", {"name": front})
            if result:
                self._contract_cache[symbol] = (now + self._contract_cache_ttl, result)
                return result
        except Exception as e:
            logger.warning("tradovate _find_contract(%s) front-month(%s) lookup failed: %s", symbol, front, e)

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
