"""Tests for _synthesize.py — numeric and categorical synthesis."""

from __future__ import annotations

import numpy as np

from analytics_toolbox.synth_kit._synthesize import (
    synthesize_categorical,
    synthesize_numeric,
)
from analytics_toolbox.synth_kit._types import CategoricalProfile, ColumnProfile


def _make_numeric_profile(**overrides) -> ColumnProfile:
    defaults = dict(
        name="age",
        sql_type="INTEGER",
        n_total=100,
        n_non_null=100,
        null_rate=0.0,
        val_min=20.0,
        val_max=79.0,
        val_mean=49.5,
        val_stddev=17.0,
        percentiles={
            "p01": 21.0, "p05": 23.0, "p10": 26.0, "p25": 34.0, "p50": 49.0,
            "p75": 64.0, "p90": 72.0, "p95": 76.0, "p99": 78.0,
        },
    )
    defaults.update(overrides)
    return ColumnProfile(**defaults)


def _make_cat_profile(**overrides) -> CategoricalProfile:
    defaults = dict(
        name="category",
        sql_type="VARCHAR",
        n_total=100,
        n_non_null=100,
        null_rate=0.0,
        value_counts={"A": 50, "B": 30, "C": 20},
    )
    defaults.update(overrides)
    return CategoricalProfile(**defaults)


# ── numeric synthesis (Task 7) ───────────────────────────────────────────────

def test_numeric_output_length():
    profile = _make_numeric_profile()
    result = synthesize_numeric(profile, n_rows=200, rng=np.random.default_rng(42))
    assert len(result) == 200


def test_numeric_values_within_min_max():
    profile = _make_numeric_profile()
    result = synthesize_numeric(profile, n_rows=500, rng=np.random.default_rng(0))
    non_null = [v for v in result if v is not None]
    assert all(profile.val_min <= v <= profile.val_max for v in non_null)


def test_numeric_null_rate_preserved():
    profile = _make_numeric_profile(null_rate=0.2, n_non_null=80)
    result = synthesize_numeric(profile, n_rows=1000, rng=np.random.default_rng(1))
    null_count = sum(1 for v in result if v is None)
    null_rate = null_count / 1000
    assert abs(null_rate - 0.2) < 0.05


def test_numeric_zero_null_rate():
    profile = _make_numeric_profile(null_rate=0.0)
    result = synthesize_numeric(profile, n_rows=100, rng=np.random.default_rng(2))
    assert all(v is not None for v in result)


def test_numeric_reproducible_with_seed():
    profile = _make_numeric_profile()
    r1 = synthesize_numeric(profile, n_rows=50, rng=np.random.default_rng(99))
    r2 = synthesize_numeric(profile, n_rows=50, rng=np.random.default_rng(99))
    assert r1 == r2


def test_numeric_all_null_column():
    """Column with 100% nulls returns all None."""
    profile = _make_numeric_profile(null_rate=1.0, n_non_null=0, val_min=None, val_max=None)
    result = synthesize_numeric(profile, n_rows=50, rng=np.random.default_rng(0))
    assert all(v is None for v in result)


# ── categorical synthesis (Task 8) ───────────────────────────────────────────

def test_categorical_output_length():
    profile = _make_cat_profile()
    result = synthesize_categorical(profile, n_rows=150, rng=np.random.default_rng(42))
    assert len(result) == 150


def test_categorical_values_from_known_set():
    profile = _make_cat_profile()
    result = synthesize_categorical(profile, n_rows=200, rng=np.random.default_rng(5))
    non_null = [v for v in result if v is not None]
    assert all(v in {"A", "B", "C"} for v in non_null)


def test_categorical_proportions_approximate():
    profile = _make_cat_profile()
    result = synthesize_categorical(profile, n_rows=10000, rng=np.random.default_rng(7))
    non_null = [v for v in result if v is not None]
    counts = {v: non_null.count(v) for v in ["A", "B", "C"]}
    total = len(non_null)
    assert abs(counts["A"] / total - 0.5) < 0.03
    assert abs(counts["B"] / total - 0.3) < 0.03
    assert abs(counts["C"] / total - 0.2) < 0.03


def test_categorical_null_rate_preserved():
    profile = _make_cat_profile(null_rate=0.1, n_non_null=90)
    result = synthesize_categorical(profile, n_rows=1000, rng=np.random.default_rng(8))
    null_count = sum(1 for v in result if v is None)
    null_rate = null_count / 1000
    assert abs(null_rate - 0.1) < 0.05


def test_categorical_aliases_replace_values():
    profile = _make_cat_profile()
    aliases = ["X", "Y", "Z"]
    result = synthesize_categorical(
        profile, n_rows=300, rng=np.random.default_rng(3), aliases=aliases
    )
    non_null = [v for v in result if v is not None]
    assert all(v in {"X", "Y", "Z"} for v in non_null)


def test_categorical_aliases_preserve_proportions():
    """Aliases replace values but preserve relative source proportions."""
    profile = _make_cat_profile()
    aliases = ["X", "Y", "Z"]
    result = synthesize_categorical(
        profile, n_rows=10000, rng=np.random.default_rng(4), aliases=aliases
    )
    non_null = [v for v in result if v is not None]
    counts = {v: non_null.count(v) for v in ["X", "Y", "Z"]}
    total = len(non_null)
    assert abs(counts["X"] / total - 0.5) < 0.03
    assert abs(counts["Y"] / total - 0.3) < 0.03
    assert abs(counts["Z"] / total - 0.2) < 0.03


def test_categorical_reproducible_with_seed():
    profile = _make_cat_profile()
    r1 = synthesize_categorical(profile, n_rows=50, rng=np.random.default_rng(11))
    r2 = synthesize_categorical(profile, n_rows=50, rng=np.random.default_rng(11))
    assert r1 == r2
