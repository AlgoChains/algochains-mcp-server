"""
Era-based splitting utilities for Numerai time-series data.

HK-1: No random splits. All splits are era-ordered. Embargo gap enforced.
§7 rule 7: holdout >= 4 eras; embargo >= 4 eras between train and val.

Eras in Numerai are weekly periods. IDs are non-persistent across eras.
"""
from __future__ import annotations

import logging
from typing import Tuple

import pandas as pd

logger = logging.getLogger(__name__)


def era_split(
    df: pd.DataFrame,
    holdout_n: int = 4,
    embargo_gap: int = 4,
    era_col: str = "era",
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Split a Numerai DataFrame into (train, validation) using era ordering.

    Parameters
    ----------
    df : DataFrame with an era column (string values like "era1", "era501", etc.)
    holdout_n : number of most-recent eras to hold out for validation
    embargo_gap : number of eras to drop between train end and val start
    era_col : name of the era column

    Returns
    -------
    (train_df, val_df) — no shared eras; embargo gap removed from both.

    Raises
    ------
    ValueError if df has no era column or insufficient unique eras.
    """
    if era_col not in df.columns:
        raise ValueError(
            f"DataFrame missing era column '{era_col}'. "
            "Numerai data must have an 'era' column — do not drop it before splitting."
        )

    unique_eras: list = sorted(df[era_col].unique(), key=_era_sort_key)
    n_eras = len(unique_eras)

    min_required = holdout_n + embargo_gap + 1
    if n_eras < min_required:
        raise ValueError(
            f"Too few eras ({n_eras}) for holdout_n={holdout_n} + embargo_gap={embargo_gap}. "
            f"Need at least {min_required} unique eras."
        )

    val_eras = unique_eras[-holdout_n:]
    train_end_era = unique_eras[-(holdout_n + embargo_gap) - 1]
    train_eras = unique_eras[: n_eras - holdout_n - embargo_gap]

    train_df = df[df[era_col].isin(set(train_eras))].copy()
    val_df = df[df[era_col].isin(set(val_eras))].copy()

    logger.info(
        "era_split: total=%d eras | train=%d eras (%d rows) | embargo=%d | val=%d eras (%d rows) | "
        "train_end=%s val_start=%s",
        n_eras,
        len(train_eras),
        len(train_df),
        embargo_gap,
        len(val_eras),
        len(val_df),
        train_end_era,
        val_eras[0] if val_eras else "N/A",
    )
    return train_df, val_df


def embargo_filter(
    df: pd.DataFrame,
    ref_era: str,
    embargo_n: int = 4,
    era_col: str = "era",
    direction: str = "after",
) -> pd.DataFrame:
    """
    Remove rows within embargo_n eras of ref_era.

    Parameters
    ----------
    direction : "after" removes eras within embargo_n after ref_era (standard)
                "before" removes eras within embargo_n before ref_era
    """
    if era_col not in df.columns:
        raise ValueError(f"DataFrame missing era column '{era_col}'")

    sorted_eras = sorted(df[era_col].unique(), key=_era_sort_key)
    try:
        ref_idx = sorted_eras.index(ref_era)
    except ValueError:
        logger.warning("embargo_filter: ref_era '%s' not found; returning df unchanged", ref_era)
        return df

    if direction == "after":
        excluded = set(sorted_eras[ref_idx + 1 : ref_idx + 1 + embargo_n])
    else:
        start = max(0, ref_idx - embargo_n)
        excluded = set(sorted_eras[start:ref_idx])

    return df[~df[era_col].isin(excluded)].copy()


def era_kfold(
    df: pd.DataFrame,
    n_splits: int = 5,
    era_col: str = "era",
    embargo_gap: int = 4,
) -> list[Tuple[pd.DataFrame, pd.DataFrame]]:
    """
    Walk-forward era k-fold splits.

    Returns list of (train_df, val_df) tuples, each with embargo gap enforced.
    Earlier eras always in train; later eras in val. Never shuffled.
    """
    if era_col not in df.columns:
        raise ValueError(f"DataFrame missing era column '{era_col}'")

    unique_eras = sorted(df[era_col].unique(), key=_era_sort_key)
    n_eras = len(unique_eras)
    min_train = max(1, n_eras // (n_splits + 1))

    folds = []
    for i in range(1, n_splits + 1):
        split_point = min_train * i
        if split_point + embargo_gap >= n_eras:
            logger.warning("era_kfold: fold %d skipped — insufficient eras remaining", i)
            continue
        train_eras = unique_eras[:split_point]
        val_eras = unique_eras[split_point + embargo_gap :]

        if not val_eras:
            continue

        train_df = df[df[era_col].isin(set(train_eras))].copy()
        val_df = df[df[era_col].isin(set(val_eras))].copy()
        folds.append((train_df, val_df))

    logger.info("era_kfold: produced %d folds from %d eras", len(folds), n_eras)
    return folds


def _era_sort_key(era_str: str) -> int:
    """
    Extract numeric suffix for sorting. Handles "era1", "era501", "1", "501".
    Falls back to string sort if no numeric suffix found.
    """
    digits = "".join(c for c in str(era_str) if c.isdigit())
    return int(digits) if digits else 0
