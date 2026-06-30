"""Fetch ACS 5-year estimates from the U.S. Census Bureau API into DataFrames."""

from __future__ import annotations

import json
import logging
import time

import httpx
import pandas as pd

from analytics_toolbox.acs._errors import CensusAPIError
from analytics_toolbox.acs._geography import build_geo_params, state_to_fips
from analytics_toolbox.acs._variable_history import CENSUS_BASE_URL, endpoint_for_variable

__all__ = [
    "CensusAPIError",
    "SUPPRESSION_SENTINELS",
    "raw_table_name",
    "fetch_variable_dataframe",
]

logger = logging.getLogger(__name__)

# Sentinel values the Census API uses for suppressed or inapplicable cells.
SUPPRESSION_SENTINELS: frozenset[str] = frozenset({"-999", "-888", "-666"})

# Seconds to wait before each retry attempt (retries only on 5xx).
_RETRY_DELAYS: tuple[int, ...] = (2, 10)


def _get(client: httpx.Client, url: str, params: dict[str, str]) -> httpx.Response:
    """GET with the API key in params, raising a key-safe error on transport failure.

    httpx exceptions embed ``request.url`` (which carries the ``?key=`` secret), so
    a transport failure is re-raised as a ``CensusAPIError`` built from the bare
    ``url`` only, with the original exception's context suppressed.
    """
    try:
        return client.get(url, params=params, timeout=60)
    except httpx.HTTPError as exc:
        raise CensusAPIError(
            f"Failed to reach the Census API at {url}: {type(exc).__name__}"
        ) from None


def raw_table_name(variable_code: str, geography_level: str) -> str:
    """Canonical raw table name for a variable+geography combination.

    The single source of truth for raw table names, referenced by the ingest
    orchestrator, the manifest, and any downstream consumer.
    """
    return f"acs_{variable_code.lower()}_{geography_level}"


def _normalize_value(raw: str | None) -> tuple[int | float | str | None, bool]:
    """Convert a raw Census API cell value to a typed Python value.

    Returns ``(value, is_suppressed)``. Value is ``None`` when suppressed or null;
    non-numeric strings (e.g. NAME, GEO_ID) are returned as-is.
    """
    if raw is None:
        return None, False
    if raw in SUPPRESSION_SENTINELS:
        return None, True
    try:
        return int(raw), False
    except ValueError:
        pass
    try:
        return float(raw), False
    except ValueError:
        pass
    return raw, False


def _fetch_acs_records(
    variable_code: str,
    geography_level: str,
    endpoint: str,
    year: int,
    geo_params: dict[str, str],
    state_fips: str,
    api_key: str,
    client: httpx.Client,
) -> list[dict]:
    """Fetch one page of ACS records for a variable, year, state, and geography.

    Raises:
        CensusAPIError: For non-200 responses or an unexpected/non-JSON body.
    """
    url = f"{CENSUS_BASE_URL}/{year}{endpoint}"
    params = {
        "get": f"GEO_ID,NAME,{variable_code.upper()}",
        "key": api_key,
        **geo_params,
    }

    resp = _get(client, url, params)
    for delay in _RETRY_DELAYS:
        if resp.status_code < 500:
            break
        logger.warning(
            "acs: retrying %s year=%s after status=%s (sleep %ss)",
            variable_code,
            year,
            resp.status_code,
            delay,
        )
        time.sleep(delay)
        resp = _get(client, url, params)

    if resp.status_code >= 400:
        # Log the bare url, not resp.url — the latter carries the api_key in its query.
        logger.error(
            "acs: fetch failed for %s year=%s status=%s url=%s",
            variable_code,
            year,
            resp.status_code,
            url,
        )
        raise CensusAPIError(
            f"Census API returned {resp.status_code} for {variable_code} "
            f"year={year}: {resp.text[:200]}"
        )

    try:
        rows = resp.json()
    except json.JSONDecodeError as exc:
        raise CensusAPIError(
            f"Census API returned non-JSON body for {variable_code} year={year} "
            f"(check that CENSUS_API_KEY is valid): {resp.text[:200]}"
        ) from exc

    if not isinstance(rows, list) or len(rows) < 1 or not isinstance(rows[0], list):
        raise CensusAPIError(
            f"Unexpected response shape for {variable_code} year={year}: {str(rows)[:200]}"
        )

    headers = [h.lower() for h in rows[0]]
    col = variable_code.lower()

    records = []
    for row in rows[1:]:
        record = dict(zip(headers, row, strict=False))
        value, is_suppressed = _normalize_value(record.get(col))
        records.append(
            {
                "geo_id": record.get("geo_id"),
                "name": record.get("name"),
                "year": year,
                "state_fips": state_fips,
                "geography_level": geography_level,
                col: value,
                f"{col}_is_suppressed": is_suppressed,
            }
        )
    return records


def fetch_variable_dataframe(
    variable_code: str,
    geography_level: str,
    states: list[str],
    years: list[int],
    api_key: str,
    client: httpx.Client,
) -> pd.DataFrame:
    """Fetch one variable at one geography across all states and years.

    All states/years are gathered into a single DataFrame so the table is a
    complete snapshot. Column order is stable: identity columns first, then the
    typed value column and its suppression flag.

    Returns:
        A DataFrame with columns ``geo_id, name, year, state_fips,
        geography_level, <code>, <code>_is_suppressed`` (empty if no rows).
    """
    endpoint = endpoint_for_variable(variable_code)
    col = variable_code.lower()
    all_records: list[dict] = []

    for state in states:
        fips = state_to_fips(state)
        geo_params = build_geo_params(geography_level, fips)
        for year in years:
            logger.info(
                "acs: fetch %s year=%s geography=%s state=%s",
                variable_code,
                year,
                geography_level,
                state,
            )
            records = _fetch_acs_records(
                variable_code, geography_level, endpoint, year, geo_params, fips, api_key, client
            )
            all_records.extend(records)

    columns = [
        "geo_id",
        "name",
        "year",
        "state_fips",
        "geography_level",
        col,
        f"{col}_is_suppressed",
    ]
    return pd.DataFrame(all_records, columns=columns)
