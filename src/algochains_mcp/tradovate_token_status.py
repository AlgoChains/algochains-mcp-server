"""Sanitized Tradovate token and Token Guardian health summaries."""
from __future__ import annotations

import base64
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_TOKEN_KEYS = {
    "access_token",
    "accesstoken",
    "accessToken",
    "token",
    "TRADOVATE_ACCESS_TOKEN",
}

_EXPIRY_KEYS = {
    "exp",
    "expires_at",
    "expiresAt",
    "expires_at_epoch",
    "expiration_time",
    "expirationTime",
}

_SECRET_KEY_PARTS = (
    "token",
    "secret",
    "password",
    "credential",
    "authorization",
    "bearer",
    "refresh",
)

_GUARDIAN_SIGNAL_KEY_PARTS = (
    "status",
    "state",
    "severity",
    "failure",
    "fail",
    "error",
    "reason",
    "tier",
    "success",
    "check",
    "updated",
    "generated",
    "captured",
    "message",
    "detail",
)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _strip_token(raw: str) -> str:
    token = raw.strip().strip("'\"")
    if token.startswith("Bearer "):
        token = token.removeprefix("Bearer ").strip()
    return token.splitlines()[0].strip() if token.splitlines() else token


def _decode_jwt_exp(token: str) -> float | None:
    jwt = _strip_token(token)
    if jwt.count(".") != 2:
        return None
    try:
        payload_segment = jwt.split(".")[1]
        padded = payload_segment + "=" * (-len(payload_segment) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        exp = payload.get("exp")
        return float(exp) if exp is not None else None
    except Exception:
        return None


def _parse_expiry(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            pass
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _find_value(data: Any, keys: set[str]) -> Any:
    if isinstance(data, dict):
        for key, value in data.items():
            if key in keys:
                return value
        for value in data.values():
            found = _find_value(value, keys)
            if found is not None:
                return found
    elif isinstance(data, list):
        for value in data:
            found = _find_value(value, keys)
            if found is not None:
                return found
    return None


def _state_age_seconds(path: Path, now: float) -> int | None:
    try:
        return max(0, int(now - path.stat().st_mtime))
    except OSError:
        return None


def _summarize_source(
    source: str,
    path: Path,
    *,
    token: str = "",
    expires_at: float | None = None,
    now: float,
) -> dict[str, Any]:
    if token and expires_at is None:
        expires_at = _decode_jwt_exp(token)

    summary: dict[str, Any] = {
        "source": source,
        "present": bool(token),
        "file_present": path.exists(),
    }
    age = _state_age_seconds(path, now)
    if age is not None:
        summary["state_age_seconds"] = age
    if expires_at is not None:
        summary["expires_in_seconds"] = int(expires_at - now)
        summary["expired"] = expires_at <= now
        summary["expires_at_utc"] = datetime.fromtimestamp(
            expires_at,
            tz=timezone.utc,
        ).isoformat().replace("+00:00", "Z")
    return summary


def _source_from_json(source: str, path: Path, now: float) -> dict[str, Any] | None:
    if not path.exists():
        return None
    data = _read_json(path)
    token = _find_value(data, _TOKEN_KEYS)
    expires_at = _parse_expiry(_find_value(data, _EXPIRY_KEYS))
    return _summarize_source(
        source,
        path,
        token=_strip_token(str(token or "")),
        expires_at=expires_at,
        now=now,
    )


def _source_from_env_file(source: str, path: Path, now: float) -> dict[str, Any] | None:
    if not path.exists():
        return None
    token = ""
    try:
        for raw_line in path.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == "TRADOVATE_ACCESS_TOKEN":
                token = _strip_token(value)
                break
    except OSError:
        return None
    return _summarize_source(source, path, token=token, now=now)


def _source_from_live_token(control_tower: Path, now: float) -> dict[str, Any] | None:
    path = control_tower / "tradovate_token_live.txt"
    if not path.exists():
        return None
    try:
        token = _strip_token(path.read_text())
    except OSError:
        token = ""
    return _summarize_source("tradovate_token_live.txt", path, token=token, now=now)


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SECRET_KEY_PARTS)


def _sanitize_guardian_value(value: Any, depth: int = 0) -> Any:
    if depth > 3:
        return None
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, child in value.items():
            if _is_secret_key(str(key)):
                continue
            lowered = str(key).lower()
            if not any(part in lowered for part in _GUARDIAN_SIGNAL_KEY_PARTS):
                continue
            clean = _sanitize_guardian_value(child, depth + 1)
            if clean is not None:
                sanitized[str(key)] = clean
        return sanitized
    if isinstance(value, list):
        cleaned = [_sanitize_guardian_value(item, depth + 1) for item in value[:10]]
        return [item for item in cleaned if item is not None]
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, str):
            return value[:500]
        return value
    return str(value)[:500]


def _guardian_state(control_tower: Path, now: float) -> dict[str, Any]:
    candidates = (
        "state/tradovate_token_guardian_state.json",
        "state/tradovate_token_guardian.json",
        "state/token_guardian_state.json",
        "state/tradovate_guardian_state.json",
        "tradovate_token_guardian_state.json",
    )
    for rel in candidates:
        path = control_tower / rel
        if not path.exists():
            continue
        data = _read_json(path)
        summary = _sanitize_guardian_value(data)
        if not isinstance(summary, dict):
            summary = {}
        summary["source"] = rel
        age = _state_age_seconds(path, now)
        if age is not None:
            summary["state_age_seconds"] = age
        return summary
    return {"status": "unknown", "detail": "Token Guardian state file not found"}


def summarize_tradovate_token_state(
    control_tower: str | Path,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """Return a secret-free summary of Tradovate token health artifacts.

    The control-tower has used several artifact layouts over time. This helper
    preserves the legacy top-level ``present`` / ``expires_in_seconds`` contract
    while reporting each source separately so stale and fresh artifacts are easy
    to distinguish during incidents.
    """
    now_epoch = time.time() if now is None else float(now)
    root = Path(control_tower)

    sources: list[dict[str, Any]] = []
    for source in (
        _source_from_live_token(root, now_epoch),
        _source_from_json("state/tradovate_token.json", root / "state" / "tradovate_token.json", now_epoch),
        _source_from_json("tradovate_session.json", root / "tradovate_session.json", now_epoch),
        _source_from_env_file(".env:TRADOVATE_ACCESS_TOKEN", root / ".env", now_epoch),
    ):
        if source is not None:
            sources.append(source)

    present_sources = [source for source in sources if source.get("present")]
    expiry_sources = [
        source for source in present_sources
        if isinstance(source.get("expires_in_seconds"), int)
    ]
    best_expiry = max(
        (int(source["expires_in_seconds"]) for source in expiry_sources),
        default=None,
    )

    present = bool(present_sources)
    if not present:
        status = "missing"
    elif best_expiry is None:
        status = "unknown_expiry"
    elif best_expiry <= 0:
        status = "expired"
    elif best_expiry <= 3600:
        status = "expiring_soon"
    else:
        status = "ok"

    summary: dict[str, Any] = {
        "present": present,
        "status": status,
        "sources": sources,
        "guardian": _guardian_state(root, now_epoch),
    }
    if best_expiry is not None:
        summary["expires_in_seconds"] = best_expiry
    if present_sources:
        summary["primary_source"] = present_sources[0]["source"]
    if status in {"missing", "expired", "expiring_soon"}:
        summary["action"] = "Run or inspect tradovate_token_guardian.py on the control tower"

    return summary
