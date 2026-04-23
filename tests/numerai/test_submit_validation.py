"""
Tests for submit.py — ID mismatch, range check, secret not logged.

Critical HK checks:
- HK-3: ID mismatch raises ValueError
- HK-5: Out-of-range predictions raise ValueError
- HK-6: NUMERAI_SECRET_KEY never appears in any raised exception message or log output
- HK-7: Upload blocked without NUMERAI_ALLOW_LIVE=1
"""
from __future__ import annotations

import io
import logging
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from algochains_mcp.tournament.numerai.submit import (
    build_submission,
    upload_predictions_gated,
    _sha256,
)


FAKE_SECRET = "FAKE_SECRET_VALUE_FOR_TESTS_ONLY"


@pytest.fixture
def live_df():
    return pd.DataFrame(
        {f"feature_{i}": np.random.rand(100) for i in range(5)},
        index=[f"live_id_{i}" for i in range(100)],
    )


@pytest.fixture
def valid_predictions(live_df):
    from scipy.stats import rankdata
    n = len(live_df)
    raw = np.linspace(0.01, 0.99, n)
    ranked = rankdata(raw, method="average") / (n + 1)
    return pd.Series(ranked, index=live_df.index, name="prediction")


@pytest.fixture
def submission_path(tmp_path):
    return tmp_path / "submission_r999.csv"


class TestBuildSubmission:
    def test_valid_submission_written(self, valid_predictions, live_df, submission_path):
        result = build_submission(valid_predictions, live_df, submission_path)
        assert submission_path.exists()
        assert result["row_count"] == 100
        assert result["id_validated"] is True
        assert result["range_validated"] is True
        assert "checksum_sha256" in result

    def test_id_mismatch_raises(self, live_df, submission_path):
        """HK-3: ID mismatch must raise ValueError."""
        wrong_ids = pd.Series(
            np.linspace(0.1, 0.9, 50),
            index=[f"wrong_id_{i}" for i in range(50)],
        )
        with pytest.raises(ValueError, match="ID mismatch"):
            build_submission(wrong_ids, live_df, submission_path)

    def test_out_of_range_raises(self, live_df, submission_path):
        """HK-5: Predictions outside [0, 1] must raise ValueError."""
        bad_preds = pd.Series(
            [1.5] * 100,  # > 1
            index=live_df.index,
        )
        with pytest.raises(ValueError, match="out of \\[0, 1\\]"):
            build_submission(bad_preds, live_df, submission_path)

    def test_degenerate_predictions_raises(self, live_df, submission_path):
        """Constant predictions (std=0) must raise ValueError."""
        const_preds = pd.Series(
            [0.5] * 100,
            index=live_df.index,
        )
        with pytest.raises(ValueError, match="std <= 0"):
            build_submission(const_preds, live_df, submission_path)

    def test_no_secret_in_exception_messages(self, live_df, submission_path, monkeypatch):
        """HK-6: Secret key must never appear in error messages."""
        monkeypatch.setenv("NUMERAI_SECRET_KEY", FAKE_SECRET)
        wrong_ids = pd.Series(
            np.linspace(0.1, 0.9, 50),
            index=[f"wrong_id_{i}" for i in range(50)],
        )
        try:
            build_submission(wrong_ids, live_df, submission_path)
        except ValueError as e:
            assert FAKE_SECRET not in str(e), "Secret key must never appear in error message (HK-6)"

    def test_checksum_is_deterministic(self, valid_predictions, live_df, tmp_path):
        path1 = tmp_path / "sub1.csv"
        path2 = tmp_path / "sub2.csv"
        build_submission(valid_predictions, live_df, path1)
        build_submission(valid_predictions, live_df, path2)
        assert _sha256(path1) == _sha256(path2)

    def test_ready_for_upload_is_false_by_default(self, valid_predictions, live_df, submission_path):
        result = build_submission(valid_predictions, live_df, submission_path)
        assert result["ready_for_upload"] is False


class TestUploadPredictionsGated:
    def test_dry_run_by_default(self, valid_predictions, live_df, submission_path, monkeypatch):
        """Without NUMERAI_ALLOW_LIVE, upload must be skipped."""
        monkeypatch.setenv("NUMERAI_ALLOW_LIVE", "0")
        monkeypatch.setenv("NUMERAI_PUBLIC_ID", "FAKE_PUB")
        monkeypatch.setenv("NUMERAI_SECRET_KEY", FAKE_SECRET)

        build_submission(valid_predictions, live_df, submission_path)
        result = upload_predictions_gated(submission_path, model_id="fake_model_id", dry_run=True)
        assert result["uploaded"] is False
        assert result["dry_run"] is True

    def test_blocked_without_allow_live(self, valid_predictions, live_df, submission_path, monkeypatch):
        """HK-7: Upload must not proceed without NUMERAI_ALLOW_LIVE=1."""
        monkeypatch.setenv("NUMERAI_ALLOW_LIVE", "0")
        monkeypatch.setenv("NUMERAI_PUBLIC_ID", "FAKE_PUB")
        monkeypatch.setenv("NUMERAI_SECRET_KEY", FAKE_SECRET)

        build_submission(valid_predictions, live_df, submission_path)
        result = upload_predictions_gated(submission_path, model_id="fake_model_id", dry_run=False)
        assert result["uploaded"] is False

    def test_no_secret_in_response(self, valid_predictions, live_df, submission_path, monkeypatch):
        """HK-6: upload response must never contain the secret key value."""
        monkeypatch.setenv("NUMERAI_ALLOW_LIVE", "0")
        monkeypatch.setenv("NUMERAI_PUBLIC_ID", "FAKE_PUB")
        monkeypatch.setenv("NUMERAI_SECRET_KEY", FAKE_SECRET)

        build_submission(valid_predictions, live_df, submission_path)
        result = upload_predictions_gated(submission_path, model_id="fake_model_id", dry_run=True)
        result_str = str(result)
        assert FAKE_SECRET not in result_str, "Secret must not appear in upload response (HK-6)"
        assert result.get("secret_in_response") is False

    def test_model_id_not_exposed(self, valid_predictions, live_df, submission_path, monkeypatch):
        """model_id hash (not value) should be in response."""
        monkeypatch.setenv("NUMERAI_ALLOW_LIVE", "0")
        monkeypatch.setenv("NUMERAI_PUBLIC_ID", "FAKE_PUB")
        monkeypatch.setenv("NUMERAI_SECRET_KEY", FAKE_SECRET)

        real_model_id = "secret-model-uuid-1234"
        build_submission(valid_predictions, live_df, submission_path)
        result = upload_predictions_gated(submission_path, model_id=real_model_id, dry_run=True)
        result_str = str(result)
        assert real_model_id not in result_str, "model_id must not appear in response verbatim"

    def test_empty_model_id_raises(self, valid_predictions, live_df, submission_path, monkeypatch):
        monkeypatch.setenv("NUMERAI_ALLOW_LIVE", "1")
        monkeypatch.setenv("NUMERAI_PUBLIC_ID", "FAKE_PUB")
        monkeypatch.setenv("NUMERAI_SECRET_KEY", FAKE_SECRET)

        build_submission(valid_predictions, live_df, submission_path)
        with pytest.raises(ValueError, match="model_id"):
            upload_predictions_gated(submission_path, model_id="", dry_run=False)

    def test_log_output_contains_no_secret(
        self, valid_predictions, live_df, submission_path, monkeypatch, caplog
    ):
        """HK-6: Secret must never appear in log records."""
        monkeypatch.setenv("NUMERAI_ALLOW_LIVE", "0")
        monkeypatch.setenv("NUMERAI_PUBLIC_ID", "FAKE_PUB")
        monkeypatch.setenv("NUMERAI_SECRET_KEY", FAKE_SECRET)

        build_submission(valid_predictions, live_df, submission_path)
        with caplog.at_level(logging.DEBUG, logger="algochains_mcp.tournament.numerai"):
            upload_predictions_gated(submission_path, model_id="model_x", dry_run=True)

        log_text = "\n".join(caplog.messages)
        assert FAKE_SECRET not in log_text, f"Secret key found in log output (HK-6): {log_text[:200]}"
