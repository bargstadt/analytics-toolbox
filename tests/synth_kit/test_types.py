"""Tests for _types.py dataclasses."""

from __future__ import annotations

import dataclasses

from analytics_toolbox.synth_kit._types import (
    CategoricalProfile,
    ColumnProfile,
    PhiMap,
    SynthConfig,
)


def test_column_profile_fields():
    fields = {f.name for f in dataclasses.fields(ColumnProfile)}
    assert fields == {
        "name", "sql_type", "n_total", "n_non_null", "null_rate",
        "val_min", "val_max", "val_mean", "val_stddev", "percentiles",
    }


def test_column_profile_instantiation():
    p = ColumnProfile(
        name="age", sql_type="INTEGER", n_total=100, n_non_null=90, null_rate=0.1,
        val_min=20.0, val_max=79.0, val_mean=49.5, val_stddev=17.1,
        percentiles={"p01": 20.0, "p05": 22.0, "p10": 25.0, "p25": 33.0, "p50": 49.0,
                     "p75": 65.0, "p90": 73.0, "p95": 77.0, "p99": 79.0},
    )
    assert p.null_rate == 0.1
    assert p.percentiles["p50"] == 49.0


def test_categorical_profile_fields():
    fields = {f.name for f in dataclasses.fields(CategoricalProfile)}
    assert fields == {"name", "sql_type", "n_total", "n_non_null", "null_rate", "value_counts"}


def test_categorical_profile_instantiation():
    p = CategoricalProfile(
        name="status", sql_type="VARCHAR", n_total=100, n_non_null=100, null_rate=0.0,
        value_counts={"active": 70, "inactive": 30},
    )
    assert p.value_counts["active"] == 70


def test_phi_map_is_dict():
    phi: PhiMap = {"patient_name": "name", "dob": "dob"}
    assert isinstance(phi, dict)
    assert phi["patient_name"] == "name"


def test_synth_config_fields():
    fields = {f.name for f in dataclasses.fields(SynthConfig)}
    assert fields == {
        "n_rows", "categorical_aliases", "phi_overrides", "suppress_phi", "random_seed"
    }


def test_synth_config_defaults():
    cfg = SynthConfig()
    assert cfg.n_rows is None
    assert cfg.categorical_aliases == {}
    assert cfg.phi_overrides == {}
    assert cfg.suppress_phi == []
    assert cfg.random_seed is None
