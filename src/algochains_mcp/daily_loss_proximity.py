"""Daily-loss proximity policy shared by order guardrails.

The hard daily-loss circuit breaker still trips at the configured limit. These
helpers cover softer proximity behavior before that limit is reached.
"""
from __future__ import annotations

from dataclasses import dataclass


DAILY_LOSS_ALERT_FRACTION = 0.80
DAILY_LOSS_SCALPER_BLOCK_FRACTION = 0.95


@dataclass(frozen=True)
class DailyLossProximity:
    daily_pnl: float
    limit_usd: float
    loss_usd: float
    loss_fraction: float
    alert: bool
    block_scalper_entry: bool
    mnq_swing_exempt: bool
    message: str


def _symbol_root(symbol: str) -> str:
    return "".join(ch for ch in symbol.upper() if ch.isalpha())


def _label_text(*labels: str | None) -> str:
    return " ".join(label.lower() for label in labels if label)


def is_mnq_swing_strategy(
    symbol: str,
    strategy_type: str | None = None,
    bot_name: str | None = None,
) -> bool:
    """Return True only for MNQ swing strategies that are not scalpers."""
    text = _label_text(strategy_type, bot_name)
    return _symbol_root(symbol).startswith("MNQ") and "swing" in text and "scalp" not in text


def is_scalper_entry(
    strategy_type: str | None = None,
    bot_name: str | None = None,
    *,
    assume_scalper_when_unknown: bool = True,
) -> bool:
    """Classify scalper entries from explicit strategy labels.

    The live futures entry path often has no strategy metadata, so the guard
    conservatively treats unknown entries as scalper entries.
    """
    text = _label_text(strategy_type, bot_name)
    if not text:
        return assume_scalper_when_unknown
    return "scalp" in text


def evaluate_daily_loss_proximity(
    daily_pnl: float,
    limit_usd: float,
    *,
    symbol: str = "",
    strategy_type: str | None = None,
    bot_name: str | None = None,
    is_new_entry: bool = True,
) -> DailyLossProximity:
    """Evaluate pre-hard-limit daily-loss proximity behavior."""
    limit = abs(float(limit_usd))
    pnl = float(daily_pnl)
    loss = max(0.0, -pnl)
    fraction = loss / limit if limit > 0 else 0.0
    alert = fraction >= DAILY_LOSS_ALERT_FRACTION
    mnq_swing_exempt = is_mnq_swing_strategy(symbol, strategy_type, bot_name)
    scalper_entry = is_scalper_entry(strategy_type, bot_name)
    block_scalper_entry = (
        is_new_entry
        and scalper_entry
        and not mnq_swing_exempt
        and fraction >= DAILY_LOSS_SCALPER_BLOCK_FRACTION
    )

    pct = fraction * 100
    buffer_usd = max(0.0, limit - loss)
    if block_scalper_entry:
        message = (
            f"Daily loss proximity {pct:.0f}% (${loss:.2f}/${limit:.0f}) - "
            "new scalper entries blocked until daily P&L recovers"
        )
    elif alert:
        if mnq_swing_exempt and fraction >= DAILY_LOSS_SCALPER_BLOCK_FRACTION:
            message = (
                f"Daily loss proximity {pct:.0f}% (${loss:.2f}/${limit:.0f}) - "
                "MNQ swing exempt from scalper-entry block"
            )
        else:
            message = (
                f"Daily loss proximity alert {pct:.0f}% "
                f"(${loss:.2f}/${limit:.0f}, ${buffer_usd:.2f} buffer)"
            )
    else:
        message = (
            f"Daily loss proximity ok {pct:.0f}% "
            f"(${loss:.2f}/${limit:.0f}, ${buffer_usd:.2f} buffer)"
        )

    return DailyLossProximity(
        daily_pnl=pnl,
        limit_usd=limit,
        loss_usd=loss,
        loss_fraction=fraction,
        alert=alert,
        block_scalper_entry=block_scalper_entry,
        mnq_swing_exempt=mnq_swing_exempt,
        message=message,
    )

