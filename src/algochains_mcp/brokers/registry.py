"""
Broker registry — discovers and manages all available broker connectors.
"""
from __future__ import annotations

import logging
from typing import Optional

from ..config import ServerConfig
from .base import BrokerConnector

logger = logging.getLogger("algochains_mcp.brokers.registry")


class BrokerRegistry:
    """Central registry for all broker connectors."""

    def __init__(self, config: ServerConfig):
        self.cfg = config
        self._connectors: dict[str, BrokerConnector] = {}

    def _build_connectors(self) -> dict[str, BrokerConnector]:
        connectors = {}

        if self.cfg.alpaca.api_key:
            from .alpaca_connector import AlpacaConnector
            connectors["alpaca"] = AlpacaConnector(self.cfg.alpaca)

        if self.cfg.ibkr.host:
            from .ibkr_connector import IBKRConnector
            connectors["ibkr"] = IBKRConnector(self.cfg.ibkr)

        if self.cfg.oanda.access_token:
            from .oanda_connector import OandaConnector
            connectors["oanda"] = OandaConnector(self.cfg.oanda)

        if self.cfg.traderspost.webhook_url:
            from .traderspost_connector import TradersPostConnector
            connectors["traderspost"] = TradersPostConnector(self.cfg.traderspost)

        if self.cfg.quantconnect.api_token:
            from .quantconnect_connector import QuantConnectConnector
            connectors["quantconnect"] = QuantConnectConnector(self.cfg.quantconnect)

        if self.cfg.tradovate.cid:
            from .tradovate import TradovateConnector
            connectors["tradovate"] = TradovateConnector(self.cfg.tradovate)

        return connectors

    async def connect_all(self) -> dict[str, bool]:
        self._connectors = self._build_connectors()
        results = {}
        for name, conn in self._connectors.items():
            try:
                ok = await conn.connect()
                results[name] = ok
                if ok:
                    logger.info("Connected: %s", name)
                else:
                    logger.warning("Failed to connect: %s", name)
            except Exception as e:
                logger.error("Error connecting %s: %s", name, e)
                results[name] = False
        return results

    async def disconnect_all(self) -> None:
        for name, conn in self._connectors.items():
            try:
                await conn.disconnect()
            except Exception as e:
                logger.error("Error disconnecting %s: %s", name, e)
        self._connectors.clear()

    def get(self, name: str) -> Optional[BrokerConnector]:
        return self._connectors.get(name)

    def list_available(self) -> list[str]:
        return list(self._connectors.keys())

    def list_configured(self) -> list[str]:
        return self.cfg.available_brokers()

    async def health_check_all(self) -> dict[str, dict]:
        results = {}
        for name, conn in self._connectors.items():
            results[name] = await conn.health_check()
        return results
