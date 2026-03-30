"""RiskDashboardEngine — VaR, ES, factor model, stress testing, drawdown, margin, Greeks, alerts."""
from __future__ import annotations
import logging, math, uuid
from datetime import datetime
from typing import Any

logger = logging.getLogger("algochains_mcp.risk_dashboard")

STRESS_SCENARIOS = {
    "covid_crash": {"name": "COVID Crash (Mar 2020)", "equity_shock": -0.34, "vol_mult": 4.0},
    "gfc_2008": {"name": "GFC (2008)", "equity_shock": -0.57, "vol_mult": 5.0},
    "dot_com": {"name": "Dot-Com Bust (2000)", "equity_shock": -0.49, "vol_mult": 2.5},
    "flash_crash": {"name": "Flash Crash (2010)", "equity_shock": -0.09, "vol_mult": 6.0},
    "rate_shock": {"name": "Rate Shock +200bp", "equity_shock": -0.15, "vol_mult": 2.0},
    "vol_spike": {"name": "VIX Spike 80", "equity_shock": -0.20, "vol_mult": 4.5},
    "black_monday": {"name": "Black Monday (1987)", "equity_shock": -0.22, "vol_mult": 8.0},
}

FACTORS = {
    "market": "Market Beta", "size": "Size (SMB)", "value": "Value (HML)",
    "momentum": "Momentum (UMD)", "volatility": "Low Volatility", "quality": "Quality (QMJ)",
}


class RiskDashboardEngine:
    def __init__(self):
        self._alerts: list[dict] = []
        self._rules: list[dict] = []

    async def calculate_var(self, portfolio: dict, method: str = "parametric",
                            confidence: float = 0.95, horizon_days: int = 1) -> dict:
        positions = portfolio.get("positions", [])
        if not positions:
            return {"success": False, "error": "No positions provided."}
        total_val = sum(p.get("market_value", 0) for p in positions)
        if total_val <= 0:
            return {"success": False, "error": "Portfolio value must be positive."}
        wvol = sum((p.get("market_value", 0) / total_val) * p.get("annual_volatility", 0.20) for p in positions)
        dvol = wvol / math.sqrt(252)
        hvol = dvol * math.sqrt(horizon_days)
        z = {0.90: 1.282, 0.95: 1.645, 0.99: 2.326}.get(confidence, 1.645)
        adj = {"parametric": 1.0, "historical": 1.15, "monte_carlo": 1.10}.get(method, 1.0)
        var_pct = z * hvol * adj
        var_dollar = var_pct * total_val
        return {"success": True, "method": method, "confidence": confidence, "horizon_days": horizon_days,
                "portfolio_value": round(total_val, 2), "var_pct": round(var_pct * 100, 4),
                "var_dollar": round(var_dollar, 2), "daily_volatility_pct": round(dvol * 100, 4),
                "annualized_volatility_pct": round(wvol * 100, 4)}

    async def calculate_expected_shortfall(self, portfolio: dict, confidence: float = 0.95,
                                           horizon_days: int = 1) -> dict:
        vr = await self.calculate_var(portfolio, "parametric", confidence, horizon_days)
        if not vr.get("success"):
            return vr
        tf = {0.90: 1.20, 0.95: 1.13, 0.99: 1.07}.get(confidence, 1.13)
        es_pct = vr["var_pct"] * tf / 100
        return {**vr, "es_pct": round(es_pct * 100, 4), "es_dollar": round(es_pct * vr["portfolio_value"], 2),
                "tail_factor": tf}

    async def get_factor_exposure(self, portfolio: dict) -> dict:
        positions = portfolio.get("positions", [])
        if not positions:
            return {"success": False, "error": "No positions provided."}
        tv = sum(p.get("market_value", 0) for p in positions)
        exposures = {}
        for fk, fn in FACTORS.items():
            we = sum((p.get("market_value", 0) / tv) * p.get(f"beta_{fk}", p.get("beta", 1.0) if fk == "market" else 0.0) for p in positions)
            exposures[fk] = {"name": fn, "exposure": round(we, 4)}
        return {"success": True, "portfolio_value": round(tv, 2), "factors": exposures}

    async def run_stress_test(self, portfolio: dict, scenario: str | None = None,
                              custom_shocks: dict | None = None) -> dict:
        positions = portfolio.get("positions", [])
        if not positions:
            return {"success": False, "error": "No positions provided."}
        tv = sum(p.get("market_value", 0) for p in positions)
        scenarios = {}
        if scenario and scenario in STRESS_SCENARIOS:
            scenarios[scenario] = STRESS_SCENARIOS[scenario]
        elif custom_shocks:
            scenarios["custom"] = {"name": "Custom", **custom_shocks}
        else:
            scenarios = STRESS_SCENARIOS
        results = {}
        for k, sc in scenarios.items():
            shock = sc.get("equity_shock", -0.10)
            loss = sum(p.get("market_value", 0) * shock * p.get("beta", 1.0) for p in positions)
            results[k] = {"scenario": sc["name"], "equity_shock": shock,
                          "portfolio_loss": round(loss, 2),
                          "loss_pct": round(loss / tv * 100, 4) if tv else 0,
                          "surviving": tv + loss > 0}
        return {"success": True, "portfolio_value": round(tv, 2), "scenarios": results,
                "available_scenarios": list(STRESS_SCENARIOS.keys())}

    async def get_drawdown_monitor(self, portfolio: dict) -> dict:
        curr = portfolio.get("current_equity", 0)
        peak = portfolio.get("peak_equity", curr)
        if peak <= 0:
            return {"success": False, "error": "Peak equity must be positive."}
        dd = (peak - curr) / peak if peak > 0 else 0
        dr = portfolio.get("avg_daily_return", 0.0005)
        rec = math.log(peak / curr) / math.log(1 + dr) if dr > 0 and curr > 0 and dd > 0 else 0
        return {"success": True, "current_equity": round(curr, 2), "peak_equity": round(peak, 2),
                "drawdown_pct": round(dd * 100, 4), "drawdown_dollar": round(peak - curr, 2),
                "est_recovery_days": round(rec, 1),
                "status": "recovery" if dd > 0.01 else "at_high"}

    async def get_margin_utilization(self, account: dict) -> dict:
        eq = account.get("equity", 0)
        mu = account.get("margin_used", 0)
        maint = account.get("maintenance_margin", mu * 0.5)
        util = mu / eq if eq > 0 else 0
        buf = eq - maint
        st = "critical" if util > 0.90 else "warning" if util > 0.75 else "elevated" if util > 0.50 else "healthy"
        return {"success": True, "equity": round(eq, 2), "margin_used": round(mu, 2),
                "utilization_pct": round(util * 100, 2), "buffer_to_call": round(buf, 2), "status": st}

    async def get_greeks_exposure(self, portfolio: dict) -> dict:
        positions = portfolio.get("positions", [])
        totals = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}
        for p in positions:
            q = p.get("quantity", 1) * p.get("multiplier", 100)
            for g in totals:
                totals[g] += p.get(g, 0) * q
        return {"success": True, "greeks": {k: round(v, 4) for k, v in totals.items()}, "positions": len(positions)}

    async def configure_risk_alert(self, alert_type: str, threshold: float,
                                    action: str = "notify", channels: list | None = None) -> dict:
        valid = {"drawdown", "var_breach", "margin", "concentration", "loss_limit"}
        if alert_type not in valid:
            return {"success": False, "error": f"Invalid type. Use: {sorted(valid)}"}
        rule = {"id": f"alert_{uuid.uuid4().hex[:8]}", "type": alert_type, "threshold": threshold,
                "action": action, "channels": channels or ["slack"], "active": True, "triggered": 0}
        self._rules.append(rule)
        return {"success": True, "rule": rule}

    async def check_risk_alerts(self, portfolio: dict) -> dict:
        triggered = []
        for r in self._rules:
            if not r["active"]:
                continue
            val, breach = 0.0, False
            if r["type"] == "drawdown":
                p, c = portfolio.get("peak_equity", 1), portfolio.get("current_equity", 1)
                val = (p - c) / p if p > 0 else 0
                breach = val >= r["threshold"]
            elif r["type"] == "margin":
                e, m = portfolio.get("equity", 1), portfolio.get("margin_used", 0)
                val = m / e if e > 0 else 0
                breach = val >= r["threshold"]
            elif r["type"] == "loss_limit":
                val = abs(portfolio.get("daily_pnl", 0))
                breach = val >= r["threshold"]
            if breach:
                r["triggered"] += 1
                a = {"rule_id": r["id"], "type": r["type"], "threshold": r["threshold"],
                     "value": round(val, 4), "at": datetime.utcnow().isoformat()}
                triggered.append(a)
                self._alerts.append(a)
        return {"success": True, "triggered": len(triggered), "alerts": triggered}

    async def get_concentration_risk(self, portfolio: dict) -> dict:
        positions = portfolio.get("positions", [])
        if not positions:
            return {"success": False, "error": "No positions."}
        tv = sum(p.get("market_value", 0) for p in positions)
        ws = sorted([(p.get("symbol", "?"), p.get("market_value", 0) / tv if tv else 0) for p in positions],
                    key=lambda x: x[1], reverse=True)
        hhi = sum(w ** 2 for _, w in ws)
        top3 = sum(w for _, w in ws[:3])
        return {"success": True, "hhi": round(hhi, 4),
                "diversification": "concentrated" if hhi > 0.25 else "moderate" if hhi > 0.15 else "diversified",
                "top_3_weight_pct": round(top3 * 100, 2),
                "positions": [{"symbol": s, "weight_pct": round(w * 100, 2)} for s, w in ws[:10]]}
