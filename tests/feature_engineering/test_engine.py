"""Tests for engine.py — compute_features() integration against in-memory DuckDB."""

import warnings

import pandas as pd
import pytest

from analytics_toolbox.feature_engineering import Agg, Guardrails, compute_features

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def spine_df():
    """3-member spine at a single as-of date."""
    return pd.DataFrame({
        "member_id": [1, 2, 3],
        "as_of_date": pd.to_datetime(["2024-01-31"] * 3),
    })


@pytest.fixture
def base_df():
    """
    Claims designed to test every boundary condition.

    as_of = 2024-01-31
    30d window = [2024-01-01, 2024-01-31)   (as_of - 30d = 2024-01-01)
    90d window = [2023-11-02, 2024-01-31)   (as_of - 90d = 2023-11-02)

    member 1:
      - 2024-01-15  $100  → inside both windows
      - 2024-01-31  $200  → ON the as_of date → must be excluded (upper bound exclusive)

    member 2:
      - 2023-12-01  $50   → inside 90d, outside 30d  (as_of-90d ≤ 2023-12-01 < as_of-30d)
      - 2024-01-10  $75   → inside both windows

    member 3: no claims → all-null features
    """
    return pd.DataFrame({
        "member_id": [1, 1, 2, 2],
        "claim_date": pd.to_datetime([
            "2024-01-15",   # m1 — inside both
            "2024-01-31",   # m1 — on as_of, excluded
            "2023-12-01",   # m2 — inside 90d only
            "2024-01-10",   # m2 — inside both
        ]),
        "paid_amount": [100.0, 200.0, 50.0, 75.0],
    })


_AGGS = [Agg("paid_sum", "SUM(paid_amount)"), Agg("claims_cnt", "COUNT(*)")]
_WINDOWS = [30, 90]


def _run(spine, base, con, **overrides):
    params = dict(
        entity_keys=["member_id"],
        as_of_col="as_of_date",
        base_date_col="claim_date",
        namespace="rx",
        aggregations=_AGGS,
        windows=_WINDOWS,
        con=con,
    )
    params.update(overrides)
    return compute_features(spine, base, **params)


def _member(result: pd.DataFrame, member_id: int) -> pd.Series:
    return result[result["member_id"] == member_id].iloc[0]


# ---------------------------------------------------------------------------
# Output shape / spine preservation
# ---------------------------------------------------------------------------

class TestOutputShape:
    def test_returns_dataframe(self, con, spine_df, base_df):
        assert isinstance(_run(spine_df, base_df, con), pd.DataFrame)

    def test_row_count_equals_spine(self, con, spine_df, base_df):
        result = _run(spine_df, base_df, con)
        assert len(result) == len(spine_df)

    def test_spine_columns_preserved(self, con, spine_df, base_df):
        result = _run(spine_df, base_df, con)
        assert "member_id" in result.columns
        assert "as_of_date" in result.columns

    def test_feature_columns_present(self, con, spine_df, base_df):
        result = _run(spine_df, base_df, con)
        expected = {
            "rx__paid_sum_30d", "rx__paid_sum_90d", "rx__claims_cnt_30d", "rx__claims_cnt_90d"
        }
        assert expected.issubset(result.columns)


# ---------------------------------------------------------------------------
# Leakage and boundary correctness
# ---------------------------------------------------------------------------

class TestLeakageAndBoundary:
    def test_claim_on_as_of_date_excluded(self, con, spine_df, base_df):
        # m1: only the 2024-01-15 claim ($100) should appear;
        # the 2024-01-31 claim ($200) is excluded
        result = _run(spine_df, base_df, con)
        m1 = _member(result, 1)
        assert m1["rx__paid_sum_30d"] == pytest.approx(100.0)
        assert m1["rx__claims_cnt_30d"] == pytest.approx(1.0)

    def test_claim_inside_both_windows(self, con, spine_df, base_df):
        # m2's 2024-01-10 claim is inside both 30d and 90d
        result = _run(spine_df, base_df, con)
        m2 = _member(result, 2)
        assert m2["rx__paid_sum_30d"] == pytest.approx(75.0)
        assert m2["rx__claims_cnt_30d"] == pytest.approx(1.0)

    def test_claim_inside_90d_not_30d(self, con, spine_df, base_df):
        # m2's 2023-12-01 claim is inside 90d but outside 30d
        result = _run(spine_df, base_df, con)
        m2 = _member(result, 2)
        assert m2["rx__paid_sum_90d"] == pytest.approx(125.0)   # 50 + 75
        assert m2["rx__claims_cnt_90d"] == pytest.approx(2.0)

    def test_window_monotonicity(self, con, spine_df, base_df):
        # For COUNT-based aggs, narrower window <= wider window
        result = _run(spine_df, base_df, con)
        for _, row in result.iterrows():
            cnt_30 = row["rx__claims_cnt_30d"]
            cnt_90 = row["rx__claims_cnt_90d"]
            if pd.notna(cnt_30) and pd.notna(cnt_90):
                assert cnt_30 <= cnt_90


# ---------------------------------------------------------------------------
# Null-fill (zero-event entities)
# ---------------------------------------------------------------------------

class TestNullFill:
    def test_zero_event_member_row_present(self, con, spine_df, base_df):
        result = _run(spine_df, base_df, con)
        assert 3 in result["member_id"].values

    def test_zero_event_member_features_are_null(self, con, spine_df, base_df):
        result = _run(spine_df, base_df, con)
        m3 = _member(result, 3)
        for col in [
            "rx__paid_sum_30d", "rx__paid_sum_90d", "rx__claims_cnt_30d", "rx__claims_cnt_90d"
        ]:
            assert pd.isna(m3[col]), f"{col} should be null for zero-event member, got {m3[col]}"


# ---------------------------------------------------------------------------
# Input types
# ---------------------------------------------------------------------------

class TestInputTypes:
    _params = dict(
        entity_keys=["member_id"], as_of_col="as_of_date",
        base_date_col="claim_date", namespace="rx",
        aggregations=_AGGS, windows=[30],
    )

    def test_accepts_dataframe(self, con, spine_df, base_df):
        result = compute_features(spine_df, base_df, con=con, **self._params)
        assert len(result) == 3

    def test_accepts_table_name(self, con, spine_df, base_df):
        con.register("test_spine", spine_df)
        con.register("test_base", base_df)
        result = compute_features("test_spine", "test_base", con=con, **self._params)
        assert len(result) == 3

    def test_con_none_creates_local_connection(self, spine_df, base_df):
        result = compute_features(spine_df, base_df, con=None, **self._params)
        assert len(result) == 3


# ---------------------------------------------------------------------------
# Fan-out guardrails
# ---------------------------------------------------------------------------

class TestFanoutGuardrails:
    def test_fanout_cap_raises(self, con, spine_df, base_df):
        with pytest.raises(RuntimeError, match="fan-out"):
            _run(spine_df, base_df, con, guardrails=Guardrails(max_fanout_rows=1))

    def test_fanout_cap_warns(self, con, spine_df, base_df):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _run(
                spine_df, base_df, con,
                guardrails=Guardrails(max_fanout_rows=1, on_fanout_exceed="warn"),
            )
        assert any("fan-out" in str(w.message).lower() for w in caught)

    def test_fanout_cap_none_skips_check(self, con, spine_df, base_df):
        # max_fanout_rows=None means no cap — should succeed
        result = _run(spine_df, base_df, con, guardrails=Guardrails(max_fanout_rows=None))
        assert len(result) == 3


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------

class TestValidationErrors:
    def test_bad_agg_expr_raises_with_namespace(self, con, spine_df, base_df):
        bad = [Agg("bad", "SUM(nonexistent_column)")]
        with pytest.raises(ValueError, match="rx"):
            _run(spine_df, base_df, con, aggregations=bad)

    def test_invalid_namespace_raises(self, con, spine_df, base_df):
        with pytest.raises(ValueError, match="namespace"):
            _run(spine_df, base_df, con, namespace="rx_claims")

    def test_missing_entity_key_on_spine_raises(self, con, spine_df, base_df):
        with pytest.raises(ValueError, match="missing_key"):
            _run(spine_df, base_df, con, entity_keys=["missing_key"])

    def test_duplicate_spine_rows_raises(self, con, base_df):
        dup_spine = pd.DataFrame({
            "member_id": [1, 1],
            "as_of_date": pd.to_datetime(["2024-01-31", "2024-01-31"]),
        })
        with pytest.raises(ValueError, match="duplicate"):
            _run(dup_spine, base_df, con)


# ---------------------------------------------------------------------------
# "all" window
# ---------------------------------------------------------------------------

class TestAllWindow:
    def test_all_window_includes_all_history(self, con, spine_df, base_df):
        result = _run(spine_df, base_df, con, windows=["all"])
        m2 = _member(result, 2)
        # Both m2 claims (2023-12-01 and 2024-01-10) should be included
        assert m2["rx__claims_cnt_all"] == pytest.approx(2.0)

    def test_all_window_respects_exclusive_upper_bound(self, con, spine_df, base_df):
        result = _run(spine_df, base_df, con, windows=["all"])
        m1 = _member(result, 1)
        # m1 has 2024-01-15 (included) and 2024-01-31 (on as_of, excluded)
        assert m1["rx__claims_cnt_all"] == pytest.approx(1.0)
