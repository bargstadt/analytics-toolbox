"""Tests for address_geocoder.

Uses synthetic geopandas GeoDataFrames (from conftest) — no real TIGER downloads.
ingest_tiger is tested by mocking httpx and injecting synthetic shapefiles;
geocode_addresses is tested by pre-loading the DuckDB with synthetic geometry data.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import duckdb
import geopandas as gpd
import pandas as pd
import pytest

from analytics_toolbox.geospatial._config import (
    GeospatialConfig,
    MatcherConfig,
    NadConfig,
    StorageConfig,
    TigerConfig,
)
from analytics_toolbox.geospatial.address_geocoder import geocode_addresses, ingest_tiger


@pytest.fixture
def config(tmp_path: Path) -> GeospatialConfig:
    return GeospatialConfig(
        nad=NadConfig(states=["OR"]),
        tiger=TigerConfig(vintage=2024, force_refresh=False),
        matching=MatcherConfig(confidence_threshold=90),
        storage=StorageConfig(
            data_dir=tmp_path,
            connection=str(tmp_path / "test.duckdb"),
        ),
    )


@pytest.fixture
def tiger_db(
    config: GeospatialConfig,
    synthetic_block_groups: gpd.GeoDataFrame,
    synthetic_zcta: gpd.GeoDataFrame,
) -> duckdb.DuckDBPyConnection:
    """Pre-load a DuckDB with synthetic TIGER geometry tables."""
    conn = duckdb.connect(config.storage.connection)
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

    return conn


def test_point_inside_polygon_gets_correct_fips(
    config: GeospatialConfig,
    tiger_db: duckdb.DuckDBPyConnection,
    matched_addresses_df: pd.DataFrame,
) -> None:
    result = geocode_addresses(matched_addresses_df, config)

    nad_row = result[result["match_method"] == "nad_match"].iloc[0]
    # matched_lat=45.5, matched_lon=-122.5 → inside first block group polygon
    assert nad_row["block_group_fips"] == "010010201001"
    assert nad_row["census_tract_fips"] == "01001020100"


def test_tiger_vintage_in_output(
    config: GeospatialConfig,
    tiger_db: duckdb.DuckDBPyConnection,
    matched_addresses_df: pd.DataFrame,
) -> None:
    result = geocode_addresses(matched_addresses_df, config)
    assert (result["tiger_vintage"] == 2024).all()


def test_location_imputed_flag(
    config: GeospatialConfig,
    tiger_db: duckdb.DuckDBPyConnection,
    matched_addresses_df: pd.DataFrame,
) -> None:
    result = geocode_addresses(matched_addresses_df, config)

    nad_row = result[result["match_method"] == "nad_match"].iloc[0]
    non_standard_row = result[result["match_method"] == "non_standard"].iloc[0]

    assert not bool(nad_row["location_imputed"])
    assert bool(non_standard_row["location_imputed"])


def test_census_tract_is_prefix_of_block_group(
    config: GeospatialConfig,
    tiger_db: duckdb.DuckDBPyConnection,
    matched_addresses_df: pd.DataFrame,
) -> None:
    result = geocode_addresses(matched_addresses_df, config)
    for _, row in result.iterrows():
        if pd.notna(row["block_group_fips"]) and pd.notna(row["census_tract_fips"]):
            assert row["block_group_fips"].startswith(row["census_tract_fips"])


def test_point_outside_all_polygons_gives_null_fips(
    config: GeospatialConfig, tiger_db: duckdb.DuckDBPyConnection
) -> None:
    outside = pd.DataFrame([{
        "Street_Address": "Far Away",
        "matched_latitude": 10.0,   # outside both synthetic polygons
        "matched_longitude": 10.0,
        "match_method": "nad_match",
    }])
    result = geocode_addresses(outside, config)
    assert pd.isna(result.iloc[0]["block_group_fips"])


def test_missing_required_columns_raises(
    config: GeospatialConfig, tiger_db: duckdb.DuckDBPyConnection
) -> None:
    bad_df = pd.DataFrame([{"Street_Address": "123 Main St"}])
    with pytest.raises(ValueError, match="matched_latitude"):
        geocode_addresses(bad_df, config)


def test_original_columns_preserved(
    config: GeospatialConfig,
    tiger_db: duckdb.DuckDBPyConnection,
    matched_addresses_df: pd.DataFrame,
) -> None:
    result = geocode_addresses(matched_addresses_df, config)
    assert "Street_Address" in result.columns
    assert len(result) == len(matched_addresses_df)


def test_output_schema_complete(
    config: GeospatialConfig,
    tiger_db: duckdb.DuckDBPyConnection,
    matched_addresses_df: pd.DataFrame,
) -> None:
    result = geocode_addresses(matched_addresses_df, config)
    expected = {"block_group_fips", "census_tract_fips", "tiger_vintage", "location_imputed"}
    assert expected.issubset(set(result.columns))


@patch("analytics_toolbox.geospatial.address_geocoder._download_shapefile")
@patch("analytics_toolbox.geospatial.address_geocoder._download_block_groups")
def test_ingest_tiger_idempotent(
    mock_dl_bg: MagicMock,
    mock_dl_zcta: MagicMock,
    config: GeospatialConfig,
    synthetic_block_groups: gpd.GeoDataFrame,
    synthetic_zcta: gpd.GeoDataFrame,
) -> None:
    mock_dl_bg.return_value = synthetic_block_groups
    mock_dl_zcta.return_value = synthetic_zcta

    ingest_tiger(config)
    ingest_tiger(config)  # should skip — tables already exist

    conn = duckdb.connect(config.storage.connection)
    count = conn.execute("SELECT COUNT(*) FROM tiger_block_groups_2024").fetchone()[0]
    conn.close()
    assert count == len(synthetic_block_groups)
    assert mock_dl_bg.call_count == 1  # only called during first ingest


@patch("analytics_toolbox.geospatial.address_geocoder._download_shapefile")
@patch("analytics_toolbox.geospatial.address_geocoder._download_block_groups")
def test_ingest_tiger_creates_zcta_centroids(
    mock_dl_bg: MagicMock,
    mock_dl_zcta: MagicMock,
    config: GeospatialConfig,
    synthetic_block_groups: gpd.GeoDataFrame,
    synthetic_zcta: gpd.GeoDataFrame,
) -> None:
    mock_dl_bg.return_value = synthetic_block_groups
    mock_dl_zcta.return_value = synthetic_zcta

    ingest_tiger(config)

    conn = duckdb.connect(config.storage.connection)
    rows = conn.execute("SELECT * FROM tiger_zcta_2024").fetchall()
    conn.close()
    assert len(rows) == len(synthetic_zcta)
    for row in rows:
        zcta5, lat, lon, geom_wkt = row
        assert lat is not None
        assert lon is not None
        assert geom_wkt is not None


def test_tiger_table_missing_raises_clear_error(
    config: GeospatialConfig, matched_addresses_df: pd.DataFrame
) -> None:
    """geocode_addresses must raise ValueError (not a raw DuckDB error) when
    ingest-tiger has not been run and the block group table doesn't exist yet.
    This is the most common day-1 mistake and the error message should say so."""
    # No tiger_db fixture — empty DuckDB, no TIGER tables loaded
    with pytest.raises(ValueError, match="ingest-tiger"):
        geocode_addresses(matched_addresses_df, config)


def test_null_lat_lon_rows_get_null_fips(
    config: GeospatialConfig, tiger_db: duckdb.DuckDBPyConnection
) -> None:
    """Rows where matched_latitude/longitude are None should survive geocoding
    with None block_group_fips rather than crashing."""
    df = pd.DataFrame([
        {
            "Street_Address": "No Coords",
            "matched_latitude": None,
            "matched_longitude": None,
            "match_method": "non_standard",
        },
        {
            "Street_Address": "Has Coords",
            "matched_latitude": 45.5,
            "matched_longitude": -122.5,
            "match_method": "nad_match",
        },
    ])
    result = geocode_addresses(df, config)
    assert len(result) == 2
    assert pd.isna(result.iloc[0]["block_group_fips"])
    assert pd.notna(result.iloc[1]["block_group_fips"])


def test_boundary_point_gets_fips(
    config: GeospatialConfig, tiger_db: duckdb.DuckDBPyConnection
) -> None:
    """A point exactly on the shared boundary between two block groups must
    still receive a block group FIPS (not be silently dropped). This tests
    the 'intersects' predicate rather than 'within'."""
    boundary = pd.DataFrame([{
        "Street_Address": "Boundary Point",
        # Exactly on the shared edge at lon=-122.0, lat=45.5 (between the two synthetic polygons)
        "matched_latitude": 45.5,
        "matched_longitude": -122.0,
        "match_method": "nad_match",
    }])
    result = geocode_addresses(boundary, config)
    # With intersects + dedup, the point should land in exactly one polygon
    assert len(result) == 1
    assert pd.notna(result.iloc[0]["block_group_fips"]), "Boundary point got null FIPS"


@patch("analytics_toolbox.geospatial.address_geocoder._download_shapefile")
@patch("analytics_toolbox.geospatial.address_geocoder._download_block_groups")
def test_interrupted_run_recovery(
    mock_dl_bg: MagicMock,
    mock_dl_zcta: MagicMock,
    config: GeospatialConfig,
    synthetic_block_groups: gpd.GeoDataFrame,
    synthetic_zcta: gpd.GeoDataFrame,
) -> None:
    """force_refresh=True must succeed even when tables already exist."""
    refresh_config = GeospatialConfig(
        nad=config.nad,
        tiger=TigerConfig(vintage=2024, force_refresh=True),
        matching=config.matching,
        storage=config.storage,
    )

    mock_dl_bg.return_value = synthetic_block_groups
    mock_dl_zcta.return_value = synthetic_zcta

    ingest_tiger(config)          # First run — creates tables
    ingest_tiger(refresh_config)  # Second run with force_refresh — must not crash

    conn = duckdb.connect(config.storage.connection)
    count = conn.execute("SELECT COUNT(*) FROM tiger_block_groups_2024").fetchone()[0]
    conn.close()
    assert count == len(synthetic_block_groups)


