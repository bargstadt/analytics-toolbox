"""Integration tests for the synthesize() public API."""

from __future__ import annotations

import re

import pandas as pd
import pytest
from sqlalchemy.engine import Engine

from analytics_toolbox.synth_kit import synthesize

# ── basic shape and schema ────────────────────────────────────────────────────

def test_output_is_dataframe(populated_engine: Engine) -> None:
    result = synthesize(populated_engine, "SELECT * FROM test_source", random_seed=0)
    assert isinstance(result, pd.DataFrame)


def test_output_columns_match_source(populated_engine: Engine) -> None:
    result = synthesize(populated_engine, "SELECT * FROM test_source", random_seed=0)
    expected = {"age", "salary", "score", "category", "status", "join_date",
                "patient_name", "email_address"}
    assert set(result.columns) == expected


def test_default_row_count_matches_source(populated_engine: Engine) -> None:
    result = synthesize(populated_engine, "SELECT * FROM test_source", random_seed=0)
    assert len(result) == 100


def test_n_rows_override(populated_engine: Engine) -> None:
    result = synthesize(populated_engine, "SELECT * FROM test_source", n_rows=250, random_seed=0)
    assert len(result) == 250


def test_column_subset_query(populated_engine: Engine) -> None:
    result = synthesize(
        populated_engine,
        "SELECT age, salary FROM test_source",
        random_seed=0,
    )
    assert set(result.columns) == {"age", "salary"}
    assert len(result) == 100


# ── PHI replacement ──────────────────────────────────────────────────────────

def test_phi_columns_detected_and_replaced(populated_engine: Engine) -> None:
    result = synthesize(populated_engine, "SELECT * FROM test_source", random_seed=0)
    # patient_name: source values are "Patient 1" ... "Patient 100"
    patient_source = {f"Patient {i}" for i in range(1, 101)}
    assert not any(v in patient_source for v in result["patient_name"] if v is not None)


def test_email_phi_valid_format(populated_engine: Engine) -> None:
    result = synthesize(populated_engine, "SELECT * FROM test_source", random_seed=1)
    email_re = re.compile(r"[^@]+@[^@]+\.[^@]+")
    for v in result["email_address"].dropna():
        assert email_re.match(str(v)), f"Invalid email: {v!r}"


# ── numeric synthesis ────────────────────────────────────────────────────────

def test_numeric_values_within_source_range(populated_engine: Engine) -> None:
    result = synthesize(populated_engine, "SELECT * FROM test_source", random_seed=2)
    non_null_salary = result["salary"].dropna()
    assert (non_null_salary >= 30000.0).all()
    assert (non_null_salary <= 84000.0).all()


def test_null_rate_preserved_approximately(populated_engine: Engine) -> None:
    result = synthesize(
        populated_engine,
        "SELECT * FROM test_source",
        n_rows=1000,
        random_seed=3,
    )
    null_rate = result["age"].isna().mean()
    assert abs(null_rate - 0.2) < 0.05


# ── categorical synthesis ────────────────────────────────────────────────────

def test_categorical_values_from_known_set(populated_engine: Engine) -> None:
    result = synthesize(populated_engine, "SELECT * FROM test_source", random_seed=4)
    valid = {"A", "B", "C"}
    for v in result["category"].dropna():
        assert v in valid


def test_categorical_aliases_used(populated_engine: Engine) -> None:
    result = synthesize(
        populated_engine,
        "SELECT * FROM test_source",
        categorical_aliases={"category": ["X", "Y", "Z"]},
        random_seed=5,
    )
    valid = {"X", "Y", "Z"}
    for v in result["category"].dropna():
        assert v in valid


# ── reproducibility ───────────────────────────────────────────────────────────

def test_random_seed_reproducible(populated_engine: Engine) -> None:
    r1 = synthesize(populated_engine, "SELECT age, salary FROM test_source", random_seed=99)
    r2 = synthesize(populated_engine, "SELECT age, salary FROM test_source", random_seed=99)
    pd.testing.assert_frame_equal(r1, r2)


# ── phi_overrides ─────────────────────────────────────────────────────────────

def test_phi_overrides_applied(populated_engine: Engine) -> None:
    result = synthesize(
        populated_engine,
        "SELECT age, score FROM test_source",
        phi_overrides={"score": "email"},
        random_seed=6,
    )
    email_re = re.compile(r"[^@]+@[^@]+\.[^@]+")
    for v in result["score"].dropna():
        assert email_re.match(str(v))


# ── suppress_phi ──────────────────────────────────────────────────────────────

def test_suppress_phi_emits_warning(populated_engine: Engine) -> None:
    with pytest.warns(UserWarning, match="patient_name"):
        synthesize(
            populated_engine,
            "SELECT * FROM test_source",
            suppress_phi=["patient_name"],
            random_seed=7,
        )


# ── date columns ──────────────────────────────────────────────────────────────

def test_date_column_non_phi_passes_through_as_synthesized(populated_engine: Engine) -> None:
    """join_date is a DATE but not a PHI name — it's treated as categorical."""
    result = synthesize(populated_engine, "SELECT join_date FROM test_source", random_seed=8)
    assert len(result) == 100
    assert "join_date" in result.columns
