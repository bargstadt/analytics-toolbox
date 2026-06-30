"""Shared fixtures for geospatial tests.

All synthetic data lives here. No real network calls, no real NAD/TIGER files.
The synthetic NAD CSV matches the column names in NAD_COLUMN_MAP so ingest
tests exercise the real rename/normalize path.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import duckdb
import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Polygon

# ---------------------------------------------------------------------------
# Synthetic NAD CSV
# ---------------------------------------------------------------------------

# Column names mirror the real NAD Release 22 national text file.
# OID_ is the integer sequence identifier (not Join_ID, which no longer exists).
# Includes a pre-directional row (OID_=9, St_PreDir=NW) to exercise street-line
# reconstruction, a PO box row, and a military address row.
SYNTHETIC_NAD_CSV = textwrap.dedent("""\
    OID_,Add_Number,St_PreDir,St_Name,St_PosTyp,St_PosDir,Post_City,State,Zip_Code,County,Latitude,Longitude,UUID
    1,100,,MAIN,ST,,PORTLAND,OR,97201,MULTNOMAH,45.5231,-122.6765,uuid-or-1
    2,200,,OAK,AVE,,PORTLAND,OR,97201,MULTNOMAH,45.5240,-122.6800,uuid-or-2
    3,300,,PINE,RD,,EUGENE,OR,97401,LANE,44.0521,-123.0868,uuid-or-3
    4,400,,ELM,DR,,SEATTLE,WA,98101,KING,47.6062,-122.3321,uuid-wa-4
    5,500,,CEDAR,BLVD,,SEATTLE,WA,98101,KING,47.6070,-122.3340,uuid-wa-5
    6,600,,MAPLE,LN,,BELLEVUE,WA,98004,KING,47.6101,-122.2015,uuid-wa-6
    7,700,,BIRCH,CT,,DALLAS,TX,75201,DALLAS,32.7767,-96.7970,uuid-tx-7
    8,800,,SPRUCE,PL,,DALLAS,TX,75201,DALLAS,32.7780,-96.7990,uuid-tx-8
    9,123,NW,BURNSIDE,ST,,PORTLAND,OR,97201,MULTNOMAH,45.5244,-122.6820,uuid-or-nw
    10,,,PO BOX 999,,,PORTLAND,OR,97201,MULTNOMAH,,,uuid-or-po
    11,,,,,,,AE,09001,,,uuid-ae-1
""")


@pytest.fixture
def synthetic_nad_csv() -> str:
    """Raw CSV text matching the NAD column layout."""
    return SYNTHETIC_NAD_CSV


# ---------------------------------------------------------------------------
# Config fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def config_yaml_path(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.duckdb"
    content = textwrap.dedent(f"""\
        storage:
          data_dir: {tmp_path}/
          connection: {db_path}

        geospatial:
          nad:
            states: [OR, WA]
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


# ---------------------------------------------------------------------------
# DuckDB fixture pre-loaded with NAD + ZCTA centroid data for matcher tests
# ---------------------------------------------------------------------------

@pytest.fixture
def populated_db(tmp_path: Path) -> duckdb.DuckDBPyConnection:
    """Temp DuckDB with nad_addresses and tiger_zcta_2024 pre-loaded."""
    conn = duckdb.connect(str(tmp_path / "test.duckdb"))

    conn.execute("""
        CREATE TABLE nad_addresses (
            nad_id VARCHAR,
            state VARCHAR,
            county_fips VARCHAR,
            nad_city VARCHAR,
            postal_code VARCHAR,
            nad_address_line_1 VARCHAR,
            normalized_address_line_1 VARCHAR,
            normalized_postal_code VARCHAR,
            latitude DOUBLE,
            longitude DOUBLE,
            is_standard_address BOOLEAN,
            address_flag VARCHAR
        )
    """)
    conn.execute("""
        INSERT INTO nad_addresses VALUES
        ('OR-001', 'OR', '41051', 'PORTLAND', '97201', '100 MAIN ST', '100 MAIN ST',
         '97201', 45.5231, -122.6765, true, 'standard'),
        ('OR-002', 'OR', '41051', 'PORTLAND', '97201', '200 OAK AVE', '200 OAK AVE',
         '97201', 45.5240, -122.6800, true, 'standard'),
        ('OR-003', 'OR', '41039', 'EUGENE',   '97401', '300 PINE RD',  '300 PINE RD',
         '97401', 44.0521, -123.0868, true, 'standard'),
        ('WA-001', 'WA', '53033', 'SEATTLE',  '98101', '400 ELM DR',  '400 ELM DR',
         '98101', 47.6062, -122.3321, true, 'standard')
    """)

    conn.execute("""
        CREATE TABLE tiger_zcta_2024 (
            zcta5 VARCHAR,
            centroid_lat DOUBLE,
            centroid_lon DOUBLE
        )
    """)
    conn.execute("""
        INSERT INTO tiger_zcta_2024 VALUES
        ('97201', 45.5200, -122.6750),
        ('97401', 44.0500, -123.0850),
        ('98101', 47.6050, -122.3300),
        ('09001', 0.0, 0.0)
    """)

    return conn


# ---------------------------------------------------------------------------
# Synthetic GeoDataFrames for geocoder tests
# ---------------------------------------------------------------------------

@pytest.fixture
def synthetic_block_groups() -> gpd.GeoDataFrame:
    """Two non-overlapping 1°×1° block group polygons with known FIPS codes."""
    return gpd.GeoDataFrame(
        {
            "block_group_fips": ["010010201001", "010010201002"],
            "geometry": [
                Polygon([(-123.0, 45.0), (-122.0, 45.0), (-122.0, 46.0), (-123.0, 46.0)]),
                Polygon([(-122.0, 45.0), (-121.0, 45.0), (-121.0, 46.0), (-122.0, 46.0)]),
            ],
        },
        crs="EPSG:4326",
    )


@pytest.fixture
def synthetic_zcta() -> gpd.GeoDataFrame:
    """Two ZCTA polygons matching the block group coverage above."""
    return gpd.GeoDataFrame(
        {
            "zcta5": ["97201", "97202"],
            "geometry": [
                Polygon([(-123.0, 45.0), (-122.0, 45.0), (-122.0, 46.0), (-123.0, 46.0)]),
                Polygon([(-122.0, 45.0), (-121.0, 45.0), (-121.0, 46.0), (-122.0, 46.0)]),
            ],
        },
        crs="EPSG:4326",
    )


@pytest.fixture
def matched_addresses_df() -> pd.DataFrame:
    """Minimal DataFrame that has already been through address_matcher."""
    return pd.DataFrame(
        [
            {
                "Street_Address": "100 Main St",
                "City": "Portland",
                "State": "OR",
                "Postal_Code": "97201",
                "matched_latitude": 45.5,
                "matched_longitude": -122.5,
                "match_method": "nad_match",
                "nad_id": "OR-001",
                "match_score": 95.0,
                "match_rank": 1,
                "matched_state_fips": "41",
                "matched_county_fips": "41051",
            },
            {
                "Street_Address": "PO Box 999",
                "City": "Portland",
                "State": "OR",
                "Postal_Code": "97201",
                "matched_latitude": 45.52,
                "matched_longitude": -122.675,
                "match_method": "non_standard",
                "nad_id": None,
                "match_score": None,
                "match_rank": 1,
                "matched_state_fips": None,
                "matched_county_fips": None,
            },
        ]
    )
