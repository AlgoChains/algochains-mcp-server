"""
Validation metrics for Numerai models.

All metrics are labeled proxy_corr / proxy_mmc — they are NOT bit-identical to the
Numerai server scoring chain (HK-10, §25).

Per-era stability is the primary signal. Single-era or aggregate CORR alone is
insufficient for live performance prediction.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from scipy import stats

logger = logging.getLogger(__name__)

PROXY_LABEL_NOTE = (
    "All metrics in this report are proxy_corr / proxy_mmc. "
    "They are NOT bit-identical to Numerai server scoring. "
    "Only the leaderboard mmcRep after scoring is authoritative (§25, HK-10)."
)


def validate_metrics(
    predictions: pd.Series,
    holdout_df: pd.DataFrame,
    target_col: str = "target_cyrus20",
    era_col: str = "era",
    neutralized: bool = False,
) -> Dict:
    """
    Compute per-era and aggregate validation metrics.

    Parameters
    ----------
    predictions : Series of predictions indexed like holdout_df.
    holdout_df : holdout DataFrame containing era and target columns.
    target_col : name of the target column.
    era_col : name of the era column.
    neutralized : whether predictions have been feature-neutralized.

    Returns
    -------
    dict with proxy_corr_mean, proxy_corr_std, era_stability, per_era, proxy_mmc_note.
    """
    if era_col not in holdout_df.columns:
        raise ValueError(f"holdout_df missing era column '{era_col}'")
    if target_col not in holdout_df.columns:
        raise ValueError(
            f"holdout_df missing target column '{target_col}'. "
            "Check that the target column name matches the dataset version."
        )

    common_idx = predictions.index.intersection(holdout_df.index)
    if len(common_idx) == 0:
        raise ValueError(
            "No common rows between predictions and holdout_df. "
            "Check that predictions were generated from holdout data."
        )

    preds = predictions.loc[common_idx]
    holdout = holdout_df.loc[common_idx]

    # Per-era Spearman correlation
    per_era_corr: Dict[str, float] = {}
    for era, era_df in holdout.groupby(era_col):
        era_preds = preds.loc[era_df.index]
        era_targets = era_df[target_col]
        corr, _ = stats.spearmanr(era_preds, era_targets)
        per_era_corr[str(era)] = float(corr) if not np.isnan(corr) else 0.0

    corr_values = np.array(list(per_era_corr.values()))

    # Summary stats
    proxy_corr_mean = float(np.mean(corr_values)) if len(corr_values) > 0 else 0.0
    proxy_corr_std = float(np.std(corr_values)) if len(corr_values) > 0 else 0.0
    proxy_corr_sharpe = (
        float(proxy_corr_mean / proxy_corr_std) if proxy_corr_std > 0 else 0.0
    )
    era_stability = float(np.mean(corr_values > 0)) if len(corr_values) > 0 else 0.0

    # Calibration drift: check if mean prediction ~= 0.5 (Numerai convention)
    pred_mean = float(preds.mean())
    pred_std = float(preds.std())
    calibration_ok = 0.3 <= pred_mean <= 0.7 and pred_std > 0.05

    report = {
        "proxy_corr_mean": proxy_corr_mean,
        "proxy_corr_std": proxy_corr_std,
        "proxy_corr_sharpe": proxy_corr_sharpe,
        "era_stability": era_stability,  # fraction of eras with positive CORR
        "n_eras": len(per_era_corr),
        "n_rows": len(common_idx),
        "prediction_mean": pred_mean,
        "prediction_std": pred_std,
        "calibration_ok": calibration_ok,
        "neutralized": neutralized,
        "per_era_proxy_corr": per_era_corr,
        "proxy_mmc_note": PROXY_LABEL_NOTE,
        "tail_eras_mean": _trimmed_mean(corr_values, trim=0.05),
        "negative_era_count": int(np.sum(corr_values < 0)),
    }

    logger.info(
        "validate_metrics: proxy_corr_mean=%.4f std=%.4f sharpe=%.2f "
        "era_stability=%.2f n_eras=%d calibration_ok=%s",
        proxy_corr_mean,
        proxy_corr_std,
        proxy_corr_sharpe,
        era_stability,
        len(per_era_corr),
        calibration_ok,
    )

    return report


def write_validation_report(
    report: Dict,
    output_path: Path,
    round_id: Optional[int] = None,
) -> None:
    """Write validation report as JSON. Excludes per_era detail for large datasets."""
    compact = {k: v for k, v in report.items() if k != "per_era_proxy_corr"}
    if round_id is not None:
        compact["round_id"] = round_id

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(compact, f, indent=2)

    logger.info("Validation report written to %s", output_path)


def _trimmed_mean(values: np.ndarray, trim: float = 0.05) -> float:
    """Trim extreme values and return mean (5% each tail by default)."""
    if len(values) < 4:
        return float(np.mean(values)) if len(values) > 0 else 0.0
    return float(stats.trim_mean(values, trim))
