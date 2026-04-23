"""
Numerai dataset download utilities.

HK-2: Uses ALGOCHAINS_STATE_DIR / GCS — no S3 / no /tmp.
HK-3: live.parquet is ALWAYS re-downloaded fresh (never cached across rounds).
HK-4: VERSION asserted at download; feature column parity checked between train/live.
HK-9: Feature sets loaded from features.json by name, not by position.
HK-14: Only columns in the selected feature set are loaded to avoid OOM.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from .config import NumeraiConfig, get_config, _get_napi

logger = logging.getLogger(__name__)


def download_training_data(
    version: Optional[str] = None,
    state_dir: Optional[Path] = None,
    feature_set: str = "medium",
    force_redownload: bool = False,
) -> Dict[str, Path]:
    """
    Download Numerai training parquet and features.json.

    GCS cache: check if already downloaded for this VERSION before re-downloading.
    Returns dict of {train_parquet: Path, features_json: Path}.
    """
    cfg = get_config()
    version = version or cfg.version
    state_dir = state_dir or cfg.data_dir()

    dest_train = state_dir / f"train_{version.replace('/', '_')}.parquet"
    dest_features = state_dir / f"features_{version.replace('/', '_')}.json"

    napi = _get_napi()

    # features.json — always fetch if missing (small file)
    if not dest_features.exists() or force_redownload:
        logger.info("Downloading features.json for %s", version)
        napi.download_dataset(f"{version}/features.json", str(dest_features))
    else:
        logger.info("features.json already cached at %s", dest_features)

    # train parquet — cache check (HK-14)
    if not dest_train.exists() or force_redownload:
        logger.info("Downloading train.parquet for %s (this may take a few minutes)", version)
        napi.download_dataset(f"{version}/train.parquet", str(dest_train))
    else:
        logger.info("train.parquet already cached at %s (use force_redownload=True to refresh)", dest_train)

    return {"train_parquet": dest_train, "features_json": dest_features}


def download_live_data(
    version: Optional[str] = None,
    state_dir: Optional[Path] = None,
) -> Dict[str, Path]:
    """
    Download the CURRENT round's live.parquet.

    HK-3: Always re-download — live IDs change every round. Never use cached live data.
    """
    cfg = get_config()
    version = version or cfg.version
    state_dir = state_dir or cfg.data_dir()

    napi = _get_napi()
    current_round = napi.get_current_round()
    dest_live = state_dir / f"live_{version.replace('/', '_')}_r{current_round}.parquet"

    logger.info(
        "Downloading live.parquet for round %d version %s (always fresh — HK-3)",
        current_round,
        version,
    )
    napi.download_dataset(f"{version}/live.parquet", str(dest_live))

    return {"live_parquet": dest_live, "round_id": current_round}


def load_feature_names(features_json: Path, feature_set: str = "medium") -> List[str]:
    """
    Load feature names from features.json by set name (HK-9).

    Returns a list of feature column names for the requested set.
    Raises KeyError if feature_set is not present in the JSON.
    """
    with open(features_json) as f:
        feature_metadata = json.load(f)

    available_sets = list(feature_metadata.get("feature_sets", {}).keys())
    if feature_set not in feature_metadata.get("feature_sets", {}):
        raise KeyError(
            f"Feature set '{feature_set}' not found in features.json. "
            f"Available sets: {available_sets}"
        )

    feature_names: List[str] = feature_metadata["feature_sets"][feature_set]
    logger.info("Loaded %d features for set '%s'", len(feature_names), feature_set)
    return feature_names


def load_train_dataframe(
    train_parquet: Path,
    features_json: Path,
    feature_set: str = "medium",
    target_col: str = "target_cyrus20",
    era_col: str = "era",
) -> pd.DataFrame:
    """
    Load training DataFrame with only selected feature columns (HK-14: OOM guard).

    Returns DataFrame with [era_col] + [feature columns] + [target_col].
    Asserts era column presence (HK-1 guard).
    """
    feature_names = load_feature_names(features_json, feature_set)
    cols_to_load = [era_col] + feature_names + [target_col]

    logger.info(
        "Loading train.parquet: %d columns (era + %d features + target)",
        len(cols_to_load),
        len(feature_names),
    )

    existing_cols = pd.read_parquet(train_parquet, columns=[]).columns.tolist()
    available_cols = [c for c in cols_to_load if c in existing_cols]
    missing_cols = [c for c in cols_to_load if c not in existing_cols]
    if missing_cols:
        logger.warning("Columns not found in parquet (skipping): %s", missing_cols)

    df = pd.read_parquet(train_parquet, columns=available_cols)

    if era_col not in df.columns:
        raise ValueError(
            f"Train parquet missing era column '{era_col}'. "
            "Numerai era column is required for era-based splitting."
        )

    return df


def load_live_dataframe(
    live_parquet: Path,
    features_json: Path,
    feature_set: str = "medium",
    era_col: str = "era",
) -> pd.DataFrame:
    """
    Load live DataFrame with selected feature columns.
    Returns DataFrame index = numerai_id, columns = feature columns (+ era if present).
    """
    feature_names = load_feature_names(features_json, feature_set)
    existing_cols = pd.read_parquet(live_parquet, columns=[]).columns.tolist()
    cols_to_load = [c for c in [era_col] + feature_names if c in existing_cols]

    logger.info("Loading live.parquet: %d columns", len(cols_to_load))
    return pd.read_parquet(live_parquet, columns=cols_to_load)


def check_feature_parity(
    train_df: pd.DataFrame,
    live_df: pd.DataFrame,
    feature_names: List[str],
) -> None:
    """
    Assert train and live share the same feature columns (HK-4 guard).
    Raises RuntimeError on mismatch.
    """
    train_feats = set(train_df.columns) & set(feature_names)
    live_feats = set(live_df.columns) & set(feature_names)

    only_train = train_feats - live_feats
    only_live = live_feats - train_feats

    if only_train or only_live:
        raise RuntimeError(
            f"Feature column mismatch between train and live parquets (HK-4).\n"
            f"Only in train: {sorted(only_train)[:10]}\n"
            f"Only in live: {sorted(only_live)[:10]}\n"
            "This usually means the dataset version changed. Re-download with the correct version."
        )

    logger.info("Feature parity check passed: %d shared features", len(train_feats & live_feats))
