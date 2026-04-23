"""
Pytest fixtures for Numerai pipeline tests.
All tests use synthetic DataFrames — no live API calls, no real credentials.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest


# ── Synthetic data helpers ────────────────────────────────────────────────────

def _make_era_df(
    n_eras: int = 20,
    rows_per_era: int = 50,
    n_features: int = 10,
    target_col: str = "target_cyrus20",
    era_col: str = "era",
    seed: int = 42,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    records = []
    for era_num in range(1, n_eras + 1):
        for row_num in range(rows_per_era):
            record = {era_col: f"era{era_num}"}
            for f_idx in range(n_features):
                record[f"feature_{f_idx:03d}"] = float(rng.uniform(0, 1))
            record[target_col] = float(rng.uniform(0, 1))
            records.append(record)
    df = pd.DataFrame(records)
    df.index = [f"id_{era_num}_{row_num}" for era_num in range(1, n_eras + 1) for row_num in range(rows_per_era)]
    return df


def _make_live_df(
    n_rows: int = 100,
    n_features: int = 10,
    seed: int = 99,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    records = []
    for i in range(n_rows):
        record = {f"feature_{f_idx:03d}": float(rng.uniform(0, 1)) for f_idx in range(n_features)}
        record["era"] = "live"
        records.append(record)
    df = pd.DataFrame(records)
    df.index = [f"live_id_{i}" for i in range(n_rows)]
    return df


def _feature_names(n: int = 10) -> List[str]:
    return [f"feature_{i:03d}" for i in range(n)]


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def train_df():
    return _make_era_df(n_eras=20, rows_per_era=50, n_features=10)


@pytest.fixture
def live_df():
    return _make_live_df(n_rows=100, n_features=10)


@pytest.fixture
def feature_names():
    return _feature_names(10)


@pytest.fixture
def target_col():
    return "target_cyrus20"


@pytest.fixture
def era_col():
    return "era"


@pytest.fixture
def valid_predictions(live_df):
    """Predictions with correct IDs and valid range."""
    import scipy.stats as ss
    n = len(live_df)
    raw = np.linspace(0.01, 0.99, n)
    ranked = ss.rankdata(raw, method="average") / (n + 1)
    return pd.Series(ranked, index=live_df.index, name="prediction")


@pytest.fixture
def tmp_state_dir(tmp_path):
    state = tmp_path / "state" / "numerai"
    state.mkdir(parents=True)
    return tmp_path


@pytest.fixture
def mock_napi():
    """Mock NumerAPI client — no real API calls."""
    with patch("algochains_mcp.tournament.numerai.config._get_napi") as mock_factory:
        mock_client = MagicMock()
        mock_client.get_current_round.return_value = 999
        mock_client.download_dataset.return_value = None
        mock_client.upload_predictions.return_value = {"status": "ok"}
        mock_factory.return_value = mock_client
        yield mock_client


@pytest.fixture
def mock_env(monkeypatch):
    """Set safe fake credentials for tests (never real values)."""
    monkeypatch.setenv("NUMERAI_PUBLIC_ID", "TEST_PUBLIC_ID_FAKE")
    monkeypatch.setenv("NUMERAI_SECRET_KEY", "TEST_SECRET_KEY_FAKE")
    monkeypatch.setenv("NUMERAI_ALLOW_LIVE", "0")
    yield
