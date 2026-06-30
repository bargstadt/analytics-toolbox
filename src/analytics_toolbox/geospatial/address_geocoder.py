"""TIGER/Line shapefile ingest and block group geocoding.

Downloads TIGER/Line block group and ZCTA shapefiles from census.gov, stores
them in DuckDB, and assigns each input address a block group FIPS code via
point-in-polygon lookup using geopandas/shapely.

Privacy: the only network calls are one-time downloads of public Census Bureau
shapefiles from census.gov. Input address data never leaves the machine.

TIGER vintage
-------------
Shapefiles are versioned by year. Table names include the vintage so multiple
years can coexist: ``tiger_block_groups_2024``, ``tiger_zcta_2024``. The vintage
used for a geocoding run is included in the output as ``tiger_vintage``.

Download URLs (verify at implementation time if downloads fail):
  Block groups: https://www2.census.gov/geo/tiger/TIGER{vintage}/BG/tl_{vintage}_{state_fips}_bg.zip
                (per-state only — no national rollup as of TIGER 2024)
  ZCTA:         https://www2.census.gov/geo/tiger/TIGER{vintage}/ZCTA520/tl_{vintage}_us_zcta520.zip
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

import duckdb
import geopandas as gpd
import httpx
import pandas as pd
from shapely import wkt as shapely_wkt

# FIPS codes for the 50 states + DC + Puerto Rico (shared single source of truth).
from analytics_toolbox._fips import STATE_FIPS as _STATE_FIPS
from analytics_toolbox.geospatial._config import GeospatialConfig

logger = logging.getLogger(__name__)

_BG_STATE_URL = (
    "https://www2.census.gov/geo/tiger/TIGER{vintage}/BG/tl_{vintage}_{fips}_bg.zip"
)
_ZCTA_URL = "https://www2.census.gov/geo/tiger/TIGER{vintage}/ZCTA520/tl_{vintage}_us_zcta520.zip"


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

def ingest_tiger(config: GeospatialConfig) -> None:
    """Download TIGER/Line shapefiles and load them into DuckDB.

    Args:
        config: Loaded ``GeospatialConfig``. ``tiger.vintage`` controls which
            year's shapefiles are downloaded. ``tiger.force_refresh`` controls
            whether existing tables are replaced or skipped.

    Creates two tables (vintage-stamped so multiple years can coexist):

    - ``tiger_block_groups_{vintage}``: block group FIPS + WKT geometry
    - ``tiger_zcta_{vintage}``: ZCTA5 code + centroid lat/lon

    Idempotent by default: skips if tables already exist unless ``force_refresh``.
    """
    vintage = config.tiger.vintage
    data_dir = Path(config.storage.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    bg_table = f"tiger_block_groups_{vintage}"
    zcta_table = f"tiger_zcta_{vintage}"

    conn = duckdb.connect(config.storage.connection)
    try:
        tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}

        if bg_table in tables and zcta_table in tables and not config.tiger.force_refresh:
            logger.info("[tiger] %s and %s already exist, skipping", bg_table, zcta_table)
            return

        if config.tiger.force_refresh:
            conn.execute(f"DROP TABLE IF EXISTS {bg_table}")  # noqa: S608
            conn.execute(f"DROP TABLE IF EXISTS {zcta_table}")  # noqa: S608

        logger.info("[tiger] downloading block group shapefiles (vintage %d)", vintage)
        bg_gdf = _download_block_groups(config.nad.states, vintage)

        logger.info("[tiger] downloading ZCTA shapefile (vintage %d)", vintage)
        zcta_gdf = _download_shapefile(_ZCTA_URL.format(vintage=vintage))

        _load_block_groups(bg_gdf, bg_table, conn, vintage)
        _load_zcta(zcta_gdf, zcta_table, conn)

        logger.info("[tiger] done — %s and %s loaded", bg_table, zcta_table)
    finally:
        conn.close()


def _download_block_groups(states: list[str], vintage: int) -> gpd.GeoDataFrame:
    """Download per-state block group shapefiles and return them concatenated.

    Census stopped distributing a national block group rollup as of TIGER 2024;
    only per-state ZIPs are available. Downloads only the states present in
    ``states`` (the same set ingested from NAD).

    Args:
        states: List of two-letter state abbreviations (uppercased).
        vintage: TIGER release year.

    Raises:
        ValueError: If a state abbreviation has no known FIPS code.
    """
    unknown = [s for s in states if s.upper() not in _STATE_FIPS]
    if unknown:
        raise ValueError(f"Unknown state abbreviation(s) for TIGER download: {unknown}")

    gdfs = []
    for state in states:
        fips = _STATE_FIPS[state.upper()]
        url = _BG_STATE_URL.format(vintage=vintage, fips=fips)
        logger.info("[tiger] downloading block groups for %s (FIPS %s)", state, fips)
        gdfs.append(_download_shapefile(url))

    return gpd.GeoDataFrame(pd.concat(gdfs, ignore_index=True), crs=gdfs[0].crs)


def _download_shapefile(url: str) -> gpd.GeoDataFrame:
    """Download a zipped shapefile and return it as a GeoDataFrame.

    Passes the raw bytes buffer directly to geopandas, which handles zip
    extraction internally. This keeps the download and read in one step so
    tests can mock gpd.read_file to inject synthetic GeoDataFrames without
    needing a real zip file.
    """
    buf = io.BytesIO()
    with httpx.stream("GET", url, follow_redirects=True, timeout=600) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes(chunk_size=1024 * 1024):
            buf.write(chunk)
    buf.seek(0)
    gdf = gpd.read_file(buf)
    return gdf.to_crs("EPSG:4326")


def _load_block_groups(
    gdf: gpd.GeoDataFrame,
    table: str,
    conn: duckdb.DuckDBPyConnection,
    vintage: int,
) -> None:
    # TIGER block group FIPS = STATEFP + COUNTYFP + TRACTCE + BLKGRPCE
    fips_col = _detect_col(gdf, ["GEOID", "BGFIPS", "GEOID10", "GEOID20", "block_group_fips"])
    geom_col = gdf.geometry.name

    df = pd.DataFrame({
        "block_group_fips": gdf[fips_col].astype(str),
        "geom_wkt": gdf[geom_col].apply(lambda g: g.wkt if g is not None else None),
    })

    conn.execute(f"""
        CREATE OR REPLACE TABLE {table} (
            block_group_fips VARCHAR,
            geom_wkt VARCHAR
        )
    """)  # noqa: S608
    conn.register("_bg_staging", df)
    conn.execute(f"INSERT INTO {table} SELECT * FROM _bg_staging")  # noqa: S608
    conn.unregister("_bg_staging")
    logger.info("[tiger] loaded %d block groups into %s", len(df), table)


def _load_zcta(
    gdf: gpd.GeoDataFrame,
    table: str,
    conn: duckdb.DuckDBPyConnection,
) -> None:
    zcta_col = _detect_col(gdf, ["ZCTA5CE20", "ZCTA5CE10", "ZCTA5", "GEOID", "GEOID20", "zcta5"])
    # Centroid computed in WGS84 — sufficient for a postal fallback (ZCTA centroids
    # are never used for precise street-level work, only as coarse fallback points).
    centroids = gdf.geometry.to_crs("EPSG:3857").centroid.to_crs("EPSG:4326")

    df = pd.DataFrame({
        "zcta5": gdf[zcta_col].astype(str).str[:5],
        "centroid_lat": centroids.y,
        "centroid_lon": centroids.x,
        # Polygon WKT in EPSG:4326 — used by nad_preprocess_ingest to impute
        # missing postal codes on NAD records via point-in-polygon spatial join.
        "geom_wkt": gdf.geometry.apply(lambda g: g.wkt if g is not None else None),
    })

    conn.execute(f"""
        CREATE OR REPLACE TABLE {table} (
            zcta5 VARCHAR,
            centroid_lat DOUBLE,
            centroid_lon DOUBLE,
            geom_wkt VARCHAR
        )
    """)  # noqa: S608
    conn.register("_zcta_staging", df)
    conn.execute(f"INSERT INTO {table} SELECT * FROM _zcta_staging")  # noqa: S608
    conn.unregister("_zcta_staging")
    logger.info("[tiger] loaded %d ZCTA polygons into %s", len(df), table)


def _detect_col(gdf: gpd.GeoDataFrame, candidates: list[str]) -> str:
    for col in candidates:
        if col in gdf.columns:
            return col
    raise RuntimeError(
        f"Could not find expected column in shapefile. "
        f"Tried: {candidates}. Available: {list(gdf.columns)}"
    )


# ---------------------------------------------------------------------------
# Geocode
# ---------------------------------------------------------------------------

def geocode_addresses(
    addresses: pd.DataFrame,
    config: GeospatialConfig,
) -> pd.DataFrame:
    """Assign block group FIPS to each address via point-in-polygon lookup.

    Args:
        addresses: DataFrame that has already been through ``match_addresses``.
            Must contain ``matched_latitude``, ``matched_longitude``, and
            ``match_method`` columns.
        config: Loaded ``GeospatialConfig``. ``tiger.vintage`` selects which
            DuckDB table is queried (``tiger_block_groups_{vintage}``).

    Returns:
        A copy of ``addresses`` with these columns appended:

        - ``block_group_fips``: 12-digit Census FIPS, or ``None`` if the point
          falls outside all block group polygons.
        - ``census_tract_fips``: First 11 characters of ``block_group_fips``.
        - ``tiger_vintage``: The TIGER release year used.
        - ``location_imputed``: ``True`` when ``match_method != "nad_match"``,
          indicating the lat/lon is a postal centroid rather than a street point.

    Raises:
        ValueError: If required columns are missing from ``addresses``.
    """
    required = {"matched_latitude", "matched_longitude", "match_method"}
    missing = required - set(addresses.columns)
    if missing:
        raise ValueError(
            f"addresses is missing required columns: {sorted(missing)}. "
            "Run match_addresses() first."
        )

    vintage = config.tiger.vintage
    bg_table = f"tiger_block_groups_{vintage}"

    conn = duckdb.connect(config.storage.connection)
    try:
        bg_rows = conn.execute(
            f"SELECT block_group_fips, geom_wkt FROM {bg_table}"  # noqa: S608
        ).fetchall()
    except duckdb.CatalogException:
        raise ValueError(
            f"Block group table '{bg_table}' not found in DuckDB. "
            "Run `analytics-toolbox ingest-tiger --config <path>` first."
        ) from None
    finally:
        conn.close()

    bg_gdf = gpd.GeoDataFrame(
        {"block_group_fips": [r[0] for r in bg_rows]},
        geometry=[shapely_wkt.loads(r[1]) for r in bg_rows],
        crs="EPSG:4326",
    )

    valid_mask = addresses["matched_latitude"].notna() & addresses["matched_longitude"].notna()
    points_gdf = gpd.GeoDataFrame(
        {"_orig_idx": addresses.index[valid_mask]},
        geometry=gpd.points_from_xy(
            addresses.loc[valid_mask, "matched_longitude"],
            addresses.loc[valid_mask, "matched_latitude"],
        ),
        crs="EPSG:4326",
        index=addresses.index[valid_mask],
    )

    if not points_gdf.empty:
        # Use "intersects" (not "within") so boundary points are not silently
        # dropped. Deduplicate by keeping the first match for any point that
        # touches multiple polygon edges simultaneously.
        joined = gpd.sjoin(points_gdf, bg_gdf, how="left", predicate="intersects")
        joined = joined[~joined.index.duplicated(keep="first")]
        fips_series = joined["block_group_fips"].reindex(addresses.index)
    else:
        fips_series = pd.Series([None] * len(addresses), index=addresses.index)

    result = addresses.copy()
    result["block_group_fips"] = fips_series.values
    result["census_tract_fips"] = result["block_group_fips"].apply(
        lambda x: x[:11] if isinstance(x, str) and len(x) >= 11 else None
    )
    result["tiger_vintage"] = vintage
    result["location_imputed"] = result["match_method"] != "nad_match"

    return result
