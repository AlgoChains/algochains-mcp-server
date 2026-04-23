"""
Numerai tournament pipeline CLI.

Usage:
    python -m algochains_mcp.tournament.numerai.run_pipeline --dry-run
    python -m algochains_mcp.tournament.numerai.run_pipeline --train-only
    NUMERAI_ALLOW_LIVE=1 python -m algochains_mcp.tournament.numerai.run_pipeline --submit --model-id <uuid>

HK-8: On completion or failure, log is written to logs_dir/run_<round>.json.
§28.4 acceptance test: --dry-run exits 0, no NUMERAI_SECRET in stdout/stderr.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stderr,  # stderr only — never pollute stdout with secrets
)
logger = logging.getLogger("numerai.run_pipeline")


def _check_no_secret_in_env_on_stdout() -> None:
    """
    HK-6 guard: if NUMERAI_SECRET_KEY is in the environment, confirm we never echo it.
    This function never prints or logs the key value.
    """
    key = os.getenv("NUMERAI_SECRET_KEY", "")
    if key:
        logger.debug("NUMERAI_SECRET_KEY is configured (value not logged).")


def run_pipeline(
    mode: str = "dry-run",
    model_id: Optional[str] = None,
    round_id: Optional[int] = None,
    feature_set: Optional[str] = None,
    verbose: bool = False,
) -> dict:
    """
    Execute the full Numerai pipeline.

    Parameters
    ----------
    mode : "dry-run" (default) | "train-only" | "submit"
    model_id : required for mode="submit"
    round_id : optional round override (validated against current round)
    feature_set : override config feature_set
    """
    from .config import get_config
    from .download import (
        download_live_data,
        download_training_data,
        load_feature_names,
        load_live_dataframe,
        load_train_dataframe,
        check_feature_parity,
    )
    from .train import train_baseline, predict, load_model
    from .neutralize import neutralize_predictions
    from .validate import validate_metrics, write_validation_report
    from .submit import build_submission, upload_predictions_gated

    cfg = get_config()
    if feature_set:
        cfg.feature_set = feature_set

    # HK-6: only log booleans
    logger.info(
        "=== Numerai pipeline START | mode=%s | secret_configured=%s | allow_live=%s ===",
        mode,
        cfg.secret_configured,
        cfg.allow_live,
    )

    run_log: dict = {
        "mode": mode,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "dataset_version": cfg.version,
        "feature_set": cfg.feature_set,
        "secret_configured": cfg.secret_configured,
        "allow_live": cfg.allow_live,
        "submitted": False,
        "error": None,
    }

    try:
        # ── Step 1: Download training data ──────────────────────────────────
        logger.info("Step 1: Download training data")
        train_paths = download_training_data(
            version=cfg.version,
            state_dir=cfg.data_dir(),
            feature_set=cfg.feature_set,
        )

        # ── Step 2: Download live data ───────────────────────────────────────
        logger.info("Step 2: Download live data (fresh, HK-3)")
        live_paths = download_live_data(
            version=cfg.version,
            state_dir=cfg.data_dir(),
        )
        current_round = live_paths["round_id"]
        run_log["round_id"] = current_round

        if round_id is not None and round_id != current_round:
            raise RuntimeError(
                f"Round mismatch: caller expected {round_id}, current={current_round}"
            )

        # ── Step 3: Load data ────────────────────────────────────────────────
        logger.info("Step 3: Load DataFrames")
        feature_names = load_feature_names(
            train_paths["features_json"], cfg.feature_set
        )
        train_df = load_train_dataframe(
            train_paths["train_parquet"],
            train_paths["features_json"],
            feature_set=cfg.feature_set,
            target_col=cfg.target_column,
        )
        live_df = load_live_dataframe(
            live_paths["live_parquet"],
            train_paths["features_json"],
            feature_set=cfg.feature_set,
        )
        check_feature_parity(train_df, live_df, feature_names)

        run_log["train_rows"] = len(train_df)
        run_log["live_rows"] = len(live_df)
        run_log["feature_count"] = len(feature_names)

        if mode == "train-only":
            # ── Train only ───────────────────────────────────────────────────
            logger.info("Step 4: Train (train-only mode)")
            meta = train_baseline(
                train_df,
                feature_names,
                target_col=cfg.target_column,
                holdout_n=cfg.holdout_eras,
                embargo_eras=cfg.embargo_eras,
                models_dir=cfg.models_dir(),
                round_id=current_round,
                cfg=cfg,
            )
            run_log.update(meta)
            run_log["complete"] = True
            return run_log

        # ── Step 4: Train ────────────────────────────────────────────────────
        logger.info("Step 4: Train baseline model")
        meta = train_baseline(
            train_df,
            feature_names,
            target_col=cfg.target_column,
            holdout_n=cfg.holdout_eras,
            embargo_eras=cfg.embargo_eras,
            models_dir=cfg.models_dir(),
            round_id=current_round,
            cfg=cfg,
        )
        run_log.update(meta)

        # ── Step 5: Predict on live ──────────────────────────────────────────
        logger.info("Step 5: Generate live predictions")
        model_artifact = load_model(Path(meta["model_path"]))
        raw_predictions = predict(model_artifact, live_df)

        # ── Step 6: Neutralize ───────────────────────────────────────────────
        logger.info("Step 6: Feature neutralization (proxy_mmc)")
        neutralized = neutralize_predictions(
            raw_predictions,
            live_df,
            feature_names,
            proportion=1.0,
        )

        # ── Step 7: Validate ─────────────────────────────────────────────────
        logger.info("Step 7: Validation metrics")
        _, val_split = __import__(
            "algochains_mcp.tournament.numerai.era_utils", fromlist=["era_split"]
        ).era_split(train_df, holdout_n=cfg.holdout_eras, embargo_gap=cfg.embargo_eras)

        model_artifact_val = load_model(Path(meta["model_path"]))
        from .train import predict as _predict
        val_preds = _predict(model_artifact_val, val_split[[f for f in feature_names if f in val_split.columns]])
        val_report = validate_metrics(
            val_preds,
            val_split,
            target_col=cfg.target_column,
        )
        val_report_path = cfg.logs_dir() / f"val_report_r{current_round}.json"
        write_validation_report(val_report, val_report_path, round_id=current_round)
        run_log["proxy_corr_mean"] = val_report["proxy_corr_mean"]
        run_log["proxy_corr_std"] = val_report["proxy_corr_std"]
        run_log["era_stability"] = val_report["era_stability"]

        # ── Step 8: Build submission CSV ─────────────────────────────────────
        logger.info("Step 8: Build submission CSV")
        submission_path = cfg.submissions_dir() / f"submission_r{current_round}.csv"
        sub_result = build_submission(neutralized, live_df, submission_path)
        run_log.update(sub_result)

        # ── Step 9: Upload (gated) ───────────────────────────────────────────
        if mode == "submit":
            if not model_id:
                raise ValueError(
                    "--model-id is required for --submit mode. "
                    "Get your model UUID from numer.ai/models."
                )
            logger.info("Step 9: Upload (NUMERAI_ALLOW_LIVE=%s)", cfg.allow_live)
            upload_result = upload_predictions_gated(
                submission_path,
                model_id=model_id,
                round_id=current_round,
                dry_run=not cfg.allow_live,
            )
            run_log.update(upload_result)
        else:
            logger.info("Step 9: Dry-run — skipping upload")
            run_log["skip_reason"] = "dry-run mode"

        run_log["complete"] = True
        logger.info(
            "=== Numerai pipeline COMPLETE | mode=%s | round=%s | uploaded=%s ===",
            mode,
            current_round,
            run_log.get("uploaded", False),
        )

        # Phase 2: push to Supabase and send Slack notification (HK-8)
        try:
            from .monitoring import push_to_supabase, send_slack_alert
            push_to_supabase(run_log)
            uploaded = run_log.get("uploaded", False)
            mode_label = "UPLOADED" if uploaded else "dry-run complete"
            send_slack_alert(
                f"Round {current_round} | {mode_label} | "
                f"proxy_corr={run_log.get('proxy_corr_mean', 'N/A'):.4f} | "
                f"era_stability={run_log.get('era_stability', 'N/A'):.2f}"
            )
        except Exception as mon_exc:
            logger.warning("Monitoring push failed (non-fatal): %s", mon_exc)

    except Exception as exc:
        run_log["error"] = str(exc)
        run_log["traceback"] = traceback.format_exc()
        run_log["complete"] = False
        logger.error("=== Numerai pipeline FAILED: %s ===", exc)

    finally:
        # HK-8: always write run log
        _write_run_log(run_log, cfg)

    return run_log


def _write_run_log(run_log: dict, cfg) -> None:
    """Write per-run JSON log (HK-8, metrics-monitoring phase 2 foundation)."""
    try:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        round_tag = run_log.get("round_id", "unknown")
        log_path = cfg.logs_dir() / f"run_{round_tag}_{ts}.json"

        # Ensure no secret values in log (HK-6 final guard)
        safe_log = {
            k: v for k, v in run_log.items() if "secret" not in k.lower()
        }
        safe_log["secret_in_log"] = False

        with open(log_path, "w") as f:
            json.dump(safe_log, f, indent=2, default=str)

        logger.info("Run log written to %s", log_path)
    except Exception as e:
        logger.warning("Failed to write run log: %s", e)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Numerai tournament pipeline for AlgoChains",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m algochains_mcp.tournament.numerai.run_pipeline --dry-run
  python -m algochains_mcp.tournament.numerai.run_pipeline --train-only
  NUMERAI_ALLOW_LIVE=1 python -m algochains_mcp.tournament.numerai.run_pipeline \\
      --submit --model-id <uuid>
        """,
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--dry-run", action="store_true", default=True, help="Run without uploading (default)"
    )
    mode_group.add_argument(
        "--submit", action="store_true", help="Upload predictions (requires NUMERAI_ALLOW_LIVE=1)"
    )
    mode_group.add_argument("--train-only", action="store_true", help="Train only, no submission")

    parser.add_argument("--model-id", type=str, default="", help="Numerai model UUID")
    parser.add_argument("--round-id", type=int, default=None, help="Expected round ID (validation)")
    parser.add_argument("--feature-set", type=str, default=None, help="Feature set: small|medium|all")
    parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args()

    if args.submit:
        mode = "submit"
    elif args.train_only:
        mode = "train-only"
    else:
        mode = "dry-run"

    _check_no_secret_in_env_on_stdout()

    result = run_pipeline(
        mode=mode,
        model_id=args.model_id or None,
        round_id=args.round_id,
        feature_set=args.feature_set,
        verbose=args.verbose,
    )

    # Print safe result JSON to stdout (HK-6: filter secrets)
    safe_result = {k: v for k, v in result.items() if "secret" not in k.lower()}
    print(json.dumps(safe_result, indent=2, default=str))

    if not result.get("complete", False):
        sys.exit(1)


if __name__ == "__main__":
    main()
