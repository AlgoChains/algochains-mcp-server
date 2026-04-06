"""
V18 Arbitrage Detector — Cross-broker price/spread arbitrage detection.

Scans multiple brokers for price discrepancies on the same asset,
identifies statistical arbitrage opportunities, and computes expected
profit after fees and slippage.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("algochains.arbitrage_detector")


@dataclass
class ArbitrageOpportunity:
    """A detected cross-broker arbitrage opportunity."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    symbol: str = ""
    buy_broker: str = ""
    sell_broker: str = ""
    buy_price: float = 0.0
    sell_price: float = 0.0
    spread_bps: float = 0.0
    est_profit_per_unit: float = 0.0
    est_fees: float = 0.0
    est_net_profit: float = 0.0
    confidence: float = 0.0
    stale: bool = False
    detected_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "buy_broker": self.buy_broker,
            "sell_broker": self.sell_broker,
            "buy_price": self.buy_price,
            "sell_price": self.sell_price,
            "spread_bps": round(self.spread_bps, 2),
            "est_profit_per_unit": round(self.est_profit_per_unit, 4),
            "est_fees": round(self.est_fees, 4),
            "est_net_profit": round(self.est_net_profit, 4),
            "confidence": round(self.confidence, 2),
            "stale": self.stale,
            "age_seconds": round(time.time() - self.detected_at, 1),
        }


# ── Fee schedule per broker (approximate, commission per share/contract) ──

BROKER_FEES: dict[str, dict] = {
    "alpaca": {"commission": 0.0, "per_contract": 0.0, "ecn_fee_bps": 0.3},
    "ibkr": {"commission": 0.005, "per_contract": 0.65, "ecn_fee_bps": 0.2},
    "tradovate": {"commission": 0.0, "per_contract": 0.79, "ecn_fee_bps": 0.0},
    "schwab": {"commission": 0.0, "per_contract": 0.65, "ecn_fee_bps": 0.1},
    "oanda": {"commission": 0.0, "per_contract": 0.0, "ecn_fee_bps": 1.0},
    "tastytrade": {"commission": 0.0, "per_contract": 0.50, "ecn_fee_bps": 0.1},
}

DEFAULT_SLIPPAGE_BPS = 2.0
MIN_SPREAD_BPS = 5.0  # Below this, not worth the execution risk


class ArbitrageDetector:
    """Detect cross-broker and statistical arbitrage opportunities.

    Workflow:
    1. Collect quotes from multiple brokers for the same symbols
    2. Compute cross-broker spread in bps
    3. Subtract estimated fees and slippage
    4. Flag opportunities above minimum threshold
    """

    def __init__(self, broker_registry=None, min_spread_bps: float = MIN_SPREAD_BPS):
        self._brokers = broker_registry
        self._min_spread = min_spread_bps
        self._opportunities: list[ArbitrageOpportunity] = []
        self._scan_count = 0

    async def scan(
        self,
        symbols: list[str],
        brokers: Optional[list[str]] = None,
        quotes: Optional[dict[str, dict[str, float]]] = None,
    ) -> dict:
        """Scan for arbitrage across brokers.

        Args:
            symbols: List of symbols to check.
            brokers: List of broker names to compare.
            quotes: Pre-fetched quotes as {broker: {symbol: price}}.
                    If None, will attempt to fetch from broker_registry.
        """
        if not brokers:
            brokers = list(BROKER_FEES.keys())[:3]

        self._scan_count += 1

        # Collect quotes
        if quotes is None:
            quotes = await self._fetch_quotes(symbols, brokers)

        opportunities: list[ArbitrageOpportunity] = []

        for symbol in symbols:
            broker_prices: list[tuple[str, float]] = []
            for broker in brokers:
                price = (quotes.get(broker) or {}).get(symbol)
                if price and price > 0:
                    broker_prices.append((broker, price))

            if len(broker_prices) < 2:
                continue

            # Find best buy (lowest) and best sell (highest)
            broker_prices.sort(key=lambda x: x[1])
            buy_broker, buy_price = broker_prices[0]
            sell_broker, sell_price = broker_prices[-1]

            if buy_price <= 0:
                continue

            spread_bps = (sell_price - buy_price) / buy_price * 10000

            # Estimate fees
            buy_fees = self._estimate_fee(buy_broker, buy_price)
            sell_fees = self._estimate_fee(sell_broker, sell_price)
            total_fees = buy_fees + sell_fees
            slippage = buy_price * DEFAULT_SLIPPAGE_BPS / 10000 * 2  # both legs

            profit_per_unit = sell_price - buy_price
            net_profit = profit_per_unit - total_fees - slippage

            if spread_bps >= self._min_spread:
                opp = ArbitrageOpportunity(
                    symbol=symbol,
                    buy_broker=buy_broker,
                    sell_broker=sell_broker,
                    buy_price=buy_price,
                    sell_price=sell_price,
                    spread_bps=spread_bps,
                    est_profit_per_unit=profit_per_unit,
                    est_fees=total_fees + slippage,
                    est_net_profit=net_profit,
                    confidence=min(spread_bps / 20, 1.0) if net_profit > 0 else 0.0,
                )
                opportunities.append(opp)

        # Mark old opps as stale, add new ones
        for old in self._opportunities:
            old.stale = True
        self._opportunities.extend(opportunities)

        # Keep only last 200
        self._opportunities = self._opportunities[-200:]

        profitable = [o for o in opportunities if o.est_net_profit > 0]

        result = {
            "scan_id": self._scan_count,
            "symbols_scanned": len(symbols),
            "brokers": brokers,
            "opportunities_found": len(opportunities),
            "profitable_after_fees": len(profitable),
            "opportunities": [o.to_dict() for o in sorted(
                opportunities, key=lambda o: o.est_net_profit, reverse=True
            )[:20]],
        }

        if profitable:
            best = max(profitable, key=lambda o: o.est_net_profit)
            result["best"] = best.to_dict()

        logger.info(
            "Arb scan #%d: %d symbols × %d brokers → %d opportunities (%d profitable)",
            self._scan_count, len(symbols), len(brokers),
            len(opportunities), len(profitable),
        )
        return result

    async def get_opportunities(self, active_only: bool = True, limit: int = 20) -> list[dict]:
        """Get recent arbitrage opportunities."""
        opps = self._opportunities
        if active_only:
            opps = [o for o in opps if not o.stale]
        return [o.to_dict() for o in sorted(
            opps, key=lambda o: o.est_net_profit, reverse=True
        )[:limit]]

    async def _fetch_quotes(
        self, symbols: list[str], brokers: list[str],
    ) -> dict[str, dict[str, float]]:
        """Fetch quotes from broker registry (if available)."""
        quotes: dict[str, dict[str, float]] = {}
        if not self._brokers:
            return quotes

        for broker_name in brokers:
            try:
                conn = self._brokers.get(broker_name)
                if not conn:
                    continue
                broker_quotes: dict[str, float] = {}
                for symbol in symbols:
                    try:
                        q = await conn.get_quote(symbol)
                        if q and hasattr(q, "last_price") and q.last_price:
                            broker_quotes[symbol] = q.last_price
                    except Exception:
                        pass
                quotes[broker_name] = broker_quotes
            except Exception as e:
                logger.debug("Failed to fetch quotes from %s: %s", broker_name, e)

        return quotes

    def _estimate_fee(self, broker: str, price: float) -> float:
        """Estimate trading fee for one leg."""
        fees = BROKER_FEES.get(broker, {"commission": 0.005, "ecn_fee_bps": 0.3})
        commission = fees.get("commission", 0.0)
        ecn = price * fees.get("ecn_fee_bps", 0.0) / 10000
        return commission + ecn
