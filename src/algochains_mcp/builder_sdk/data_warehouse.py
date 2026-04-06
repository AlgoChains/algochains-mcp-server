"""Supabase Data Warehouse Client — read-only access to AlgoChains data.

Builder tier ($199/mo) provides access to:
- Crypto_minute: 409M rows (BTC, ETH, SOL, etc.)
- Stocks_minute: 1.3B rows (all US equities)
- Forex_minute: 1.4B rows (70+ pairs)

Schema: ticker, open, high, low, close, volume, window_start, transactions

Connection uses Supabase PostgREST with RLS-enforced read-only access.
API key is validated server-side; no direct DB credentials exposed.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger("algochains_mcp.builder_sdk")

WAREHOUSE_PROJECTS = {
    "crypto": {
        "project_ref": "bwfyebmxcdzamcjpsgpj",
        "table": "Crypto_minute",
        "rows": "409M",
        "description": "Cryptocurrency minute bars — BTC, ETH, SOL, DOGE, etc.",
    },
    "stocks": {
        "project_ref": "irpcqlegtkxtpvkfnrqh",
        "table": "Stocks_minute",
        "rows": "1.3B",
        "description": "US equities minute bars — all major tickers",
    },
    "forex": {
        "project_ref": "auxbddwafgqxjmhsfwab",
        "table": "Forex_minute",
        "rows": "1.4B",
        "description": "Forex minute bars — 70+ currency pairs",
    },
}


@dataclass
class DataQuery:
    """Query specification for data warehouse."""
    asset_class: str
    ticker: str
    start_date: str | None = None
    end_date: str | None = None
    limit: int = 10000
    order: str = "desc"

    def validate(self) -> list[str]:
        errors = []
        if self.asset_class not in WAREHOUSE_PROJECTS:
            errors.append(
                f"Invalid asset_class '{self.asset_class}'. "
                f"Choose from: {list(WAREHOUSE_PROJECTS.keys())}"
            )
        if not self.ticker:
            errors.append("ticker is required")
        if self.limit > 100000:
            errors.append("limit cannot exceed 100,000 rows per query")
        return errors


class DataWarehouseClient:
    """Read-only client for AlgoChains data warehouses.

    Requires a valid Builder tier API key (ALGOCHAINS_BUILDER_KEY).
    All queries go through the AlgoChains API gateway which validates
    the license and proxies to the appropriate Supabase project.
    """

    def __init__(self, api_key: str | None = None, gateway_url: str | None = None):
        self.api_key = api_key or os.getenv("ALGOCHAINS_BUILDER_KEY", "")
        self.gateway_url = gateway_url or os.getenv(
            "ALGOCHAINS_GATEWAY_URL", "https://algochains.ai/api/v1/data"
        )
        self._query_count = 0
        self._max_queries_per_hour = 1000

    def list_warehouses(self) -> dict:
        """List available data warehouses and their metadata."""
        return {
            "warehouses": WAREHOUSE_PROJECTS,
            "schema": {
                "columns": ["ticker", "open", "high", "low", "close",
                            "volume", "window_start", "transactions"],
                "types": {
                    "ticker": "text",
                    "open": "float8",
                    "high": "float8",
                    "low": "float8",
                    "close": "float8",
                    "volume": "float8",
                    "window_start": "timestamptz",
                    "transactions": "int4",
                },
            },
            "total_rows": "3.09B+",
            "access_tier": "Builder ($199/mo)",
        }

    async def query(self, query: DataQuery) -> dict:
        """Execute a data warehouse query.

        Returns data in a structured format suitable for backtesting.
        """
        errors = query.validate()
        if errors:
            return {"error": True, "errors": errors}

        if not self.api_key:
            return {
                "error": True,
                "message": "No Builder API key configured. "
                           "Set ALGOCHAINS_BUILDER_KEY or subscribe at algochains.ai/pricing",
            }

        warehouse = WAREHOUSE_PROJECTS[query.asset_class]

        try:
            import httpx
            async with httpx.AsyncClient(timeout=30.0) as client:
                params = {
                    "table": warehouse["table"],
                    "project": warehouse["project_ref"],
                    "ticker": query.ticker.upper(),
                    "limit": min(query.limit, 100000),
                    "order": query.order,
                }
                if query.start_date:
                    params["start_date"] = query.start_date
                if query.end_date:
                    params["end_date"] = query.end_date

                resp = await client.get(
                    self.gateway_url,
                    params=params,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "X-Client": "algochains-mcp-server",
                    },
                )

                if resp.status_code == 401:
                    return {
                        "error": True,
                        "message": "Invalid or expired Builder API key. "
                                   "Renew at algochains.ai/account",
                    }
                if resp.status_code == 403:
                    return {
                        "error": True,
                        "message": "Builder tier required. Upgrade at algochains.ai/pricing",
                    }

                resp.raise_for_status()
                self._query_count += 1
                return resp.json()

        except ImportError:
            return {
                "error": True,
                "message": "httpx not installed. Run: pip install httpx",
            }
        except Exception as e:
            logger.error("Data warehouse query failed: %s", e)
            return {"error": True, "message": str(e)}

    def get_usage(self) -> dict:
        """Return current session query usage."""
        return {
            "queries_this_session": self._query_count,
            "max_per_hour": self._max_queries_per_hour,
            "remaining": max(0, self._max_queries_per_hour - self._query_count),
        }
