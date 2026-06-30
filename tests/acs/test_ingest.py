"""End-to-end test of ingest_acs against a mocked Census API."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import httpx

from analytics_toolbox.acs._config import AcsConfig
from analytics_toolbox.acs.ingest import ingest_acs
from analytics_toolbox.geospatial._config import StorageConfig


def _storage(tmp_path: Path) -> StorageConfig:
    return StorageConfig(data_dir=tmp_path, connection=str(tmp_path / "atb.duckdb"))


def _config() -> AcsConfig:
    return AcsConfig.model_validate(
        {
            "states": ["IA"],
            "reports": [
                {
                    "name": "poverty",
                    "variables": [
                        {"code": "B01001_001E", "geographies": ["block_group"]},
                        {"code": "S1701_C03_001E", "geographies": ["county"]},
                    ],
                }
            ],
        }
    )


def test_ingest_loads_raw_tables_and_manifest(
    tmp_path: Path, census_client: httpx.Client, valid_years: list[int]
):
    storage = _storage(tmp_path)
    manifest = ingest_acs(_config(), storage, api_key="test-key", client=census_client)

    # Two (variable, geography) combos -> two raw tables.
    assert len(manifest.tables) == 2
    assert {t.table for t in manifest.tables} == {
        "acs_b01001_001e_block_group",
        "acs_s1701_c03_001e_county",
    }

    # 2 rows per (state, year) x len(valid_years) years, 1 state.
    expected_rows = 2 * len(valid_years)
    by_table = {t.table: t for t in manifest.tables}
    assert by_table["acs_b01001_001e_block_group"].row_count == expected_rows
    assert by_table["acs_b01001_001e_block_group"].suppressed_count == len(valid_years)
    assert by_table["acs_b01001_001e_block_group"].year_min == valid_years[0]
    assert by_table["acs_b01001_001e_block_group"].year_max == valid_years[-1]

    # Data actually persisted to the DuckDB file under the raw schema.
    con = duckdb.connect(str(tmp_path / "atb.duckdb"))
    try:
        n = con.execute("SELECT count(*) FROM raw.acs_b01001_001e_block_group").fetchone()[0]
        assert n == expected_rows
    finally:
        con.close()

    # Manifest written beside the data.
    manifest_path = tmp_path / "acs.manifest.json"
    assert manifest_path.exists()
    data = json.loads(manifest_path.read_text())
    assert len(data["tables"]) == 2


def test_ingest_can_skip_manifest(tmp_path: Path, census_client: httpx.Client):
    storage = _storage(tmp_path)
    ingest_acs(_config(), storage, api_key="test-key", write_manifest=False, client=census_client)
    assert not (tmp_path / "acs.manifest.json").exists()
