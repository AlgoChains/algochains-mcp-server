"""Sync Tradovate position/order fetch for safety watchdogs."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from .config import TradovateConfig
from .paths import default_control_tower


def resolve_tradovate_access_token(
    control_tower: Path | None = None,
) -> tuple[str, str]:
    """Return (token, source). Prefer Token Guardian live file over stale .env."""
    root = control_tower or default_control_tower()

    token_file = root / "tradovate_token_live.txt"
    if token_file.exists():
        try:
            line = token_file.read_text(encoding="utf-8").splitlines()[0].strip()
            token = line.replace("Bearer ", "").strip().strip("'\"")
            if token:
                return token, "tradovate_token_live.txt"
        except OSError:
            pass

    env_token = (
        os.getenv("TRADOVATE_ACCESS_TOKEN", "")
        .strip()
        .strip("'\"")
        .replace("Bearer ", "")
    )
    if env_token:
        return env_token, "env:TRADOVATE_ACCESS_TOKEN"

    return "", "none"


def _load_tradovate_env(control_tower: Path) -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(control_tower / ".env")
    except ImportError:
        pass


def _normalize_working_order(raw: dict[str, Any]) -> dict[str, Any]:
    order_type = raw.get("orderType") or raw.get("ordType") or ""
    return {
        **raw,
        "orderType": order_type,
        "contractId": raw.get("contractId"),
    }


def _normalize_position(raw: dict[str, Any], symbol: str | None = None) -> dict[str, Any]:
    name = symbol or raw.get("contractName") or (raw.get("contract") or {}).get("name")
    return {
        **raw,
        "contractId": raw.get("contractId"),
        "contractName": name,
        "netPos": raw.get("netPos", 0),
        "netPrice": raw.get("netPrice"),
    }


async def _fetch_async(control_tower: Path) -> dict[str, Any]:
    from .brokers.tradovate import TradovateConnector

    _load_tradovate_env(control_tower)

    token, token_source = resolve_tradovate_access_token(control_tower)
    cfg = TradovateConfig()
    if token:
        cfg.access_token = token

    if not cfg.env:
        return {
            "error": "TRADOVATE_ENV not set — cannot determine which account to check",
            "status": "CONFIG_ERROR",
            "action": "Set TRADOVATE_ENV=demo or TRADOVATE_ENV=live in .env",
            "broker_verified": False,
            "token_source": token_source,
        }

    conn = TradovateConnector(cfg)
    try:
        connected = await conn.connect()
        if not connected:
            return {
                "error": "Tradovate authentication failed",
                "status": "ERROR",
                "broker_verified": False,
                "token_source": token_source,
                "environment": cfg.env.upper(),
            }

        positions_objs = await conn.get_positions()
        orders_objs = await conn.get_orders(status="open")

        positions = [_normalize_position(p.raw, p.symbol) for p in positions_objs if p.raw]
        working_orders = [
            _normalize_working_order(o.raw) for o in orders_objs if isinstance(o.raw, dict)
        ]

        return {
            "status": "OK",
            "positions": positions,
            "working_orders": working_orders,
            "environment": cfg.env.upper(),
            "broker_verified": True,
            "token_source": token_source,
            "account_spec": conn._account_spec,
        }
    except Exception as exc:
        message = str(exc)
        status = "ERROR"
        if "401" in message or "authentication" in message.lower():
            status = "DEGRADED"
        return {
            "error": f"Tradovate connection failed: {exc}",
            "status": status,
            "broker_verified": False,
            "token_source": token_source,
            "environment": cfg.env.upper(),
        }
    finally:
        try:
            await conn.disconnect()
        except Exception:
            pass


def fetch_tradovate_book(control_tower: Path | None = None) -> dict[str, Any]:
    """Fetch open positions and working orders via Token Guardian token when available."""
    root = control_tower or default_control_tower()
    return asyncio.run(_fetch_async(root))
