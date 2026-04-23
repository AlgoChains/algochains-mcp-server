"""
Numerai model zoo — 2-3 diverse architectures for MMC diversity.

Diversity principle (§2.1, Michael Oliver): "Okay performance + high uniqueness >
high performance + redundancy." Different feature subsets = different meta-model
contributions = higher combined MMC when ensembled.

Three architectures:
1. baseline_lgbm    — LightGBM, medium features, standard params (Hello Numerai notebook)
2. sparse_lgbm      — LightGBM, small feature subset, higher regularization (low corr with #1)
3. xgb_ensemble     — XGBoost, different feature subset with random seed diversity

Each model is saved to models/numerai/zoo/<name>_r<round>.pkl
Ensemble = weighted average of predictions (equal weight by default).

HK-16: All zoo artifacts in models/numerai/ — never in models/cl_* or models/mnq_*.
"""
from __future__ import annotations

import json
import logging
import pickle
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .config import NumeraiConfig, get_config
from .era_utils import era_split

logger = logging.getLogger(__name__)

# Model zoo definitions — each has a unique feature strategy and random seed
ZOO_SPECS = {
    "baseline_lgbm": {
        "feature_set_strategy": "medium_all",
        "params": {
            "n_estimators": 2000,
            "learning_rate": 0.01,
            "max_depth": 5,
            "num_leaves": 31,
            "colsample_bytree": 0.1,
            "subsample": 0.5,
            "min_child_samples": 20,
            "reg_lambda": 1.0,
            "random_state": 42,
        },
        "model_type": "lgbm",
        "weight": 1.0,
        "description": "Standard LightGBM baseline (Hello Numerai notebook params)",
    },
    "sparse_lgbm": {
        "feature_set_strategy": "random_half",
        "params": {
            "n_estimators": 1000,
            "learning_rate": 0.005,
            "max_depth": 4,
            "num_leaves": 15,
            "colsample_bytree": 0.05,
            "subsample": 0.4,
            "min_child_samples": 50,
            "reg_lambda": 5.0,
            "random_state": 137,
        },
        "model_type": "lgbm",
        "weight": 1.0,
        "description": "Sparse LightGBM — high regularization, 50% random feature subset (MMC diversity)",
    },
    "xgb_diverse": {
        "feature_set_strategy": "random_third",
        "params": {
            "n_estimators": 500,
            "learning_rate": 0.02,
            "max_depth": 4,
            "subsample": 0.6,
            "colsample_bytree": 0.08,
            "reg_lambda": 2.0,
            "reg_alpha": 0.5,
            "random_state": 271,
            "n_jobs": -1,
            "verbosity": 0,
        },
        "model_type": "xgb",
        "weight": 1.0,
        "description": "XGBoost with 1/3 random feature subset (different boosting algorithm = MMC diversity)",
    },
}


def _select_features(
    all_features: List[str],
    strategy: str,
    seed: int = 42,
) -> List[str]:
    """Select feature subset per strategy."""
    rng = np.random.default_rng(seed)
    if strategy == "medium_all":
        return all_features
    elif strategy == "random_half":
        n = max(1, len(all_features) // 2)
        return list(rng.choice(all_features, size=n, replace=False))
    elif strategy == "random_third":
        n = max(1, len(all_features) // 3)
        return list(rng.choice(all_features, size=n, replace=False))
    else:
        return all_features


def train_zoo(
    train_df: pd.DataFrame,
    feature_names: List[str],
    target_col: str = "target_cyrus20",
    era_col: str = "era",
    holdout_n: int = 4,
    embargo_eras: int = 4,
    models_dir: Optional[Path] = None,
    round_id: Optional[int] = None,
    cfg: Optional[NumeraiConfig] = None,
    zoo_names: Optional[List[str]] = None,
) -> Dict[str, Dict]:
    """
    Train all zoo models and return their metadata.

    Returns dict: {model_name: meta_dict}.
    Each meta_dict includes model_path, feature_count, proxy_corr_mean, etc.
    """
    cfg = cfg or get_config()
    models_dir = models_dir or (cfg.models_dir() / "zoo")
    models_dir.mkdir(parents=True, exist_ok=True)

    names_to_train = zoo_names or list(ZOO_SPECS.keys())
    results = {}

    train_split, val_split = era_split(
        train_df, holdout_n=holdout_n, embargo_gap=embargo_eras, era_col=era_col
    )

    for model_name in names_to_train:
        if model_name not in ZOO_SPECS:
            logger.warning("train_zoo: unknown model %s, skipping", model_name)
            continue

        spec = ZOO_SPECS[model_name]
        logger.info("train_zoo: training %s (%s)", model_name, spec["description"])

        selected_feats = _select_features(
            feature_names,
            spec["feature_set_strategy"],
            seed=spec["params"].get("random_state", 42),
        )
        available_feats = [f for f in selected_feats if f in train_df.columns]

        try:
            meta = _train_single(
                model_name=model_name,
                spec=spec,
                train_split=train_split,
                val_split=val_split,
                feature_names=available_feats,
                target_col=target_col,
                era_col=era_col,
                models_dir=models_dir,
                round_id=round_id,
                dataset_version=cfg.version,
            )
            results[model_name] = meta
        except Exception as exc:
            logger.error("train_zoo: %s failed: %s", model_name, exc)
            results[model_name] = {"error": str(exc), "model_name": model_name}

    return results


def predict_ensemble(
    model_results: Dict[str, Dict],
    live_df: pd.DataFrame,
    models_dir: Path,
    feature_names: List[str],
    round_id: Optional[int] = None,
) -> pd.Series:
    """
    Generate ensemble predictions from all zoo models.

    Equal-weighted average (can extend to performance-weighted in Phase 4).
    Returns Series of ensemble predictions in [0, 1].
    """
    from scipy.stats import rankdata

    all_preds = []
    weights = []

    for model_name, meta in model_results.items():
        if "error" in meta:
            logger.warning("predict_ensemble: skipping %s (training failed)", model_name)
            continue

        model_path = Path(meta.get("model_path", ""))
        if not model_path.exists():
            logger.warning("predict_ensemble: model file missing for %s", model_name)
            continue

        try:
            with open(model_path, "rb") as f:
                artifact = pickle.load(f)

            model = artifact["model"]
            model_feats = artifact["feature_names"]
            avail = [f for f in model_feats if f in live_df.columns]

            X_live = live_df[avail].fillna(0.0).values.astype(np.float32)
            raw_preds = model.predict(X_live)
            all_preds.append(raw_preds)
            weights.append(ZOO_SPECS.get(model_name, {}).get("weight", 1.0))
            logger.info("predict_ensemble: %s → %d predictions", model_name, len(raw_preds))
        except Exception as exc:
            logger.warning("predict_ensemble: %s predict failed: %s", model_name, exc)

    if not all_preds:
        raise RuntimeError("No zoo models produced predictions. Check training logs.")

    total_weight = sum(weights)
    ensemble_raw = sum(p * w / total_weight for p, w in zip(all_preds, weights))
    ranked = rankdata(ensemble_raw, method="average")
    normalized = ranked / (len(ranked) + 1)

    logger.info(
        "predict_ensemble: %d models averaged, n=%d, mean=%.4f std=%.4f",
        len(all_preds),
        len(normalized),
        float(normalized.mean()),
        float(normalized.std()),
    )
    return pd.Series(normalized, index=live_df.index, name="prediction")


def _train_single(
    model_name: str,
    spec: Dict,
    train_split: pd.DataFrame,
    val_split: pd.DataFrame,
    feature_names: List[str],
    target_col: str,
    era_col: str,
    models_dir: Path,
    round_id: Optional[int],
    dataset_version: str,
) -> Dict:
    """Train one zoo model and save artifact."""
    from scipy.stats import spearmanr

    X_train = train_split[feature_names].fillna(0.0).values.astype(np.float32)
    y_train = train_split[target_col].fillna(0.5).values
    X_val = val_split[feature_names].fillna(0.0).values.astype(np.float32)
    y_val = val_split[target_col].fillna(0.5).values

    params = dict(spec["params"])

    if spec["model_type"] == "lgbm":
        import lightgbm as lgb
        model = lgb.LGBMRegressor(**params)
        model.fit(
            X_train, y_train,
            eval_set=[(X_val, y_val)],
            callbacks=[lgb.early_stopping(50, verbose=False)],
        )
    elif spec["model_type"] == "xgb":
        try:
            import xgboost as xgb
        except ImportError:
            raise ImportError("xgboost is required for xgb_diverse. pip install xgboost")
        model = xgb.XGBRegressor(**params)
        model.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=False)
    else:
        raise ValueError(f"Unknown model_type: {spec['model_type']}")

    val_preds = model.predict(X_val)
    corr, _ = spearmanr(val_preds, y_val)

    round_tag = f"r{round_id}" if round_id else "latest"
    model_path = models_dir / f"{model_name}_{round_tag}.pkl"

    with open(model_path, "wb") as f:
        pickle.dump({
            "model": model,
            "feature_names": feature_names,
            "target_col": target_col,
            "model_name": model_name,
            "spec": spec,
            "dataset_version": dataset_version,
            "round_id": round_id,
        }, f)

    meta = {
        "model_name": model_name,
        "model_path": str(model_path),
        "feature_count": len(feature_names),
        "proxy_corr_val": float(corr) if not np.isnan(corr) else 0.0,
        "model_type": spec["model_type"],
        "feature_strategy": spec["feature_set_strategy"],
        "description": spec["description"],
        "dataset_version": dataset_version,
        "round_id": round_id,
    }

    logger.info(
        "train_zoo: %s → proxy_corr=%.4f, %d features, saved to %s",
        model_name, meta["proxy_corr_val"], len(feature_names), model_path,
    )
    return meta
