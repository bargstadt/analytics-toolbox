"""Resolve the valid year range for an ACS variable by comparing label metadata.

An ACS variable code is only comparable across years while its label is stable;
when the Census Bureau redefines a code the label changes, and earlier years are a
different series. ``resolve_valid_years`` walks backward from the most recent
release and stops at the first label change, so each variable pulls only the span
of years it is consistent over.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import httpx

from analytics_toolbox.acs._errors import CensusAPIError, YearNotAvailableError

logger = logging.getLogger(__name__)

CENSUS_BASE_URL = "https://api.census.gov/data"
EARLIEST_ACS5_YEAR = 2009
# Walk back from one year before the current year — the most recently released
# ACS 5-year estimates lag about 12 months behind the calendar year.
CENSUS_BASE_YEAR: int = datetime.now().year - 1

# Endpoint suffix routed by variable code prefix.
_ENDPOINT_FOR_PREFIX: dict[str, str] = {
    "DP": "/acs/acs5/profile",
    "S": "/acs/acs5/subject",
}
_ENDPOINT_DEFAULT = "/acs/acs5"

# Module-level cache: (year, endpoint_suffix) -> variables dict. Avoids re-fetching
# the (large) variables metadata within a single process.
_VARIABLES_CACHE: dict[tuple[int, str], dict] = {}


def endpoint_for_variable(variable_code: str) -> str:
    """Return the ACS endpoint suffix for a variable code based on its prefix."""
    upper = variable_code.upper()
    for prefix, endpoint in _ENDPOINT_FOR_PREFIX.items():
        if upper.startswith(prefix):
            return endpoint
    return _ENDPOINT_DEFAULT


def _disk_cache_path(cache_dir: Path, year: int, endpoint: str) -> Path:
    slug = endpoint.lstrip("/").replace("/", "_")
    return cache_dir / str(year) / f"{slug}.json"


def _fetch_variables_json(
    year: int,
    endpoint: str,
    api_key: str,
    client: httpx.Client,
    cache_dir: Path | None = None,
) -> dict:
    """Fetch and cache the variables metadata for a year and endpoint.

    Caches in memory for the process, and on disk under ``cache_dir`` when given
    (the metadata is large — caching avoids re-downloading it across runs).

    Raises:
        YearNotAvailableError: If the Census API returns 404 (year not published).
        CensusAPIError: For a transport failure or other non-200 response. Messages
            are built from the bare ``url`` and status only — never from httpx's
            text, which would carry the ``?key=`` API key.
    """
    cache_key = (year, endpoint)
    if cache_key in _VARIABLES_CACHE:
        return _VARIABLES_CACHE[cache_key]

    if cache_dir is not None:
        disk_path = _disk_cache_path(cache_dir, year, endpoint)
        if disk_path.exists():
            try:
                data = json.loads(disk_path.read_text())
                _VARIABLES_CACHE[cache_key] = data
                return data
            except (json.JSONDecodeError, OSError):
                pass  # corrupt or unreadable — fall through to a network request

    url = f"{CENSUS_BASE_URL}/{year}{endpoint}/variables.json"
    logger.debug("acs: fetching variable metadata year=%s endpoint=%s", year, endpoint)
    try:
        resp = client.get(url, params={"key": api_key}, timeout=30)
    except httpx.HTTPError as exc:
        # httpx exceptions embed the key-bearing request URL — suppress and sanitize.
        raise CensusAPIError(
            f"Failed to reach the Census variables endpoint at {url}: {type(exc).__name__}"
        ) from None

    if resp.status_code == 404:
        raise YearNotAvailableError(
            f"Variables metadata not available for year {year} at {endpoint}"
        )
    if resp.status_code >= 400:
        raise CensusAPIError(
            f"Census variables endpoint returned {resp.status_code} for year={year} "
            f"at {endpoint} (check that CENSUS_API_KEY is valid)"
        )

    try:
        data = resp.json().get("variables", {})
    except json.JSONDecodeError as exc:
        # resp.text is the body (no key); safe to include a snippet.
        raise CensusAPIError(
            f"Census variables endpoint returned non-JSON for year={year} at {endpoint} "
            f"(check that CENSUS_API_KEY is valid): {resp.text[:200]}"
        ) from exc
    _VARIABLES_CACHE[cache_key] = data

    if cache_dir is not None:
        try:
            disk_path = _disk_cache_path(cache_dir, year, endpoint)
            disk_path.parent.mkdir(parents=True, exist_ok=True)
            disk_path.write_text(json.dumps(data))
        except OSError:
            pass  # disk cache is best-effort

    return data


def resolve_valid_years(
    variable_code: str,
    api_key: str,
    client: httpx.Client,
    cache_dir: Path | None = None,
) -> list[int]:
    """Return all consecutive years for which the variable's label is stable.

    Walks backward from ``CENSUS_BASE_YEAR`` to ``EARLIEST_ACS5_YEAR``, stopping at
    the first year where the label changes or the variable is absent. Each variable
    resolves independently.

    Returns:
        Years in ascending order (possibly empty if the variable is never found).
    """
    endpoint = endpoint_for_variable(variable_code)
    reference_label: str | None = None
    valid_years: list[int] = []

    for year in range(CENSUS_BASE_YEAR, EARLIEST_ACS5_YEAR - 1, -1):
        try:
            variables = _fetch_variables_json(year, endpoint, api_key, client, cache_dir)
        except YearNotAvailableError:
            logger.debug("acs: year %s not available for %s", year, variable_code)
            continue

        meta = variables.get(variable_code.upper()) or variables.get(variable_code)
        if meta is None:
            logger.debug("acs: variable %s absent in %s", variable_code, year)
            break

        label = meta.get("label", "")
        if reference_label is None:
            reference_label = label
        elif label != reference_label:
            logger.info("acs: label changed for %s at %s; stopping walk", variable_code, year)
            break

        valid_years.append(year)

    result = sorted(valid_years)
    span = f"{result[0]}-{result[-1]}" if result else "none"
    logger.info("acs: resolved %d year(s) for %s (%s)", len(result), variable_code, span)
    return result
