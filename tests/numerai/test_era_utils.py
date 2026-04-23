"""
Tests for era_utils.py — era-based splitting, embargo, k-fold.

HK-1: Verifies no random splits; era ordering; embargo gap enforced.
"""
import numpy as np
import pandas as pd
import pytest

from algochains_mcp.tournament.numerai.era_utils import (
    embargo_filter,
    era_kfold,
    era_split,
    _era_sort_key,
)


@pytest.fixture
def small_df():
    """20 eras, 10 rows each."""
    records = []
    for era_num in range(1, 21):
        for _ in range(10):
            records.append({"era": f"era{era_num}", "feature_a": float(era_num), "target": 0.5})
    df = pd.DataFrame(records)
    df.index = range(len(df))
    return df


class TestEraSplit:
    def test_basic_split(self, small_df):
        train, val = era_split(small_df, holdout_n=4, embargo_gap=4)
        train_eras = set(train["era"].unique())
        val_eras = set(val["era"].unique())
        assert not (train_eras & val_eras), "Train and val must not share eras"

    def test_holdout_count(self, small_df):
        _, val = era_split(small_df, holdout_n=4, embargo_gap=4)
        assert val["era"].nunique() == 4

    def test_embargo_gap(self, small_df):
        """Train eras must not be within embargo_gap of val eras."""
        train, val = era_split(small_df, holdout_n=4, embargo_gap=4)
        train_max = max(_era_sort_key(e) for e in train["era"].unique())
        val_min = min(_era_sort_key(e) for e in val["era"].unique())
        assert val_min - train_max > 4, "Embargo gap of 4 eras required"

    def test_no_random_split(self, small_df):
        """Val must be the LAST eras, not random."""
        _, val = era_split(small_df, holdout_n=4, embargo_gap=4)
        val_era_nums = sorted(_era_sort_key(e) for e in val["era"].unique())
        assert val_era_nums == [17, 18, 19, 20], "Val must be last N eras"

    def test_missing_era_column_raises(self, small_df):
        df_no_era = small_df.drop(columns=["era"])
        with pytest.raises(ValueError, match="era"):
            era_split(df_no_era)

    def test_too_few_eras_raises(self):
        df = pd.DataFrame({"era": ["era1"] * 5, "target": [0.5] * 5})
        with pytest.raises(ValueError, match="Too few eras"):
            era_split(df, holdout_n=4, embargo_gap=4)

    def test_row_counts_sum_to_less_than_total(self, small_df):
        """Train + val < total due to embargo rows being dropped."""
        train, val = era_split(small_df, holdout_n=4, embargo_gap=4)
        assert len(train) + len(val) <= len(small_df)

    def test_train_is_chronologically_before_val(self, small_df):
        train, val = era_split(small_df, holdout_n=4, embargo_gap=4)
        train_max = max(_era_sort_key(e) for e in train["era"].unique())
        val_min = min(_era_sort_key(e) for e in val["era"].unique())
        assert train_max < val_min


class TestEmbargoFilter:
    def test_removes_adjacent_eras(self, small_df):
        filtered = embargo_filter(small_df, ref_era="era10", embargo_n=2, direction="after")
        removed_eras = {"era11", "era12"}
        remaining_eras = set(filtered["era"].unique())
        assert not (removed_eras & remaining_eras)

    def test_before_direction(self, small_df):
        filtered = embargo_filter(small_df, ref_era="era10", embargo_n=2, direction="before")
        removed_eras = {"era8", "era9"}
        remaining_eras = set(filtered["era"].unique())
        assert not (removed_eras & remaining_eras)

    def test_missing_era_column_raises(self, small_df):
        df_no_era = small_df.drop(columns=["era"])
        with pytest.raises(ValueError):
            embargo_filter(df_no_era, ref_era="era10")


class TestEraKfold:
    def test_produces_folds(self, small_df):
        folds = era_kfold(small_df, n_splits=3, embargo_gap=2)
        assert len(folds) >= 1
        for train_f, val_f in folds:
            assert len(train_f) > 0
            assert len(val_f) > 0

    def test_no_era_overlap_in_folds(self, small_df):
        folds = era_kfold(small_df, n_splits=3, embargo_gap=2)
        for train_f, val_f in folds:
            train_eras = set(train_f["era"].unique())
            val_eras = set(val_f["era"].unique())
            assert not (train_eras & val_eras)

    def test_walk_forward_ordering(self, small_df):
        """For each fold, all val eras must be after all train eras."""
        folds = era_kfold(small_df, n_splits=3, embargo_gap=2)
        for train_f, val_f in folds:
            train_max = max(_era_sort_key(e) for e in train_f["era"].unique())
            val_min = min(_era_sort_key(e) for e in val_f["era"].unique())
            assert train_max < val_min


class TestEraSortKey:
    def test_numeric_eras(self):
        assert _era_sort_key("era1") == 1
        assert _era_sort_key("era501") == 501
        assert _era_sort_key("1") == 1

    def test_non_numeric_era(self):
        assert _era_sort_key("live") == 0
