"""Tests for fixtures/medicaid.py — verifies edge cases are seeded correctly."""

import pandas as pd
import pytest

from analytics_toolbox.feature_engineering.fixtures.medicaid import (
    FIXTURE_MEMBERS,
    make_medicaid_fixture,
    make_spine,
)

# Canonical as-of date used to define boundary claims in the fixture
AS_OF = pd.Timestamp("2024-06-30")


@pytest.fixture
def seeded_con(con):
    make_medicaid_fixture(con, seed=42)
    return con


# ---------------------------------------------------------------------------
# Table presence and schema
# ---------------------------------------------------------------------------

class TestFixtureTables:
    def test_members_table_exists(self, seeded_con):
        result = seeded_con.execute("SELECT COUNT(*) FROM members").fetchone()[0]
        assert result >= 1

    def test_rx_claims_table_exists(self, seeded_con):
        result = seeded_con.execute("SELECT COUNT(*) FROM rx_claims").fetchone()[0]
        assert result >= 1

    def test_med_claims_table_exists(self, seeded_con):
        result = seeded_con.execute("SELECT COUNT(*) FROM med_claims").fetchone()[0]
        assert result >= 1

    def test_members_schema(self, seeded_con):
        cols = {row[0] for row in seeded_con.execute("DESCRIBE members").fetchall()}
        assert {"member_id", "dob", "sex", "eligibility_category", "county_fips",
                "enroll_start", "enroll_end"}.issubset(cols)

    def test_rx_claims_schema(self, seeded_con):
        cols = {row[0] for row in seeded_con.execute("DESCRIBE rx_claims").fetchall()}
        assert {"claim_id", "member_id", "claim_date", "ndc_code", "drug_class",
                "paid_amount", "days_supply", "quantity"}.issubset(cols)

    def test_med_claims_schema(self, seeded_con):
        cols = {row[0] for row in seeded_con.execute("DESCRIBE med_claims").fetchall()}
        assert {"claim_id", "member_id", "claim_date", "place_of_service",
                "provider_type", "dx_primary", "paid_amount", "claim_type"}.issubset(cols)


# ---------------------------------------------------------------------------
# FIXTURE_MEMBERS constants
# ---------------------------------------------------------------------------

class TestFixtureMembers:
    def test_all_roles_present(self):
        for role in ("zero_claims", "on_as_of_boundary", "lower_boundary",
                     "high_volume", "multi_snapshot"):
            assert role in FIXTURE_MEMBERS, f"FIXTURE_MEMBERS missing role {role!r}"

    def test_all_member_ids_exist_in_members_table(self, seeded_con):
        ids = list(FIXTURE_MEMBERS.values())
        placeholders = ", ".join(str(i) for i in ids)
        count = seeded_con.execute(
            f"SELECT COUNT(*) FROM members WHERE member_id IN ({placeholders})"
        ).fetchone()[0]
        assert count == len(ids)


# ---------------------------------------------------------------------------
# Edge case: zero-claims member
# ---------------------------------------------------------------------------

class TestZeroClaimsMember:
    def test_zero_rx_claims(self, seeded_con):
        mid = FIXTURE_MEMBERS["zero_claims"]
        count = seeded_con.execute(
            f"SELECT COUNT(*) FROM rx_claims WHERE member_id = {mid}"
        ).fetchone()[0]
        assert count == 0

    def test_zero_med_claims(self, seeded_con):
        mid = FIXTURE_MEMBERS["zero_claims"]
        count = seeded_con.execute(
            f"SELECT COUNT(*) FROM med_claims WHERE member_id = {mid}"
        ).fetchone()[0]
        assert count == 0


# ---------------------------------------------------------------------------
# Edge case: claim on the as-of boundary (must be excluded from all windows)
# ---------------------------------------------------------------------------

class TestOnAsOfBoundary:
    def test_rx_claim_exists_on_as_of_date(self, seeded_con):
        mid = FIXTURE_MEMBERS["on_as_of_boundary"]
        count = seeded_con.execute(
            f"SELECT COUNT(*) FROM rx_claims "
            f"WHERE member_id = {mid} AND claim_date = '{AS_OF.date()}'"
        ).fetchone()[0]
        assert count >= 1, "Fixture must have a claim on the as-of date for exclusion testing"


# ---------------------------------------------------------------------------
# Edge case: lower-boundary claim (must appear at window edge)
# ---------------------------------------------------------------------------

class TestLowerBoundaryMember:
    def test_has_claim_exactly_at_30d_boundary(self, seeded_con):
        mid = FIXTURE_MEMBERS["lower_boundary"]
        boundary = (AS_OF - pd.Timedelta(days=30)).date()
        count = seeded_con.execute(
            f"SELECT COUNT(*) FROM rx_claims "
            f"WHERE member_id = {mid} AND claim_date = '{boundary}'"
        ).fetchone()[0]
        assert count >= 1, (
            f"Fixture must have a claim on {boundary} (as_of - 30d) for boundary testing"
        )

    def test_has_claim_one_day_before_30d_boundary(self, seeded_con):
        mid = FIXTURE_MEMBERS["lower_boundary"]
        outside = (AS_OF - pd.Timedelta(days=31)).date()
        count = seeded_con.execute(
            f"SELECT COUNT(*) FROM rx_claims "
            f"WHERE member_id = {mid} AND claim_date = '{outside}'"
        ).fetchone()[0]
        assert count >= 1, (
            f"Fixture must have a claim on {outside} (as_of - 31d) for exclusion testing"
        )


# ---------------------------------------------------------------------------
# Edge case: high-volume member
# ---------------------------------------------------------------------------

class TestHighVolumeMember:
    def test_has_many_claims(self, seeded_con):
        mid = FIXTURE_MEMBERS["high_volume"]
        count = seeded_con.execute(
            f"SELECT COUNT(*) FROM rx_claims WHERE member_id = {mid}"
        ).fetchone()[0]
        assert count >= 500


# ---------------------------------------------------------------------------
# make_spine
# ---------------------------------------------------------------------------

class TestMakeSpine:
    def test_single_as_of_all_members(self, seeded_con):
        spine = make_spine(seeded_con, as_of_dates=[AS_OF])
        assert "member_id" in spine.columns
        assert "as_of_date" in spine.columns
        n_members = seeded_con.execute("SELECT COUNT(*) FROM members").fetchone()[0]
        assert len(spine) == n_members

    def test_multiple_as_of_dates_fan_out(self, seeded_con):
        dates = [AS_OF, AS_OF - pd.Timedelta(days=30)]
        spine = make_spine(seeded_con, as_of_dates=dates)
        n_members = seeded_con.execute("SELECT COUNT(*) FROM members").fetchone()[0]
        assert len(spine) == n_members * 2

    def test_subset_of_members(self, seeded_con):
        ids = [FIXTURE_MEMBERS["zero_claims"], FIXTURE_MEMBERS["high_volume"]]
        spine = make_spine(seeded_con, as_of_dates=[AS_OF], member_ids=ids)
        assert len(spine) == 2
        assert set(spine["member_id"].tolist()) == set(ids)

    def test_deterministic(self, seeded_con):
        s1 = make_spine(seeded_con, as_of_dates=[AS_OF])
        s2 = make_spine(seeded_con, as_of_dates=[AS_OF])
        pd.testing.assert_frame_equal(s1.reset_index(drop=True), s2.reset_index(drop=True))
