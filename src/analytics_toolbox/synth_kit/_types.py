"""Core dataclasses for synth_kit."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PhiMap = dict[str, str]


@dataclass
class ColumnProfile:
    """Statistical profile for a numeric column."""

    name: str
    sql_type: str
    n_total: int
    n_non_null: int
    null_rate: float
    val_min: float | None
    val_max: float | None
    val_mean: float | None
    val_stddev: float | None
    percentiles: dict[str, float]


@dataclass
class CategoricalProfile:
    """Statistical profile for a categorical column."""

    name: str
    sql_type: str
    n_total: int
    n_non_null: int
    null_rate: float
    value_counts: dict[str, int]


@dataclass
class SynthConfig:
    """User-supplied synthesis options."""

    n_rows: int | None = None
    categorical_aliases: dict[str, list[Any]] = field(default_factory=dict)
    phi_overrides: dict[str, str] = field(default_factory=dict)
    suppress_phi: list[str] = field(default_factory=list)
    random_seed: int | None = None
