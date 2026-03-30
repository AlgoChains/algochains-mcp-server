"""TemplateManager — browse and fork pre-built strategy templates."""

from __future__ import annotations

import copy
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .spec import StrategySpec

logger = logging.getLogger("algochains_mcp.strategy_builder.templates")

BUILTIN_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "tpl_momentum_rsi",
        "name": "RSI Momentum",
        "description": "Buy oversold RSI, sell overbought. Works best on daily/hourly equities.",
        "category": "momentum",
        "asset_classes": ["equity", "crypto"],
        "timeframes": ["1h", "4h", "daily"],
        "spec": {
            "name": "RSI Momentum",
            "indicators": [
                {"name": "rsi", "period": 14, "source": "close"},
                {"name": "atr", "period": 14},
            ],
            "entry_rules": {
                "long": {
                    "conditions": [
                        {"indicator": "rsi", "operator": "<", "value": 30},
                    ],
                    "logic": "AND",
                },
                "short": {
                    "conditions": [
                        {"indicator": "rsi", "operator": ">", "value": 70},
                    ],
                    "logic": "AND",
                },
            },
            "exit_rules": {
                "stop_loss": {"type": "atr_multiple", "multiplier": 2.0},
                "take_profit": {"type": "atr_multiple", "multiplier": 4.0},
                "time_exit": {"bars": 20},
            },
            "position_sizing": {
                "method": "risk_pct",
                "risk_per_trade": 0.01,
                "max_positions": 3,
            },
        },
    },
    {
        "id": "tpl_mean_reversion_bb",
        "name": "Bollinger Band Mean Reversion",
        "description": "Enter on BB lower band touch with RSI confirmation. Exit at middle band.",
        "category": "mean_reversion",
        "asset_classes": ["equity", "forex"],
        "timeframes": ["15min", "1h", "4h"],
        "spec": {
            "name": "BB Mean Reversion",
            "indicators": [
                {"name": "bbands", "period": 20, "std_dev": 2.0, "source": "close"},
                {"name": "rsi", "period": 14, "source": "close"},
                {"name": "atr", "period": 14},
            ],
            "entry_rules": {
                "long": {
                    "conditions": [
                        {"indicator": "close", "operator": "<", "ref": "bbands.lower"},
                        {"indicator": "rsi", "operator": "<", "value": 35},
                    ],
                    "logic": "AND",
                },
            },
            "exit_rules": {
                "stop_loss": {"type": "atr_multiple", "multiplier": 1.5},
                "take_profit": {"type": "indicator_cross", "target": "bbands.middle"},
                "trailing_stop": {"type": "atr_multiple", "multiplier": 2.0, "activation": 1.0},
            },
            "position_sizing": {
                "method": "risk_pct",
                "risk_per_trade": 0.01,
                "max_positions": 3,
            },
        },
    },
    {
        "id": "tpl_ema_crossover",
        "name": "EMA Crossover Trend Following",
        "description": "Classic fast/slow EMA crossover with ATR stops. Robust across markets.",
        "category": "trend",
        "asset_classes": ["equity", "forex", "futures", "crypto"],
        "timeframes": ["1h", "4h", "daily"],
        "spec": {
            "name": "EMA Crossover",
            "indicators": [
                {"name": "ema", "period": 9, "source": "close"},
                {"name": "ema", "period": 21, "source": "close"},
                {"name": "atr", "period": 14},
            ],
            "entry_rules": {
                "long": {
                    "conditions": [
                        {"indicator": "ema_9", "operator": ">", "ref": "ema_21"},
                    ],
                    "logic": "AND",
                },
                "short": {
                    "conditions": [
                        {"indicator": "ema_9", "operator": "<", "ref": "ema_21"},
                    ],
                    "logic": "AND",
                },
            },
            "exit_rules": {
                "stop_loss": {"type": "atr_multiple", "multiplier": 2.0},
                "take_profit": {"type": "atr_multiple", "multiplier": 4.0},
                "trailing_stop": {"type": "atr_multiple", "multiplier": 2.5, "activation": 1.5},
            },
            "position_sizing": {
                "method": "risk_pct",
                "risk_per_trade": 0.01,
                "max_positions": 5,
            },
        },
    },
    {
        "id": "tpl_breakout_volume",
        "name": "Volume Breakout",
        "description": "Breakout from consolidation with volume confirmation. Best for stocks.",
        "category": "breakout",
        "asset_classes": ["equity"],
        "timeframes": ["15min", "1h", "daily"],
        "spec": {
            "name": "Volume Breakout",
            "indicators": [
                {"name": "donchian", "period": 20},
                {"name": "atr", "period": 14},
                {"name": "obv", "period": 20},
            ],
            "entry_rules": {
                "long": {
                    "conditions": [
                        {"indicator": "close", "operator": ">", "ref": "donchian.upper"},
                        {"indicator": "volume", "operator": ">", "ref": "volume_sma_20", "multiplier": 1.5},
                    ],
                    "logic": "AND",
                },
            },
            "exit_rules": {
                "stop_loss": {"type": "atr_multiple", "multiplier": 2.0},
                "take_profit": {"type": "atr_multiple", "multiplier": 5.0},
                "trailing_stop": {"type": "atr_multiple", "multiplier": 3.0, "activation": 2.0},
            },
            "position_sizing": {
                "method": "risk_pct",
                "risk_per_trade": 0.01,
                "max_positions": 3,
            },
        },
    },
    {
        "id": "tpl_pairs_cointegration",
        "name": "Pairs Mean Reversion",
        "description": "Statistical arbitrage on cointegrated pairs. Market-neutral.",
        "category": "pairs",
        "asset_classes": ["equity"],
        "timeframes": ["1h", "daily"],
        "spec": {
            "name": "Pairs Cointegration",
            "indicators": [
                {"name": "zscore", "period": 20, "source": "spread"},
                {"name": "atr", "period": 14},
            ],
            "entry_rules": {
                "long": {
                    "conditions": [
                        {"indicator": "zscore", "operator": "<", "value": -2.0},
                    ],
                    "logic": "AND",
                },
                "short": {
                    "conditions": [
                        {"indicator": "zscore", "operator": ">", "value": 2.0},
                    ],
                    "logic": "AND",
                },
            },
            "exit_rules": {
                "stop_loss": {"type": "percent", "multiplier": 0.03},
                "take_profit": {"type": "indicator_cross", "target": "zscore_zero"},
                "time_exit": {"bars": 10},
            },
            "position_sizing": {
                "method": "equal_weight",
                "risk_per_trade": 0.02,
                "max_positions": 4,
            },
        },
    },
]


class TemplateManager:
    """Manage and fork pre-built strategy templates."""

    def __init__(self, templates_dir: str | None = None):
        self._templates: dict[str, dict[str, Any]] = {
            t["id"]: t for t in BUILTIN_TEMPLATES
        }
        if templates_dir:
            self._load_from_dir(templates_dir)

    def _load_from_dir(self, templates_dir: str) -> None:
        path = Path(templates_dir)
        if not path.exists():
            return
        for f in path.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                if "id" in data:
                    self._templates[data["id"]] = data
            except Exception as e:
                logger.warning("Failed to load template %s: %s", f, e)

    def list_templates(
        self,
        category: str | None = None,
        asset_class: str | None = None,
    ) -> dict[str, Any]:
        templates = list(self._templates.values())
        if category:
            templates = [t for t in templates if t.get("category") == category]
        if asset_class:
            templates = [t for t in templates if asset_class in t.get("asset_classes", [])]

        return {
            "count": len(templates),
            "templates": [
                {
                    "id": t["id"],
                    "name": t["name"],
                    "description": t["description"],
                    "category": t.get("category", ""),
                    "asset_classes": t.get("asset_classes", []),
                    "timeframes": t.get("timeframes", []),
                }
                for t in templates
            ],
        }

    def get_template(self, template_id: str) -> dict[str, Any] | None:
        return self._templates.get(template_id)

    def fork_template(
        self,
        template_id: str,
        new_name: str | None = None,
        symbols: list[str] | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        template = self._templates.get(template_id)
        if not template:
            return {"success": False, "error": f"Template '{template_id}' not found."}

        spec_data = copy.deepcopy(template["spec"])

        if new_name:
            spec_data["name"] = new_name
        else:
            spec_data["name"] = f"{spec_data['name']} (fork)"

        if symbols:
            spec_data["symbols"] = symbols

        if overrides:
            for key, value in overrides.items():
                spec_data[key] = value

        spec_data["id"] = f"spec_{uuid.uuid4().hex[:12]}"
        spec_data["status"] = "draft"
        spec_data["created_at"] = datetime.utcnow().isoformat()

        spec = StrategySpec.from_dict(spec_data)

        return {
            "success": True,
            "forked_from": template_id,
            "spec": spec.to_dict(),
            "next_steps": "Customize parameters, then run backtest_strategy to test.",
        }
