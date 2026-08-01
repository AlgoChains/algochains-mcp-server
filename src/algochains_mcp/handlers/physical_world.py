"""Read-only physical-world event intelligence handlers.

These handlers are advisory only. They never read broker state, never place orders,
and never promote event scores to trading decisions. Polling daemons populate the
event table from real source APIs; MCP tools expose deterministic summaries and
asset mappings for downstream research.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Awaitable, Callable

ASSET_EVENT_MAP: dict[str, list[str]] = {
    "CL": ["energy_inventory", "wildfire_hotspot", "grid_event", "weather_alert"],
    "NG": ["energy_inventory", "weather_alert", "grid_event"],
    "MNQ": ["prediction_market", "grid_event", "weather_alert"],
    "NQ": ["prediction_market", "grid_event", "weather_alert"],
    "MES": ["prediction_market", "grid_event", "weather_alert"],
    "ES": ["prediction_market", "grid_event", "weather_alert"],
    "BTC": ["prediction_market", "grid_event"],
    "ETH": ["prediction_market", "grid_event"],
}

SOURCE_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "noaa_nws_cap": {"node": "sonia_air", "class": "weather_alert", "license_status": "public_api"},
    "usgs_earthquake": {"node": "sonia_air", "class": "earthquake", "license_status": "public_api"},
    "kalshi_public_rest": {"node": "sonia_air", "class": "prediction_market", "license_status": "public_api"},
    "polymarket_gamma": {"node": "sonia_air", "class": "prediction_market", "license_status": "public_api"},
    "nasa_firms": {"node": "desktop", "class": "wildfire_hotspot", "license_status": "source_terms_required"},
    "eia_inventory": {"node": "desktop", "class": "energy_inventory", "license_status": "api_key_required"},
    "iso_rto": {"node": "desktop", "class": "grid_event", "license_status": "source_terms_required"},
}

Handler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


async def get_physical_event_sources(arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "ok",
        "sources": SOURCE_DESCRIPTIONS,
        "real_data_only": True,
        "notes": "Rows are produced by pollers from real source APIs; no mock payloads are generated.",
    }


async def map_physical_event_assets(arguments: dict[str, Any]) -> dict[str, Any]:
    symbol = str(arguments.get("symbol", "")).upper()
    if symbol:
        return {"status": "ok", "symbol": symbol, "event_types": ASSET_EVENT_MAP.get(symbol, [])}
    return {"status": "ok", "asset_event_map": ASSET_EVENT_MAP}


async def score_physical_event_alpha(arguments: dict[str, Any]) -> dict[str, Any]:
    symbol = str(arguments.get("symbol", "")).upper()
    event_type = str(arguments.get("event_type", "")).lower()
    severity = _as_float(arguments.get("severity"), default=0.0)
    freshness_minutes = max(_as_float(arguments.get("freshness_minutes"), default=9999.0), 0.0)
    liquidity_proxy = max(_as_float(arguments.get("liquidity_proxy"), default=0.0), 0.0)

    mapped = event_type in {e.lower() for e in ASSET_EVENT_MAP.get(symbol, [])}
    freshness_score = max(0.0, 1.0 - min(freshness_minutes, 240.0) / 240.0)
    severity_score = min(abs(severity) / 10.0, 1.0)
    liquidity_score = min(liquidity_proxy / 1_000_000.0, 1.0)
    score = round((0.45 * severity_score) + (0.35 * freshness_score) + (0.20 * liquidity_score), 4)
    if not mapped:
        score = round(score * 0.35, 4)

    return {
        "status": "ok",
        "symbol": symbol,
        "event_type": event_type,
        "mapped_to_asset": mapped,
        "alpha_priority_score": score,
        "authority": "agent_memory",
        "broker_truth": False,
        "decision_use": "research_queue_only",
    }


async def get_sonia_air_heartbeat(arguments: dict[str, Any]) -> dict[str, Any]:
    root = Path(os.getenv("ALGOCHAINS_CONTROL_TOWER", "/Users/treycsa/CascadeProjects/algochains-control-tower"))
    heartbeat_path = root / "state" / "sonia_air_heartbeat.json"
    if not heartbeat_path.exists():
        return {
            "status": "offline_or_not_bootstrapped",
            "node_id": "sonia_air",
            "heartbeat_path": str(heartbeat_path),
            "fallback": "treat Sonia Air as unavailable and route event polling back to Mac/desktop",
        }
    try:
        return {"status": "ok", "heartbeat": json.loads(heartbeat_path.read_text(encoding="utf-8"))}
    except Exception as exc:
        return {"status": "error", "node_id": "sonia_air", "error": str(exc)}


def _as_float(value: Any, *, default: float) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


PHYSICAL_WORLD_HANDLERS: dict[str, Handler] = {
    "get_physical_event_sources": get_physical_event_sources,
    "map_physical_event_assets": map_physical_event_assets,
    "score_physical_event_alpha": score_physical_event_alpha,
    "get_sonia_air_heartbeat": get_sonia_air_heartbeat,
}
