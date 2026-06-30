"""Convenience function that chains the full geospatial pipeline.

For callers who want a single call rather than wiring the three steps manually.
Each step is still independently importable and callable for debugging or
partial pipeline use.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from analytics_toolbox.geospatial.address_geocoder import geocode_addresses
from analytics_toolbox.geospatial.address_matcher import match_addresses
from analytics_toolbox.geospatial.address_normalizer import normalize_addresses


def geocode_address_table(
    addresses: pd.DataFrame,
    config_path: str | Path,
    *,
    street_address_col: str = "Street_Address",
    city_col: str = "City",
    state_col: str = "State",
    postal_code_col: str = "Postal_Code",
) -> pd.DataFrame:
    """Geocode a DataFrame of addresses to Census block groups.

    Chains three steps in sequence:

    1. ``normalize_addresses`` — standardize per USPS Pub 28, flag non-standard.
    2. ``match_addresses`` — fuzzy-match against NAD in DuckDB (threshold from config).
    3. ``geocode_addresses`` — point-in-polygon against TIGER block groups in DuckDB.

    All original columns are preserved and the original index is maintained.
    Every input row appears in the output exactly once.

    Prerequisites (run once before calling this function):

    - ``analytics-toolbox ingest-nad --config <path>`` to populate ``nad_addresses``
    - ``analytics-toolbox ingest-tiger --config <path>`` to populate TIGER tables

    Args:
        addresses: One row per address. Must contain the columns named by the
            ``*_col`` parameters (defaults match the ``normalize_addresses`` API).
            Any extra columns are passed through untouched.
        config_path: Path to the YAML config file. Controls which DuckDB,
            TIGER vintage, and confidence threshold to use.
        street_address_col: Column holding the unparsed street line.
        city_col: Column holding the city.
        state_col: Column holding the state abbreviation.
        postal_code_col: Column holding the ZIP/postal code.

    Returns:
        ``addresses`` with all normalizer, matcher, and geocoder columns appended.
        See the individual module docstrings for the full column list.
    """
    from analytics_toolbox._config import load_config  # lazy — avoids circular import
    geo_config = load_config(config_path).geospatial

    normalized = normalize_addresses(
        addresses,
        street_address_col=street_address_col,
        city_col=city_col,
        state_col=state_col,
        postal_code_col=postal_code_col,
    )
    matched = match_addresses(normalized, geo_config)
    return geocode_addresses(matched, geo_config)
