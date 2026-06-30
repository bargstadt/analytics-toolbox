"""Ingest ACS data into a local DuckDB ``raw`` schema.

The single entry point most users need. It fetches the requested ACS variables
from the Census API, lands them in the ``raw`` schema (one table per
variable+geography) via the toolbox's sanctioned, audited write path, and returns
a manifest describing what was loaded.

ACS 5-year estimates are public aggregate data, so the on-disk write is certified
PHI-free — the rare case where ``certify_no_phi=True`` is honestly assertable.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from analytics_toolbox._storage import StorageConfig
from analytics_toolbox.acs._census_api import fetch_variable_dataframe, raw_table_name
from analytics_toolbox.acs._config import AcsConfig
from analytics_toolbox.acs._manifest import RunManifest, build_manifest
from analytics_toolbox.acs._settings import resolve_api_key
from analytics_toolbox.acs._variable_history import resolve_valid_years
from analytics_toolbox.utils import on_disk_con, save_table

logger = logging.getLogger(__name__)

# Data always lands in the ``raw`` schema — the handoff contract for consumers.
RAW_SCHEMA = "raw"


def ingest_acs(
    acs_config: AcsConfig,
    storage: StorageConfig,
    *,
    api_key: str | None = None,
    write_manifest: bool = True,
    client: httpx.Client | None = None,
) -> RunManifest:
    """Ingest the ACS variables described by ``acs_config`` into DuckDB.

    Every run is a full replace: each ``raw`` table is rebuilt to a complete,
    self-consistent snapshot.

    Args:
        acs_config: The ``acs:`` config slice (states + reports).
        storage: The shared storage config (DuckDB connection + data directory).
        api_key: Census API key. Falls back to the ``CENSUS_API_KEY`` env var
            (or a ``.env`` file).
        write_manifest: When True, write ``acs.manifest.json`` into the data dir.
        client: Optional httpx client (a test/customisation seam). When omitted, a
            client is created and closed internally; when provided, the caller owns
            its lifecycle.

    Returns:
        A :class:`RunManifest` describing the tables that were loaded.

    Raises:
        ValueError: If no API key is available, or the storage connection is a
            MotherDuck (``md:``) string (cloud egress is not wired up here).
    """
    key = resolve_api_key(api_key)
    cache_dir = storage.data_dir / "acs_metadata_cache"

    # Ensure the data dir and the DuckDB file's parent exist before connecting.
    storage.data_dir.mkdir(parents=True, exist_ok=True)
    Path(storage.connection).parent.mkdir(parents=True, exist_ok=True)

    logger.info(
        "acs: ingest start — states=%s reports=%s -> %s",
        acs_config.states,
        [r.name for r in acs_config.reports],
        storage.connection,
    )

    owns_client = client is None
    client = client or httpx.Client()
    con = on_disk_con(storage.connection)
    try:
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {RAW_SCHEMA}")
        for code, geo in acs_config.variable_geographies():
            years = resolve_valid_years(code, key, client, cache_dir)
            if not years:
                logger.warning("acs: no valid years for %s; skipping", code)
                continue

            df = fetch_variable_dataframe(code, geo, acs_config.states, years, key, client)
            if df.empty:
                logger.warning("acs: no rows for %s at %s; skipping", code, geo)
                continue

            table = raw_table_name(code, geo)
            # ACS is public aggregate data: certify PHI-free for the disk write.
            save_table(
                df,
                f"{RAW_SCHEMA}.{table}",
                con=con,
                certify_no_phi=True,
                if_exists="replace",
            )

        manifest = build_manifest(acs_config, con, schema=RAW_SCHEMA)
    finally:
        con.close()
        if owns_client:
            client.close()

    if write_manifest:
        path = manifest.write(storage.data_dir / "acs.manifest.json")
        logger.info("acs: wrote manifest to %s", path)

    logger.info("acs: ingest done — %d table(s) loaded", len(manifest.tables))
    return manifest
