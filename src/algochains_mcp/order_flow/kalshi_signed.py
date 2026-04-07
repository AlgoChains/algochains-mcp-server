"""
Kalshi REST v2 — RSA-PSS signed requests (production pattern).

Kalshi does **not** use ``Authorization: Token ...`` for current trade APIs.
Per https://docs.kalshi.com/getting_started/quick_start_authenticated_requests:

  - KALSHI-ACCESS-KEY: API key ID
  - KALSHI-ACCESS-TIMESTAMP: ms since epoch (string)
  - KALSHI-ACCESS-SIGNATURE: base64(RSA-PSS-SHA256(timestamp + method + path_without_query))

``path_without_query`` must be only the path, e.g. ``/trade-api/v2/markets``.

Host defaults to ``https://api.elections.kalshi.com`` (override with KALSHI_API_HOST).
Demo: ``https://demo-api.kalshi.co``.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("algochains_mcp.order_flow.kalshi_signed")

DEFAULT_HOST = "https://api.elections.kalshi.com"


def _load_private_key_pem() -> Any:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    path = os.getenv("KALSHI_PRIVATE_KEY_PATH", "").strip()
    pem_inline = os.getenv("KALSHI_PRIVATE_KEY_PEM", "").strip()

    if path:
        raw = Path(path).expanduser().read_bytes()
    elif pem_inline:
        raw = pem_inline.encode("utf-8")
    else:
        return None, None

    try:
        key = serialization.load_pem_private_key(raw, password=None)
    except Exception as exc:
        logger.warning("Kalshi private key load failed: %s", exc)
        return None, None

    def sign(message: bytes) -> str:
        sig = key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return base64.b64encode(sig).decode("ascii")

    return sign, key


def kalshi_configured() -> bool:
    access = os.getenv("KALSHI_ACCESS_KEY", "").strip()
    has_pem = bool(os.getenv("KALSHI_PRIVATE_KEY_PATH", "").strip() or os.getenv("KALSHI_PRIVATE_KEY_PEM", "").strip())
    return bool(access and has_pem)


def kalshi_signed_get(
    path_with_leading_slash: str,
    query: Optional[dict[str, str]] = None,
) -> tuple[int, dict[str, Any] | str]:
    """
    GET a Kalshi trade-api path. ``path_with_leading_slash`` is e.g.
    ``/trade-api/v2/markets`` — no query string here; pass query dict separately.
    Returns (http_code, parsed_json_or_error_string).
    """
    sign_fn, _ = _load_private_key_pem()
    access_key = os.getenv("KALSHI_ACCESS_KEY", "").strip()

    if not sign_fn or not access_key:
        return 0, (
            "Kalshi RSA auth not configured. Set KALSHI_ACCESS_KEY and "
            "KALSHI_PRIVATE_KEY_PATH (PEM file) or KALSHI_PRIVATE_KEY_PEM. "
            "See https://docs.kalshi.com/getting_started/api_keys"
        )

    host = os.getenv("KALSHI_API_HOST", DEFAULT_HOST).rstrip("/")
    qs = urllib.parse.urlencode(query or {})
    full_url = f"{host}{path_with_leading_slash}"
    if qs:
        full_url = f"{full_url}?{qs}"

    ts = str(int(time.time() * 1000))
    sign_path = path_with_leading_slash.split("?")[0]
    msg = f"{ts}GET{sign_path}".encode("utf-8")
    signature = sign_fn(msg)

    req = urllib.request.Request(
        full_url,
        headers={
            "KALSHI-ACCESS-KEY": access_key,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "User-Agent": "AlgoChains-MCP/22.6",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8")
            try:
                parsed: dict[str, Any] | list[Any] = json.loads(body)
            except json.JSONDecodeError:
                return resp.status, body[:2000]
            if isinstance(parsed, dict):
                return resp.status, parsed
            return resp.status, {"_non_object_response": parsed}
    except urllib.error.HTTPError as exc:
        try:
            err_body = exc.read().decode("utf-8")
        except Exception:
            err_body = str(exc)
        return exc.code, err_body[:2000]
    except Exception as exc:
        logger.warning("Kalshi request failed: %s", exc)
        return 0, str(exc)


def kalshi_signed_post(
    path_with_leading_slash: str,
    body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any] | str]:
    """
    POST to a Kalshi trade-api path with RSA-PSS signing.
    Used for order placement and portfolio operations.

    Signing pattern (same as GET but method=POST):
      msg = timestamp + "POST" + path_without_query
    """
    sign_fn, _ = _load_private_key_pem()
    access_key = os.getenv("KALSHI_ACCESS_KEY", "").strip()

    if not sign_fn or not access_key:
        return 0, (
            "Kalshi RSA auth not configured. Set KALSHI_ACCESS_KEY and "
            "KALSHI_PRIVATE_KEY_PATH (PEM file) or KALSHI_PRIVATE_KEY_PEM. "
            "See https://docs.kalshi.com/getting_started/api_keys"
        )

    host = os.getenv("KALSHI_API_HOST", DEFAULT_HOST).rstrip("/")
    full_url = f"{host}{path_with_leading_slash}"

    ts = str(int(time.time() * 1000))
    sign_path = path_with_leading_slash.split("?")[0]
    msg = f"{ts}POST{sign_path}".encode("utf-8")
    signature = sign_fn(msg)

    payload = json.dumps(body or {}).encode("utf-8")
    req = urllib.request.Request(
        full_url,
        data=payload,
        headers={
            "KALSHI-ACCESS-KEY": access_key,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "Content-Type": "application/json",
            "User-Agent": "AlgoChains-MCP/22.8",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body_resp = resp.read().decode("utf-8")
            try:
                parsed: dict[str, Any] | list[Any] = json.loads(body_resp)
            except json.JSONDecodeError:
                return resp.status, body_resp[:2000]
            if isinstance(parsed, dict):
                return resp.status, parsed
            return resp.status, {"_non_object_response": parsed}
    except urllib.error.HTTPError as exc:
        try:
            err_body = exc.read().decode("utf-8")
        except Exception:
            err_body = str(exc)
        return exc.code, err_body[:2000]
    except Exception as exc:
        logger.warning("Kalshi POST request failed: %s", exc)
        return 0, str(exc)
