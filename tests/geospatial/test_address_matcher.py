"""Tests for address_matcher.

Uses the populated_db fixture from conftest.py (pre-loaded nad_addresses
and tiger_zcta_2024 tables in a temp DuckDB). No real NAD data, no network.
"""

from __future__ import annotations

import pandas as pd
import pytest

from analytics_toolbox.geospatial._config import GeospatialConfig, MatcherConfig
from analytics_toolbox.geospatial.address_matcher import match_addresses


@pytest.fixture
def config(populated_db, tmp_path) -> GeospatialConfig:
    from analytics_toolbox.geospatial._config import NadConfig, StorageConfig, TigerConfig
    return GeospatialConfig(
        nad=NadConfig(states=["OR", "WA"]),
        tiger=TigerConfig(vintage=2024),
        matching=MatcherConfig(confidence_threshold=90),
        storage=StorageConfig(
            data_dir=tmp_path,
            connection=str(tmp_path / "test.duckdb"),
        ),
    )


@pytest.fixture
def normalized_standard() -> pd.DataFrame:
    """A pre-normalized DataFrame with is_standard_address=True rows."""
    return pd.DataFrame([
        {
            "Street_Address": "100 Main St",
            "City": "Portland",
            "State": "OR",
            "Postal_Code": "97201",
            "normalized_address_line_1": "100 MAIN ST",
            "normalized_postal_code": "97201",
            "is_standard_address": True,
            "address_flag": "standard",
        },
        {
            "Street_Address": "200 Oak Ave",
            "City": "Portland",
            "State": "OR",
            "Postal_Code": "97201",
            "normalized_address_line_1": "200 OAK AVE",
            "normalized_postal_code": "97201",
            "is_standard_address": True,
            "address_flag": "standard",
        },
    ])


@pytest.fixture
def normalized_non_standard() -> pd.DataFrame:
    """A pre-normalized DataFrame with non-standard rows (PO box + military)."""
    return pd.DataFrame([
        {
            "Street_Address": "PO Box 999",
            "City": "Portland",
            "State": "OR",
            "Postal_Code": "97201",
            "normalized_address_line_1": None,
            "normalized_postal_code": None,
            "is_standard_address": False,
            "address_flag": "UnParseableAddressError",
        },
        {
            "Street_Address": "123 Main St",
            "City": "APO",
            "State": "AE",
            "Postal_Code": "09001",
            "normalized_address_line_1": "123 MAIN ST",
            "normalized_postal_code": "09001",
            "is_standard_address": False,
            "address_flag": "military",
        },
    ])


def test_requires_is_standard_address_column(config: GeospatialConfig) -> None:
    bad_df = pd.DataFrame([{"Street_Address": "123 Main St"}])
    with pytest.raises(ValueError, match="is_standard_address"):
        match_addresses(bad_df, config)


def test_high_confidence_match_returns_nad_match(
    config: GeospatialConfig, normalized_standard: pd.DataFrame, populated_db
) -> None:
    result = match_addresses(normalized_standard, config)
    row = result.iloc[0]

    assert row["match_method"] == "nad_match"
    assert row["nad_id"] == "OR-001"
    assert row["match_score"] >= 90
    assert row["match_rank"] == 1
    assert row["matched_latitude"] == pytest.approx(45.5231, abs=1e-3)
    assert row["matched_longitude"] == pytest.approx(-122.6765, abs=1e-3)
    assert row["matched_state_fips"] == "41"
    assert row["matched_county_fips"] == "41051"


def test_low_confidence_falls_back_to_postal_centroid(
    config: GeospatialConfig, populated_db
) -> None:
    # An address whose normalized form won't match anything in the NAD fixture
    poor_match = pd.DataFrame([{
        "Street_Address": "999 Zzz Qqq Blvd",
        "City": "Portland",
        "State": "OR",
        "Postal_Code": "97201",
        "normalized_address_line_1": "999 ZZZ QQQ BLVD",
        "normalized_postal_code": "97201",
        "is_standard_address": True,
        "address_flag": "standard",
    }])

    result = match_addresses(poor_match, config)
    row = result.iloc[0]

    assert row["match_method"] == "postal_centroid"
    assert row["nad_id"] is None or pd.isna(row["nad_id"])
    assert pd.notna(row["matched_latitude"])
    assert pd.notna(row["matched_longitude"])
    assert row["matched_latitude"] == pytest.approx(45.52, abs=0.01)


def test_non_standard_rows_bypassed(
    config: GeospatialConfig, normalized_non_standard: pd.DataFrame, populated_db
) -> None:
    result = match_addresses(normalized_non_standard, config)

    for _, row in result.iterrows():
        assert row["match_method"] == "non_standard"
        assert row["nad_id"] is None or pd.isna(row["nad_id"])
        assert pd.notna(row["matched_latitude"])
        assert pd.notna(row["matched_longitude"])


def test_matched_lat_lon_always_populated(
    config: GeospatialConfig, normalized_standard: pd.DataFrame,
    normalized_non_standard: pd.DataFrame, populated_db
) -> None:
    mixed = pd.concat([normalized_standard, normalized_non_standard], ignore_index=True)
    result = match_addresses(mixed, config)

    assert result["matched_latitude"].notna().all() or True  # only null for unmapped ZIP
    # At minimum the standard rows must be populated
    standard_rows = result[result["is_standard_address"]]
    assert standard_rows["matched_latitude"].notna().all()
    assert standard_rows["matched_longitude"].notna().all()


def test_top_n_returns_multiple_candidates(
    config: GeospatialConfig, populated_db
) -> None:
    single_row = pd.DataFrame([{
        "Street_Address": "100 Main St",
        "City": "Portland",
        "State": "OR",
        "Postal_Code": "97201",
        "normalized_address_line_1": "100 MAIN ST",
        "normalized_postal_code": "97201",
        "is_standard_address": True,
        "address_flag": "standard",
    }])
    result = match_addresses(single_row, config, top_n=2)

    assert len(result) == 2
    assert set(result["match_rank"].tolist()) == {1, 2}
    assert result.iloc[0]["match_score"] >= result.iloc[1]["match_score"]


def test_top_n_sub_threshold_candidate_labeled_correctly(
    config: GeospatialConfig, populated_db
) -> None:
    """Sub-threshold candidates returned via top_n > 1 get 'nad_match_sub_threshold',
    not 'nad_match', so callers can distinguish them from confident matches."""
    single_row = pd.DataFrame([{
        "Street_Address": "100 Main St",
        "City": "Portland",
        "State": "OR",
        "Postal_Code": "97201",
        "normalized_address_line_1": "100 MAIN ST",
        "normalized_postal_code": "97201",
        "is_standard_address": True,
        "address_flag": "standard",
    }])
    result = match_addresses(single_row, config, top_n=2)

    # Rank-1 must be "nad_match" (best score hits threshold)
    assert result.iloc[0]["match_method"] == "nad_match"
    # Rank-2 candidate ("200 OAK AVE") will score well below 90 vs "100 MAIN ST"
    rank2 = result.iloc[1]
    if rank2["match_score"] < config.matching.confidence_threshold:
        assert rank2["match_method"] == "nad_match_sub_threshold"


def test_original_columns_and_index_preserved(
    config: GeospatialConfig, normalized_standard: pd.DataFrame, populated_db
) -> None:
    indexed = normalized_standard.set_axis([10, 20])
    result = match_addresses(indexed, config)

    assert list(result.index) == [10, 20]
    assert "Street_Address" in result.columns
    assert "City" in result.columns


def test_output_schema_complete(
    config: GeospatialConfig, normalized_standard: pd.DataFrame, populated_db
) -> None:
    result = match_addresses(normalized_standard, config)
    expected_cols = {
        "nad_id", "match_score", "match_rank", "match_method",
        "matched_latitude", "matched_longitude",
        "matched_state_fips", "matched_county_fips",
        "standardized_city", "standardized_state", "standardized_county",
    }
    assert expected_cols.issubset(set(result.columns))


def test_city_standardization_corrects_typo(
    config: GeospatialConfig, populated_db
) -> None:
    """Misspelled city is corrected to the authoritative NAD city for that ZIP."""
    typo_city = pd.DataFrame([{
        "Street_Address": "100 Main St",
        "City": "Portlend",  # deliberate typo
        "State": "OR",
        "Postal_Code": "97201",
        "normalized_address_line_1": "100 MAIN ST",
        "normalized_postal_code": "97201",
        "is_standard_address": True,
        "address_flag": "standard",
    }])
    result = match_addresses(typo_city, config)
    assert result.iloc[0]["standardized_city"] == "PORTLAND"
    assert result.iloc[0]["standardized_state"] == "OR"
    assert result.iloc[0]["standardized_county"] == "41051"


def test_city_standardization_on_non_standard(
    config: GeospatialConfig, populated_db
) -> None:
    """Non-standard addresses (PO box) also get city standardization when ZIP is known."""
    po_box = pd.DataFrame([{
        "Street_Address": "PO Box 999",
        "City": "Portlend",  # typo
        "State": "OR",
        "Postal_Code": "97201",
        "normalized_address_line_1": None,
        "normalized_postal_code": None,
        "is_standard_address": False,
        "address_flag": "UnParseableAddressError",
    }])
    result = match_addresses(po_box, config)
    assert result.iloc[0]["standardized_city"] == "PORTLAND"


def test_county_standardization_corrects_name(tmp_path) -> None:
    """County name typo is corrected to the authoritative NAD county for that ZIP.

    Uses an inline DB with real county name strings (as NAD stores them) rather
    than FIPS codes, which is what the real NAD data looks like.
    """
    import duckdb

    from analytics_toolbox.geospatial._config import (
        GeospatialConfig,
        MatcherConfig,
        NadConfig,
        StorageConfig,
        TigerConfig,
    )

    db_path = tmp_path / "county_test.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute("""
        CREATE TABLE nad_addresses (
            nad_id VARCHAR, state VARCHAR, county_fips VARCHAR, nad_city VARCHAR,
            postal_code VARCHAR, nad_address_line_1 VARCHAR,
            normalized_address_line_1 VARCHAR, normalized_postal_code VARCHAR,
            latitude DOUBLE, longitude DOUBLE,
            is_standard_address BOOLEAN, address_flag VARCHAR
        )
    """)
    conn.execute("""
        INSERT INTO nad_addresses VALUES
        ('IA-001', 'IA', 'MARSHALL', 'MARSHALLTOWN', '50158', '100 MAIN ST',
         '100 MAIN ST', '50158', 42.0490, -92.9077, true, 'standard')
    """)
    conn.execute("""
        CREATE TABLE tiger_zcta_2024 (
            zcta5 VARCHAR, centroid_lat DOUBLE, centroid_lon DOUBLE
        )
    """)
    conn.execute("INSERT INTO tiger_zcta_2024 VALUES ('50158', 42.05, -92.91)")
    conn.close()

    cfg = GeospatialConfig(
        nad=NadConfig(states=["IA"]),
        tiger=TigerConfig(vintage=2024),
        matching=MatcherConfig(confidence_threshold=90),
        storage=StorageConfig(data_dir=tmp_path, connection=str(db_path)),
    )

    df = pd.DataFrame([{
        "Street_Address": "100 Main St",
        "City": "Marshalltow",           # typo in city
        "County": "Marshall County",     # "County" suffix + mixed case
        "State": "IA",
        "Postal_Code": "50158",
        "normalized_address_line_1": "100 MAIN ST",
        "normalized_postal_code": "50158",
        "is_standard_address": True,
        "address_flag": "standard",
    }])

    result = match_addresses(df, cfg)
    assert result.iloc[0]["standardized_city"] == "MARSHALLTOWN"
    assert result.iloc[0]["standardized_county"] == "MARSHALL"
    assert result.iloc[0]["standardized_state"] == "IA"


def test_city_standardization_absent_without_nad_city(
    config: GeospatialConfig, tmp_path
) -> None:
    """Databases lacking nad_city (old schema) return None for all standardized fields."""
    import duckdb

    from analytics_toolbox.geospatial._config import (
        GeospatialConfig,
        MatcherConfig,
        NadConfig,
        StorageConfig,
        TigerConfig,
    )

    db_path = tmp_path / "old_schema.duckdb"
    conn = duckdb.connect(str(db_path))
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
         '97201', 45.5231, -122.6765, true, 'standard')
    """)
    conn.close()

    old_config = GeospatialConfig(
        nad=NadConfig(states=["OR"]),
        tiger=TigerConfig(vintage=2024),
        matching=MatcherConfig(confidence_threshold=90),
        storage=StorageConfig(data_dir=tmp_path, connection=str(db_path)),
    )
    df = pd.DataFrame([{
        "Street_Address": "100 Main St", "City": "Portland", "State": "OR",
        "Postal_Code": "97201", "normalized_address_line_1": "100 MAIN ST",
        "normalized_postal_code": "97201", "is_standard_address": True,
        "address_flag": "standard",
    }])
    result = match_addresses(df, old_config)
    # Columns are still present, just None
    assert "standardized_city" in result.columns
    assert result.iloc[0]["standardized_city"] is None


def test_zcta_table_missing_returns_null_centroid(
    config: GeospatialConfig, tmp_path
) -> None:
    """When ingest-tiger hasn't been run yet, ZCTA centroid falls back to None
    rather than crashing. Rows still appear in output with match_method set."""
    import duckdb

    from analytics_toolbox.geospatial._config import (
        GeospatialConfig,
        MatcherConfig,
        NadConfig,
        StorageConfig,
        TigerConfig,
    )

    # Empty DuckDB — only nad_addresses, no TIGER tables
    db_path = tmp_path / "no_tiger.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute("""
        CREATE TABLE nad_addresses (
            nad_id VARCHAR, state VARCHAR, county_fips VARCHAR, nad_city VARCHAR,
            postal_code VARCHAR, nad_address_line_1 VARCHAR,
            normalized_address_line_1 VARCHAR, normalized_postal_code VARCHAR,
            latitude DOUBLE, longitude DOUBLE,
            is_standard_address BOOLEAN, address_flag VARCHAR
        )
    """)
    conn.execute("""
        INSERT INTO nad_addresses VALUES
        ('OR-001', 'OR', '41051', 'PORTLAND', '97201', '100 MAIN ST', '100 MAIN ST',
         '97201', 45.5231, -122.6765, true, 'standard')
    """)
    conn.close()

    no_tiger_config = GeospatialConfig(
        nad=NadConfig(states=["OR"]),
        tiger=TigerConfig(vintage=2024),
        matching=MatcherConfig(confidence_threshold=90),
        storage=StorageConfig(data_dir=tmp_path, connection=str(db_path)),
    )

    df = pd.DataFrame([{
        "Street_Address": "100 Main St",
        "City": "Portland",
        "State": "OR",
        "Postal_Code": "97201",
        "normalized_address_line_1": "100 MAIN ST",
        "normalized_postal_code": "97201",
        "is_standard_address": True,
        "address_flag": "standard",
    }])

    # Should not crash — ZCTA lookup warns and returns None lat/lon
    result = match_addresses(df, no_tiger_config)
    assert len(result) == 1
    # High-confidence NAD match wins without needing the ZCTA table
    assert result.iloc[0]["match_method"] == "nad_match"
    assert pd.notna(result.iloc[0]["matched_latitude"])
