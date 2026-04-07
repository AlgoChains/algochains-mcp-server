"""White-label customization engine for tenant branding. State persisted to state/white_label.json."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("algochains_mcp.cloud_saas.white_label")

_STATE_DIR = Path(os.getenv("ALGOCHAINS_STATE_DIR", "state"))
_WHITE_LABEL_FILE = _STATE_DIR / "white_label.json"

_VALID_COLORS_RE = __import__("re").compile(r"^#[0-9a-fA-F]{3,6}$")


def _load_configs() -> dict[str, dict]:
    if _WHITE_LABEL_FILE.exists():
        try:
            return json.loads(_WHITE_LABEL_FILE.read_text())
        except Exception as e:
            logger.warning("Could not load white_label file: %s", e)
    return {}


def _save_configs(configs: dict[str, dict]) -> None:
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        _WHITE_LABEL_FILE.write_text(json.dumps(configs, indent=2, default=str))
    except Exception as e:
        logger.error("Could not persist white_label configs: %s", e)


class WhiteLabelEngine:
    """White-label customization for tenant branding. Configs persist across restarts."""

    def __init__(self) -> None:
        self._configs: dict[str, dict] = _load_configs()

    async def configure(self, tenant_id: str, branding: dict) -> dict:
        try:
            primary_color = branding.get("primary_color", "#1a1a2e")
            if primary_color and not _VALID_COLORS_RE.match(primary_color):
                return {
                    "status": "error",
                    "error": f"primary_color must be a hex color (e.g. #1a1a2e), got: {primary_color!r}",
                }

            now = datetime.now(timezone.utc).isoformat()
            existing = self._configs.get(tenant_id, {})
            config: dict[str, Any] = {
                **existing,
                "tenant_id": tenant_id,
                "logo_url": branding.get("logo_url", existing.get("logo_url", "")),
                "primary_color": primary_color,
                "company_name": branding.get("company_name", existing.get("company_name", "")),
                "custom_domain": branding.get("custom_domain", existing.get("custom_domain", "")),
                "favicon_url": branding.get("favicon_url", existing.get("favicon_url", "")),
                "accent_color": branding.get("accent_color", existing.get("accent_color", "")),
                "support_email": branding.get("support_email", existing.get("support_email", "")),
                "updated_at": now,
            }
            if "created_at" not in config:
                config["created_at"] = now
            self._configs[tenant_id] = config
            _save_configs(self._configs)
            return {"status": "ok", "config": config}
        except Exception as e:
            logger.error("configure failed: %s", e)
            return {"status": "error", "error": str(e)}

    async def get_config(self, tenant_id: str) -> dict:
        try:
            config = self._configs.get(tenant_id)
            if not config:
                return {
                    "status": "error",
                    "error": f"No white-label config for tenant '{tenant_id}'. Call configure() first.",
                }
            return {"status": "ok", "config": config}
        except Exception as e:
            logger.error("get_config failed: %s", e)
            return {"status": "error", "error": str(e)}

    async def list_configs(self) -> dict:
        return {
            "status": "ok",
            "configs": list(self._configs.values()),
            "count": len(self._configs),
        }

    async def delete_config(self, tenant_id: str) -> dict:
        if tenant_id not in self._configs:
            return {"status": "error", "error": f"No white-label config for tenant '{tenant_id}'"}
        del self._configs[tenant_id]
        _save_configs(self._configs)
        return {"status": "ok", "deleted_tenant_id": tenant_id}
