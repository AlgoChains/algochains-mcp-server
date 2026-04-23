"""
Tests for neutralize.py — feature neutralization.

Verifies:
- Output is labeled proxy_mmc (not raw corr)
- Output is in [0, 1] range (submission-safe)
- proportion=0 returns unchanged predictions
- Alignment with features_df index works correctly
"""
import numpy as np
import pandas as pd
import pytest
from scipy import stats

from algochains_mcp.tournament.numerai.neutralize import (
    neutralize_predictions,
    compute_feature_exposure,
    _gaussianize,
    _rank_to_01,
)


@pytest.fixture
def sample_predictions():
    n = 200
    rng = np.random.default_rng(42)
    raw = rng.uniform(0, 1, n)
    return pd.Series(raw, index=[f"id_{i}" for i in range(n)], name="raw")


@pytest.fixture
def sample_features_df(sample_predictions):
    rng = np.random.default_rng(7)
    n = len(sample_predictions)
    data = {f"feature_{i}": rng.uniform(0, 1, n) for i in range(5)}
    return pd.DataFrame(data, index=sample_predictions.index)


class TestNeutralizePredictions:
    def test_output_range_in_01(self, sample_predictions, sample_features_df):
        result = neutralize_predictions(
            sample_predictions, sample_features_df, list(sample_features_df.columns)
        )
        assert result.between(0, 1).all(), "Neutralized predictions must be in [0, 1]"

    def test_output_name_is_proxy_mmc(self, sample_predictions, sample_features_df):
        result = neutralize_predictions(
            sample_predictions, sample_features_df, list(sample_features_df.columns)
        )
        assert result.name == "proxy_mmc", "Output must be labeled proxy_mmc (HK-10)"

    def test_proportion_zero_unchanged(self, sample_predictions, sample_features_df):
        result = neutralize_predictions(
            sample_predictions, sample_features_df, list(sample_features_df.columns), proportion=0
        )
        pd.testing.assert_series_equal(result, sample_predictions, check_names=False)

    def test_output_has_nonzero_std(self, sample_predictions, sample_features_df):
        result = neutralize_predictions(
            sample_predictions, sample_features_df, list(sample_features_df.columns)
        )
        assert result.std() > 0, "Neutralized predictions must have non-zero std"

    def test_missing_features_returns_unchanged(self, sample_predictions, sample_features_df):
        result = neutralize_predictions(
            sample_predictions, sample_features_df, ["nonexistent_feature"]
        )
        pd.testing.assert_series_equal(result, sample_predictions, check_names=False)

    def test_index_alignment(self, sample_predictions, sample_features_df):
        # Use only half the features rows
        partial_features = sample_features_df.iloc[:100]
        result = neutralize_predictions(
            sample_predictions, partial_features, list(partial_features.columns)
        )
        # Result should only have the common index
        assert len(result) == 100
        assert set(result.index) == set(partial_features.index)

    def test_reduced_feature_exposure_after_neutralization(self, sample_predictions, sample_features_df):
        """After neutralization, correlation with features should decrease."""
        feat_names = list(sample_features_df.columns)
        raw_exposure = compute_feature_exposure(sample_predictions, sample_features_df, feat_names)
        neutralized = neutralize_predictions(sample_predictions, sample_features_df, feat_names)
        neu_exposure = compute_feature_exposure(neutralized, sample_features_df, feat_names)

        raw_max_abs = raw_exposure.abs().mean()
        neu_max_abs = neu_exposure.abs().mean()
        assert neu_max_abs < raw_max_abs, "Neutralization should reduce feature exposure"


class TestGaussianize:
    def test_output_is_normal_distributed(self):
        rng = np.random.default_rng(0)
        x = rng.uniform(0, 1, 1000)
        g = _gaussianize(x)
        _, p_value = stats.normaltest(g)
        assert p_value > 0.01, "Gaussianized output should be approximately normal"

    def test_monotonic_with_input(self):
        x = np.array([0.1, 0.5, 0.9, 0.3, 0.7])
        g = _gaussianize(x)
        corr, _ = stats.spearmanr(x, g)
        assert corr > 0.99, "Gaussianize must be monotonic"


class TestRankTo01:
    def test_range(self):
        x = np.array([3.0, 1.0, 4.0, 1.0, 5.0])
        result = _rank_to_01(x)
        assert result.min() > 0
        assert result.max() < 1

    def test_monotonic(self):
        x = np.linspace(0, 1, 100)
        result = _rank_to_01(x)
        corr, _ = stats.spearmanr(x, result)
        assert corr > 0.99
