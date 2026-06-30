"""Tests for ACS geography helpers."""

from __future__ import annotations

import pytest

from analytics_toolbox.acs._geography import STATE_FIPS, build_geo_params, state_to_fips


class TestStateToFips:
    def test_known_state(self):
        assert state_to_fips("IA") == "19"

    def test_case_insensitive(self):
        assert state_to_fips("ia") == "19"

    def test_unknown_state_raises(self):
        with pytest.raises(KeyError, match="Unknown state"):
            state_to_fips("ZZ")

    def test_all_fips_two_digits(self):
        assert all(len(v) == 2 and v.isdigit() for v in STATE_FIPS.values())


class TestBuildGeoParams:
    def test_block_group(self):
        params = build_geo_params("block_group", "19")
        assert params == {"for": "block group:*", "in": "state:19 county:* tract:*"}

    def test_tract(self):
        params = build_geo_params("tract", "19")
        assert params == {"for": "tract:*", "in": "state:19 county:*"}

    def test_county(self):
        params = build_geo_params("county", "19")
        assert params == {"for": "county:*", "in": "state:19"}

    def test_unknown_geography_raises(self):
        with pytest.raises(KeyError):
            build_geo_params("zip", "19")
