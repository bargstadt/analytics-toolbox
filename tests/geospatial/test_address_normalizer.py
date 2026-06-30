import pandas as pd
import pytest

from analytics_toolbox.geospatial import normalize_addresses


@pytest.fixture
def sample_addresses() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Street_Address": "123 southwest Main street",
                "City": "Boring",
                "State": "or",
                "Postal_Code": "97203",
                "County": "Clackamas",
            },
            {
                "Street_Address": "PO Box 4521",
                "City": "Des Moines",
                "State": "IA",
                "Postal_Code": "50301",
                "County": "Polk",
            },
            {
                "Street_Address": "123 Main St",
                "City": "APO",
                "State": "AE",
                "Postal_Code": "09001",
                "County": None,
            },
            {
                "Street_Address": "456 Oak Rd",
                "City": "Anytown",
                "State": "IA",
                "Postal_Code": "123456789012",
                "County": "Polk",
            },
        ]
    )


def test_standard_address_is_normalized(sample_addresses: pd.DataFrame) -> None:
    result = normalize_addresses(sample_addresses)
    row = result.iloc[0]

    assert row["normalized_address_line_1"] == "123 SW MAIN ST"
    assert row["normalized_city"] == "BORING"
    assert row["normalized_state"] == "OR"
    assert row["is_standard_address"]
    assert row["address_flag"] == "standard"
    assert not row["is_military"]


def test_po_box_is_flagged_non_standard(sample_addresses: pd.DataFrame) -> None:
    result = normalize_addresses(sample_addresses)
    row = result.iloc[1]

    assert not row["is_standard_address"]
    assert row["address_flag"] == "UnParseableAddressError"
    assert pd.isna(row["normalized_address_line_1"])
    assert row["normalization_note"] is not None


def test_military_address_is_flagged(sample_addresses: pd.DataFrame) -> None:
    result = normalize_addresses(sample_addresses)
    row = result.iloc[2]

    assert row["is_military"]
    assert row["address_flag"] == "military"
    assert not row["is_standard_address"]
    # scourgify still successfully parses the street line for military
    # addresses — only the military flag marks it non-standard.
    assert row["normalized_address_line_1"] == "123 MAIN ST"


def test_invalid_postal_code_is_flagged(sample_addresses: pd.DataFrame) -> None:
    result = normalize_addresses(sample_addresses)
    row = result.iloc[3]

    assert not row["is_standard_address"]
    assert row["address_flag"] == "AddressValidationError"


def test_original_columns_and_index_are_preserved(sample_addresses: pd.DataFrame) -> None:
    weird_index = sample_addresses.set_axis([10, 20, 30, 40])
    result = normalize_addresses(weird_index)

    assert list(result.index) == [10, 20, 30, 40]
    assert result["County"].tolist()[:2] == ["Clackamas", "Polk"]
    assert pd.isna(result["County"].iloc[2])
    assert result["County"].iloc[3] == "Polk"


def test_missing_required_column_raises() -> None:
    bad_input = pd.DataFrame([{"Street_Address": "123 Main St"}])

    with pytest.raises(ValueError, match="Missing required column"):
        normalize_addresses(bad_input)
