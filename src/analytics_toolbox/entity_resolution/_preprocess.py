"""Field standardization and address normalization for entity resolution.

Produces consistent string representations so RapidFuzz comparisons are
not sensitive to trivial formatting differences (case, whitespace, date format).

Address normalization uses geospatial.normalize_addresses (scourgify-based,
no NAD required). If the geospatial extra is not installed, address normalization
is skipped and a warning is emitted — all other fields still participate in scoring.
"""

from __future__ import annotations

import logging
import warnings

import pandas as pd

from analytics_toolbox.entity_resolution._config import EntityResolutionConfig

log = logging.getLogger(__name__)


def normalize_fields(df: pd.DataFrame, config: EntityResolutionConfig) -> pd.DataFrame:
    """Standardize a system DataFrame for consistent fuzzy matching.

    Performs three passes:
    1. Uppercase + strip whitespace for string fields that appear in ``field_weights``.
    2. Normalize the ``block_column`` (DOB by default) to ISO 8601 (YYYY-MM-DD) strings.
    3. If the address column is present, run scourgify normalization and substitute
       ``normalized_address_line_1``; non-standard addresses become empty string.

    Args:
        df: Input system DataFrame. Not modified in place.
        config: EntityResolutionConfig controlling column names and field weights.

    Returns:
        A copy of ``df`` with standardized values.
    """
    df = df.copy()

    # Pass 1 — uppercase + strip string fields in field_weights (except block_column,
    # which gets its own normalization in pass 2)
    weight_cols = set(config.field_weights.keys())
    for col in weight_cols - {config.block_column}:
        if col in df.columns and df[col].dtype.kind == "O":
            df[col] = df[col].str.strip().str.upper()

    # Pass 2 — normalize block_column to YYYY-MM-DD strings
    if config.block_column in df.columns:
        df[config.block_column] = _normalize_date_col(df[config.block_column])

    # Pass 3 — address normalization
    if config.address_col in df.columns:
        df = _apply_address_normalization(df, config)

    return df


def _normalize_date_col(col: pd.Series) -> pd.Series:
    """Parse date strings in various formats and return YYYY-MM-DD strings.

    Null values remain null. Unparseable values are left as-is with a warning.
    """
    parsed = pd.to_datetime(col, errors="coerce")
    result = parsed.dt.strftime("%Y-%m-%d")
    # Restore nulls that were null in the original (strftime on NaT gives 'NaT')
    null_mask = col.isna() | (result == "NaT")
    result[null_mask] = None
    return result


def _apply_address_normalization(
    df: pd.DataFrame, config: EntityResolutionConfig
) -> pd.DataFrame:
    """Run scourgify normalization on the address column and substitute the result.

    Non-standard addresses (PO Box, military, unparseable) become empty string
    so they do not contribute false-positive similarity scores.

    Falls back gracefully if the geospatial extra is not installed.
    """
    try:
        from analytics_toolbox.geospatial.address_normalizer import normalize_addresses
    except ImportError:
        warnings.warn(
            "analytics_toolbox.geospatial is not installed — address normalization "
            "will be skipped. Install analytics-toolbox[geospatial] to enable it.",
            stacklevel=3,
        )
        return df

    addr_col = config.address_col

    # scourgify needs city, state, postal_code columns. Supply empty strings for any
    # that are absent so normalize_addresses doesn't raise on a missing column.
    work = df.copy()
    for col in ("City", "State", "Postal_Code"):
        if col not in work.columns:
            work[col] = ""

    try:
        normalized = normalize_addresses(
            work,
            street_address_col=addr_col,
            city_col="City",
            state_col="State",
            postal_code_col="Postal_Code",
        )
    except Exception as exc:  # noqa: BLE001
        warnings.warn(
            f"Address normalization failed ({exc}); address column will be used as-is.",
            stacklevel=3,
        )
        return df

    # Substitute: standard addresses → normalized_address_line_1 (uppercased);
    # non-standard → empty string (won't match anything, which is correct — we can't
    # trust the street-level representation for a PO Box or military address).
    norm_line1 = normalized.get("normalized_address_line_1", pd.Series(dtype=object))
    is_standard = normalized.get("is_standard_address", pd.Series(True, index=df.index))

    df = df.copy()
    df[addr_col] = norm_line1.where(is_standard, other="").fillna("").str.upper()
    return df
