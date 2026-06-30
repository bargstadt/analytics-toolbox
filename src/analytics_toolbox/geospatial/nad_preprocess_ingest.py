"""Download and ingest the National Address Database (NAD) into DuckDB.

Privacy: the only network call this module makes is a one-time download of
public reference data from a government source (.gov domain). Input address
data is never transmitted anywhere — normalization and ingestion happen
entirely locally.

NAD source
----------
As of NAD Release 22, the US DOT distributes the NAD as a single national
ZIP file (~8.45 GB, ~97 million records) via datahub.transportation.gov
(dataset fc2s-wawr). There is no longer a per-state filtered download
endpoint. The full national file is downloaded once to ``storage.data_dir``
and reused for all state ingests; state filtering happens locally via DuckDB.

URL stability
-------------
The download URL is stored in ``_config._DEFAULT_NAD_URL`` and can be
overridden per-config via ``nad.url``. If the blob asset ID changes on a
future NAD release, update that constant (or override in config). Check
https://www.transportation.gov/gis/national-address-database for current
download links.

Column mapping (Release 22)
---------------------------
``NAD_COLUMN_MAP`` documents the mapping from raw NAD column names to the
internal schema. Release 22 uses different column names from older ArcGIS
exports. The street address line is reconstructed from component columns
(``Add_Number``, ``St_PreDir``, ``St_Name``, ``St_PosTyp``, ``St_PosDir``)
rather than a direct rename. Verify against the NAD data dictionary if column
names change in a future release.
"""

from __future__ import annotations

import logging
import time
import zipfile
from pathlib import Path

import duckdb
import geopandas as gpd
import httpx
import pandas as pd
from shapely import wkt as shapely_wkt

from analytics_toolbox.geospatial._config import GeospatialConfig
from analytics_toolbox.geospatial.address_normalizer import normalize_addresses

logger = logging.getLogger(__name__)

# Reference documentation for NAD Release 22 column names.
# Keys are NAD source column names; values are internal schema column names.
# Street line components (Add_Number, St_PreDir, St_Name, St_PosTyp, St_PosDir)
# are reconstructed inline rather than renamed directly — see _read_and_normalize.
NAD_COLUMN_MAP: dict[str, str] = {
    "OID_": "nad_id",           # Integer sequence ID (Release 22+)
    "UUID": "nad_id",           # UUID fallback when OID_ absent
    "State": "state",
    "County": "county_fips",    # County name; NAD does not include FIPS codes
    "Zip_Code": "postal_code",  # Release 22 (was "ZipCode" in older ArcGIS exports)
    "Add_Number": "_house_number",     # Release 22 (was "AddressNumber")
    "St_PreDir": "_pre_dir",           # Release 22 (was "PreDirectional")
    "St_Name": "_street_name",         # Release 22 (was "StreetName")
    "St_PosTyp": "_post_type",         # Release 22 (was "StreetNamePostType")
    "St_PosDir": "_post_dir",          # Release 22 (was "PostDirectional")
    "Post_City": "_city",              # Postal delivery city (Release 22)
    "Inc_Muni": "_city",               # Incorporated municipality (fallback)
    "Latitude": "latitude",
    "Longitude": "longitude",
}

_NAD_SCHEMA = """
    CREATE TABLE IF NOT EXISTS nad_addresses (
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
"""


def sample_nad_addresses(
    n: int,
    state: str,
    config: GeospatialConfig,
    seed: int | None = None,
) -> list[str]:
    """Return up to n randomly sampled street addresses from the NAD for a given state.

    Reads directly from the local DuckDB file — no network calls.

    Args:
        n: Maximum number of addresses to return.
        state: Two-letter US state code (e.g. "IA"). Case-insensitive.
        config: GeospatialConfig pointing to the ingested DuckDB database.
        seed: Optional integer seed for reservoir sampling. None = non-deterministic.

    Returns:
        List of normalized street address strings (e.g. "215 E GRAND AVE").
        May be shorter than n if fewer addresses exist for that state.
    """
    conn = duckdb.connect(str(config.storage.connection), read_only=True)
    try:
        # n and seed are safe Python ints (not user input) — formatted directly into
        # the SQL because DuckDB's SAMPLE clause does not support parameter binding.
        repeatable = f" REPEATABLE({int(seed)})" if seed is not None else ""
        rows = conn.execute(
            f"""
            SELECT normalized_address_line_1
            FROM (
                SELECT normalized_address_line_1
                FROM nad_addresses
                WHERE state = ?
                  AND normalized_address_line_1 IS NOT NULL
                  AND normalized_address_line_1 != ''
                  AND is_standard_address = true
            ) _filtered
            USING SAMPLE reservoir({int(n)} ROWS){repeatable}
            """,
            [state.upper()],
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def ingest_nad(config: GeospatialConfig) -> None:
    """Download the NAD national file, normalize addresses, and ingest into DuckDB.

    Args:
        config: Fully loaded ``GeospatialConfig``. The ``nad.states`` list
            controls which states are ingested from the national file.
            ``nad.force_refresh`` controls whether existing state rows are
            replaced or skipped.

    The national NAD ZIP (~8.45 GB) is downloaded once to
    ``config.storage.data_dir`` and reused across all state ingests. After
    initial download, running ``ingest_nad`` for additional states does not
    re-download the national file.

    For each state in ``config.nad.states``:
    - If rows already exist for that state and ``force_refresh`` is False: skip.
    - If ``force_refresh`` is True: delete existing rows for that state first.
    - Filter the national file by state, normalize addresses, and insert.
    """
    data_dir = Path(config.storage.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    conn = duckdb.connect(config.storage.connection)
    try:
        conn.execute(_NAD_SCHEMA)
        # Migrate existing tables that pre-date the nad_city column
        existing_cols = {r[0] for r in conn.execute("DESCRIBE nad_addresses").fetchall()}
        if "nad_city" not in existing_cols:
            conn.execute("ALTER TABLE nad_addresses ADD COLUMN nad_city VARCHAR")
        for state in config.nad.states:
            _ingest_state(state, config, conn, data_dir)
    finally:
        conn.close()


def _ingest_state(
    state: str,
    config: GeospatialConfig,
    conn: duckdb.DuckDBPyConnection,
    data_dir: Path,
) -> None:
    state = state.upper()

    if not config.nad.force_refresh:
        existing = conn.execute(
            "SELECT COUNT(*) FROM nad_addresses WHERE state = ?", [state]
        ).fetchone()[0]
        if existing > 0:
            logger.info("[nad] %s: %d rows present, skipping", state, existing)
            return

    if config.nad.force_refresh:
        conn.execute("DELETE FROM nad_addresses WHERE state = ?", [state])
        logger.info("[nad] %s: cleared existing rows for refresh", state)

    txt_path = _ensure_national_txt(data_dir, config.nad.url)
    logger.info("[nad] %s: reading from national file", state)

    df = _read_and_normalize(txt_path, state)
    if df.empty:
        logger.warning("[nad] %s: no valid rows after filtering, skipping", state)
        return

    df = _impute_missing_postal_codes(df, conn, config.tiger.vintage)
    _insert_rows(df, conn)
    count = conn.execute(
        "SELECT COUNT(*) FROM nad_addresses WHERE state = ?", [state]
    ).fetchone()[0]
    logger.info("[nad] %s: inserted %d rows", state, count)


def _ensure_national_txt(data_dir: Path, url: str) -> Path:
    """Download and extract the NAD national ZIP if not already present.

    Downloads ``TXT.zip`` from datahub.transportation.gov (~8.45 GB) and
    extracts the contained text file. The download is resumable: if the
    connection drops mid-transfer, the next call picks up where it left off
    using HTTP Range requests (``Accept-Ranges: bytes`` is advertised by the
    server). Both the ZIP and extracted file are retained in ``data_dir`` so
    subsequent state ingests don't re-download.

    Args:
        data_dir: Directory where downloaded and extracted files are stored.
        url: Direct download URL for the NAD national ZIP.

    Returns:
        Path to the extracted national NAD text file.
    """
    zip_path = data_dir / "nad_national.zip"
    txt_path = data_dir / "nad_national.txt"

    if txt_path.exists() and _txt_looks_valid(txt_path):
        logger.info("[nad] national file already at %s, skipping download", txt_path)
        return txt_path
    elif txt_path.exists():
        logger.warning(
            "[nad] %s appears corrupt (starts with non-CSV content); re-extracting", txt_path
        )
        txt_path.unlink()

    if not zip_path.exists() or not _zip_is_complete(zip_path):
        _download_resumable(url, zip_path)

    logger.info("[nad] extracting national ZIP to %s", txt_path)
    with zipfile.ZipFile(zip_path) as zf:
        entry = _find_nad_data_entry(zf)
        logger.info(
            "[nad] extracting entry '%s' (%.1f GB uncompressed) — this may take several minutes",
            entry.filename,
            entry.file_size / 1e9,
        )
        written = 0
        last_logged_gb = 0
        chunk = 8 * 1024 * 1024  # 8 MB
        with zf.open(entry) as src, open(txt_path, "wb") as dst:
            while True:
                buf = src.read(chunk)
                if not buf:
                    break
                dst.write(buf)
                written += len(buf)
                gb_written = int(written / 1e9)
                if gb_written > last_logged_gb:
                    logger.info("[nad] extraction progress: %d GB written", gb_written)
                    last_logged_gb = gb_written
    logger.info("[nad] extraction complete: %.1f GB written to %s", written / 1e9, txt_path)

    if not _txt_looks_valid(txt_path):
        txt_path.unlink()
        raise RuntimeError(
            f"[nad] extracted '{entry.filename}' but it starts with non-CSV content "
            f"(XML or binary). The ZIP entry selected by _find_nad_data_entry is not "
            f"the expected comma-delimited NAD data file. Inspect the ZIP contents and "
            f"update _find_nad_data_entry if the entry name has changed."
        )

    return txt_path


def _zip_is_complete(path: Path) -> bool:
    """Return True if the ZIP file has a valid end-of-central-directory record."""
    try:
        with zipfile.ZipFile(path) as zf:
            return bool(zf.namelist())
    except (zipfile.BadZipFile, EOFError, OSError):
        return False


_NAD_MIN_BYTES = 35 * 1024 * 1024 * 1024  # 35 GB — rejects partial extractions


def _txt_looks_valid(path: Path) -> bool:
    """Return True if the file is a plausible full NAD CSV, not XML/binary/truncated."""
    try:
        stat = path.stat()
        if stat.st_size < _NAD_MIN_BYTES:
            return False
        with open(path, "rb") as fh:
            head = fh.read(16)
        xml_sigs = (b"<?xml", b"\xef\xbb\xbf<?xml")
        return bool(head) and not any(head.startswith(sig) for sig in xml_sigs)
    except OSError:
        return False


def _find_nad_data_entry(zf: zipfile.ZipFile) -> zipfile.ZipInfo:
    """Return the ZipInfo for the NAD data file within the ZIP.

    The NAD TXT.zip may contain metadata/schema files (XML, XSD) alongside the
    actual comma-delimited data. This function skips known non-data extensions
    and returns the largest remaining entry. Falls back to the largest entry
    overall if nothing survives the filter.
    """
    non_data_exts = {".xml", ".xsd", ".json", ".pdf", ".readme", ".md", ".txt.xml"}
    infos = zf.infolist()
    candidates = [
        info for info in infos
        if not any(info.filename.lower().endswith(ext) for ext in non_data_exts)
    ]
    if not candidates:
        candidates = infos
    return max(candidates, key=lambda info: info.file_size)


def _download_resumable(url: str, dest_path: Path, max_retries: int = 5) -> None:
    """Stream-download a large file with HTTP Range resume and exponential-backoff retry.

    On each attempt, checks how many bytes are already written to ``dest_path``
    and sends ``Range: bytes=N-`` so the download continues from that offset
    rather than restarting. Falls back to a full restart if the server returns
    200 instead of 206 (does not support Range).

    Args:
        url: URL to download.
        dest_path: Destination file path. Appended to on resume; created on first run.
        max_retries: Maximum retry attempts after the first failure.
    """
    for attempt in range(max_retries):
        existing = dest_path.stat().st_size if dest_path.exists() else 0
        headers: dict[str, str] = {}
        mode = "wb"
        if existing > 0:
            headers["Range"] = f"bytes={existing}-"
            mode = "ab"
            logger.info("[nad] resuming from %.2f GB already written", existing / 1e9)
        else:
            logger.info(
                "[nad] downloading national ZIP (~8.5 GB) from %s — "
                "one-time download; future state ingests reuse this file",
                url,
            )
        try:
            with httpx.stream(
                "GET", url, headers=headers, follow_redirects=True, timeout=7200
            ) as resp:
                if resp.status_code == 416:
                    logger.info("[nad] ZIP already complete (server returned 416)")
                    return
                resp.raise_for_status()
                if resp.status_code == 200 and existing > 0:
                    logger.warning(
                        "[nad] server does not support Range requests; restarting from byte 0"
                    )
                    mode = "wb"
                with open(dest_path, mode) as fh:
                    for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                        fh.write(chunk)
            logger.info("[nad] download complete: %s", dest_path)
            return
        except (httpx.RemoteProtocolError, httpx.ReadTimeout, httpx.ConnectError) as exc:
            downloaded = dest_path.stat().st_size if dest_path.exists() else 0
            if attempt < max_retries - 1:
                delay = 2**attempt  # 1, 2, 4, 8 seconds
                logger.warning(
                    "[nad] connection dropped at %.2f GB (%s); retrying in %ds"
                    " (attempt %d/%d)",
                    downloaded / 1e9, exc, delay, attempt + 1, max_retries,
                )
                time.sleep(delay)
            else:
                raise


def _read_and_normalize(txt_path: Path, state: str) -> pd.DataFrame:
    """Filter the national NAD text file by state, build street lines, and normalize."""
    # DuckDB scans the full CSV but only returns matching rows, avoiding loading
    # all 97M records into memory. The path is escaped for the SQL literal.
    txt_str = str(txt_path).replace("'", "''")
    tmp_conn = duckdb.connect()
    try:
        # state is already uppercased; NAD stores state codes in uppercase.
        # null_padding=true is required: NAD rows may have trailing empty fields
        # (e.g. military addresses with no lat/lon) causing DuckDB's sniffer to
        # fail column detection without it.
        # quote='"' must be set explicitly: NAD uses double-quote quoting for fields
        # that contain commas (e.g. owner names like "BUSH PAINTING, INC"), but the
        # sniffer misses this because quoted fields are sparse in the sample window.
        raw = tmp_conn.execute(
            f"SELECT * FROM read_csv("
            f"'{txt_str}', all_varchar=true, header=true, null_padding=true, quote='\"'"
            f") WHERE State = ?",
            [state],
        ).df()
    finally:
        tmp_conn.close()

    if raw.empty:
        return raw

    def _col(name: str) -> pd.Series:
        return raw.get(
            name,
            pd.Series([""] * len(raw), index=raw.index, dtype=str),
        ).fillna("").str.strip()

    # Reconstruct a full street line from NAD Release 22 component columns.
    # Collapse multiple internal spaces (produced by empty optional fields)
    # into a single space so "100  MAIN  ST" becomes "100 MAIN ST".
    raw["_street_line"] = (
        _col("Add_Number") + " " +
        _col("St_PreDir") + " " +
        _col("St_Name") + " " +
        _col("St_PosTyp") + " " +
        _col("St_PosDir")
    ).str.replace(r"\s+", " ", regex=True).str.strip()

    # Prefer OID_ (integer sequence, Release 22+); fall back to UUID or generated.
    if "OID_" in raw.columns:
        nad_id_series = raw["OID_"].fillna("").astype(str)
    elif "UUID" in raw.columns:
        nad_id_series = raw["UUID"].fillna("").astype(str)
    else:
        nad_id_series = pd.Series(
            [f"{state}-{i}" for i in range(len(raw))],
            index=raw.index,
            dtype=str,
        )

    # City: prefer Post_City (postal delivery name); fall back to Inc_Muni.
    if "Post_City" in raw.columns:
        city_series = _col("Post_City")
    elif "Inc_Muni" in raw.columns:
        city_series = _col("Inc_Muni")
    else:
        city_series = pd.Series([""] * len(raw), index=raw.index, dtype=str)

    # ZIP column name changed between NAD ArcGIS exports (ZipCode) and Release 22 (Zip_Code)
    zip_series = _col("Zip_Code") if "Zip_Code" in raw.columns else _col("ZipCode")

    addr_df = pd.DataFrame({
        "Street_Address": raw["_street_line"],
        "City": city_series,
        "State": _col("State"),
        "Postal_Code": zip_series,
        "_nad_id": nad_id_series,
        "_county": _col("County"),
        "_latitude": pd.to_numeric(
            raw.get("Latitude", pd.Series([None] * len(raw), index=raw.index)),
            errors="coerce",
        ),
        "_longitude": pd.to_numeric(
            raw.get("Longitude", pd.Series([None] * len(raw), index=raw.index)),
            errors="coerce",
        ),
        "_nad_address_line_1": raw["_street_line"],
    })

    normalized = normalize_addresses(addr_df)

    return pd.DataFrame({
        "nad_id": normalized["_nad_id"].astype(str),
        "state": state,
        "county_fips": normalized["_county"],
        "nad_city": city_series.str.upper().str.strip(),
        "postal_code": normalized["Postal_Code"],
        "nad_address_line_1": normalized["_nad_address_line_1"],
        "normalized_address_line_1": normalized["normalized_address_line_1"],
        "normalized_postal_code": normalized["normalized_postal_code"],
        "latitude": normalized["_latitude"],
        "longitude": normalized["_longitude"],
        "is_standard_address": normalized["is_standard_address"],
        "address_flag": normalized["address_flag"],
    })


def _impute_missing_postal_codes(
    df: pd.DataFrame,
    conn: duckdb.DuckDBPyConnection,
    vintage: int,
) -> pd.DataFrame:
    """Fill normalized_postal_code for NAD rows that have lat/lon but no ZIP.

    Uses a point-in-polygon spatial join against the TIGER ZCTA polygons
    already loaded in DuckDB. Silently skips if the ZCTA table is absent
    or predates the geom_wkt column (run ingest-tiger with force_refresh=True
    to pick up the polygon column).

    Args:
        df: Output of ``_read_and_normalize`` for one state.
        conn: Open DuckDB connection to the analytics_toolbox database.
        vintage: TIGER vintage year (selects the ``tiger_zcta_{vintage}`` table).

    Returns:
        df with ``normalized_postal_code`` and ``postal_code`` filled where
        a ZCTA polygon contains the NAD point. Rows still missing a postal
        code after the join are left as-is.
    """
    missing_mask = (
        df["normalized_postal_code"].isna() | (df["normalized_postal_code"].str.strip() == "")
    ) & df["latitude"].notna() & df["longitude"].notna()

    if not missing_mask.any():
        return df

    n_missing = int(missing_mask.sum())
    zcta_table = f"tiger_zcta_{vintage}"

    try:
        available_cols = {r[0] for r in conn.execute(f"DESCRIBE {zcta_table}").fetchall()}  # noqa: S608
    except duckdb.CatalogException:
        logger.warning(
            "[nad] ZCTA table %s not found — run ingest-tiger first to enable "
            "postal code imputation for %d rows with lat/lon but no ZIP",
            zcta_table, n_missing,
        )
        return df

    if "geom_wkt" not in available_cols:
        logger.warning(
            "[nad] ZCTA table %s has no geom_wkt column — re-run ingest-tiger "
            "with force_refresh=True to enable postal code imputation for %d rows",
            zcta_table, n_missing,
        )
        return df

    logger.info("[nad] imputing postal codes for %d rows with lat/lon but no ZIP", n_missing)

    zcta_rows = conn.execute(
        f"SELECT zcta5, geom_wkt FROM {zcta_table} WHERE geom_wkt IS NOT NULL"  # noqa: S608
    ).fetchall()

    zcta_gdf = gpd.GeoDataFrame(
        {"zcta5": [r[0] for r in zcta_rows]},
        geometry=[shapely_wkt.loads(r[1]) for r in zcta_rows],
        crs="EPSG:4326",
    )

    missing_df = df[missing_mask]
    points_gdf = gpd.GeoDataFrame(
        index=missing_df.index,
        geometry=gpd.points_from_xy(missing_df["longitude"], missing_df["latitude"]),
        crs="EPSG:4326",
    )

    joined = gpd.sjoin(points_gdf, zcta_gdf, how="left", predicate="intersects")
    joined = joined[~joined.index.duplicated(keep="first")]
    imputed = joined["zcta5"].reindex(df.index)

    filled_mask = missing_mask & imputed.notna()
    if not filled_mask.any():
        logger.warning(
            "[nad] point-in-polygon matched 0 of %d rows — check CRS alignment", n_missing
        )
        return df

    df = df.copy()
    df.loc[filled_mask, "normalized_postal_code"] = imputed[filled_mask]
    df.loc[filled_mask, "postal_code"] = imputed[filled_mask]

    logger.info(
        "[nad] imputed postal codes for %d/%d rows (%.1f%%)",
        int(filled_mask.sum()), n_missing, filled_mask.sum() / n_missing * 100,
    )
    return df


def _insert_rows(df: pd.DataFrame, conn: duckdb.DuckDBPyConnection) -> None:
    conn.register("_nad_staging", df)
    cols = ", ".join(df.columns)
    conn.execute(f"INSERT INTO nad_addresses ({cols}) SELECT {cols} FROM _nad_staging")  # noqa: S608
    conn.unregister("_nad_staging")
