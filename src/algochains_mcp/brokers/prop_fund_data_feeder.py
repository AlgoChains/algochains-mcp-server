"""Real-data feeder for prop fund evaluation.

Converts real Tradovate fills into the inputs that
``evaluate_strategy_for_fund`` and ``simulate_drawdown_against_fund_rules``
require. FAILS CLOSED per real-data-only policy — never
fabricates daily returns; if real fill data is unavailable, returns an
explicit error and refuses to produce synthetic metrics.

Pipeline:
  1. Pull fill history from Tradovate `/fill/list` (via TradovateConnector).
  2. FALLBACK: If Tradovate REST returns 0 fills (common for demo accounts
     where fill history is not accessible via API), pull from the control-tower's
     ``performance_tracker.db`` which the bot writes to directly.
  3. FIFO-match entries with exits on a per-symbol basis to compute
     realized P&L per round trip.
  4. Bucket round-trips by trading day (America/New_York).
  5. Emit:
       - daily_pnl_series: [float, ...]  real USD P&L per trading day
       - max_daily_loss_usd, max_drawdown_usd, avg_profit_per_day_usd
       - holds_overnight, max_position_contracts (observed, real)
       - min_trading_days_per_month (active calendar days / month * 20)

Public entry points:
  - build_prop_fund_inputs(...)  -> dict (used by MCP server)
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("algochains_mcp.brokers.prop_fund_data_feeder")

_ET = ZoneInfo("America/New_York")

# Tradovate tick-value lookup (USD per contract per point).
# Used to compute round-trip P&L when broker does not pre-fill it.
# Values match the scalper's own CONTRACT_STOPS multipliers.
_CONTRACT_POINT_VALUES: dict[str, float] = {
    "MNQ": 2.0,
    "NQ": 20.0,
    "MES": 5.0,
    "ES": 50.0,
    "MCL": 100.0,
    "CL": 1000.0,
    "MGC": 10.0,
    "GC": 100.0,
    "M2K": 5.0,
    "RTY": 50.0,
}


def _point_value(symbol: str) -> float:
    # Strip contract-month suffix (e.g. "MNQM6" -> "MNQ")
    root = "".join(ch for ch in symbol if ch.isalpha()).upper()
    for key, val in _CONTRACT_POINT_VALUES.items():
        if root.startswith(key):
            return val
    return 0.0


@dataclass
class _OpenLot:
    qty: int      # positive for long, negative for short
    price: float
    timestamp: datetime


def _fifo_match_pnl(fills: list[dict], symbol_root_override: Optional[str] = None) -> list[dict]:
    """FIFO-match entries with exits to compute realized P&L per round trip.

    Expects fills with at minimum these fields:
        symbol, qty (int), price (float), action ("Buy"/"Sell"), timestamp (ISO str)

    Returns a list of round-trip dicts:
        {"symbol", "qty", "entry_price", "exit_price", "entry_ts", "exit_ts", "pnl_usd"}
    """
    lots_by_symbol: dict[str, deque[_OpenLot]] = defaultdict(deque)
    round_trips: list[dict] = []

    # Sort fills chronologically
    def _parse_ts(s: str) -> datetime:
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return datetime.now(tz=timezone.utc)

    fills_sorted = sorted(fills, key=lambda f: _parse_ts(str(f.get("timestamp", ""))))

    for f in fills_sorted:
        sym = str(f.get("symbol", "")).strip()
        if not sym:
            continue
        root = symbol_root_override or "".join(ch for ch in sym if ch.isalpha()).upper()
        try:
            qty = int(f.get("qty", f.get("quantity", 0)))
            price = float(f.get("price", 0.0))
        except (TypeError, ValueError):
            continue
        if qty <= 0 or price <= 0:
            continue
        action = str(f.get("action", "")).lower()
        if action in ("buy", "b"):
            signed = qty
        elif action in ("sell", "s"):
            signed = -qty
        else:
            continue
        ts = _parse_ts(str(f.get("timestamp", "")))

        lots = lots_by_symbol[root]
        remaining = signed

        # Close opposite-side lots FIFO
        while lots and ((lots[0].qty > 0 and remaining < 0) or (lots[0].qty < 0 and remaining > 0)):
            lot = lots[0]
            match_qty = min(abs(lot.qty), abs(remaining))
            pv = _point_value(root)
            direction = 1 if lot.qty > 0 else -1  # long vs short
            pnl = direction * (price - lot.price) * match_qty * pv
            round_trips.append({
                "symbol": root,
                "qty": match_qty,
                "entry_price": lot.price,
                "exit_price": price,
                "entry_ts": lot.timestamp.isoformat(),
                "exit_ts": ts.isoformat(),
                "pnl_usd": round(pnl, 2),
                "direction": "long" if lot.qty > 0 else "short",
            })
            if abs(lot.qty) == match_qty:
                lots.popleft()
            else:
                lot.qty = lot.qty - direction * match_qty
            remaining = remaining + direction * match_qty

        # Remaining is a new open lot
        if remaining != 0:
            lots.append(_OpenLot(qty=remaining, price=price, timestamp=ts))

    return round_trips


def _bucket_by_trading_day(round_trips: list[dict]) -> list[tuple[str, float, int]]:
    """Return list of (iso_date, daily_pnl_usd, round_trips_closed) in ET."""
    by_day: dict[str, dict] = defaultdict(lambda: {"pnl": 0.0, "count": 0})
    for rt in round_trips:
        try:
            ts = datetime.fromisoformat(rt["exit_ts"].replace("Z", "+00:00"))
        except Exception:
            continue
        day = ts.astimezone(_ET).date().isoformat()
        by_day[day]["pnl"] += rt["pnl_usd"]
        by_day[day]["count"] += 1
    rows = [(d, round(b["pnl"], 2), b["count"]) for d, b in sorted(by_day.items())]
    return rows


def _compute_summary(daily: list[tuple[str, float, int]]) -> dict[str, Any]:
    if not daily:
        return {
            "days_traded": 0,
            "daily_pnl_series": [],
            "max_daily_loss_usd": 0.0,
            "total_pnl_usd": 0.0,
            "avg_profit_per_day_usd": 0.0,
            "max_drawdown_usd": 0.0,
            "max_position_contracts_observed": 0,
        }
    pnls = [d[1] for d in daily]
    # Max single-day loss (positive number, magnitude of worst day)
    max_daily_loss = abs(min([p for p in pnls if p < 0], default=0.0))
    total = sum(pnls)
    # Running drawdown from cumulative high-water mark
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        cum += p
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
    return {
        "days_traded": len(daily),
        "daily_pnl_series": pnls,
        "daily_rows": [{"date": d, "pnl_usd": p, "round_trips": n} for (d, p, n) in daily],
        "max_daily_loss_usd": round(max_daily_loss, 2),
        "total_pnl_usd": round(total, 2),
        "avg_profit_per_day_usd": round(total / len(daily), 2),
        "max_drawdown_usd": round(max_dd, 2),
    }


async def _pull_tradovate_fills(
    account_id: Optional[int],
    since_days: int,
) -> list[dict]:
    """Pull raw fills from Tradovate.

    Fails loudly if Tradovate is unreachable — we never fabricate fills.
    Returns a list of fill dicts (may be empty if no trades in window).
    """
    import os

    from ..config import TradovateConfig
    from .tradovate import TradovateConnector  # local import to avoid circularity

    cfg = TradovateConfig()
    if not (cfg.cid and cfg.secret):
        raise RuntimeError(
            "Tradovate credentials missing: set TRADOVATE_CID and TRADOVATE_SECRET in the "
            "environment (same as control tower / prop monitor). Cannot pull fills."
        )

    connector = TradovateConnector(cfg)
    connected = await connector.connect()
    if not connected:
        raise RuntimeError(
            "Tradovate connect() returned False — check TRADOVATE_CID, TRADOVATE_SECRET, "
            "and TRADOVATE_ENV (live vs demo)."
        )

    # Default account: explicit arg > TRADOVATE_ACCOUNT_ID > connector's first listed account
    eff_account: Optional[int] = account_id
    if eff_account is None:
        env_acct = os.environ.get("TRADOVATE_ACCOUNT_ID", "").strip()
        if env_acct.isdigit():
            eff_account = int(env_acct)
        else:
            eff_account = int(getattr(connector, "_account_id", 0) or 0) or None

    try:
        # Pull everything then filter client-side — /fill/list takes no params
        raw = await connector._get("/fill/list", {})
        if not isinstance(raw, list):
            raise RuntimeError(
                f"Tradovate /fill/list returned non-list ({type(raw).__name__}); "
                "real fill data unavailable — refusing to synthesize."
            )
        cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
        filtered = []
        for f in raw:
            if eff_account is not None and f.get("accountId") != eff_account:
                continue
            ts = f.get("timestamp", "")
            if ts:
                try:
                    when = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                    if when < cutoff:
                        continue
                except Exception:
                    pass
            # Normalize field names (Tradovate fill payloads vary by endpoint version)
            _qty = int(f.get("qty") or f.get("quantity") or f.get("fillQty") or 0)
            _raw_act = f.get("action") or f.get("side")
            if isinstance(_raw_act, str) and _raw_act.lower().startswith("b"):
                _action = "Buy"
            elif isinstance(_raw_act, str) and _raw_act.lower().startswith("s"):
                _action = "Sell"
            elif f.get("bought") is True:
                _action = "Buy"
            elif f.get("bought") is False:
                _action = "Sell"
            else:
                _action = "Buy" if _qty > 0 else "Sell"
                _qty = abs(_qty)
            _px = float(f.get("price") or f.get("netPrice") or f.get("priceNumerator") or 0.0)
            _sym = (
                f.get("contractName")
                or f.get("symbol")
                or ""
            )
            if not _sym and f.get("contractId"):
                _sym = str(f.get("contractId"))
            filtered.append({
                "symbol": _sym,
                "qty": abs(_qty) if _qty else 0,
                "price": _px,
                "action": _action,
                "timestamp": str(ts),
                "accountId": f.get("accountId"),
                "orderId": f.get("orderId"),
            })
        return filtered
    finally:
        try:
            await connector.disconnect()
        except Exception:
            pass


def _pull_from_performance_tracker(
    symbol: str,
    lookback_days: int,
    db_path: Optional[str] = None,
) -> list[dict]:
    """Pull trade data from the control-tower's performance_tracker.db.

    This is used as a fallback when Tradovate REST /fill/list returns 0 records
    (which is the case for demo accounts where fill history is not exposed via the
    REST API).  The DB is written by the bot's ``log_trade_to_db()`` method.

    Returns a list of fill-like dicts compatible with ``_fifo_match_pnl``.
    Trades that have an exit_time and exit_price are converted to entry+exit fill
    pairs.  Trades with no exit (orphaned entries) are treated as open lots only —
    they count toward max_position_contracts but not toward P&L.
    """
    import sqlite3, pathlib

    # Resolve DB path: env override → explicit arg → repo-relative default
    env_db = os.environ.get("PERFORMANCE_TRACKER_DB", "")
    if db_path is None:
        db_path = env_db or ""
    if not db_path:
        # Walk up from this file to find the control-tower root
        _here = pathlib.Path(__file__).resolve()
        for _parent in _here.parents:
            _candidate = _parent / "performance_tracker.db"
            if _candidate.exists():
                db_path = str(_candidate)
                break
    if not db_path or not pathlib.Path(db_path).exists():
        logger.warning(
            "performance_tracker.db not found — cannot provide fallback fill data. "
            "Searched env PERFORMANCE_TRACKER_DB and parent directories."
        )
        return []

    try:
        conn = sqlite3.connect(db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
        sym_root = "".join(ch for ch in symbol if ch.isalpha()).upper()

        rows = conn.execute(
            """
            SELECT id, bot_name, symbol, direction, entry_price, exit_price,
                   size, entry_time, exit_time, pnl
            FROM trades
            WHERE symbol = ?
              AND entry_time >= ?
            ORDER BY entry_time
            """,
            (sym_root, cutoff),
        ).fetchall()
        conn.close()
    except Exception as exc:
        logger.warning("performance_tracker.db read failed: %s", exc)
        return []

    fills: list[dict] = []
    for row in rows:
        entry_ts = str(row["entry_time"] or "")
        direction = str(row["direction"] or "BUY").upper()
        entry_action = "Buy" if direction == "BUY" else "Sell"
        close_action = "Sell" if direction == "BUY" else "Buy"
        qty = int(row["size"] or 1)
        entry_px = float(row["entry_price"] or 0.0)
        exit_px = row["exit_price"]
        exit_ts = row["exit_time"]

        # Entry fill
        if entry_px > 0:
            fills.append({
                "symbol": sym_root,
                "qty": qty,
                "price": entry_px,
                "action": entry_action,
                "timestamp": entry_ts,
                "source": "performance_tracker",
            })
        # Exit fill (only if we have a confirmed exit)
        if exit_px and exit_ts:
            fills.append({
                "symbol": sym_root,
                "qty": qty,
                "price": float(exit_px),
                "action": close_action,
                "timestamp": str(exit_ts),
                "source": "performance_tracker",
            })

    logger.info(
        "performance_tracker.db fallback: %d rows → %d fills for %s in last %d days",
        len(rows),
        len(fills),
        sym_root,
        lookback_days,
    )
    return fills


def build_prop_fund_inputs(
    strategy_name: str,
    symbol: str,
    lookback_days: int = 90,
    account_id: Optional[int] = None,
    fills_override: Optional[list[dict]] = None,
) -> dict:
    """Build real-data inputs for evaluate_strategy_for_prop_fund.

    REAL DATA ONLY. If the Tradovate fill fetch fails or returns zero fills
    for the window AND no ``fills_override`` is supplied, this returns an
    error — it NEVER fabricates metrics.

    Args:
        strategy_name:    Identifier (e.g. "FUTURES_SCALPER_UPGRADED").
        symbol:           Instrument root (e.g. "MNQ").
        lookback_days:    How far back to scan Tradovate fills.
        account_id:       Optional Tradovate account ID to filter.
        fills_override:   For testing / replay — provide pre-fetched fills.

    Returns:
        dict with keys suitable for evaluate_strategy_for_fund():
            strategy_name, symbol, max_daily_loss_usd, max_drawdown_usd,
            avg_profit_per_day_usd, holds_overnight (bool),
            trades_news (bool, unknown from fills — conservatively False),
            max_position_contracts, min_trading_days_per_month,
            historical_returns_daily (list[float] pct of account),
            daily_pnl_series (list[float] USD),
            source, data_ok, error (if any).
    """
    source = "tradovate_live"
    fills: list[dict]
    error = None

    if fills_override is not None:
        fills = list(fills_override)
        source = "override"
    else:
        try:
            try:
                fills = asyncio.run(_pull_tradovate_fills(account_id, lookback_days))
            except RuntimeError as _re:
                # Only substitute a fresh loop when asyncio.run is illegal (nested loop).
                _msg = str(_re).lower()
                if (
                    "cannot be called from a running event loop" in _msg
                    or "already running" in _msg
                ):
                    loop = asyncio.new_event_loop()
                    try:
                        asyncio.set_event_loop(loop)
                        fills = loop.run_until_complete(
                            _pull_tradovate_fills(account_id, lookback_days)
                        )
                    finally:
                        loop.close()
                else:
                    raise
        except Exception as e:
            logger.error("Tradovate fill pull failed: %s", e)
            _detail = str(e).strip().rstrip(".")
            return {
                "data_ok": False,
                "error": f"Could not pull real Tradovate fills: {_detail}. "
                         "Refusing to synthesize metrics (real-data-only policy).",
                "strategy_name": strategy_name,
                "symbol": symbol,
            }

    if not fills:
        # Tradovate REST returned 0 fills — common for demo accounts where fill history
        # is not accessible via API. Try performance_tracker.db as a real-data fallback.
        logger.info(
            "Tradovate REST returned 0 fills — attempting performance_tracker.db fallback"
        )
        fallback_fills = _pull_from_performance_tracker(symbol, lookback_days)
        if fallback_fills:
            fills = fallback_fills
            source = "performance_tracker_db"
            logger.info(
                "Using %d fills from performance_tracker.db (Tradovate REST empty)",
                len(fills),
            )
        else:
            return {
                "data_ok": False,
                "error": (
                    f"Zero fills found for account_id={account_id} in last {lookback_days} days. "
                    "Checked Tradovate REST API and performance_tracker.db. "
                    "Cannot evaluate without real trade history. "
                    "Ensure the bot is running and trades are being logged to performance_tracker.db."
                ),
                "strategy_name": strategy_name,
                "symbol": symbol,
                "source": source,
                "debug_hint": (
                    "performance_tracker.db exists but has no closed trades in the lookback window. "
                    "The exit logging bug (demo/bracket paths skipping log_trade_to_db) has been "
                    "patched — new exits will be recorded going forward."
                ),
            }

    # Filter to the requested symbol
    sym_root = "".join(ch for ch in symbol if ch.isalpha()).upper()
    sym_fills = [
        f for f in fills
        if "".join(ch for ch in str(f.get("symbol", "")) if ch.isalpha()).upper().startswith(sym_root)
    ]
    if not sym_fills:
        return {
            "data_ok": False,
            "error": f"No fills for {symbol} found in {lookback_days}-day window.",
            "strategy_name": strategy_name,
            "symbol": symbol,
            "source": source,
            "total_fills_any_symbol": len(fills),
        }

    round_trips = _fifo_match_pnl(sym_fills, symbol_root_override=sym_root)
    daily = _bucket_by_trading_day(round_trips)
    summary = _compute_summary(daily)

    # Max position observed = peak abs open qty across time
    max_pos = 0
    running = 0
    for f in sorted(sym_fills, key=lambda x: str(x.get("timestamp", ""))):
        q = int(f.get("qty", 0))
        running += q if str(f.get("action", "")).lower().startswith("b") else -q
        max_pos = max(max_pos, abs(running))

    # Overnight detection: net position non-zero at end of any calendar day (ET).
    # V2 FIX: previous logic checked `ts.hour >= 17 and cursor_position != 0` which
    # would fire even when a position was opened AND closed in the evening session
    # on the same calendar day. Correct approach: bucket deltas by ET date, walk
    # dates in order, carry the running cursor, and flag when it's still non-zero
    # at the end of any date.
    holds_overnight = False
    _eod_by_date: dict[str, int] = defaultdict(int)
    _cursor_eod = 0
    for f in sorted(sym_fills, key=lambda x: str(x.get("timestamp", ""))):
        try:
            ts = datetime.fromisoformat(
                str(f.get("timestamp", "")).replace("Z", "+00:00")
            ).astimezone(_ET)
        except Exception:
            continue
        q = int(f.get("qty", 0))
        delta = q if str(f.get("action", "")).lower().startswith("b") else -q
        _eod_by_date[ts.date().isoformat()] += delta

    _cursor_eod = 0
    for _date in sorted(_eod_by_date.keys()):
        _cursor_eod += _eod_by_date[_date]
        if _cursor_eod != 0:
            holds_overnight = True
            break

    account_ref = 50000.0  # canonical for % conversion; caller can override
    historical_pct = [round(p / account_ref, 6) for p in summary["daily_pnl_series"]]

    days_traded = summary["days_traded"]
    span_days = lookback_days or 1
    min_days_per_month = round(days_traded * 30.0 / span_days)

    return {
        "data_ok": True,
        "source": source,
        "strategy_name": strategy_name,
        "symbol": sym_root,
        "lookback_days": lookback_days,
        "fills_analyzed": len(sym_fills),
        "round_trips": len(round_trips),
        "days_traded": days_traded,
        # Inputs for evaluate_strategy_for_prop_fund:
        "max_daily_loss_usd": summary["max_daily_loss_usd"],
        "max_drawdown_usd": summary["max_drawdown_usd"],
        "avg_profit_per_day_usd": summary["avg_profit_per_day_usd"],
        "holds_overnight": holds_overnight,
        "trades_news": False,  # Bot already enforces news blackout; confirm via log audit
        "max_position_contracts": max_pos,
        "min_trading_days_per_month": min_days_per_month,
        "historical_returns_daily": historical_pct,
        "daily_pnl_series": summary["daily_pnl_series"],
        "daily_rows": summary.get("daily_rows", []),
        "total_pnl_usd": summary.get("total_pnl_usd", 0.0),
        "computed_at": datetime.now(tz=timezone.utc).isoformat(),
        "note": (
            "trades_news=False is inferred from bot's news blackout config — "
            "verify by cross-referencing timestamps against FOMC/NFP/CPI calendar."
        ),
    }
