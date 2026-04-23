"""
LightGBM baseline trainer for Numerai Classic.

Based on Hello Numerai notebook:
https://colab.research.google.com/github/numerai/example-scripts/blob/master/numerai/hello_numerai.ipynb

HK-16: models/numerai/ namespace only. Never touches cl_feature_names.pkl, MNQ PKL,
or any futures model artifact.
HK-1: era-based splits with embargo enforced via era_utils.era_split.

CPU-based training (LightGBM is efficient without GPU for this workload).
"""
from __future__ import annotations

import hashlib
import json
import logging
import pickle
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .config import NumeraiConfig, get_config
from .era_utils import era_split

logger = logging.getLogger(__name__)

# LightGBM parameters matching the Hello Numerai baseline
_LGBM_PARAMS = {
    "n_estimators": 2000,
    "learning_rate": 0.01,
    "max_depth": 5,
    "num_leaves": 31,
    "colsample_bytree": 0.1,
    "subsample": 0.5,
    "min_child_samples": 20,
    "reg_lambda": 1.0,
    "random_state": 42,
    "n_jobs": -1,
    "verbose": -1,
}


def train_baseline(
    train_df: pd.DataFrame,
    feature_names: List[str],
    target_col: str = "target_cyrus20",
    era_col: str = "era",
    holdout_n: int = 4,
    embargo_eras: int = 4,
    models_dir: Optional[Path] = None,
    round_id: Optional[int] = None,
    cfg: Optional[NumeraiConfig] = None,
) -> Dict:
    """
    Train a LightGBM baseline model with era-based CV.

    Parameters
    ----------
    train_df : full training DataFrame (era + feature cols + target).
    feature_names : list of feature columns to use.
    target_col : target column name.
    era_col : era column name.
    holdout_n : eras to reserve as holdout (HK-1, §7 rule 7: min 4).
    embargo_eras : embargo gap between train and val (min 4).
    models_dir : where to save the model PKL.
    round_id : current round (used in artifact naming).

    Returns
    -------
    dict with model_path, val_proxy_corr_mean, val_proxy_corr_std, era_count,
    feature_count, params.
    """
    try:
        import lightgbm as lgb
    except ImportError:
        raise ImportError(
            "lightgbm is required for training. Install via: pip install lightgbm"
        )

    cfg = cfg or get_config()
    models_dir = models_dir or cfg.models_dir()

    # Era split (HK-1: no random split)
    train_split, val_split = era_split(
        train_df,
        holdout_n=holdout_n,
        embargo_gap=embargo_eras,
        era_col=era_col,
    )

    available_feats = [f for f in feature_names if f in train_df.columns]
    if len(available_feats) == 0:
        raise ValueError("No feature columns available in train_df. Check feature_names list.")

    logger.info(
        "train_baseline: %d train rows, %d val rows, %d features, target=%s",
        len(train_split),
        len(val_split),
        len(available_feats),
        target_col,
    )

    X_train = train_split[available_feats].fillna(0.0).values.astype(np.float32)
    y_train = train_split[target_col].fillna(0.5).values

    X_val = val_split[available_feats].fillna(0.0).values.astype(np.float32)
    y_val = val_split[target_col].fillna(0.5).values

    model = lgb.LGBMRegressor(**_LGBM_PARAMS)
    model.fit(
        X_train,
        y_train,
        eval_set=[(X_val, y_val)],
        callbacks=[lgb.early_stopping(stopping_rounds=50, verbose=False)],
    )

    # Per-era validation correlation
    val_predictions = pd.Series(
        model.predict(X_val),
        index=val_split.index,
        name="proxy_corr",
    )

    from .validate import validate_metrics

    metrics = validate_metrics(
        val_predictions,
        val_split[[era_col, target_col]],
        target_col=target_col,
        era_col=era_col,
    )

    # Save model artifact — isolated to models/numerai/ (HK-16)
    round_tag = f"r{round_id}" if round_id else "latest"
    model_path = models_dir / f"model_{round_tag}.pkl"
    meta_path = models_dir / f"model_{round_tag}_meta.json"

    with open(model_path, "wb") as f:
        pickle.dump(
            {
                "model": model,
                "feature_names": available_feats,
                "target_col": target_col,
                "dataset_version": cfg.version,
                "round_id": round_id,
                "params": _LGBM_PARAMS,
            },
            f,
        )

    meta = {
        "model_path": str(model_path),
        "feature_count": len(available_feats),
        "train_era_count": train_split[era_col].nunique(),
        "val_era_count": val_split[era_col].nunique(),
        "holdout_n": holdout_n,
        "embargo_eras": embargo_eras,
        "proxy_corr_mean": metrics["proxy_corr_mean"],
        "proxy_corr_std": metrics["proxy_corr_std"],
        "era_stability": metrics["era_stability"],
        "dataset_version": cfg.version,
        "round_id": round_id,
        "params": _LGBM_PARAMS,
        "proxy_mmc_note": metrics["proxy_mmc_note"],
        "model_checksum": _sha256_pickle(model_path),
    }

    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)

    logger.info(
        "train_baseline: model saved to %s | proxy_corr_mean=%.4f std=%.4f era_stability=%.2f",
        model_path,
        metrics["proxy_corr_mean"],
        metrics["proxy_corr_std"],
        metrics["era_stability"],
    )

    return meta


def load_model(model_path: Path) -> dict:
    """Load a saved model artifact. Returns dict with 'model' and 'feature_names'."""
    with open(model_path, "rb") as f:
        return pickle.load(f)


def predict(model_artifact: dict, live_df: pd.DataFrame) -> pd.Series:
    """
    Generate predictions from a loaded model artifact.

    Returns Series of raw [0, 1] predictions (ranked).
    """
    from scipy.stats import rankdata

    model = model_artifact["model"]
    feature_names: List[str] = model_artifact["feature_names"]

    available = [f for f in feature_names if f in live_df.columns]
    if len(available) != len(feature_names):
        missing = set(feature_names) - set(live_df.columns)
        raise ValueError(
            f"Live data missing {len(missing)} model features (HK-4). "
            f"First 5 missing: {sorted(missing)[:5]}. "
            "Ensure train and live use the same dataset version."
        )

    X_live = live_df[available].fillna(0.0).values.astype(np.float32)
    raw_preds = model.predict(X_live)

    # Rank to [0, 1] (HK-5: predictions must be in range)
    ranked = rankdata(raw_preds, method="average")
    normalized = ranked / (len(ranked) + 1)

    return pd.Series(normalized, index=live_df.index, name="prediction")


def _sha256_pickle(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]
