"""Integration tests — SPEC.md §11 scenarios against the Medicaid fixture.

Each class maps to one named scenario from the spec. All use the full fixture
so they exercise realistic data volume and variety, not toy edge cases.
"""

import warnings

import pandas as pd
import pytest

from analytics_toolbox.feature_engineering import Agg, Guardrails, compute_features, join_features
from analytics_toolbox.feature_engineering.examples.medicaid_features import (
    med_utilization_features,
    rx_spend_features,
)
from analytics_toolbox.feature_engineering.fixtures.medicaid import (
    FIXTURE_MEMBERS,
    make_medicaid_fixture,
    make_spine,
)

_AS_OF = pd.Timestamp("2024-06-30")
_GRAIN = ["member_id", "as_of_date"]


@pytest.fixture
def fcon(con):
    make_medicaid_fixture(con, seed=42)
    return con


@pytest.fixture
def spine(fcon):
    return make_spine(fcon, as_of_dates=[_AS_OF])


# ---------------------------------------------------------------------------
# §11.1 — Leakage: claim on the as-of date never contributes
# ---------------------------------------------------------------------------

class TestLeakage:
    def test_on_as_of_claim_excluded_from_all_windows(self, fcon, spine):
        """Member 2 has exactly one rx claim, dated on 2024-06-30.
        With exclusive upper bound it must not appear in any window."""
        result = rx_spend_features(spine, fcon, windows=(30, 90, 365))
        mid = FIXTURE_MEMBERS["on_as_of_boundary"]
        row = result[result["member_id"] == mid].iloc[0]
        for w in (30, 90, 365):
            assert pd.isna(row[f"rx__claims_cnt_{w}d"]), (
                f"Claim on as-of date should not appear in {w}d window"
            )

    def test_on_as_of_claim_excluded_from_all_window(self, fcon, spine):
        result = rx_spend_features(spine, fcon, windows=["all"])
        mid = FIXTURE_MEMBERS["on_as_of_boundary"]
        row = result[result["member_id"] == mid].iloc[0]
        assert pd.isna(row["rx__claims_cnt_all"])


# ---------------------------------------------------------------------------
# §11.2 — Lower boundary: as_of−N included; as_of−N−1 excluded
# ---------------------------------------------------------------------------

class TestLowerBoundary:
    def test_30d_boundary_claim_appears(self, fcon, spine):
        """Claim dated exactly as_of − 30d must appear in the 30d window."""
        result = rx_spend_features(spine, fcon, windows=(30,))
        mid = FIXTURE_MEMBERS["lower_boundary"]
        row = result[result["member_id"] == mid].iloc[0]
        assert row["rx__claims_cnt_30d"] == pytest.approx(1.0), (
            "Claim at as_of-30d should be included (lower bound is inclusive)"
        )

    def test_31d_claim_excluded_from_30d_window(self, fcon, spine):
        """Claim dated as_of − 31d must NOT appear in the 30d window."""
        result = rx_spend_features(spine, fcon, windows=(30, 90))
        mid = FIXTURE_MEMBERS["lower_boundary"]
        row = result[result["member_id"] == mid].iloc[0]
        # 30d: only the as_of-30d claim → count=1
        # 90d: both claims (as_of-30d and as_of-31d are both inside 90d) → count=2
        assert row["rx__claims_cnt_30d"] == pytest.approx(1.0)
        assert row["rx__claims_cnt_90d"] == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# §11.3 — Null-fill: zero-event entity returns null features, not zeros
# ---------------------------------------------------------------------------

class TestNullFill:
    def test_zero_claims_member_present_in_output(self, fcon, spine):
        result = rx_spend_features(spine, fcon)
        mid = FIXTURE_MEMBERS["zero_claims"]
        assert mid in result["member_id"].values

    def test_zero_claims_member_all_features_null(self, fcon, spine):
        result = rx_spend_features(spine, fcon, windows=(30, 90, 365))
        mid = FIXTURE_MEMBERS["zero_claims"]
        row = result[result["member_id"] == mid].iloc[0]
        feature_cols = [c for c in result.columns if c not in _GRAIN]
        for col in feature_cols:
            assert pd.isna(row[col]), f"{col} should be null for zero-event member, got {row[col]}"

    def test_null_not_zero(self, fcon, spine):
        result = rx_spend_features(spine, fcon, windows=(30,))
        mid = FIXTURE_MEMBERS["zero_claims"]
        row = result[result["member_id"] == mid].iloc[0]
        # Use pd.isna — DuckDB returns nullable Int64 so `!= 0` raises TypeError on pd.NA
        assert pd.isna(row["rx__claims_cnt_30d"]), (
            f"Expected null for zero-event member, got {row['rx__claims_cnt_30d']!r}"
        )


# ---------------------------------------------------------------------------
# §11.4 — Monotonicity: narrower windows ≤ wider windows for count-based aggs
# ---------------------------------------------------------------------------

class TestMonotonicity:
    def test_claims_cnt_monotonic_across_windows(self, fcon, spine):
        result = rx_spend_features(spine, fcon, windows=(30, 90, 365))
        for _, row in result.iterrows():
            cnt30 = row["rx__claims_cnt_30d"]
            cnt90 = row["rx__claims_cnt_90d"]
            cnt365 = row["rx__claims_cnt_365d"]
            if pd.notna(cnt30) and pd.notna(cnt90):
                assert cnt30 <= cnt90, f"member {row['member_id']}: cnt_30d > cnt_90d"
            if pd.notna(cnt90) and pd.notna(cnt365):
                assert cnt90 <= cnt365, f"member {row['member_id']}: cnt_90d > cnt_365d"


# ---------------------------------------------------------------------------
# §11.5 — Multi-as-of fan-out: per-snapshot evaluation
# ---------------------------------------------------------------------------

class TestMultiAsOf:
    def test_two_snapshots_give_two_rows_per_member(self, fcon):
        dates = [_AS_OF, _AS_OF - pd.Timedelta(days=90)]
        spine = make_spine(fcon, as_of_dates=dates,
                           member_ids=[FIXTURE_MEMBERS["multi_snapshot"]])
        result = rx_spend_features(spine, fcon, windows=["all"])
        mid = FIXTURE_MEMBERS["multi_snapshot"]
        assert len(result[result["member_id"] == mid]) == 2

    def test_claims_cnt_differs_between_snapshots(self, fcon):
        """Later snapshot has more history → higher claims_cnt_all."""
        later = _AS_OF
        earlier = _AS_OF - pd.Timedelta(days=90)
        mid = FIXTURE_MEMBERS["multi_snapshot"]
        spine = make_spine(fcon, as_of_dates=[later, earlier], member_ids=[mid])
        result = rx_spend_features(spine, fcon, windows=["all"])

        cnt_later = result[result["as_of_date"] == later]["rx__claims_cnt_all"].iloc[0]
        cnt_earlier = result[result["as_of_date"] == earlier]["rx__claims_cnt_all"].iloc[0]
        assert cnt_later > cnt_earlier, (
            "Later snapshot should include more history than earlier snapshot"
        )

    def test_event_only_in_correct_snapshot(self, fcon):
        """The on-as-of claim for member 2 at _AS_OF must not appear in the prior snapshot."""
        mid = FIXTURE_MEMBERS["on_as_of_boundary"]
        prior = _AS_OF - pd.Timedelta(days=1)
        spine = make_spine(fcon, as_of_dates=[_AS_OF, prior], member_ids=[mid])
        result = rx_spend_features(spine, fcon, windows=["all"])

        # At _AS_OF: claim is on the boundary → excluded → null
        at_asof = result[result["as_of_date"] == _AS_OF]["rx__claims_cnt_all"].iloc[0]
        assert pd.isna(at_asof)

        # At prior (_AS_OF - 1): the claim is also excluded (it's dated _AS_OF, not before _AS_OF-1)
        # So both should be null — what matters is the claim never leaks forward
        at_prior = result[result["as_of_date"] == prior]["rx__claims_cnt_all"].iloc[0]
        assert pd.isna(at_prior)


# ---------------------------------------------------------------------------
# §11.6 — Grain integrity: output row count == spine row count; grain unique
# ---------------------------------------------------------------------------

class TestGrainIntegrity:
    def test_row_count_equals_spine(self, fcon, spine):
        result = rx_spend_features(spine, fcon)
        assert len(result) == len(spine)

    def test_grain_is_unique(self, fcon, spine):
        result = rx_spend_features(spine, fcon)
        dupes = result.duplicated(subset=_GRAIN).sum()
        assert dupes == 0

    def test_grain_integrity_with_group_cols(self, fcon):
        """Drug-class spine: one row per (member × drug_class × as_of)."""
        drug_classes = ["analgesic", "cardiovascular"]
        members = fcon.execute("SELECT member_id FROM members").df()["member_id"].tolist()
        spine = pd.DataFrame([
            {"member_id": mid, "as_of_date": _AS_OF, "drug_class": dc}
            for mid in members for dc in drug_classes
        ])
        result = compute_features(
            spine, "rx_claims",
            entity_keys=["member_id"], as_of_col="as_of_date",
            base_date_col="claim_date", namespace="rx",
            aggregations=[Agg("claims_cnt", "COUNT(*)")],
            windows=[30], group_cols=["drug_class"], con=fcon,
        )
        assert len(result) == len(spine)
        assert result.duplicated(subset=["member_id", "drug_class", "as_of_date"]).sum() == 0


# ---------------------------------------------------------------------------
# §11.7 — window="all": unbounded history, exclusive upper bound still holds
# ---------------------------------------------------------------------------

class TestAllWindow:
    def test_all_includes_all_history(self, fcon, spine):
        result = rx_spend_features(spine, fcon, windows=["all"])
        mid = FIXTURE_MEMBERS["multi_snapshot"]
        row = result[result["member_id"] == mid].iloc[0]
        # 18 monthly claims, all before _AS_OF
        assert row["rx__claims_cnt_all"] == pytest.approx(18.0)

    def test_all_respects_exclusive_upper_bound(self, fcon, spine):
        result = rx_spend_features(spine, fcon, windows=["all"])
        mid = FIXTURE_MEMBERS["on_as_of_boundary"]
        row = result[result["member_id"] == mid].iloc[0]
        assert pd.isna(row["rx__claims_cnt_all"])


# ---------------------------------------------------------------------------
# §11.8 — Composition: join_features preserves grain; collision raises
# ---------------------------------------------------------------------------

class TestComposition:
    def test_join_preserves_grain(self, fcon, spine):
        rx = rx_spend_features(spine, fcon, windows=(30,))
        med = med_utilization_features(spine, fcon, windows=(30,))
        combined = join_features([rx, med], on=_GRAIN)
        assert len(combined) == len(spine)
        assert combined.duplicated(subset=_GRAIN).sum() == 0

    def test_join_has_all_feature_columns(self, fcon, spine):
        rx = rx_spend_features(spine, fcon, windows=(30,))
        med = med_utilization_features(spine, fcon, windows=(30,))
        combined = join_features([rx, med], on=_GRAIN)
        assert "rx__paid_sum_30d" in combined.columns
        assert "med__claims_cnt_30d" in combined.columns

    def test_join_collision_raises(self, fcon, spine):
        rx = rx_spend_features(spine, fcon, windows=(30,))
        # Create a second frame with a colliding non-grain column
        collider = rx.rename(columns={"rx__paid_sum_30d": "rx__paid_sum_30d"})
        with pytest.raises(ValueError, match="rx__paid_sum_30d"):
            join_features([rx, collider], on=_GRAIN)


# ---------------------------------------------------------------------------
# §11.9 — Guardrails: fan-out cap raise/warn; spine-uniqueness error
# ---------------------------------------------------------------------------

class TestGuardrails:
    # Call compute_features directly — the example wrappers don't expose guardrails.
    _aggs = [Agg("cnt", "COUNT(*)")]
    _kwargs = dict(
        entity_keys=["member_id"], as_of_col="as_of_date",
        base_date_col="claim_date", namespace="rx",
        aggregations=_aggs, windows=[30],
    )

    def test_fanout_cap_raises(self, fcon, spine):
        with pytest.raises(RuntimeError, match="fan-out"):
            compute_features(
                spine, "rx_claims", con=fcon,
                guardrails=Guardrails(max_fanout_rows=1),
                **self._kwargs,
            )

    def test_fanout_cap_warns(self, fcon, spine):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            compute_features(
                spine, "rx_claims", con=fcon,
                guardrails=Guardrails(max_fanout_rows=1, on_fanout_exceed="warn"),
                **self._kwargs,
            )
        assert any("fan-out" in str(w.message).lower() for w in caught)

    def test_fanout_cap_none_always_succeeds(self, fcon, spine):
        result = compute_features(
            spine, "rx_claims", con=fcon,
            guardrails=Guardrails(max_fanout_rows=None),
            **self._kwargs,
        )
        assert len(result) == len(spine)


# ---------------------------------------------------------------------------
# §11.10 — Spine uniqueness: duplicate rows raise with count
# ---------------------------------------------------------------------------

class TestSpineUniqueness:
    def test_duplicate_spine_raises_with_count(self, fcon):
        dup_spine = pd.DataFrame({
            "member_id": [1, 1, 2],
            "as_of_date": pd.to_datetime(["2024-06-30", "2024-06-30", "2024-06-30"]),
        })
        with pytest.raises(ValueError, match="1 duplicate"):
            compute_features(
                dup_spine, "rx_claims",
                entity_keys=["member_id"], as_of_col="as_of_date",
                base_date_col="claim_date", namespace="rx",
                aggregations=[Agg("cnt", "COUNT(*)")],
                windows=[30], con=fcon,
            )


# ---------------------------------------------------------------------------
# §11.11 — Missing column: names the offending column and which table
# ---------------------------------------------------------------------------

class TestMissingColumn:
    def test_missing_entity_key_on_base_names_column(self, fcon, spine):
        with pytest.raises(ValueError, match="nonexistent_key"):
            compute_features(
                spine, "rx_claims",
                entity_keys=["nonexistent_key"], as_of_col="as_of_date",
                base_date_col="claim_date", namespace="rx",
                aggregations=[Agg("cnt", "COUNT(*)")],
                windows=[30], con=fcon,
            )

    def test_missing_as_of_col_on_spine_names_column(self, fcon):
        bad_spine = pd.DataFrame({"member_id": [1, 2]})
        with pytest.raises(ValueError, match="wrong_col"):
            compute_features(
                bad_spine, "rx_claims",
                entity_keys=["member_id"], as_of_col="wrong_col",
                base_date_col="claim_date", namespace="rx",
                aggregations=[Agg("cnt", "COUNT(*)")],
                windows=[30], con=fcon,
            )
