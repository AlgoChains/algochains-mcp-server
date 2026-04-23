"""
Phase 2 monitoring for Numerai tournament pipeline.

Responsibilities:
- Per-round structured JSON log (already written by run_pipeline.py, indexed here)
- Optional push to Supabase numerai_runs table (HK-11: dedicated keys, no bot_id reuse)
- Slack alert on missed submit window (HK-8)
- Dead-man check: if no submission by end of Sat, alert

HK-12: numerai_status/alerts are separate from futures get_bot_health.
HK-11: numerai_model_id is separate from marketplace bot_id.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Numerai submission window: Tuesday–Saturday UTC (approximate — verify at numer.ai)
SUBMISSION_DAYS = {1, 2, 3, 4, 5}  # Mon=0, Tue=1, ..., Sat=5


def read_round_log(logs_dir: Path, round_id: int) -> Optional[Dict]:
    """
    Read the most recent run log for a given round_id.
    Returns None if no log found.
    """
    pattern = f"run_{round_id}_*.json"
    matches = sorted(logs_dir.glob(pattern), key=lambda p: p.stat().st_mtime)
    if not matches:
        return None
    with open(matches[-1]) as f:
        return json.load(f)


def list_round_logs(logs_dir: Path, limit: int = 10) -> List[Dict]:
    """Return summary of last N run logs sorted by most recent."""
    logs = sorted(logs_dir.glob("run_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    result = []
    for log_path in logs[:limit]:
        try:
            with open(log_path) as f:
                data = json.load(f)
            # Strip per_era details to keep response compact
            data.pop("per_era_proxy_corr", None)
            result.append({"file": log_path.name, **data})
        except Exception as e:
            result.append({"file": log_path.name, "error": str(e)})
    return result


def check_submission_window() -> Dict:
    """
    Check if current UTC time is within a Numerai submission window.
    Returns {in_window, day_name, utc_now, next_deadline_hint}.
    """
    now = datetime.now(timezone.utc)
    day = now.weekday()  # Mon=0 .. Sun=6
    in_window = day in SUBMISSION_DAYS
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return {
        "in_window": in_window,
        "day_name": day_names[day],
        "utc_now": now.isoformat(),
        "submission_days": "Tuesday–Saturday",
        "next_deadline_hint": "Verify exact window at numer.ai — this is approximate.",
    }


def push_to_supabase(
    run_log: Dict,
    supabase_url: Optional[str] = None,
    supabase_key: Optional[str] = None,
) -> Dict:
    """
    Push a round log to the numerai_runs Supabase table.

    HK-11: Uses numerai_model_id column, not bot_id. Dedicated table.
    Returns {pushed, error} dict.

    Table schema (see migration 20260424_numerai_runs.sql):
        round_id, numerai_model_id, tournament, dataset_version,
        proxy_corr_mean, proxy_corr_std, era_stability, feature_count,
        submitted, uploaded, started_at, error
    """
    url = supabase_url or os.getenv("SUPABASE_URL", "")
    key = supabase_key or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "") or os.getenv("SUPABASE_ANON_KEY", "")

    if not url or not key:
        return {"pushed": False, "reason": "SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not configured"}

    try:
        import httpx

        row = {
            "round_id": run_log.get("round_id"),
            "numerai_model_id": run_log.get("model_id_hash", ""),  # hash only (HK-6)
            "tournament": "classic",
            "dataset_version": run_log.get("dataset_version", ""),
            "proxy_corr_mean": run_log.get("proxy_corr_mean"),
            "proxy_corr_std": run_log.get("proxy_corr_std"),
            "era_stability": run_log.get("era_stability"),
            "feature_count": run_log.get("feature_count"),
            "submitted": run_log.get("complete", False),
            "uploaded": run_log.get("uploaded", False),
            "started_at": run_log.get("started_at"),
            "error": run_log.get("error"),
        }

        resp = httpx.post(
            f"{url}/rest/v1/numerai_runs",
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
            json=row,
            timeout=10.0,
        )
        resp.raise_for_status()
        logger.info("push_to_supabase: round %s pushed to numerai_runs", row["round_id"])
        return {"pushed": True, "round_id": row["round_id"]}

    except Exception as exc:
        logger.warning("push_to_supabase failed: %s", exc)
        return {"pushed": False, "error": str(exc)}


def send_slack_alert(
    message: str,
    channel: Optional[str] = None,
    webhook_url: Optional[str] = None,
    is_error: bool = False,
) -> Dict:
    """
    Send a Slack notification for Numerai pipeline events.

    HK-8: Call on completion AND on failure.
    HK-12: Use a dedicated Numerai channel — not #mnq-alerts.

    Uses NUMERAI_SLACK_WEBHOOK (preferred) or SLACK_WEBHOOK_URL fallback.
    Message never contains NUMERAI_SECRET_KEY.
    """
    webhook = (
        webhook_url
        or os.getenv("NUMERAI_SLACK_WEBHOOK", "")
        or os.getenv("SLACK_WEBHOOK_URL", "")
    )

    if not webhook:
        logger.info("send_slack_alert: no webhook configured, skipping. message=%s", message[:80])
        return {"sent": False, "reason": "No webhook URL configured (NUMERAI_SLACK_WEBHOOK)"}

    # Guard: ensure no secret in message (HK-6)
    secret = os.getenv("NUMERAI_SECRET_KEY", "")
    if secret and secret in message:
        logger.error("send_slack_alert: BLOCKED — secret key found in message content (HK-6)")
        return {"sent": False, "reason": "HK-6: secret key in message — send blocked"}

    emoji = ":x:" if is_error else ":chart_with_upwards_trend:"
    payload = {
        "text": f"{emoji} *Numerai Pipeline* | {message}",
        "channel": channel or "#numerai-pipeline",
    }

    try:
        import httpx

        resp = httpx.post(webhook, json=payload, timeout=5.0)
        resp.raise_for_status()
        logger.info("send_slack_alert: sent to %s", channel or "#numerai-pipeline")
        return {"sent": True}
    except Exception as exc:
        logger.warning("send_slack_alert failed: %s", exc)
        return {"sent": False, "error": str(exc)}


def dead_man_check(
    logs_dir: Path,
    round_id: int,
    alert_if_not_submitted: bool = True,
) -> Dict:
    """
    Dead-man check: verify a submission was made for the current round.
    If not, and we're past the window, send a Slack alert (HK-8).

    Returns {submitted, round_id, alert_sent}.
    """
    log = read_round_log(logs_dir, round_id)
    submitted = bool(log and log.get("complete") and log.get("uploaded"))

    window_info = check_submission_window()
    alert_sent = False

    if not submitted and alert_if_not_submitted and not window_info["in_window"]:
        msg = (
            f"Dead-man check FAILED for round {round_id}: "
            f"no submission found and window has closed ({window_info['day_name']} UTC). "
            "Check logs at ALGOCHAINS_STATE_DIR/numerai/logs/."
        )
        result = send_slack_alert(msg, is_error=True)
        alert_sent = result.get("sent", False)
        logger.error("dead_man_check: %s", msg)

    return {
        "submitted": submitted,
        "round_id": round_id,
        "in_window": window_info["in_window"],
        "alert_sent": alert_sent,
        "log_found": log is not None,
    }


def stopgo_review_status(
    logs_dir: Path,
    rounds_threshold_8: int = 8,
    rounds_threshold_12: int = 12,
) -> Dict:
    """
    HK-18 stop/go review helper.

    Counts completed rounds and flags when week-8 and week-12 reviews are due.
    See NUMERAI_NUMEROO_BLUEPRINT.md Stop/Go decisions table for logging results.

    Returns {rounds_completed, week8_review_due, week12_review_due, action}.
    """
    all_logs = sorted(logs_dir.glob("run_*.json"), key=lambda p: p.stat().st_mtime)
    uploaded_rounds = []
    for log_path in all_logs:
        try:
            with open(log_path) as f:
                data = json.load(f)
            if data.get("uploaded") and data.get("round_id"):
                uploaded_rounds.append(data["round_id"])
        except Exception:
            pass

    # Deduplicate rounds
    unique_rounds = len(set(uploaded_rounds))

    week8_due = unique_rounds >= rounds_threshold_8
    week12_due = unique_rounds >= rounds_threshold_12

    action = "continue"
    if week12_due:
        action = "WEEK-12 REVIEW DUE — check mmcRep trend; decide continue/pause/pivot"
    elif week8_due:
        action = "WEEK-8 REVIEW DUE — check mmcRep trend (rolling 4-round average)"

    return {
        "rounds_submitted": unique_rounds,
        "week8_review_due": week8_due,
        "week12_review_due": week12_due,
        "action": action,
        "blueprint_ref": "docs/NUMERAI_NUMEROO_BLUEPRINT.md#stopgo-decisions",
        "hk18_note": (
            "If mmcRep trend is neutral or negative at week 12, pause auto-submit "
            "and re-evaluate vs MCPT/foundation opportunity cost (HK-18)."
        ),
    }
