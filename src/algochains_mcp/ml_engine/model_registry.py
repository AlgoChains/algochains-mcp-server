"""MLflow-style model versioning and promotion."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


class ModelRegistry:
    """MLflow-style model versioning and promotion."""

    VALID_STAGES = ("dev", "staging", "production", "archived")

    def __init__(self, trainer: Any = None) -> None:
        self._trainer = trainer
        self._registry: dict[str, dict] = {}

    async def register(self, model_id: str, name: str, version: str | None = None, metrics: dict | None = None, tags: list[str] | None = None) -> dict:
        try:
            registry_id = uuid.uuid4().hex[:12]
            entry = {
                "registry_id": registry_id,
                "model_id": model_id,
                "name": name,
                "version": version or "1.0.0",
                "metrics": metrics or {},
                "tags": tags or [],
                "stage": "dev",
                "registered_at": datetime.now(timezone.utc).isoformat(),
            }
            self._registry[registry_id] = entry
            return {"status": "ok", "entry": entry}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def promote(self, registry_id: str, stage: str) -> dict:
        try:
            if stage not in self.VALID_STAGES:
                return {"status": "error", "error": f"Invalid stage: {stage}. Must be one of {self.VALID_STAGES}"}
            entry = self._registry.get(registry_id)
            if entry:
                entry["stage"] = stage
                entry["promoted_at"] = datetime.now(timezone.utc).isoformat()
            if self._trainer and entry:
                model = self._trainer.get_model(entry["model_id"])
                if model:
                    model["stage"] = stage
                    model["promoted_at"] = datetime.now(timezone.utc).isoformat()
            return {
                "status": "ok",
                "registry_id": registry_id,
                "new_stage": stage,
                "promoted_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def list_models(self, stage: str | None = None, name_filter: str | None = None) -> dict:
        try:
            entries = list(self._registry.values())
            if stage:
                entries = [e for e in entries if e.get("stage") == stage]
            if name_filter:
                entries = [e for e in entries if name_filter.lower() in e.get("name", "").lower()]
            return {"status": "ok", "models": entries, "count": len(entries)}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def compare(self, model_ids: list[str]) -> dict:
        try:
            comparisons = []
            for mid in model_ids:
                for entry in self._registry.values():
                    if entry.get("model_id") == mid:
                        comparisons.append({"model_id": mid, "metrics": entry.get("metrics", {}), "stage": entry.get("stage")})
                        break
                else:
                    if self._trainer:
                        model = self._trainer.get_model(mid)
                        if model:
                            comparisons.append({"model_id": mid, "metrics": model.get("metrics", {}), "stage": model.get("stage")})
            return {"status": "ok", "comparisons": comparisons}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def archive(self, registry_id: str, reason: str | None = None) -> dict:
        try:
            result = await self.promote(registry_id, "archived")
            if reason:
                result["reason"] = reason
            return result
        except Exception as e:
            return {"status": "error", "error": str(e)}
