"""Daily-loss proximity policy shared by MCP guardrail entry points."""

from __future__ import annotations

from dataclasses import dataclass


DAILY_LOSS_ALERT_PCT: float = 80.0
SCALPER_DAILY_LOSS_BLOCK_PCT: float = 95.0


@dataclass(frozen=True)
class DailyLossProximityDecision:
    approved: bool
    level: str
    loss: float
    usage_pct: float
    buffer: float
    reason: str
    warning: bool = False
    should_trip_circuit_breaker: bool = False


def _context_tokens(strategy_type: str | None = None, bot_name: str | None = None) -> str:
    return " ".join(part.lower() for part in (strategy_type, bot_name) if part).strip()


def is_mnq_swing_exempt(
    symbol: str,
    strategy_type: str | None = None,
    bot_name: str | None = None,
) -> bool:
    """Return True for MNQ swing strategies exempt from the 95% scalper-entry block."""
    context = _context_tokens(strategy_type, bot_name)
    symbol_upper = symbol.upper()
    return symbol_upper.startswith("MNQ") and "swing" in context and "scalp" not in context


def is_scalper_entry(
    strategy_type: str | None = None,
    bot_name: str | None = None,
    is_entry: bool = True,
) -> bool:
    """Classify default/explicit scalper entry attempts for the 95% proximity block."""
    if not is_entry:
        return False

    context = _context_tokens(strategy_type, bot_name)
    if not context:
        # Missing strategy context is treated as scalper-like for fail-safe behavior.
        return True
    if "scalp" in context or "scalper" in context:
        return True
    if "swing" in context:
        return False
    return False


def evaluate_daily_loss_proximity(
    daily_pnl: float,
    max_loss: float,
    *,
    symbol: str = "",
    strategy_type: str | None = None,
    bot_name: str | None = None,
    is_entry: bool = True,
    alert_pct: float = DAILY_LOSS_ALERT_PCT,
    block_pct: float = SCALPER_DAILY_LOSS_BLOCK_PCT,
) -> DailyLossProximityDecision:
    """Evaluate daily P&L against alert, scalper-block, and hard-stop thresholds."""
    if max_loss <= 0:
        return DailyLossProximityDecision(
            approved=False,
            level="config_error",
            loss=0.0,
            usage_pct=0.0,
            buffer=0.0,
            reason="Daily loss guard misconfigured: max loss must be positive",
        )

    loss = max(0.0, -daily_pnl)
    usage_pct = loss / max_loss * 100.0
    buffer = max(0.0, max_loss - loss)

    if loss >= max_loss:
        return DailyLossProximityDecision(
            approved=False,
            level="hard_block",
            loss=loss,
            usage_pct=usage_pct,
            buffer=buffer,
            reason=(
                f"Daily loss ${loss:.2f} is {usage_pct:.1f}% of ${max_loss:.2f} "
                "limit - trading halted"
            ),
            should_trip_circuit_breaker=True,
        )

    mnq_swing_exempt = is_mnq_swing_exempt(symbol, strategy_type, bot_name)
    scalper_entry = is_scalper_entry(strategy_type, bot_name, is_entry)

    if usage_pct >= block_pct and scalper_entry and not mnq_swing_exempt:
        return DailyLossProximityDecision(
            approved=False,
            level="proximity_block",
            loss=loss,
            usage_pct=usage_pct,
            buffer=buffer,
            reason=(
                f"Daily loss ${loss:.2f} is {usage_pct:.1f}% of ${max_loss:.2f} "
                f"limit - new scalper entries blocked at {block_pct:.0f}%"
            ),
            warning=True,
        )

    if usage_pct >= block_pct and mnq_swing_exempt:
        return DailyLossProximityDecision(
            approved=True,
            level="exempt_warning",
            loss=loss,
            usage_pct=usage_pct,
            buffer=buffer,
            reason=(
                f"Daily loss ${loss:.2f} is {usage_pct:.1f}% of ${max_loss:.2f} "
                f"limit; MNQ swing exempt from {block_pct:.0f}% scalper-entry block "
                f"(${buffer:.2f} buffer)"
            ),
            warning=True,
        )

    if usage_pct >= alert_pct:
        return DailyLossProximityDecision(
            approved=True,
            level="alert",
            loss=loss,
            usage_pct=usage_pct,
            buffer=buffer,
            reason=(
                f"Daily loss ${loss:.2f} is {usage_pct:.1f}% of ${max_loss:.2f} "
                f"limit - alert threshold {alert_pct:.0f}% reached "
                f"(${buffer:.2f} buffer)"
            ),
            warning=True,
        )

    return DailyLossProximityDecision(
        approved=True,
        level="ok",
        loss=loss,
        usage_pct=usage_pct,
        buffer=buffer,
        reason=(
            f"Daily P&L ${daily_pnl:.2f} ({usage_pct:.1f}% of ${max_loss:.2f} "
            f"loss limit, ${buffer:.2f} buffer)"
        ),
    )
