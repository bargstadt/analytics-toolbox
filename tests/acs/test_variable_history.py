"""Tests for ACS variable year-range resolution."""

from __future__ import annotations

import httpx
import pytest

from analytics_toolbox.acs._errors import CensusAPIError
from analytics_toolbox.acs._variable_history import (
    _fetch_variables_json,
    endpoint_for_variable,
    resolve_valid_years,
)


class TestEndpointForVariable:
    def test_detailed_table(self):
        assert endpoint_for_variable("B01001_001E") == "/acs/acs5"

    def test_subject_table(self):
        assert endpoint_for_variable("S1701_C03_001E") == "/acs/acs5/subject"

    def test_data_profile(self):
        assert endpoint_for_variable("DP05_0001E") == "/acs/acs5/profile"


class TestResolveValidYears:
    def test_resolves_stable_label_window(
        self, census_client: httpx.Client, valid_years: list[int]
    ):
        years = resolve_valid_years("B01001_001E", "test-key", census_client)
        assert years == valid_years  # ascending, stops at the label change

    def test_absent_variable_returns_empty(self):
        def handler(request):
            # Always 200 but the variable is never present.
            return httpx.Response(200, json={"variables": {}})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        assert resolve_valid_years("B99999_999E", "test-key", client) == []


class TestMetadataKeyHygiene:
    def test_http_error_does_not_leak_api_key(self):
        def handler(request):
            return httpx.Response(403, text="forbidden")

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with pytest.raises(CensusAPIError) as exc:
            _fetch_variables_json(2021, "/acs/acs5", "SUPERSECRETKEY", client)
        assert "SUPERSECRETKEY" not in str(exc.value)

    def test_transport_error_does_not_leak_api_key(self):
        def handler(request):
            raise httpx.ConnectError("connection refused")

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with pytest.raises(CensusAPIError) as exc:
            _fetch_variables_json(2021, "/acs/acs5", "SUPERSECRETKEY", client)
        assert "SUPERSECRETKEY" not in str(exc.value)
