"""ComplianceEngine — pre-trade gates, post-trade surveillance, audit trail, wash trade detection."""
from __future__ import annotations
import hashlib, logging, uuid
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger("algochains_mcp.compliance")

DEFAULT_LIMITS = {
    "max_position_pct": 0.10, "max_order_value": 500000, "max_daily_loss": 10000,
    "max_daily_orders": 500, "restricted_symbols": [], "wash_trade_window_sec": 30,
}


class ComplianceEngine:
    def __init__(self):
        self._profiles: dict[str, dict] = {}
        self._audit_trail: list[dict] = []
        self._trade_log: list[dict] = []
        self._violations: list[dict] = []
        self._kill_switch_active = False

    # ── Pre-Trade Check ──────────────────────────────────────────

    async def pre_trade_check(self, order: dict, account: dict, profile_id: str | None = None) -> dict:
        limits = self._profiles.get(profile_id, {}).get("limits", DEFAULT_LIMITS) if profile_id else DEFAULT_LIMITS
        violations = []
        symbol = order.get("symbol", "")
        qty = order.get("qty", 0)
        price = order.get("price", 0)
        side = order.get("side", "buy")
        order_value = qty * price

        if self._kill_switch_active:
            violations.append({"rule": "kill_switch", "severity": "critical", "msg": "Trading halted by kill switch."})

        if symbol in limits.get("restricted_symbols", []):
            violations.append({"rule": "restricted_list", "severity": "critical", "msg": f"{symbol} is restricted."})

        equity = account.get("equity", 1)
        if order_value > equity * limits.get("max_position_pct", 0.10):
            violations.append({"rule": "position_limit", "severity": "high",
                               "msg": f"Order {order_value:.0f} exceeds {limits['max_position_pct']:.0%} of equity."})

        if order_value > limits.get("max_order_value", 500000):
            violations.append({"rule": "order_size", "severity": "high",
                               "msg": f"Order value ${order_value:,.0f} exceeds max ${limits['max_order_value']:,.0f}."})

        daily_pnl = account.get("daily_pnl", 0)
        if daily_pnl < -limits.get("max_daily_loss", 10000):
            violations.append({"rule": "daily_loss", "severity": "critical",
                               "msg": f"Daily loss ${abs(daily_pnl):,.0f} exceeds limit ${limits['max_daily_loss']:,.0f}."})

        # Wash trade detection
        window = limits.get("wash_trade_window_sec", 30)
        now = datetime.utcnow()
        opposite = "sell" if side == "buy" else "buy"
        recent = [t for t in self._trade_log if t["symbol"] == symbol and t["side"] == opposite
                  and (now - datetime.fromisoformat(t["time"])).total_seconds() < window]
        if recent:
            violations.append({"rule": "wash_trade", "severity": "high",
                               "msg": f"Potential wash trade: opposite {symbol} trade {window}s ago."})

        passed = not any(v["severity"] == "critical" for v in violations)
        entry = {"id": f"ptc_{uuid.uuid4().hex[:8]}", "order": order, "passed": passed,
                 "violations": violations, "checked_at": now.isoformat()}
        self._audit(entry, "pre_trade_check")
        return {"success": True, "passed": passed, "violations": violations, "check_id": entry["id"]}

    # ── Post-Trade Surveillance ──────────────────────────────────

    async def post_trade_surveillance(self, trades: list[dict]) -> dict:
        findings = []
        for t in trades:
            self._trade_log.append({
                "symbol": t.get("symbol", ""), "side": t.get("side", ""),
                "qty": t.get("qty", 0), "price": t.get("price", 0),
                "time": t.get("time", datetime.utcnow().isoformat()),
            })

        # Layering detection: multiple orders at different prices quickly cancelled
        syms = set(t.get("symbol") for t in trades)
        for sym in syms:
            sym_trades = [t for t in trades if t.get("symbol") == sym]
            cancelled = [t for t in sym_trades if t.get("status") == "cancelled"]
            if len(cancelled) >= 3:
                findings.append({"type": "layering_suspect", "symbol": sym, "severity": "high",
                                 "detail": f"{len(cancelled)} cancelled orders detected."})

            # Momentum ignition: large order followed by cancellation
            for i, t in enumerate(sym_trades[:-1]):
                nxt = sym_trades[i + 1]
                if t.get("qty", 0) > 1000 and nxt.get("status") == "cancelled":
                    findings.append({"type": "momentum_ignition_suspect", "symbol": sym, "severity": "high",
                                     "detail": f"Large order ({t['qty']}) followed by cancel."})

        for f in findings:
            self._violations.append({**f, "detected_at": datetime.utcnow().isoformat()})

        self._audit({"trades": len(trades), "findings": len(findings)}, "post_trade_surveillance")
        return {"success": True, "trades_analyzed": len(trades), "findings": findings}

    # ── Audit Trail ──────────────────────────────────────────────

    def _audit(self, data: dict, action: str) -> None:
        prev_hash = self._audit_trail[-1]["hash"] if self._audit_trail else "0" * 64
        entry_str = f"{prev_hash}:{action}:{datetime.utcnow().isoformat()}"
        new_hash = hashlib.sha256(entry_str.encode()).hexdigest()
        self._audit_trail.append({
            "seq": len(self._audit_trail), "action": action, "data": data,
            "hash": new_hash, "prev_hash": prev_hash,
            "timestamp": datetime.utcnow().isoformat(),
        })

    async def get_audit_trail(self, limit: int = 50, action_filter: str | None = None) -> dict:
        trail = self._audit_trail
        if action_filter:
            trail = [e for e in trail if e["action"] == action_filter]
        recent = trail[-limit:]
        # Verify chain integrity
        valid = True
        for i in range(1, len(self._audit_trail)):
            if self._audit_trail[i]["prev_hash"] != self._audit_trail[i - 1]["hash"]:
                valid = False
                break
        return {"success": True, "entries": len(recent), "chain_valid": valid,
                "total_entries": len(self._audit_trail), "trail": recent}

    # ── Kill Switch ──────────────────────────────────────────────

    async def activate_kill_switch(self, reason: str) -> dict:
        self._kill_switch_active = True
        entry = {"reason": reason, "activated_at": datetime.utcnow().isoformat()}
        self._audit(entry, "kill_switch_activated")
        logger.critical("KILL SWITCH ACTIVATED: %s", reason)
        return {"success": True, "kill_switch": "active", "reason": reason}

    async def deactivate_kill_switch(self, reason: str) -> dict:
        self._kill_switch_active = False
        self._audit({"reason": reason}, "kill_switch_deactivated")
        return {"success": True, "kill_switch": "inactive", "reason": reason}

    # ── Compliance Profile ───────────────────────────────────────

    async def set_compliance_profile(self, profile_id: str, limits: dict) -> dict:
        merged = {**DEFAULT_LIMITS, **limits}
        self._profiles[profile_id] = {"id": profile_id, "limits": merged,
                                       "updated_at": datetime.utcnow().isoformat()}
        self._audit({"profile_id": profile_id, "limits": merged}, "profile_updated")
        return {"success": True, "profile": self._profiles[profile_id]}

    async def get_compliance_profile(self, profile_id: str) -> dict:
        p = self._profiles.get(profile_id)
        if not p:
            return {"success": True, "profile_id": profile_id, "limits": DEFAULT_LIMITS, "source": "default"}
        return {"success": True, **p, "source": "custom"}

    # ── Best Execution Report ────────────────────────────────────

    async def best_execution_report(self, trades: list[dict]) -> dict:
        results = []
        for t in trades:
            fill = t.get("fill_price", 0)
            mid = t.get("mid_price", fill)
            slip = abs(fill - mid) / mid if mid > 0 else 0
            results.append({
                "symbol": t.get("symbol"), "side": t.get("side"),
                "fill_price": fill, "mid_price": mid,
                "slippage_bps": round(slip * 10000, 2),
                "venue": t.get("venue", "unknown"),
                "assessment": "good" if slip < 0.001 else "acceptable" if slip < 0.005 else "poor",
            })
        avg_slip = sum(r["slippage_bps"] for r in results) / len(results) if results else 0
        self._audit({"trades": len(trades), "avg_slippage_bps": avg_slip}, "best_execution_report")
        return {"success": True, "trades": len(results), "avg_slippage_bps": round(avg_slip, 2), "results": results}

    # ── Wash Trade Alerts ─────────────────────────────────────────

    async def get_wash_trade_alerts(self, days: int = 30) -> dict:
        cutoff = datetime.utcnow() - timedelta(days=days)
        wash = [v for v in self._violations if v.get("type") == "wash_trade"
                or any(vv.get("rule") == "wash_trade" for vv in (v.get("violations", []) if isinstance(v.get("violations"), list) else []))]
        # Also scan trade_log for potential wash trades
        alerts = []
        symbols = set(t["symbol"] for t in self._trade_log)
        for sym in symbols:
            sym_trades = sorted(
                [t for t in self._trade_log if t["symbol"] == sym],
                key=lambda x: x["time"],
            )
            for i, t in enumerate(sym_trades):
                for j in range(i + 1, len(sym_trades)):
                    t2 = sym_trades[j]
                    if t["side"] != t2["side"]:
                        try:
                            dt = abs((datetime.fromisoformat(t2["time"]) - datetime.fromisoformat(t["time"])).total_seconds())
                        except (ValueError, TypeError):
                            continue
                        if dt < 86400 * 30:
                            alerts.append({
                                "symbol": sym, "buy_time": t["time"] if t["side"] == "buy" else t2["time"],
                                "sell_time": t2["time"] if t2["side"] == "sell" else t["time"],
                                "gap_seconds": round(dt), "severity": "high" if dt < 3600 else "medium",
                            })
                            break
        return {"success": True, "alerts": alerts, "count": len(alerts), "lookback_days": days}

    # ── Restricted List ───────────────────────────────────────────

    async def set_restricted_list(self, profile_id: str, symbols: list[str] | None = None,
                                   sectors: list[str] | None = None,
                                   countries: list[str] | None = None) -> dict:
        p = self._profiles.get(profile_id)
        if not p:
            p = {"id": profile_id, "limits": {**DEFAULT_LIMITS}, "updated_at": datetime.utcnow().isoformat()}
            self._profiles[profile_id] = p
        if symbols is not None:
            p["limits"]["restricted_symbols"] = symbols
        if sectors is not None:
            p["limits"]["restricted_sectors"] = sectors
        if countries is not None:
            p["limits"]["restricted_countries"] = countries
        p["updated_at"] = datetime.utcnow().isoformat()
        self._audit({"profile_id": profile_id, "symbols": symbols, "sectors": sectors, "countries": countries}, "restricted_list_updated")
        return {"success": True, "profile_id": profile_id, "restricted_symbols": p["limits"].get("restricted_symbols", []),
                "restricted_sectors": p["limits"].get("restricted_sectors", []),
                "restricted_countries": p["limits"].get("restricted_countries", [])}

    # ── Surveillance Scan ─────────────────────────────────────────

    async def run_surveillance_scan(self, lookback_hours: int = 24) -> dict:
        cutoff = datetime.utcnow() - timedelta(hours=lookback_hours)
        recent = []
        for t in self._trade_log:
            try:
                if datetime.fromisoformat(t["time"]) >= cutoff:
                    recent.append(t)
            except (ValueError, TypeError):
                recent.append(t)
        result = await self.post_trade_surveillance(recent)
        wash = await self.get_wash_trade_alerts(days=1)
        return {
            "success": True, "lookback_hours": lookback_hours,
            "trades_scanned": len(recent),
            "surveillance_findings": result.get("findings", []),
            "wash_trade_alerts": wash.get("alerts", []),
            "total_issues": len(result.get("findings", [])) + wash.get("count", 0),
        }

    # ── Compliance Status ─────────────────────────────────────────

    async def get_compliance_status(self, account: dict, profile_id: str | None = None) -> dict:
        limits = self._profiles.get(profile_id, {}).get("limits", DEFAULT_LIMITS) if profile_id else DEFAULT_LIMITS
        daily_pnl = account.get("daily_pnl", 0)
        equity = account.get("equity", 1)
        loss_pct = abs(daily_pnl) / equity if equity > 0 and daily_pnl < 0 else 0
        max_loss = limits.get("max_daily_loss", 10000)
        open_violations = [v for v in self._violations if not v.get("resolved")]
        return {
            "success": True,
            "kill_switch_active": self._kill_switch_active,
            "daily_pnl": daily_pnl,
            "daily_loss_limit": max_loss,
            "daily_loss_usage_pct": round(abs(daily_pnl) / max_loss * 100, 2) if max_loss > 0 else 0,
            "open_violations": len(open_violations),
            "total_trades_logged": len(self._trade_log),
            "audit_entries": len(self._audit_trail),
            "profile": profile_id or "default",
            "status": "halted" if self._kill_switch_active else "warning" if loss_pct > 0.5 * (limits.get("max_daily_loss", 10000) / equity) else "healthy",
        }
