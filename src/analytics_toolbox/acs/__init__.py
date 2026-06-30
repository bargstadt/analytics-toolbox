"""acs: pull U.S. Census ACS 5-year estimates into a local DuckDB ``raw`` schema.

SQL-light, offline-by-design ingest: the only network calls are to the public
Census Bureau API. Data lands in the ``raw`` schema (one table per
variable+geography), keyed by block group / tract / county FIPS — the same FIPS
``geospatial`` geocodes addresses to, so ACS demographics join straight onto
geocoded records.

Quick start::

    from analytics_toolbox._config import load_config
    from analytics_toolbox.acs import ingest_acs

    cfg = load_config("config.yaml")
    manifest = ingest_acs(cfg.acs, cfg.storage, api_key="...")
    print(manifest.tables)

The package only ingests into ``raw`` — transformation is left to whatever
tooling you prefer downstream (SQL, pandas, R).
"""

from __future__ import annotations

from analytics_toolbox.acs._census_api import raw_table_name
from analytics_toolbox.acs._config import AcsConfig, ReportConfig, VariableConfig
from analytics_toolbox.acs._manifest import (
    ColumnInfo,
    RunManifest,
    TableManifest,
    build_manifest,
)
from analytics_toolbox.acs._settings import resolve_api_key
from analytics_toolbox.acs.ingest import RAW_SCHEMA, ingest_acs

__all__ = [
    "ingest_acs",
    "RAW_SCHEMA",
    "raw_table_name",
    "resolve_api_key",
    "build_manifest",
    "AcsConfig",
    "ReportConfig",
    "VariableConfig",
    "RunManifest",
    "TableManifest",
    "ColumnInfo",
]
