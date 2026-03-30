"""MLflow-style model versioning and promotion."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class ModelRegistry:
    """MLflow-style model versioning and promotion."""

    VALID_STAGES = ("dev", "staging", "production", "archived")

    def __init__(self, trainer: Any = None) -> None:
        self._trainer = trainer

    async def register(self, model_id: str, metadata: dict) -> dict:
        try:
            return {
                "status": "ok",
                "model_id": model_id,
                "metadata": metadata,
                "registered_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def promote(self, model_id: str, target_stage: str, reason: str) -> dict:
        try:
            if target_stage not in self.VALID_STAGES:
                return {"status": "error", "error": f"Invalid stage: {target_stage}. Must be one of {self.VALID_STAGES}"}
            if self._trainer:
                model = self._trainer.get_model(model_id)
                if model:
                    model["stage"] = target_stage
                    model["promoted_at"] = datetime.now(timezone.utc).isoformat()
            return {
                "status": "ok",
                "model_id": model_id,
                "new_stage": target_stage,
                "reason": reason,
                "promoted_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def list_models(self, status: str | None = None, sort_by: str | None = None) -> dict:
        try:
            models = []
            if self._trainer:
                for m in self._trainer._models.values():
                    if status and m.get("stage") != status:
                        continue
                    models.append(m)
            if sort_by and models:
                models.sort(key=lambda x: x.get(sort_by, ""), reverse=True)
            return {"status": "ok", "models": models, "count": len(models)}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def compare(self, model_ids: list[str], eval_range: dict | None = None, metrics: list[str] | None = None) -> dict:
        try:
            comparisons = []
            if self._trainer:
                for mid in model_ids:
                    model = self._trainer.get_model(mid)
                    if model:
                        comparisons.append({"model_id": mid, "metrics": model.get("metrics", {}), "stage": model.get("stage")})
            return {"status": "ok", "comparisons": comparisons, "eval_range": eval_range}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def archive(self, model_id: str) -> dict:
        return await self.promote(model_id, "archived", "Archived by user")
