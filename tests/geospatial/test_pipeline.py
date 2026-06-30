"""End-to-end tests for geocode_address_table.

Wires all four pipeline steps together using synthetic fixtures and mocked
network calls. Verifies the full contract: every input row survives,
all expected output columns are present, non-standard addresses flow through
correctly, and the index is preserved.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import duckdb
import geopandas as gpd
import pandas as pd
import pytest

from analytics_toolbox.geospatial import geocode_address_table


@pytest.fixture
def full_config_yaml(
    tmp_path: Path,
    synthetic_block_groups: gpd.GeoDataFrame,
    synthetic_zcta: gpd.GeoDataFrame,
) -> Path:
    """Config YAML pointing at a temp DuckDB pre-loaded with TIGER data."""
    db_path = tmp_path / "pipeline_test.duckdb"
    conn = duckdb.connect(str(db_path))

    conn.execute("""
        CREATE TABLE tiger_block_groups_2024 (
            block_group_fips VARCHAR,
            geom_wkt VARCHAR
        )
    """)
    for _, row in synthetic_block_groups.iterrows():
        conn.execute(
            "INSERT INTO tiger_block_groups_2024 VALUES (?, ?)",
            [row["block_group_fips"], row["geometry"].wkt],
        )

    conn.execute("""
        CREATE TABLE tiger_zcta_2024 (
            zcta5 VARCHAR,
            centroid_lat DOUBLE,
            centroid_lon DOUBLE
        )
    """)
    for _, row in synthetic_zcta.iterrows():
        centroid = row["geometry"].centroid
        conn.execute(
            "INSERT INTO tiger_zcta_2024 VALUES (?, ?, ?)",
            [row["zcta5"], centroid.y, centroid.x],
        )

    conn.execute("""
        CREATE TABLE nad_addresses (
            nad_id VARCHAR, state VARCHAR, county_fips VARCHAR,
            postal_code VARCHAR, nad_address_line_1 VARCHAR,
            normalized_address_line_1 VARCHAR, normalized_postal_code VARCHAR,
            latitude DOUBLE, longitude DOUBLE,
            is_standard_address BOOLEAN, address_flag VARCHAR
        )
    """)
    conn.execute("""
        INSERT INTO nad_addresses VALUES
        ('OR-001', 'OR', '41051', '97201', '100 MAIN ST', '100 MAIN ST',
         '97201', 45.5, -122.5, true, 'standard')
    """)
    conn.close()

    content = textwrap.dedent(f"""\
        storage:
          data_dir: {tmp_path}/
          connection: {db_path}

        geospatial:
          nad:
            states: [OR]
            force_refresh: false
          tiger:
            vintage: 2024
            force_refresh: false
          matching:
            confidence_threshold: 90
    """)
    p = tmp_path / "config.yaml"
    p.write_text(content)
    return p


@pytest.fixture
def raw_addresses() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Street_Address": "100 Main St",
                "City": "Portland",
                "State": "OR",
                "Postal_Code": "97201",
                "County": "Multnomah",
            },
            {
                "Street_Address": "PO Box 999",
                "City": "Portland",
                "State": "OR",
                "Postal_Code": "97201",
                "County": "Multnomah",
            },
            {
                "Street_Address": "456 Oak Ave",
                "City": "Portland",
                "State": "OR",
                "Postal_Code": "97201",
                "County": "Multnomah",
            },
        ],
        index=[10, 20, 30],
    )


def test_no_rows_dropped(full_config_yaml: Path, raw_addresses: pd.DataFrame) -> None:
    result = geocode_address_table(raw_addresses, full_config_yaml)
    assert len(result) == len(raw_addresses)


def test_index_preserved(full_config_yaml: Path, raw_addresses: pd.DataFrame) -> None:
    result = geocode_address_table(raw_addresses, full_config_yaml)
    assert list(result.index) == [10, 20, 30]


def test_original_columns_preserved(full_config_yaml: Path, raw_addresses: pd.DataFrame) -> None:
    result = geocode_address_table(raw_addresses, full_config_yaml)
    for col in raw_addresses.columns:
        assert col in result.columns


def test_output_schema_complete(full_config_yaml: Path, raw_addresses: pd.DataFrame) -> None:
    result = geocode_address_table(raw_addresses, full_config_yaml)
    expected = {
        # normalizer
        "normalized_address_line_1", "normalized_postal_code",
        "is_standard_address", "address_flag",
        # matcher
        "nad_id", "match_score", "match_rank", "match_method",
        "matched_latitude", "matched_longitude",
        # geocoder
        "block_group_fips", "census_tract_fips", "tiger_vintage", "location_imputed",
    }
    assert expected.issubset(set(result.columns))


def test_standard_address_gets_block_group(
    full_config_yaml: Path, raw_addresses: pd.DataFrame
) -> None:
    result = geocode_address_table(raw_addresses, full_config_yaml)
    standard_row = result.loc[10]
    assert bool(standard_row["is_standard_address"])
    assert pd.notna(standard_row["block_group_fips"])
    assert not bool(standard_row["location_imputed"])


def test_po_box_uses_centroid_fallback(full_config_yaml: Path, raw_addresses: pd.DataFrame) -> None:
    result = geocode_address_table(raw_addresses, full_config_yaml)
    po_row = result.loc[20]
    assert po_row["match_method"] == "non_standard"
    assert po_row["location_imputed"] is True or po_row["location_imputed"] == True  # noqa: E712


def test_top_level_import() -> None:
    from analytics_toolbox.geospatial import geocode_address_table as fn
    assert callable(fn)


def test_custom_column_names(full_config_yaml: Path) -> None:
    df = pd.DataFrame([{
        "addr": "100 Main St",
        "city": "Portland",
        "st": "OR",
        "zip": "97201",
    }])
    result = geocode_address_table(
        df,
        full_config_yaml,
        street_address_col="addr",
        city_col="city",
        state_col="st",
        postal_code_col="zip",
    )
    assert len(result) == 1
    assert "addr" in result.columns
