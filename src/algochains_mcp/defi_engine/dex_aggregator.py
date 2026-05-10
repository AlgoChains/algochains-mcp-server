"""DEX aggregation — route swaps across Uniswap, Sushi, Curve, 1inch.

SIMULATION MODE (default): All methods return synthetic responses with
``"status": "simulation"`` to avoid misleading agents about real on-chain
execution.  Set ``ALGOCHAINS_DEFI_ENABLED=true`` in the environment to signal
that a real Web3 backend (e.g. multicall RPC) has been wired up.

Agents MUST check ``result["status"]`` before treating any output as live.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any

# BUG-22 FIX: Previously _DEFI_LIVE=True (via ALGOCHAINS_DEFI_ENABLED=true) caused
# execute_swap and other methods to return status="ok" with a uuid4 tx_hash — implying
# a real on-chain transaction occurred when none did. No real Web3 execution path exists.
# Changed: _DEFI_LIVE is always forced to False until a real Web3 backend is wired.
# The env var ALGOCHAINS_DEFI_ENABLED is now reserved for future live implementation.
# When set, we log a one-time warning so operators know the flag has no live effect yet.
_DEFI_LIVE = False  # Reserved — no real Web3 execution path implemented yet
if os.getenv("ALGOCHAINS_DEFI_ENABLED", "").lower() in ("1", "true", "yes"):
    import logging as _dex_log
    _dex_log.getLogger("algochains_mcp.defi_engine.dex_aggregator").warning(
        "ALGOCHAINS_DEFI_ENABLED=true is set but no real Web3 execution backend is wired. "
        "All DEX operations will return simulation status. "
        "This flag is reserved for future implementation — remove it to suppress this warning."
    )
_SIM_BANNER = (
    "SIMULATION — No real Web3 execution backend is wired. "
    "Results are synthetic and do not reflect real on-chain state."
)


class DEXAggregator:
    """Aggregate and route swaps across decentralized exchanges.

    All methods include ``"status": "simulation"`` in their response until
    ``ALGOCHAINS_DEFI_ENABLED=true`` is set and a real Web3 backend is wired.
    """

    SUPPORTED_DEXES = ("uniswap_v3", "sushiswap", "curve", "balancer", "1inch")

    def __init__(self) -> None:
        self._quotes: dict[str, dict] = {}

    async def get_quote(self, token_in: str, token_out: str, amount: float, chain: str | None = None) -> dict:
        try:
            quote_id = uuid.uuid4().hex[:12]
            quote = {
                "id": quote_id,
                "token_in": token_in,
                "token_out": token_out,
                "amount_in": amount,
                "estimated_out": 0.0,
                "price_impact_pct": 0.0,
                "best_route": [],
                "chain": chain or "ethereum",
                "quoted_at": datetime.now(timezone.utc).isoformat(),
            }
            self._quotes[quote_id] = quote
            status = "ok" if _DEFI_LIVE else "simulation"
            result = {"status": status, "quote": quote}
            if not _DEFI_LIVE:
                result["simulation_warning"] = _SIM_BANNER
            return result
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def execute_swap(self, quote_id: str, slippage_tolerance: float = 0.005, deadline_minutes: int = 20) -> dict:
        try:
            quote = self._quotes.get(quote_id)
            if not quote:
                return {"status": "error", "error": f"Quote {quote_id} not found"}
            tx_hash = uuid.uuid4().hex
            status = "ok" if _DEFI_LIVE else "simulation"
            result = {
                "status": status,
                "tx_hash": tx_hash,
                "quote_id": quote_id,
                "slippage_tolerance": slippage_tolerance,
                "deadline_minutes": deadline_minutes,
                "executed_at": datetime.now(timezone.utc).isoformat(),
            }
            if not _DEFI_LIVE:
                result["simulation_warning"] = _SIM_BANNER
            return result
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def get_liquidity(self, token_in: str, token_out: str, chain: str | None = None) -> dict:
        try:
            status = "ok" if _DEFI_LIVE else "simulation"
            result = {
                "status": status,
                "token_in": token_in,
                "token_out": token_out,
                "chain": chain or "ethereum",
                "total_liquidity_usd": 0.0,
                "dexes": [{"name": d, "liquidity_usd": 0.0} for d in self.SUPPORTED_DEXES],
                "as_of": datetime.now(timezone.utc).isoformat(),
            }
            if not _DEFI_LIVE:
                result["simulation_warning"] = _SIM_BANNER
            return result
        except Exception as e:
            return {"status": "error", "error": str(e)}
