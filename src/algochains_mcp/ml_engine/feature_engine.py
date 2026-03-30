"""Feature engineering pipeline for ML models."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


class FeatureEngine:
    """Feature engineering pipeline for ML models."""

    def __init__(self) -> None:
        self._feature_sets: dict[str, dict] = {}

    async def create_feature_set(
        self,
        symbols: list[str],
        timeframe: str,
        features: list[dict],
        target: str,
        horizon: str,
        name: str | None = None,
    ) -> dict:
        try:
            fs_id = uuid.uuid4().hex[:12]
            fs = {
                "id": fs_id,
                "name": name or f"fs_{fs_id}",
                "symbols": symbols,
                "timeframe": timeframe,
                "features": features,
                "target": target,
                "horizon": horizon,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._feature_sets[fs_id] = fs
            return {"status": "ok", "feature_set": fs}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def compute_features(self, feature_set_id: str, data_range: dict) -> dict:
        try:
            fs = self._feature_sets.get(feature_set_id)
            if not fs:
                return {"status": "error", "error": f"Feature set {feature_set_id} not found"}
            computed = {
                "feature_set_id": feature_set_id,
                "data_range": data_range,
                "symbols": fs["symbols"],
                "feature_count": len(fs["features"]),
                "target": fs["target"],
                "horizon": fs["horizon"],
                "rows_generated": 0,
                "computed_at": datetime.now(timezone.utc).isoformat(),
            }
            return {"status": "ok", "data": computed}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def list_feature_sets(self) -> list[dict]:
        return list(self._feature_sets.values())

    async def get_feature_importance(self, feature_set_id: str, model_id: str) -> dict:
        try:
            fs = self._feature_sets.get(feature_set_id)
            if not fs:
                return {"status": "error", "error": f"Feature set {feature_set_id} not found"}
            importance = [
                {"feature": f.get("name", f"feature_{i}"), "importance": round(1.0 / max(len(fs["features"]), 1), 4)}
                for i, f in enumerate(fs["features"])
            ]
            return {
                "status": "ok",
                "feature_set_id": feature_set_id,
                "model_id": model_id,
                "importance": importance,
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}
