"""
Feature neutralization for Numerai predictions.

Implements the official feature neutralization approach from:
https://colab.research.google.com/github/numerai/example-scripts/blob/master/numerai/feature_neutralization.ipynb

ALL outputs of this module are labeled proxy_mmc — they are NOT bit-identical to
the Numerai server MMC transform (tie-kept rank → Gaussian → orthogonalize → covariance).
See §15, §25, HK-10 of the Numeroo Bot Blueprint.

Two-gate design (HK-7):
- Neutralization is a prediction-improvement step only.
- Staking decisions are out of scope here (Gate 2 = manual UI only).
"""
from __future__ import annotations

import logging
from typing import List, Optional

import numpy as np
import pandas as pd
from scipy import stats

logger = logging.getLogger(__name__)


def neutralize_predictions(
    predictions: pd.Series,
    features_df: pd.DataFrame,
    feature_names: List[str],
    proportion: float = 1.0,
) -> pd.Series:
    """
    Neutralize predictions against a set of features.

    Removes linear exposure to the provided features from predictions
    by projecting out their component.

    Parameters
    ----------
    predictions : Series of raw model predictions (same index as features_df).
    features_df : DataFrame containing feature columns to neutralize against.
    feature_names : list of feature column names to use for neutralization.
    proportion : fraction of neutralization to apply (0=none, 1=full).

    Returns
    -------
    Series of neutralized predictions. These are labeled proxy_mmc.
    """
    if proportion == 0:
        logger.info("neutralize_predictions: proportion=0, returning predictions unchanged")
        return predictions.copy()

    available_feats = [f for f in feature_names if f in features_df.columns]
    if not available_feats:
        logger.warning(
            "neutralize_predictions: no requested features found in features_df. "
            "Returning predictions unchanged."
        )
        return predictions.copy()

    # Align index
    common_idx = predictions.index.intersection(features_df.index)
    if len(common_idx) != len(predictions):
        logger.warning(
            "neutralize_predictions: %d prediction rows, %d common rows with features_df. "
            "Using intersection.",
            len(predictions),
            len(common_idx),
        )

    preds_aligned = predictions.loc[common_idx]
    feat_aligned = features_df.loc[common_idx, available_feats].fillna(0.0)

    # Gaussianize predictions before neutralization (per official notebook)
    preds_ranked = _gaussianize(preds_aligned.values)

    # Feature matrix
    feat_matrix = feat_aligned.values.astype(np.float32)

    # OLS: neutralize preds against features
    neutralized = _neutralize_array(preds_ranked, feat_matrix, proportion)

    # Re-rank to [0, 1] for submission compatibility
    result = _rank_to_01(neutralized)

    out = pd.Series(result, index=common_idx, name="proxy_mmc")
    logger.info(
        "neutralize_predictions: proportion=%.2f, %d features, "
        "output mean=%.4f std=%.4f (labeled proxy_mmc)",
        proportion,
        len(available_feats),
        float(out.mean()),
        float(out.std()),
    )
    return out


def _neutralize_array(
    scores: np.ndarray,
    exposures: np.ndarray,
    proportion: float = 1.0,
) -> np.ndarray:
    """
    Project out the linear component of exposures from scores.
    scores shape: (n,)
    exposures shape: (n, k)
    """
    scores = scores.reshape(-1, 1)
    exposures_with_intercept = np.hstack([exposures, np.ones((len(exposures), 1))])

    # Least-squares fit of scores onto exposures
    correction = exposures_with_intercept @ np.linalg.lstsq(
        exposures_with_intercept, scores, rcond=None
    )[0]

    neutralized = scores - proportion * correction
    return neutralized.ravel()


def _gaussianize(x: np.ndarray) -> np.ndarray:
    """Rank → Gaussian transform (ties averaged)."""
    ranks = stats.rankdata(x, method="average")
    n = len(ranks)
    # Scale to (0, 1) exclusive, then inverse normal
    scaled = ranks / (n + 1)
    return stats.norm.ppf(scaled)


def _rank_to_01(x: np.ndarray) -> np.ndarray:
    """Rank and re-scale to (0, 1) for submission. HK-5 guard output."""
    ranks = stats.rankdata(x, method="average")
    return ranks / (len(ranks) + 1)


def compute_feature_exposure(
    predictions: pd.Series,
    features_df: pd.DataFrame,
    feature_names: Optional[List[str]] = None,
) -> pd.Series:
    """
    Compute per-feature Spearman correlation of predictions with each feature.
    High exposure to a feature = low neutralization effectiveness.

    Returns Series of (feature_name → correlation).
    """
    cols = feature_names or [c for c in features_df.columns if c.startswith("feature_")]
    available = [c for c in cols if c in features_df.columns]

    common_idx = predictions.index.intersection(features_df.index)
    pred_vals = predictions.loc[common_idx]

    results = {}
    for col in available:
        feat_vals = features_df.loc[common_idx, col]
        corr, _ = stats.spearmanr(pred_vals, feat_vals)
        results[col] = float(corr) if not np.isnan(corr) else 0.0

    return pd.Series(results).sort_values(key=abs, ascending=False)
