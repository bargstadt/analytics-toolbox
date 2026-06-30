"""Exceptions for the acs module.

Kept dependency-free and in their own module so both ``_census_api`` and
``_variable_history`` can raise them without an import cycle.
"""

from __future__ import annotations


class CensusAPIError(Exception):
    """Raised when the Census API returns an unexpected or error response.

    Messages are built from the bare endpoint URL and status only — never from
    httpx's exception text, which would carry the ``?key=`` API key in the URL.
    """


class YearNotAvailableError(Exception):
    """Raised when the Census API returns 404 for a year (not yet published)."""
