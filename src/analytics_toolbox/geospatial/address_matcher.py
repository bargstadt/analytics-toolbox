"""Fuzzy address matching against the National Address Database.

Matches a pre-normalized input address table against the ingested NAD table
in DuckDB. Blocking by postal code keeps the candidate set tractable before
RapidFuzz scoring. Falls back to ZCTA centroid coordinates for any address
that cannot be confidently matched at the street level.

Privacy: all data stays local. The only reads here are from the caller's own
DuckDB file — no data is sent to any external API or service.
"""

from __future__ import annotations

import logging
from typing import Any

import duckdb
import numpy as np
import pandas as pd
from rapidfuzz import fuzz, process

from analytics_toolbox.geospatial._config import GeospatialConfig

logger = logging.getLogger(__name__)


def match_addresses(
    addresses: pd.DataFrame,
    config: GeospatialConfig,
    *,
    top_n: int = 1,
) -> pd.DataFrame:
    """Fuzzy-match a normalized address table against the NAD in DuckDB.

    Args:
        addresses: Pre-normalized DataFrame. Must contain ``is_standard_address``
            (produced by ``normalize_addresses``). Raises ``ValueError`` if absent.
        config: Loaded ``GeospatialConfig`` supplying the DuckDB connection string
            and confidence threshold.
        top_n: Number of NAD candidates to return per input row. When ``top_n=1``
            (default) the output has the same number of rows as the input. When
            ``top_n > 1`` each input row is repeated up to ``top_n`` times with a
            ``match_rank`` column (1 = best). Callers doing human adjudication
            should filter to ``match_rank == 1`` for the automated path.

    Returns:
        A copy of ``addresses`` with match output columns appended:

        - ``nad_id``: Matched NAD record identifier, or ``None`` if no match.
        - ``match_score``: RapidFuzz WRatio score (0–100), or ``None``.
        - ``match_rank``: 1-based rank among candidates (always 1 when top_n=1).
        - ``match_method``: One of ``"nad_match"``, ``"nad_match_sub_threshold"``
          (top_n > 1 only, for candidates below the confidence threshold),
          ``"postal_centroid"``, or ``"non_standard"``.
        - ``matched_latitude`` / ``matched_longitude``: NAD point or ZCTA centroid.
          Always populated except when the postal code has no ZCTA mapping.
        - ``matched_state_fips`` / ``matched_county_fips``: From NAD (first 2 chars
          of county_fips for state), or ``None`` for centroid fallbacks.

    Raises:
        ValueError: If ``is_standard_address`` column is missing from ``addresses``.
    """
    if "is_standard_address" not in addresses.columns:
        raise ValueError(
            "addresses must have an 'is_standard_address' column. "
            "Run normalize_addresses() first."
        )

    conn = duckdb.connect(config.storage.connection)
    vintage = config.tiger.vintage
    threshold = config.matching.confidence_threshold

    # Candidates and ZCTA centroids are keyed by postal code — fetch each once
    # and reuse across all rows sharing that postal code. This reduces DuckDB
    # queries from O(n_rows) to O(n_unique_postal_codes).
    candidate_cache: dict[Any, list[dict[str, Any]]] = {}
    zcta_cache: dict[str | None, dict[str, float | None]] = {}
    city_lookup_cache: dict[str | None, list[tuple[str, str, str]]] = {}

    result_rows: list[dict[str, Any]] = []
    result_index: list[Any] = []

    # Convert once to a list of plain dicts for O(1) row access throughout the loop.
    # This avoids repeated Series construction that iterrows() incurs per row.
    records = addresses.to_dict("records")
    pos_of = {idx: i for i, idx in enumerate(addresses.index)}

    # City standardization requires the nad_city column; gracefully skip if absent
    # (databases ingested before this column was added will lack it).
    try:
        conn.execute("SELECT nad_city FROM nad_addresses LIMIT 0")
        has_nad_city = True
    except duckdb.Error:
        has_nad_city = False

    def _city_std(postal: str | None, record: dict[str, Any]) -> dict[str, str | None]:
        if not has_nad_city:
            return {
                "standardized_city": None,
                "standardized_state": None,
                "standardized_county": None,
            }
        if postal not in city_lookup_cache:
            city_lookup_cache[postal] = _fetch_city_lookup(postal, conn)
        return _standardize_city(
            _input_city(record), _input_county(record), city_lookup_cache[postal]
        )

    try:
        standard_mask = addresses["is_standard_address"].fillna(False).astype(bool)

        # Non-standard (PO boxes, military, unparseable): ZCTA centroid, no scoring
        for orig_idx in addresses.index[~standard_mask]:
            record = records[pos_of[orig_idx]]
            postal = _best_postal_code(record)
            if postal not in zcta_cache:
                zcta_cache[postal] = _zcta_centroid(postal, conn, vintage)
            result_rows.append({
                **record,
                **_centroid_result(zcta_cache[postal], "non_standard"),
                **_city_std(postal, record),
            })
            result_index.append(orig_idx)

        # Standard addresses: group by postal code for batch candidate fetch and
        # vectorized scoring. cdist scores an entire ZIP group in one C-level call
        # rather than calling WRatio once per (address, candidate) pair.
        standard = addresses.loc[standard_mask]
        postal_keys = pd.Series(
            [_best_postal_code(records[pos_of[idx]]) for idx in standard.index],
            index=standard.index,
            dtype=object,
        )

        grouped = standard.groupby(postal_keys, sort=False, dropna=False)
        for postal, group_idx in grouped.groups.items():
            group_index = list(group_idx)

            if postal not in candidate_cache:
                candidate_cache[postal] = _fetch_candidates(postal, conn)
            candidates = candidate_cache[postal]

            if not candidates:
                # County-level fallback is per-row: records grouped under the same
                # missing postal code may be in entirely different counties, so each
                # row resolves its own county candidates independently.
                for orig_idx in group_index:
                    record = records[pos_of[orig_idx]]
                    county = _input_county(record)
                    state_val = str(record.get("State") or record.get("state") or "")
                    row_candidates: list[dict[str, Any]] = []
                    if county and state_val:
                        county_key = (state_val, county)
                        if county_key not in candidate_cache:
                            candidate_cache[county_key] = _fetch_candidates_by_county(
                                state_val, county, conn
                            )
                        row_candidates = candidate_cache[county_key]

                    if not row_candidates:
                        if postal not in zcta_cache:
                            zcta_cache[postal] = _zcta_centroid(postal, conn, vintage)
                        result_rows.append({
                            **record,
                            **_centroid_result(zcta_cache[postal], "postal_centroid"),
                            **_city_std(postal, record),
                        })
                        result_index.append(orig_idx)
                        continue

                    query_str = str(record.get("normalized_address_line_1") or "")
                    if not query_str:
                        if postal not in zcta_cache:
                            zcta_cache[postal] = _zcta_centroid(postal, conn, vintage)
                        result_rows.append({
                            **record,
                            **_centroid_result(zcta_cache[postal], "postal_centroid"),
                            **_city_std(postal, record),
                        })
                        result_index.append(orig_idx)
                        continue

                    row_scores = np.array(
                        [
                            fuzz.WRatio(query_str, c["normalized_address_line_1"])
                            for c in row_candidates
                        ],
                        dtype=np.float64,
                    )
                    if top_n == 1:
                        best_idx = int(np.argmax(row_scores))
                        best_score = float(row_scores[best_idx])
                        top_indices: list[int] = [best_idx]
                    else:
                        top_indices = list(np.argsort(row_scores)[::-1])
                        best_score = float(row_scores[top_indices[0]])

                    if best_score < threshold:
                        if postal not in zcta_cache:
                            zcta_cache[postal] = _zcta_centroid(postal, conn, vintage)
                        result_rows.append({
                            **record,
                            **_centroid_result(zcta_cache[postal], "postal_centroid"),
                            **_city_std(postal, record),
                        })
                        result_index.append(orig_idx)
                        continue

                    city_std_result = _city_std(postal, record)
                    for rank, j in enumerate(top_indices[:top_n], start=1):
                        score = float(row_scores[j])
                        method = (
                            "nad_match_county_block"
                            if score >= threshold
                            else "nad_match_sub_threshold"
                        )
                        result_rows.append({
                            **record,
                            **_nad_match_result(score, rank, row_candidates[j], method),
                            **city_std_result,
                        })
                        result_index.append(orig_idx)
                continue

            # ZIP-level candidates — batch-score the whole group with cdist.
            # scores_matrix shape: (len(group_index), len(candidates))
            query_strings = [
                str(records[pos_of[orig_idx]].get("normalized_address_line_1") or "")
                for orig_idx in group_index
            ]
            candidate_strings = [c["normalized_address_line_1"] for c in candidates]
            scores_matrix = process.cdist(query_strings, candidate_strings, scorer=fuzz.WRatio)

            for i, orig_idx in enumerate(group_index):
                record = records[pos_of[orig_idx]]
                query_str = query_strings[i]

                if not query_str:
                    if postal not in zcta_cache:
                        zcta_cache[postal] = _zcta_centroid(postal, conn, vintage)
                    result_rows.append({
                        **record,
                        **_centroid_result(zcta_cache[postal], "postal_centroid"),
                        **_city_std(postal, record),
                    })
                    result_index.append(orig_idx)
                    continue

                row_scores = scores_matrix[i]
                if top_n == 1:
                    best_idx = int(np.argmax(row_scores))
                    best_score = float(row_scores[best_idx])
                    top_indices = [best_idx]
                else:
                    top_indices = list(np.argsort(row_scores)[::-1])
                    best_score = float(row_scores[top_indices[0]])

                if best_score < threshold:
                    if postal not in zcta_cache:
                        zcta_cache[postal] = _zcta_centroid(postal, conn, vintage)
                    result_rows.append({
                        **record,
                        **_centroid_result(zcta_cache[postal], "postal_centroid"),
                        **_city_std(postal, record),
                    })
                    result_index.append(orig_idx)
                    continue

                city_std_result = _city_std(postal, record)
                for rank, j in enumerate(top_indices[:top_n], start=1):
                    score = float(row_scores[j])
                    method = "nad_match" if score >= threshold else "nad_match_sub_threshold"
                    result_rows.append({
                        **record,
                        **_nad_match_result(score, rank, candidates[j], method),
                        **city_std_result,
                    })
                    result_index.append(orig_idx)

    finally:
        conn.close()

    # Restore original row order — non-standard rows are processed first and groupby
    # may reorder standard rows; stable argsort on original position fixes both.
    if result_index:
        order = np.argsort([pos_of[idx] for idx in result_index], kind="stable")
        result_rows = [result_rows[i] for i in order]
        result_index = [result_index[i] for i in order]

    return pd.DataFrame(result_rows, index=pd.Index(result_index))


def _best_postal_code(record: dict[str, Any]) -> str | None:
    """Return the best available 5-digit postal code from an address record.

    Tries normalized_postal_code first, then common raw column names as fallback
    for non-standard addresses where normalization failed but the original value
    is still present.
    """
    for col in ("normalized_postal_code", "Postal_Code", "postal_code", "ZIP", "zip"):
        val = record.get(col)
        if val and pd.notna(val):
            return str(val)[:5]
    return None


def _fetch_candidates(
    postal_code: str | None,
    conn: duckdb.DuckDBPyConnection,
) -> list[dict[str, Any]]:
    if not postal_code:
        return []
    rows = conn.execute(
        """
        SELECT nad_id,
               normalized_address_line_1,
               latitude,
               longitude,
               LEFT(county_fips, 2) AS matched_state_fips,
               county_fips           AS matched_county_fips
        FROM nad_addresses
        WHERE normalized_postal_code = ?
          AND is_standard_address = true
        """,
        [postal_code],
    ).fetchall()
    cols = [
        "nad_id", "normalized_address_line_1", "latitude", "longitude",
        "matched_state_fips", "matched_county_fips",
    ]
    return [dict(zip(cols, r, strict=True)) for r in rows]


def _fetch_city_lookup(
    postal_code: str | None,
    conn: duckdb.DuckDBPyConnection,
) -> list[tuple[str, str, str]]:
    """Return distinct (nad_city, county_fips, state) tuples for a postal code.

    Used to standardize the city/county/state names on the input address against
    the authoritative NAD values for that ZIP, independent of whether a
    street-level match was found.
    """
    if not postal_code:
        return []
    rows = conn.execute(
        """
        SELECT DISTINCT nad_city, county_fips, state
        FROM nad_addresses
        WHERE normalized_postal_code = ?
          AND nad_city IS NOT NULL
          AND nad_city != ''
        """,
        [postal_code],
    ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def _input_city(record: dict[str, Any]) -> str | None:
    """Return the best available city string from an address record, uppercased."""
    raw = record.get("normalized_city") or record.get("City") or record.get("city")
    if raw:
        return str(raw).upper().strip() or None
    return None


def _input_county(record: dict[str, Any]) -> str | None:
    """Return the best available county string, uppercased and stripped of ' County' suffix."""
    for col in ("County", "county", "county_name"):
        raw = record.get(col)
        if raw and pd.notna(raw):
            cleaned = str(raw).upper().strip().removesuffix(" COUNTY").strip()
            return cleaned or None
    return None


def _standardize_city(
    input_city: str | None,
    input_county: str | None,
    city_lookup: list[tuple[str, str, str]],
) -> dict[str, str | None]:
    """Fuzzy-match input city and county against NAD values for a postal code.

    Scores each (nad_city, nad_county, state) tuple by averaging WRatio across
    whichever signals are available — city alone, county alone, or both combined.
    The tuple with the highest average score wins, which handles cross-county ZIPs
    cleanly: the city+county pair that best fits the input together is returned.
    """
    if not city_lookup:
        return {"standardized_city": None, "standardized_state": None, "standardized_county": None}
    if len(city_lookup) == 1:
        # Unambiguous ZIP — return the single NAD entry unconditionally
        city, county, state = city_lookup[0]
        return {
            "standardized_city": city,
            "standardized_state": state,
            "standardized_county": county,
        }
    if not input_city and not input_county:
        return {"standardized_city": None, "standardized_state": None, "standardized_county": None}

    def _score(t: tuple[str, str, str]) -> float:
        nad_city, nad_county, _ = t
        scores = []
        if input_city:
            scores.append(fuzz.WRatio(input_city, nad_city))
        if input_county:
            scores.append(fuzz.WRatio(input_county, nad_county))
        return sum(scores) / len(scores)

    city, county, state = max(city_lookup, key=_score)
    return {"standardized_city": city, "standardized_state": state, "standardized_county": county}


def _fetch_candidates_by_county(
    state: str,
    county: str,
    conn: duckdb.DuckDBPyConnection,
) -> list[dict[str, Any]]:
    """Fetch NAD candidates blocked by state + county name.

    Used as a fallback when postal code blocking yields no candidates.
    The ``county_fips`` column in ``nad_addresses`` stores county names
    (not FIPS codes), so matching uses ILIKE for case-insensitive partial
    match. Candidate sets are larger than ZIP-level blocks — expect lower
    match precision, tagged as ``nad_match_county_block`` downstream.
    """
    rows = conn.execute(
        """
        SELECT nad_id,
               normalized_address_line_1,
               latitude,
               longitude,
               NULL AS matched_state_fips,
               county_fips AS matched_county_fips
        FROM nad_addresses
        WHERE state = ?
          AND county_fips ILIKE ?
          AND is_standard_address = true
        """,
        [state.upper(), f"%{county}%"],
    ).fetchall()
    cols = [
        "nad_id", "normalized_address_line_1", "latitude", "longitude",
        "matched_state_fips", "matched_county_fips",
    ]
    return [dict(zip(cols, r, strict=True)) for r in rows]


def _zcta_centroid(
    postal_code: str | None,
    conn: duckdb.DuckDBPyConnection,
    vintage: int,
) -> dict[str, float | None]:
    if not postal_code:
        return {"centroid_lat": None, "centroid_lon": None}
    table = f"tiger_zcta_{vintage}"
    try:
        row = conn.execute(
            f"SELECT centroid_lat, centroid_lon FROM {table} WHERE zcta5 = ?",  # noqa: S608
            [postal_code[:5]],
        ).fetchone()
    except duckdb.CatalogException:
        logger.warning("ZCTA table %s not found; run ingest-tiger first", table)
        return {"centroid_lat": None, "centroid_lon": None}

    if row is None:
        logger.warning("No ZCTA centroid for postal code %s", postal_code[:5])
        return {"centroid_lat": None, "centroid_lon": None}
    return {"centroid_lat": row[0], "centroid_lon": row[1]}


def _centroid_result(centroid: dict[str, float | None], method: str) -> dict[str, Any]:
    return {
        "nad_id": None,
        "match_score": None,
        "match_rank": 1,
        "match_method": method,
        "matched_latitude": centroid["centroid_lat"],
        "matched_longitude": centroid["centroid_lon"],
        "matched_state_fips": None,
        "matched_county_fips": None,
    }


def _nad_match_result(
    score: float, rank: int, candidate: dict[str, Any], method: str = "nad_match"
) -> dict[str, Any]:
    return {
        "nad_id": candidate["nad_id"],
        "match_score": score,
        "match_rank": rank,
        "match_method": method,
        "matched_latitude": candidate["latitude"],
        "matched_longitude": candidate["longitude"],
        "matched_state_fips": candidate["matched_state_fips"],
        "matched_county_fips": candidate["matched_county_fips"],
    }
