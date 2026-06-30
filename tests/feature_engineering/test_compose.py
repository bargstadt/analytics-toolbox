import pandas as pd
import pytest

from analytics_toolbox.feature_engineering import join_features

_GRAIN = ["member_id", "as_of_date"]


def _make_frame(member_ids, cols: dict) -> pd.DataFrame:
    """Build a spine-grain DataFrame with extra feature columns."""
    base = {
        "member_id": member_ids,
        "as_of_date": pd.to_datetime(["2024-01-31"] * len(member_ids)),
    }
    return pd.DataFrame({**base, **cols})


@pytest.fixture
def rx_frame():
    return _make_frame(
        [1, 2, 3],
        {"rx__paid_sum_30d": [100.0, 75.0, None], "rx__claims_cnt_30d": [1.0, 1.0, None]},
    )


@pytest.fixture
def med_frame():
    return _make_frame(
        [1, 2, 3],
        {"med__claims_cnt_30d": [2.0, 0.0, 1.0], "med__paid_sum_30d": [300.0, 0.0, 50.0]},
    )


@pytest.fixture
def dx_frame():
    return _make_frame([1, 2, 3], {"dx__ed_visits_30d": [1.0, 0.0, 0.0]})


# ---------------------------------------------------------------------------
# Basic behavior
# ---------------------------------------------------------------------------

class TestJoinFeaturesBasic:
    def test_two_frames_row_count_preserved(self, rx_frame, med_frame):
        result = join_features([rx_frame, med_frame], on=_GRAIN)
        assert len(result) == 3

    def test_two_frames_column_count(self, rx_frame, med_frame):
        result = join_features([rx_frame, med_frame], on=_GRAIN)
        expected_cols = set(_GRAIN) | set(rx_frame.columns) | set(med_frame.columns)
        assert set(result.columns) == expected_cols

    def test_three_frames(self, rx_frame, med_frame, dx_frame):
        result = join_features([rx_frame, med_frame, dx_frame], on=_GRAIN)
        assert len(result) == 3
        assert "rx__paid_sum_30d" in result.columns
        assert "med__claims_cnt_30d" in result.columns
        assert "dx__ed_visits_30d" in result.columns

    def test_grain_columns_appear_once(self, rx_frame, med_frame):
        result = join_features([rx_frame, med_frame], on=_GRAIN)
        assert result.columns.tolist().count("member_id") == 1
        assert result.columns.tolist().count("as_of_date") == 1

    def test_values_correct_after_join(self, rx_frame, med_frame):
        result = join_features([rx_frame, med_frame], on=_GRAIN)
        row = result[result["member_id"] == 1].iloc[0]
        assert row["rx__paid_sum_30d"] == pytest.approx(100.0)
        assert row["med__claims_cnt_30d"] == pytest.approx(2.0)

    def test_null_values_preserved(self, rx_frame, med_frame):
        result = join_features([rx_frame, med_frame], on=_GRAIN)
        row = result[result["member_id"] == 3].iloc[0]
        assert pd.isna(row["rx__paid_sum_30d"])


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

class TestJoinFeaturesErrors:
    def test_empty_list_raises(self):
        with pytest.raises(ValueError, match="empty"):
            join_features([], on=_GRAIN)

    def test_collision_raises_with_column_name(self, rx_frame):
        # Two frames with the same non-grain column
        dup_frame = _make_frame([1, 2, 3], {"rx__paid_sum_30d": [5.0, 6.0, 7.0]})
        with pytest.raises(ValueError, match="rx__paid_sum_30d"):
            join_features([rx_frame, dup_frame], on=_GRAIN)

    def test_collision_raises_with_frame_indices(self, rx_frame):
        dup_frame = _make_frame([1, 2, 3], {"rx__paid_sum_30d": [5.0, 6.0, 7.0]})
        with pytest.raises(ValueError, match="frame"):
            join_features([rx_frame, dup_frame], on=_GRAIN)

    def test_grain_columns_shared_across_frames_ok(self, rx_frame, med_frame):
        # Grain columns ARE expected to be shared — must not raise
        join_features([rx_frame, med_frame], on=_GRAIN)  # no raise

    def test_single_frame_returns_frame(self, rx_frame):
        result = join_features([rx_frame], on=_GRAIN)
        assert set(result.columns) == set(rx_frame.columns)
        assert len(result) == len(rx_frame)
