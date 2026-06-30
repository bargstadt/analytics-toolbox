"""Smoke tests for examples/medicaid_features.py — verify grain and column names."""

import pandas as pd
import pytest

from analytics_toolbox.feature_engineering.examples.medicaid_features import (
    med_utilization_features,
    rx_spend_features,
    rx_spend_features_by_drug_class,
)
from analytics_toolbox.feature_engineering.fixtures.medicaid import (
    FIXTURE_MEMBERS,
    make_medicaid_fixture,
    make_spine,
)

_AS_OF = pd.Timestamp("2024-06-30")


@pytest.fixture
def seeded_con(con):
    make_medicaid_fixture(con, seed=42)
    return con


@pytest.fixture
def spine(seeded_con):
    return make_spine(seeded_con, as_of_dates=[_AS_OF])


@pytest.fixture
def drug_class_spine(seeded_con):
    """Spine cross-joined with drug classes for group_cols demonstration."""
    members = seeded_con.execute("SELECT member_id FROM members").df()
    drug_classes = ["analgesic", "antibiotic", "antidiabetic", "cardiovascular", "psychiatric"]
    rows = [
        {"member_id": mid, "as_of_date": _AS_OF, "drug_class": dc}
        for mid in members["member_id"]
        for dc in drug_classes
    ]
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# rx_spend_features
# ---------------------------------------------------------------------------

class TestRxSpendFeatures:
    def test_row_count_equals_spine(self, seeded_con, spine):
        result = rx_spend_features(spine, seeded_con)
        assert len(result) == len(spine)

    def test_expected_columns_present(self, seeded_con, spine):
        result = rx_spend_features(spine, seeded_con)
        for window in [30, 90, 365]:
            for agg in ["paid_sum", "claims_cnt", "ndc_ndist", "dsupply_sum"]:
                assert f"rx__{agg}_{window}d" in result.columns, \
                    f"Missing column rx__{agg}_{window}d"

    def test_spine_columns_preserved(self, seeded_con, spine):
        result = rx_spend_features(spine, seeded_con)
        assert "member_id" in result.columns
        assert "as_of_date" in result.columns

    def test_zero_claims_member_is_null(self, seeded_con, spine):
        result = rx_spend_features(spine, seeded_con)
        mid = FIXTURE_MEMBERS["zero_claims"]
        row = result[result["member_id"] == mid].iloc[0]
        assert pd.isna(row["rx__paid_sum_30d"])
        assert pd.isna(row["rx__claims_cnt_30d"])

    def test_custom_windows(self, seeded_con, spine):
        result = rx_spend_features(spine, seeded_con, windows=(7, 30))
        assert "rx__paid_sum_7d" in result.columns
        assert "rx__paid_sum_30d" in result.columns
        assert "rx__paid_sum_90d" not in result.columns

    def test_high_volume_member_has_claims(self, seeded_con, spine):
        result = rx_spend_features(spine, seeded_con, windows=(365,))
        mid = FIXTURE_MEMBERS["high_volume"]
        row = result[result["member_id"] == mid].iloc[0]
        assert row["rx__claims_cnt_365d"] > 0


# ---------------------------------------------------------------------------
# rx_spend_features_by_drug_class
# ---------------------------------------------------------------------------

class TestRxSpendFeaturesByDrugClass:
    def test_row_count_equals_spine(self, seeded_con, drug_class_spine):
        result = rx_spend_features_by_drug_class(drug_class_spine, seeded_con)
        assert len(result) == len(drug_class_spine)

    def test_expected_columns_present(self, seeded_con, drug_class_spine):
        result = rx_spend_features_by_drug_class(drug_class_spine, seeded_con)
        assert "rx__paid_sum_30d" in result.columns
        assert "drug_class" in result.columns

    def test_drug_class_in_output(self, seeded_con, drug_class_spine):
        result = rx_spend_features_by_drug_class(drug_class_spine, seeded_con)
        assert set(result["drug_class"].unique()) == {
            "analgesic", "antibiotic", "antidiabetic", "cardiovascular", "psychiatric"
        }


# ---------------------------------------------------------------------------
# med_utilization_features
# ---------------------------------------------------------------------------

class TestMedUtilizationFeatures:
    def test_row_count_equals_spine(self, seeded_con, spine):
        result = med_utilization_features(spine, seeded_con)
        assert len(result) == len(spine)

    def test_expected_columns_present(self, seeded_con, spine):
        result = med_utilization_features(spine, seeded_con)
        for window in [30, 90, 365]:
            for agg in [
                "claims_cnt", "paid_sum", "ed_visits_cnt", "inpatient_cnt", "provider_ndist"
            ]:
                assert f"med__{agg}_{window}d" in result.columns, \
                    f"Missing column med__{agg}_{window}d"

    def test_spine_columns_preserved(self, seeded_con, spine):
        result = med_utilization_features(spine, seeded_con)
        assert "member_id" in result.columns
        assert "as_of_date" in result.columns

    def test_ed_visits_non_negative(self, seeded_con, spine):
        result = med_utilization_features(spine, seeded_con)
        non_null = result["med__ed_visits_cnt_30d"].dropna()
        assert (non_null >= 0).all()

    def test_ed_visits_leq_total_claims(self, seeded_con, spine):
        result = med_utilization_features(spine, seeded_con)
        mask = result["med__claims_cnt_30d"].notna() & result["med__ed_visits_cnt_30d"].notna()
        subset = result[mask]
        assert (subset["med__ed_visits_cnt_30d"] <= subset["med__claims_cnt_30d"]).all()
