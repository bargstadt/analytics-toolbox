"""Public synthesize() function — orchestrates Phase 1 profiling and Phase 2 synthesis."""

from __future__ import annotations

import logging
import pathlib
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.engine import Engine

from analytics_toolbox.synth_kit._detect import detect_phi
from analytics_toolbox.synth_kit._phi import PhiReplacer
from analytics_toolbox.synth_kit._profile import (
    profile_categorical,
    profile_numeric,
    query_schema,
)
from analytics_toolbox.synth_kit._synthesize import (
    synthesize_categorical,
    synthesize_numeric,
)
from analytics_toolbox.synth_kit._types import CategoricalProfile, SynthConfig

logger = logging.getLogger(__name__)

# Type names returned by cursor.description[i][1].__name__ that warrant numeric profiling
_NUMERIC_TYPES = frozenset({"int", "float", "Decimal", "complex"})

# Column names that look like a US state code column
_STATE_COL_NAMES = frozenset({"state", "state_code", "patient_state", "member_state", "st"})


def _is_numeric_type(type_name: str) -> bool:
    return type_name in _NUMERIC_TYPES or any(
        kw in type_name.lower()
        for kw in ("int", "float", "double", "decimal", "numeric", "real", "number")
    )


def _load_nad_pool(
    engine: Engine,
    query: str,
    col_names: list[str],
    phi_map: dict[str, str],
    geospatial_config: Any,
    random_seed: int | None,
) -> tuple[list[str], str | None, CategoricalProfile | None]:
    """Try to load a pool of real NAD addresses for the dominant state in the query result.

    Returns:
        (pool, state_col, state_profile) — state_col and state_profile are non-None when
        a state column was found, so the caller can skip re-profiling it in the main loop.
    """
    state_col = next(
        (c for c in col_names if c.lower() in _STATE_COL_NAMES and c not in phi_map),
        None,
    )
    if not state_col:
        return [], None, None

    state_profile = profile_categorical(engine, query, state_col)
    if not state_profile.value_counts:
        return [], state_col, state_profile

    dominant_state = max(state_profile.value_counts, key=state_profile.value_counts.get)
    logger.info(
        "NAD address pool: detected dominant state %s from column %r", dominant_state, state_col
    )

    # Resolve config object — accept GeospatialConfig directly or a path to config.yaml
    config = geospatial_config
    if isinstance(config, str | pathlib.Path):
        try:
            from analytics_toolbox._config import load_config
            config = load_config(config).geospatial
        except Exception as e:
            logger.warning("Could not load geospatial config from %r: %s", geospatial_config, e)
            return [], state_col, state_profile

    try:
        from analytics_toolbox.geospatial.nad_preprocess_ingest import sample_nad_addresses
        pool = sample_nad_addresses(500, state=dominant_state, config=config, seed=random_seed)
        logger.info("NAD address pool: loaded %d addresses for %s", len(pool), dominant_state)
        return pool, state_col, state_profile
    except Exception as e:
        logger.warning("NAD address pool unavailable (%s): address PHI will use Faker", e)
        return [], state_col, state_profile


def synthesize(
    engine: Engine,
    query: str,
    *,
    config: SynthConfig | None = None,
    n_rows: int | None = None,
    categorical_aliases: dict[str, list] | None = None,
    phi_overrides: dict[str, str] | None = None,
    suppress_phi: list[str] | None = None,
    random_seed: int | None = None,
    geospatial_config: str | pathlib.Path | None = None,
) -> pd.DataFrame:
    """Produce a fully synthetic version of a SQL query result.

    All profiling runs server-side as SQL aggregates. Raw rows are never
    fetched into Python. PHI columns are replaced wholesale via Faker and
    sequential IDs.

    Args:
        engine: SQLAlchemy engine connected to the source database.
        query: SQL query whose result to synthesize. Wrapped in a CTE — not
            string-interpolated with user data.
        config: Optional ``SynthConfig`` supplying defaults for the keyword
            arguments below. Individual kwargs take precedence over config fields.
        n_rows: Synthetic row count. None matches the source row count.
        categorical_aliases: {column: [replacement_values]} — alias list
            replaces source values while preserving relative proportions.
        phi_overrides: {column: phi_type} — force-classify columns, merged
            with auto-detection.
        suppress_phi: Column names to exclude from PHI auto-detection.
            Emits a warning for each column that would have been detected.
        random_seed: Seed for numpy/Faker RNGs. None = non-reproducible.
        geospatial_config: ``GeospatialConfig`` object or path to a
            ``config.yaml`` file. When provided and the result set contains a
            state column, address PHI is replaced with real NAD addresses
            sampled for the dominant state. Falls back to ``faker.street_address()``
            when NAD is unavailable or the geospatial extra is not installed.

    Returns:
        pd.DataFrame with the same column names as the source query, all PHI
        replaced, numeric values drawn from synthetic distributions, and
        categorical values sampled proportionally.
    """
    # Merge SynthConfig — individual kwargs take precedence
    if config is not None:
        if n_rows is None:
            n_rows = config.n_rows
        if categorical_aliases is None and config.categorical_aliases:
            categorical_aliases = config.categorical_aliases
        if phi_overrides is None and config.phi_overrides:
            phi_overrides = config.phi_overrides
        if suppress_phi is None and config.suppress_phi:
            suppress_phi = config.suppress_phi
        if random_seed is None:
            random_seed = config.random_seed

    # ── Phase 1: server-side profiling ───────────────────────────────────────

    schema, source_n_rows = query_schema(engine, query)
    target_n = n_rows if n_rows is not None else source_n_rows

    col_names = [name for name, _ in schema]
    col_types = {name: type_str for name, type_str in schema}

    phi_map = detect_phi(col_names, phi_overrides=phi_overrides, suppress_phi=suppress_phi)
    logger.info("Detected PHI columns: %s", phi_map)

    aliases = categorical_aliases or {}

    # Load NAD address pool if geospatial config is available; cache state profile to avoid
    # re-profiling the state column in the synthesis loop below.
    nad_pool: list[str] = []
    nad_state_col: str | None = None
    cached_profiles: dict[str, CategoricalProfile] = {}
    if geospatial_config is not None:
        nad_pool, nad_state_col, state_profile = _load_nad_pool(
            engine, query, col_names, phi_map, geospatial_config, random_seed
        )
        if nad_state_col is not None and state_profile is not None:
            cached_profiles[nad_state_col] = state_profile

    # ── Phase 2: synthesis ───────────────────────────────────────────────────

    rng = np.random.default_rng(random_seed)
    phi_replacer = PhiReplacer(random_seed=random_seed, nad_address_pool=nad_pool)

    output: dict[str, list] = {}

    for col in col_names:
        if col in phi_map:
            # PHI: replace wholesale — never sample real values
            output[col] = phi_replacer.replace(col, phi_map[col], [None] * target_n)

        elif _is_numeric_type(col_types[col]):
            profile = profile_numeric(engine, query, col)
            output[col] = synthesize_numeric(profile, target_n, rng)

        else:
            # Categorical (strings, dates, booleans) — use cached profile if available
            cat_profile = cached_profiles.get(col) or profile_categorical(engine, query, col)
            col_aliases = aliases.get(col)
            output[col] = synthesize_categorical(cat_profile, target_n, rng, aliases=col_aliases)

    return pd.DataFrame(output, columns=col_names)
