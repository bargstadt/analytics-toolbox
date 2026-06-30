"""Shared fixtures for acs tests: a mocked Census API over httpx.MockTransport.

No network and no extra dependency — the production code already accepts an
``httpx.Client``, so tests inject one backed by a MockTransport handler that
emulates the two Census endpoints used: ``variables.json`` (metadata, drives the
year walk) and the data endpoint (the actual estimates).
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest

# The mocked variable is "valid" (stable label) for 2019-2021. Years >= 2022 are
# "not yet published" (404); 2018 and earlier have a changed label (series break).
VALID_YEARS = [2019, 2020, 2021]
_STABLE_LABEL = "Estimate!!Total:"
_CHANGED_LABEL = "Estimate!!Total (REDEFINED):"


def _variables_response(year: int) -> httpx.Response:
    if year >= 2022:
        return httpx.Response(404, json={"error": "not published"})
    label = _STABLE_LABEL if year in VALID_YEARS else _CHANGED_LABEL
    return httpx.Response(
        200,
        json={
            "variables": {
                "B01001_001E": {"label": label},
                "S1701_C03_001E": {"label": label},
            }
        },
    )


def _data_response(request: httpx.Request) -> httpx.Response:
    # get=GEO_ID,NAME,<VAR> — the variable is the last requested field.
    var = request.url.params.get("get", "GEO_ID,NAME,B01001_001E").split(",")[-1]
    rows = [
        ["GEO_ID", "NAME", var, "state", "county", "tract", "block group"],
        ["1500000US190010001001", "Block Group 1", "1234", "19", "001", "000100", "1"],
        # A suppressed cell (sentinel) to exercise suppression handling.
        ["1500000US190010001002", "Block Group 2", "-666", "19", "001", "000100", "2"],
    ]
    return httpx.Response(200, json=rows)


def census_handler(request: httpx.Request) -> httpx.Response:
    """Route a mocked Census API request to metadata or data.

    Paths look like ``/data/<year>/acs/acs5[/subject|/profile][/variables.json]``.
    """
    parts = request.url.path.strip("/").split("/")
    year = int(next(p for p in parts if p.isdigit()))
    if request.url.path.endswith("variables.json"):
        return _variables_response(year)
    return _data_response(request)


@pytest.fixture
def valid_years() -> list[int]:
    """The years the mocked variable is valid for (stable label)."""
    return list(VALID_YEARS)


@pytest.fixture
def census_client() -> Iterator[httpx.Client]:
    """An httpx.Client whose transport emulates the Census API."""
    with httpx.Client(transport=httpx.MockTransport(census_handler)) as client:
        yield client


@pytest.fixture(autouse=True)
def _clear_metadata_cache():
    """Reset the process-level variables-metadata cache between tests."""
    from analytics_toolbox.acs import _variable_history

    _variable_history._VARIABLES_CACHE.clear()
    yield
    _variable_history._VARIABLES_CACHE.clear()
