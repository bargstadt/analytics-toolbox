"""Tests for the Census API fetch layer."""

from __future__ import annotations

import httpx
import pytest

from analytics_toolbox.acs import _census_api
from analytics_toolbox.acs._census_api import (
    CensusAPIError,
    _fetch_acs_records,
    _normalize_value,
    fetch_variable_dataframe,
    raw_table_name,
)
from analytics_toolbox.acs._variable_history import endpoint_for_variable


class TestRawTableName:
    def test_lowercases_and_joins(self):
        assert raw_table_name("B01001_001E", "block_group") == "acs_b01001_001e_block_group"


class TestNormalizeValue:
    def test_none(self):
        assert _normalize_value(None) == (None, False)

    @pytest.mark.parametrize("sentinel", ["-999", "-888", "-666"])
    def test_suppression_sentinels(self, sentinel):
        assert _normalize_value(sentinel) == (None, True)

    def test_int(self):
        assert _normalize_value("1234") == (1234, False)

    def test_float(self):
        assert _normalize_value("12.5") == (12.5, False)

    def test_non_numeric_string(self):
        assert _normalize_value("Block Group 1") == ("Block Group 1", False)


class TestFetchAcsRecords:
    def test_parses_rows_and_suppression(self, census_client: httpx.Client):
        records = _fetch_acs_records(
            "B01001_001E",
            "block_group",
            endpoint_for_variable("B01001_001E"),
            2021,
            {"for": "block group:*", "in": "state:19 county:* tract:*"},
            "19",
            "test-key",
            census_client,
        )
        assert len(records) == 2
        first, second = records
        assert first["b01001_001e"] == 1234
        assert first["b01001_001e_is_suppressed"] is False
        assert first["year"] == 2021
        assert first["state_fips"] == "19"
        assert first["geography_level"] == "block_group"
        # Second row carries the suppression sentinel.
        assert second["b01001_001e"] is None
        assert second["b01001_001e_is_suppressed"] is True

    def test_http_error_raises(self):
        def handler(request):
            return httpx.Response(400, text="bad request")

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with pytest.raises(CensusAPIError, match="returned 400"):
            _fetch_acs_records(
                "B01001_001E", "tract", "/acs/acs5", 2021, {"for": "x"}, "19", "k", client
            )

    def test_retries_on_5xx_then_succeeds(self, monkeypatch):
        monkeypatch.setattr(_census_api.time, "sleep", lambda *_: None)
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(503, text="unavailable")
            return httpx.Response(200, json=[["GEO_ID", "NAME", "B01001_001E"], ["g", "n", "5"]])

        client = httpx.Client(transport=httpx.MockTransport(handler))
        records = _fetch_acs_records(
            "B01001_001E", "tract", "/acs/acs5", 2021, {"for": "x"}, "19", "k", client
        )
        assert calls["n"] == 2
        assert records[0]["b01001_001e"] == 5

    def test_non_json_body_raises(self):
        def handler(request):
            return httpx.Response(200, text="<html>not json</html>")

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with pytest.raises(CensusAPIError, match="non-JSON"):
            _fetch_acs_records(
                "B01001_001E", "tract", "/acs/acs5", 2021, {"for": "x"}, "19", "k", client
            )

    def test_transport_error_does_not_leak_api_key(self):
        # httpx exceptions embed the key-bearing request URL; the error must not.
        def handler(request):
            raise httpx.ConnectError("connection refused")

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with pytest.raises(CensusAPIError) as exc:
            _fetch_acs_records(
                "B01001_001E",
                "tract",
                "/acs/acs5",
                2021,
                {"for": "x"},
                "19",
                "SUPERSECRETKEY",
                client,
            )
        assert "SUPERSECRETKEY" not in str(exc.value)


class TestFetchVariableDataframe:
    def test_shape_and_columns(self, census_client: httpx.Client):
        df = fetch_variable_dataframe(
            "B01001_001E", "block_group", ["IA"], [2020, 2021], "test-key", census_client
        )
        # 2 rows per (state, year) x 2 years = 4 rows
        assert len(df) == 4
        assert list(df.columns) == [
            "geo_id",
            "name",
            "year",
            "state_fips",
            "geography_level",
            "b01001_001e",
            "b01001_001e_is_suppressed",
        ]
        assert set(df["year"]) == {2020, 2021}
        assert df["b01001_001e_is_suppressed"].sum() == 2  # one suppressed row per year
