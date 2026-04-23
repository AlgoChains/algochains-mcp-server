"""
Submission generation and upload for Numerai.

Security gates (HK-0, HK-6, HK-7):
- NUMERAI_SECRET_KEY never logged — only boolean presence.
- Gate 1: NUMERAI_ALLOW_LIVE=1 required for uploads.
- Gate 2: NMR staking not handled here (manual UI only, out of scope Phase 1-3).

Validation gates before any upload attempt (HK-3, HK-5):
- IDs must exactly match live.parquet IDs for the current round.
- All prediction values must be in [0, 1].
- Prediction std must be > 0 (degenerate distribution check).

HK-8: Always log completion/failure; check round_id before upload.
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Optional

import pandas as pd

from .config import NumeraiConfig, _get_napi, get_config

logger = logging.getLogger(__name__)

PREDICTION_COL = "prediction"


def build_submission(
    predictions: pd.Series,
    live_df: pd.DataFrame,
    output_path: Path,
) -> dict:
    """
    Build and validate the submission CSV.

    Validates:
    - Prediction IDs exactly match live_df.index (HK-3)
    - All values in [0, 1] (HK-5)
    - std > 0 (degenerate prediction guard)
    - 'prediction' column name (Numerai convention)

    Returns dict with {output_path, checksum, row_count, id_validated, range_validated}.
    """
    live_ids = set(live_df.index)
    pred_ids = set(predictions.index)

    missing_from_preds = live_ids - pred_ids
    extra_in_preds = pred_ids - live_ids

    if missing_from_preds or extra_in_preds:
        raise ValueError(
            f"ID mismatch between predictions and live.parquet (HK-3). "
            f"Missing from predictions: {len(missing_from_preds)} rows. "
            f"Extra in predictions: {len(extra_in_preds)} rows. "
            "Regenerate predictions using the current round's live.parquet."
        )

    # Range check (HK-5)
    if not predictions.between(0, 1).all():
        bad = predictions[~predictions.between(0, 1)]
        raise ValueError(
            f"Predictions out of [0, 1] range (HK-5). "
            f"{len(bad)} invalid values. "
            f"min={float(predictions.min()):.4f} max={float(predictions.max()):.4f}. "
            "Apply rank normalization before submitting."
        )

    if predictions.std() <= 0:
        raise ValueError(
            "Degenerate predictions: std <= 0. "
            "All predictions are the same value — Numerai will score this as zero."
        )

    # Build CSV
    submission = predictions.to_frame(name=PREDICTION_COL)
    submission.index.name = "numerai_id"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    submission.to_csv(output_path)

    checksum = _sha256(output_path)

    result = {
        "output_path": str(output_path),
        "checksum_sha256": checksum,
        "row_count": len(submission),
        "prediction_mean": float(predictions.mean()),
        "prediction_std": float(predictions.std()),
        "id_validated": True,
        "range_validated": True,
        "ready_for_upload": False,  # default safe; set True only in upload_predictions_gated
    }

    logger.info(
        "build_submission: %d rows, mean=%.4f std=%.4f, checksum=%s, path=%s",
        result["row_count"],
        result["prediction_mean"],
        result["prediction_std"],
        checksum[:12],
        output_path,
    )
    return result


def upload_predictions_gated(
    submission_path: Path,
    model_id: str,
    round_id: Optional[int] = None,
    dry_run: bool = True,
) -> dict:
    """
    Upload submission to Numerai — gated behind NUMERAI_ALLOW_LIVE=1.

    Default = dry_run (safe). Burns are irreversible — fail closed (§7 rule 6).

    Parameters
    ----------
    submission_path : Path to the validated submission CSV.
    model_id : Numerai model UUID (from numer.ai/models).
    round_id : expected round ID (verified against napi.get_current_round()).
    dry_run : if True, skip the actual upload call and log intent.

    Returns
    -------
    dict with {uploaded, round_id, row_count, dry_run, secret_in_response: False}.
    """
    cfg = get_config()

    if not submission_path.exists():
        raise FileNotFoundError(
            f"Submission file not found: {submission_path}. "
            "Run build_submission() first."
        )

    if not model_id or not model_id.strip():
        raise ValueError(
            "model_id must be set to upload predictions. "
            "Retrieve your model UUID from numer.ai/models."
        )

    # Count rows
    submission = pd.read_csv(submission_path, index_col=0)
    row_count = len(submission)

    result = {
        "uploaded": False,
        "dry_run": dry_run or not cfg.allow_live,
        "row_count": row_count,
        "model_id_hash": hashlib.sha256(model_id.encode()).hexdigest()[:12],  # not the real ID
        "secret_in_response": False,  # HK-6: never put secrets in response
    }

    if dry_run or not cfg.allow_live:
        reason = "dry_run=True" if dry_run else "NUMERAI_ALLOW_LIVE not set"
        logger.info(
            "upload_predictions_gated: SKIPPING upload (%s). "
            "Set NUMERAI_ALLOW_LIVE=1 and dry_run=False to upload.",
            reason,
        )
        result["skip_reason"] = reason
        result["submission_path"] = str(submission_path)
        return result

    if not cfg.secret_configured:
        raise RuntimeError(
            "NUMERAI_SECRET_KEY not configured. "
            "secret_configured=%s. Set env var before uploading." % cfg.secret_configured
        )

    # Verify round_id before upload (HK-8)
    napi = _get_napi()
    current_round = napi.get_current_round()

    if round_id is not None and round_id != current_round:
        raise RuntimeError(
            f"Round mismatch: expected {round_id}, current={current_round}. "
            "Regenerate live predictions for the current round before uploading."
        )

    result["round_id"] = current_round

    logger.info(
        "upload_predictions_gated: UPLOADING %d rows for round %d (NUMERAI_ALLOW_LIVE=1)",
        row_count,
        current_round,
    )

    napi.upload_predictions(str(submission_path), model_id=model_id)

    result["uploaded"] = True
    result["dry_run"] = False
    logger.info("upload_predictions_gated: upload complete for round %d", current_round)

    return result


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
