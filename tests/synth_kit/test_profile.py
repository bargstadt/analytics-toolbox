"""Tests for _profile.py — schema extraction, numeric profiling, categorical profiling."""

from __future__ import annotations

import pytest
from sqlalchemy.engine import Engine

from analytics_toolbox.synth_kit._profile import (
    profile_categorical,
    profile_numeric,
    query_schema,
)
from analytics_toolbox.synth_kit._types import CategoricalProfile, ColumnProfile

# ── schema extraction (Task 4) ───────────────────────────────────────────────

def test_query_schema_returns_column_names(populated_engine: Engine) -> None:
    cols, n_rows = query_schema(populated_engine, "SELECT * FROM test_source")
    col_names = [c for c, _ in cols]
    assert "age" in col_names
    assert "salary" in col_names
    assert "category" in col_names
    assert "patient_name" in col_names


def test_query_schema_returns_row_count(populated_engine: Engine) -> None:
    _, n_rows = query_schema(populated_engine, "SELECT * FROM test_source")
    assert n_rows == 100


def test_query_schema_returns_type_info(populated_engine: Engine) -> None:
    cols, _ = query_schema(populated_engine, "SELECT * FROM test_source")
    col_dict = dict(cols)
    # types should be non-empty strings
    for dtype in col_dict.values():
        assert isinstance(dtype, str) and len(dtype) > 0


def test_query_schema_respects_column_subset(populated_engine: Engine) -> None:
    cols, n_rows = query_schema(populated_engine, "SELECT age, salary FROM test_source")
    col_names = [c for c, _ in cols]
    assert col_names == ["age", "salary"]
    assert n_rows == 100


def test_query_schema_filtered_query(populated_engine: Engine) -> None:
    _, n_rows = query_schema(
        populated_engine, "SELECT * FROM test_source WHERE status = 'active'"
    )
    assert n_rows == 70


# ── numeric profiling (Task 5) ───────────────────────────────────────────────

def test_profile_numeric_returns_column_profile(populated_engine: Engine) -> None:
    profile = profile_numeric(populated_engine, "SELECT * FROM test_source", "salary")
    assert isinstance(profile, ColumnProfile)
    assert profile.name == "salary"


def test_profile_numeric_row_counts(populated_engine: Engine) -> None:
    profile = profile_numeric(populated_engine, "SELECT * FROM test_source", "salary")
    assert profile.n_total == 100
    assert profile.n_non_null == 100
    assert profile.null_rate == pytest.approx(0.0)


def test_profile_numeric_null_rate(populated_engine: Engine) -> None:
    profile = profile_numeric(populated_engine, "SELECT * FROM test_source", "age")
    assert profile.n_total == 100
    assert profile.n_non_null == 80
    assert profile.null_rate == pytest.approx(0.2)


def test_profile_numeric_min_max(populated_engine: Engine) -> None:
    profile = profile_numeric(populated_engine, "SELECT * FROM test_source", "age")
    assert profile.val_min == pytest.approx(21.0, abs=1)
    assert profile.val_max == pytest.approx(79.0, abs=1)


def test_profile_numeric_percentiles_keys(populated_engine: Engine) -> None:
    profile = profile_numeric(populated_engine, "SELECT * FROM test_source", "salary")
    expected_keys = {"p01", "p05", "p10", "p25", "p50", "p75", "p90", "p95", "p99"}
    assert set(profile.percentiles.keys()) == expected_keys


def test_profile_numeric_percentiles_ordered(populated_engine: Engine) -> None:
    profile = profile_numeric(populated_engine, "SELECT * FROM test_source", "salary")
    p = profile.percentiles
    assert p["p01"] <= p["p05"] <= p["p10"] <= p["p25"] <= p["p50"]
    assert p["p50"] <= p["p75"] <= p["p90"] <= p["p95"] <= p["p99"]


def test_profile_numeric_mean_within_range(populated_engine: Engine) -> None:
    profile = profile_numeric(populated_engine, "SELECT * FROM test_source", "salary")
    assert profile.val_mean is not None
    assert profile.val_min <= profile.val_mean <= profile.val_max


# ── categorical profiling (Task 6) ──────────────────────────────────────────

def test_profile_categorical_returns_profile(populated_engine: Engine) -> None:
    profile = profile_categorical(populated_engine, "SELECT * FROM test_source", "category")
    assert isinstance(profile, CategoricalProfile)
    assert profile.name == "category"


def test_profile_categorical_value_counts(populated_engine: Engine) -> None:
    profile = profile_categorical(populated_engine, "SELECT * FROM test_source", "category")
    assert set(profile.value_counts.keys()) == {"A", "B", "C"}
    assert profile.value_counts["A"] == 50
    assert profile.value_counts["B"] == 30
    assert profile.value_counts["C"] == 20


def test_profile_categorical_null_rate(populated_engine: Engine) -> None:
    profile = profile_categorical(populated_engine, "SELECT * FROM test_source", "category")
    assert profile.null_rate == pytest.approx(0.0)
    assert profile.n_non_null == 100


def test_profile_categorical_status_column(populated_engine: Engine) -> None:
    profile = profile_categorical(populated_engine, "SELECT * FROM test_source", "status")
    assert profile.value_counts["active"] == 70
    assert profile.value_counts["inactive"] == 30


def test_profile_categorical_limit_500(engine: Engine) -> None:
    """Categorical profiling caps at 500 distinct values — verify no error for large cardinality."""
    from sqlalchemy import text
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE high_card AS
            SELECT i::VARCHAR AS val
            FROM generate_series(1, 1000) t(i)
        """))
    profile = profile_categorical(engine, "SELECT val FROM high_card", "val")
    assert len(profile.value_counts) <= 500
