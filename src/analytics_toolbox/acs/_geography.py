"""Geography helpers: state FIPS codes and Census API for/in parameters."""

from __future__ import annotations

from analytics_toolbox._fips import STATE_FIPS

__all__ = ["STATE_FIPS", "state_to_fips", "build_geo_params"]

# Census API for/in parameter templates per geography level. Passed as separate
# keys to the HTTP client's params= (never concatenated) so spaces are
# percent-encoded correctly per parameter.
_GEO_PARAMS: dict[str, dict[str, str]] = {
    "block_group": {
        "for": "block group:*",
        "in": "state:{fips} county:* tract:*",
    },
    "tract": {
        "for": "tract:*",
        "in": "state:{fips} county:*",
    },
    "county": {
        "for": "county:*",
        "in": "state:{fips}",
    },
}


def state_to_fips(abbreviation: str) -> str:
    """Return the two-digit FIPS code for a state abbreviation.

    Args:
        abbreviation: USPS state abbreviation (case-insensitive), e.g. ``"IA"``.

    Returns:
        The two-digit state FIPS code, e.g. ``"19"``.

    Raises:
        KeyError: If the abbreviation is not recognised.
    """
    fips = STATE_FIPS.get(abbreviation.upper())
    if fips is None:
        raise KeyError(
            f"Unknown state abbreviation '{abbreviation}'. Supported: {sorted(STATE_FIPS)}"
        )
    return fips


def build_geo_params(geography_level: str, state_fips: str) -> dict[str, str]:
    """Return Census API for/in query params for a geography level and state FIPS.

    Args:
        geography_level: One of ``block_group``, ``tract``, ``county``.
        state_fips: Two-digit state FIPS code.

    Returns:
        A dict with separate ``for`` and ``in`` keys, ready to pass to the HTTP
        client's ``params=`` so each value is encoded correctly.

    Raises:
        KeyError: If ``geography_level`` is not supported.
    """
    template = _GEO_PARAMS[geography_level]
    return {k: v.format(fips=state_fips) for k, v in template.items()}
