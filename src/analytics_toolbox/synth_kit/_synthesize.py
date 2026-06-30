"""Phase 2 synthesis: numeric interpolation and categorical sampling."""

from __future__ import annotations

import numpy as np

from analytics_toolbox.synth_kit._types import CategoricalProfile, ColumnProfile

_PERCENTILE_KEYS = ("p01", "p05", "p10", "p25", "p50", "p75", "p90", "p95", "p99")
_PERCENTILE_VALS = np.array([0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99])


def synthesize_numeric(
    profile: ColumnProfile,
    n_rows: int,
    rng: np.random.Generator,
) -> list[float | None]:
    """Synthesize numeric values from an empirical 11-point CDF.

    Args:
        profile: Aggregate stats from Phase 1.
        n_rows: Number of synthetic rows to produce.
        rng: Seeded numpy Generator for reproducibility.

    Returns:
        List of floats with None inserted at the source null rate.
    """
    if profile.null_rate >= 1.0 or profile.val_min is None or profile.val_max is None:
        return [None] * n_rows

    n_nulls = round(profile.null_rate * n_rows)
    n_real = n_rows - n_nulls

    if n_real == 0:
        return [None] * n_rows

    # Build 11-point CDF: min/max as anchors at 0 and 1
    cdf_x = np.concatenate([[0.0], _PERCENTILE_VALS, [1.0]])
    cdf_y = np.array(
        [profile.val_min]
        + [profile.percentiles[k] for k in _PERCENTILE_KEYS]
        + [profile.val_max]
    )

    # Sample uniform quantiles then interpolate
    u = rng.uniform(0.0, 1.0, size=n_real)
    sampled = np.interp(u, cdf_x, cdf_y)
    sampled = np.clip(sampled, profile.val_min, profile.val_max)

    result: list[float | None] = sampled.tolist()
    result.extend([None] * n_nulls)
    rng.shuffle(result)  # type: ignore[arg-type]
    return result


def synthesize_categorical(
    profile: CategoricalProfile,
    n_rows: int,
    rng: np.random.Generator,
    aliases: list[str] | None = None,
) -> list[str | None]:
    """Synthesize categorical values sampled proportionally from the source distribution.

    Args:
        profile: Value counts from Phase 1.
        n_rows: Number of synthetic rows to produce.
        rng: Seeded numpy Generator for reproducibility.
        aliases: Optional replacement value list. Must have the same length as the
            distinct source values (ordered by descending count) — alias[i] replaces
            the i-th most common source value.

    Returns:
        List of strings with None inserted at the source null rate.
    """
    n_nulls = round(profile.null_rate * n_rows)
    n_real = n_rows - n_nulls

    if n_real == 0 or not profile.value_counts:
        return [None] * n_rows

    # Sort by descending count for stable alias mapping
    sorted_items = sorted(profile.value_counts.items(), key=lambda kv: -kv[1])
    source_values = [v for v, _ in sorted_items]
    counts = np.array([c for _, c in sorted_items], dtype=float)
    probs = counts / counts.sum()

    if aliases is not None:
        if len(aliases) != len(sorted_items):
            raise ValueError(
                f"aliases has {len(aliases)} entries but column {profile.name!r} has "
                f"{len(sorted_items)} distinct values"
            )
        output_values = list(aliases)
    else:
        output_values = source_values

    indices = rng.choice(len(output_values), size=n_real, p=probs)
    result: list[str | None] = [output_values[i] for i in indices]
    result.extend([None] * n_nulls)
    rng.shuffle(result)  # type: ignore[arg-type]
    return result
