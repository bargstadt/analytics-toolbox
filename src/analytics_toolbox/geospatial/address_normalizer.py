"""Offline address normalization.

Standardizes street address formatting per USPS Publication 28 using
usaddress-scourgify, and flags addresses that can't be trusted for a
precise street-level match (military addresses, PO boxes, anything
scourgify can't parse) so downstream matching and geocoding steps know to
fall back to a postal-code centroid instead.

Privacy: this module is fully offline. Only ``normalize_address_record``
is imported from scourgify. Do NOT import or call
``scourgify.get_geocoder_normalized_addr`` — that function calls Google's
geocoder API and would send address data to a third party, which this
pipeline must never do. scourgify imports the ``geocoder`` package
unconditionally at module load time regardless of whether that function
is used; ``analytics_toolbox.geospatial``'s ``__init__.py`` stubs
``geocoder`` out via ``_scourgify_compat`` before this module (or any
other in this package) ever imports scourgify — see that module for why.
"""

from __future__ import annotations

import pandas as pd
from scourgify import normalize_address_record
from scourgify.exceptions import AddressNormalizationError

MILITARY_STATE_CODES = {"AA", "AE", "AP"}
MILITARY_CITY_VALUES = {"APO", "FPO", "DPO"}

NORMALIZED_FIELDS = ("address_line_1", "address_line_2", "city", "state", "postal_code")


def normalize_addresses(
    addresses: pd.DataFrame,
    *,
    street_address_col: str = "Street_Address",
    city_col: str = "City",
    state_col: str = "State",
    postal_code_col: str = "Postal_Code",
) -> pd.DataFrame:
    """Normalize a table of distinct addresses.

    Args:
        addresses: One row per distinct address. Any column not listed
            below (e.g. County) is passed through untouched — scourgify
            has no concept of county, so this module doesn't either.
        street_address_col: Column holding the unparsed street line.
        city_col: Column holding the city.
        state_col: Column holding the state abbreviation.
        postal_code_col: Column holding the ZIP/postal code.

    Returns:
        A copy of ``addresses`` with these columns appended:

        - ``normalized_address_line_1`` / ``_line_2``: standardized street
          line and secondary unit, USPS-abbreviated (e.g. "ST", "APT").
          None if normalization failed.
        - ``normalized_city`` / ``_state`` / ``_postal_code``: standardized
          city, state, and zero-padded ZIP/ZIP+4. None if normalization
          failed.
        - ``is_military``: True if city is APO/FPO/DPO or state is
          AA/AE/AP. Determined from the raw input, independent of whether
          scourgify could parse the street line, since scourgify does not
          flag military addresses on its own.
        - ``address_flag``: ``"standard"``, ``"military"``, or the
          scourgify exception name (``"UnParseableAddressError"``,
          ``"IncompleteAddressError"``, ``"AddressValidationError"``,
          ``"AmbiguousAddressError"``) when normalization failed. PO boxes
          reliably surface as ``UnParseableAddressError``.
        - ``is_standard_address``: True only when ``address_flag`` is
          ``"standard"``. False here means: don't attempt a precise
          street-level match downstream — use the postal-code centroid.
        - ``normalization_note``: the scourgify error message, for
          troubleshooting. None when normalization succeeded.

    Raises:
        ValueError: if any of the required columns are missing.
    """
    required = {street_address_col, city_col, state_col, postal_code_col}
    missing = required - set(addresses.columns)
    if missing:
        raise ValueError(f"Missing required column(s): {sorted(missing)}")

    addr_map = {
        "address_line_1": street_address_col,
        "city": city_col,
        "state": state_col,
        "postal_code": postal_code_col,
    }

    normalized_rows = [
        _normalize_one(record, addr_map, city_col, state_col)
        for record in addresses.to_dict("records")
    ]

    return pd.concat(
        [addresses.reset_index(drop=True), pd.DataFrame(normalized_rows)],
        axis=1,
    ).set_axis(addresses.index)


def _normalize_one(record: dict, addr_map: dict[str, str], city_col: str, state_col: str) -> dict:
    """Normalize a single address record. See ``normalize_addresses`` for field meanings."""
    raw_city = str(record.get(city_col) or "").strip().upper()
    raw_state = str(record.get(state_col) or "").strip().upper()
    is_military = raw_city in MILITARY_CITY_VALUES or raw_state in MILITARY_STATE_CODES

    try:
        normalized = normalize_address_record(record, addr_map=addr_map)
        scourgify_flag, note = "standard", None
    except AddressNormalizationError as exc:
        normalized = dict.fromkeys(NORMALIZED_FIELDS)
        scourgify_flag, note = type(exc).__name__, str(exc)

    address_flag = "military" if (is_military and scourgify_flag == "standard") else scourgify_flag

    return {
        "normalized_address_line_1": normalized.get("address_line_1"),
        "normalized_address_line_2": normalized.get("address_line_2"),
        "normalized_city": normalized.get("city"),
        "normalized_state": normalized.get("state"),
        "normalized_postal_code": normalized.get("postal_code"),
        "is_military": is_military,
        "address_flag": address_flag,
        "is_standard_address": address_flag == "standard",
        "normalization_note": note,
    }
