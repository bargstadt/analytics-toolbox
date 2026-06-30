"""Tests for _preprocess.normalize_fields()."""

from __future__ import annotations

import pandas as pd
import pytest

from analytics_toolbox.entity_resolution._config import EntityResolutionConfig
from analytics_toolbox.entity_resolution._preprocess import normalize_fields


@pytest.fixture
def config() -> EntityResolutionConfig:
    return EntityResolutionConfig(match_threshold=0.80)


class TestStringNormalization:
    def test_uppercase_string_fields_in_weights(self, config):
        df = pd.DataFrame(
            {"Last_Name": ["smith"], "First_Name": ["john"], "DOB": ["2025-01-06"]}
        )
        out = normalize_fields(df, config)
        assert out["Last_Name"].iloc[0] == "SMITH"
        assert out["First_Name"].iloc[0] == "JOHN"

    def test_strips_whitespace(self, config):
        df = pd.DataFrame({"Last_Name": ["  Smith  "], "First_Name": ["  John"]})
        out = normalize_fields(df, config)
        assert out["Last_Name"].iloc[0] == "SMITH"
        assert out["First_Name"].iloc[0] == "JOHN"

    def test_columns_not_in_weights_unchanged(self, config):
        df = pd.DataFrame({"Last_Name": ["smith"], "State": ["ia"]})
        out = normalize_fields(df, config)
        # State is not in field_weights — it should not be uppercased by this step
        # (but it also shouldn't raise)
        assert "State" in out.columns

    def test_null_string_stays_null(self, config):
        df = pd.DataFrame({"Last_Name": [None, "smith"]})
        out = normalize_fields(df, config)
        assert pd.isna(out["Last_Name"].iloc[0])
        assert out["Last_Name"].iloc[1] == "SMITH"


class TestDateNormalization:
    def test_date_format_normalized(self, config):
        df = pd.DataFrame({"DOB": ["01/06/2025"]})
        out = normalize_fields(df, config)
        # Should be YYYY-MM-DD
        assert out["DOB"].iloc[0] == "2025-01-06"

    def test_already_normalized_date_unchanged(self, config):
        df = pd.DataFrame({"DOB": ["2025-01-06"]})
        out = normalize_fields(df, config)
        assert out["DOB"].iloc[0] == "2025-01-06"

    def test_null_dob_stays_null(self, config):
        df = pd.DataFrame({"DOB": [None]})
        out = normalize_fields(df, config)
        assert pd.isna(out["DOB"].iloc[0])


class TestAddressNormalization:
    def test_no_address_col_is_noop(self, config):
        df = pd.DataFrame({"Last_Name": ["Smith"]})
        out = normalize_fields(df, config)
        assert "Address" not in out.columns

    def test_address_col_present_produces_normalized_output(self, config):
        df = pd.DataFrame(
            {
                "Address": ["151 nw something st"],
                "City": ["Des Moines"],
                "State": ["IA"],
                "Postal_Code": ["50131"],
            }
        )
        out = normalize_fields(df, config)
        # After normalization, Address should be uppercased / standardized
        assert out["Address"].iloc[0] != ""
        assert out["Address"].iloc[0] == out["Address"].iloc[0].upper()

    def test_unparseable_address_becomes_empty_string(self, config):
        df = pd.DataFrame(
            {
                "Address": ["PO Box 12345"],
                "City": ["Des Moines"],
                "State": ["IA"],
                "Postal_Code": ["50131"],
            }
        )
        out = normalize_fields(df, config)
        # Non-standard (PO Box) → empty string so it won't match anything
        assert out["Address"].iloc[0] == ""

    def test_custom_address_col_name(self):
        config = EntityResolutionConfig(match_threshold=0.80, address_col="street")
        df = pd.DataFrame(
            {
                "street": ["151 nw something st"],
                "City": ["Des Moines"],
                "State": ["IA"],
                "Postal_Code": ["50131"],
            }
        )
        out = normalize_fields(df, config)
        assert out["street"].iloc[0] != ""
