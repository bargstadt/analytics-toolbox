"""Tests for the ACS run manifest built by introspecting DuckDB."""

from __future__ import annotations

import json

from analytics_toolbox.acs._config import AcsConfig
from analytics_toolbox.acs._manifest import build_manifest
from analytics_toolbox.utils import in_memory_con


def _config() -> AcsConfig:
    return AcsConfig.model_validate(
        {
            "states": ["IA"],
            "reports": [
                {
                    "name": "r",
                    "variables": [
                        {"code": "B01001_001E", "geographies": ["tract"]},
                        # This combo is intentionally never loaded -> omitted from manifest.
                        {"code": "S1701_C03_001E", "geographies": ["county"]},
                    ],
                }
            ],
        }
    )


def _seed_table(con):
    con.execute("CREATE SCHEMA IF NOT EXISTS raw")
    con.execute(
        "CREATE TABLE raw.acs_b01001_001e_tract "
        "(geo_id VARCHAR, name VARCHAR, year INTEGER, b01001_001e INTEGER, "
        "b01001_001e_is_suppressed BOOLEAN)"
    )
    con.execute(
        "INSERT INTO raw.acs_b01001_001e_tract VALUES "
        "('g1','n1',2020,100,false), ('g2','n2',2021,NULL,true), ('g3','n3',2021,200,false)"
    )


def test_build_manifest_introspects_loaded_table():
    con = in_memory_con()
    _seed_table(con)
    manifest = build_manifest(_config(), con, schema="raw")

    assert manifest.schema == "raw"
    assert manifest.states == ["IA"]
    # Only the loaded combo appears; the absent one is omitted.
    assert len(manifest.tables) == 1

    t = manifest.tables[0]
    assert t.table == "acs_b01001_001e_tract"
    assert t.variable_code == "B01001_001E"
    assert t.geography_level == "tract"
    assert t.row_count == 3
    assert t.year_min == 2020
    assert t.year_max == 2021
    assert t.suppressed_count == 1
    assert {c.name for c in t.columns} >= {"geo_id", "year", "b01001_001e_is_suppressed"}


def test_manifest_write_roundtrip(tmp_path):
    con = in_memory_con()
    _seed_table(con)
    manifest = build_manifest(_config(), con, schema="raw")
    path = manifest.write(tmp_path / "acs.manifest.json")

    data = json.loads(path.read_text())
    assert data["schema"] == "raw"
    assert data["tables"][0]["row_count"] == 3
    assert data["tables"][0]["suppressed_count"] == 1
