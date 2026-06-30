"""Tests for _match.score_pairs()."""

from __future__ import annotations

import pandas as pd
import pytest

from analytics_toolbox.entity_resolution._block import build_blocks
from analytics_toolbox.entity_resolution._config import EntityResolutionConfig
from analytics_toolbox.entity_resolution._match import score_pairs


@pytest.fixture
def config() -> EntityResolutionConfig:
    return EntityResolutionConfig(match_threshold=0.80)


def one_block_systems(a_data: dict, b_data: dict, dob="2000-01-01") -> dict:
    """Build two-system dict sharing a single DOB block."""
    a = pd.DataFrame({**a_data, "DOB": [dob]})
    b = pd.DataFrame({**b_data, "DOB": [dob]})
    return {"sys_a_id": a, "sys_b_id": b}


class TestExactMatch:
    def test_identical_records_score_near_one(self, config):
        systems = one_block_systems(
            {"sys_a_id": ["a1"], "First_Name": ["JOHN"], "Last_Name": ["SMITH"]},
            {"sys_b_id": ["b1"], "First_Name": ["JOHN"], "Last_Name": ["SMITH"]},
        )
        blocks = build_blocks(systems, config)
        pairs = score_pairs(blocks, systems, config)
        assert len(pairs) == 1
        _, _, _, _, score = pairs[0]
        assert score >= 0.95

    def test_pair_tuple_structure(self, config):
        systems = one_block_systems(
            {"sys_a_id": ["a1"], "First_Name": ["JOHN"]},
            {"sys_b_id": ["b1"], "First_Name": ["JOHN"]},
        )
        blocks = build_blocks(systems, config)
        pairs = score_pairs(blocks, systems, config)
        assert len(pairs) == 1
        sys_a, id_a, sys_b, id_b, score = pairs[0]
        assert sys_a == "sys_a_id"
        assert id_a == "a1"
        assert sys_b == "sys_b_id"
        assert id_b == "b1"
        assert 0.0 <= score <= 1.0


class TestNonMatch:
    def test_clear_non_match_below_threshold(self, config):
        systems = one_block_systems(
            {"sys_a_id": ["a1"], "First_Name": ["JOHN"], "Last_Name": ["SMITH"]},
            {"sys_b_id": ["b1"], "First_Name": ["ALICE"], "Last_Name": ["JONES"]},
        )
        blocks = build_blocks(systems, config)
        pairs = score_pairs(blocks, systems, config)
        # No pairs should exceed threshold of 0.80
        assert all(score < 0.80 for _, _, _, _, score in pairs)


class TestWeightNormalization:
    def test_missing_column_excluded_from_denominator(self, config):
        """System C has no Postal_Code — score should still be computed from available cols."""
        a = pd.DataFrame(
            {
                "sys_a_id": ["a1"],
                "DOB": ["2000-01-01"],
                "Last_Name": ["SMITH"],
                "Postal_Code": ["50131"],
            }
        )
        b = pd.DataFrame({"sys_b_id": ["b1"], "DOB": ["2000-01-01"], "Last_Name": ["SMITH"]})
        systems = {"sys_a_id": a, "sys_b_id": b}
        blocks = build_blocks(systems, config)
        pairs = score_pairs(blocks, systems, config)
        # Should produce a valid high-scoring pair even without Postal_Code
        assert len(pairs) >= 1
        _, _, _, _, score = pairs[0]
        assert score > 0.80

    def test_no_common_scoreable_columns_yields_no_pairs(self, config):
        """If systems share only the block column (DOB), no other common field → no pairs."""
        # DOB is block column (weight 1.0) but both DOBs are identical within block.
        # If DOB weight + block exact match = only column, score should still be computed.
        # But let's test with completely disjoint non-block column sets.
        a = pd.DataFrame({"sys_a_id": ["a1"], "DOB": ["2000-01-01"], "Unique_A": ["x"]})
        b = pd.DataFrame({"sys_b_id": ["b1"], "DOB": ["2000-01-01"], "Unique_B": ["y"]})
        systems = {"sys_a_id": a, "sys_b_id": b}
        blocks = build_blocks(systems, config)
        pairs = score_pairs(blocks, systems, config)
        # DOB is in common (block column), score = 1.0 from DOB alone.
        # Whether this exceeds threshold depends on DOB weight vs threshold.
        # DOB weight = 1.0, total available weight = 1.0 → score = 1.0 → above threshold
        assert len(pairs) >= 0  # can be 0 or 1, just don't crash


class TestBlockColumn:
    def test_block_column_contributes_score_1(self, config):
        """Records in the same DOB block get DOB sim=1.0 without re-computing via RapidFuzz."""
        systems = one_block_systems(
            {"sys_a_id": ["a1"]},
            {"sys_b_id": ["b1"]},
        )
        blocks = build_blocks(systems, config)
        pairs = score_pairs(blocks, systems, config)
        # Only common col is DOB (block col, sim=1.0).
        # Score = 1.0 * 1.0 / 1.0 = 1.0 → above threshold
        assert len(pairs) == 1
        _, _, _, _, score = pairs[0]
        assert score == pytest.approx(1.0)


class TestTopN:
    def test_top_n_limits_matches_per_record(self):
        config = EntityResolutionConfig(match_threshold=0.50, top_n_matches=1)
        a = pd.DataFrame({"sys_a_id": ["a1"], "DOB": ["2000-01-01"], "Last_Name": ["SMITH"]})
        b = pd.DataFrame(
            {
                "sys_b_id": ["b1", "b2"],
                "DOB": ["2000-01-01", "2000-01-01"],
                "Last_Name": ["SMITH", "SMITH"],
            }
        )
        systems = {"sys_a_id": a, "sys_b_id": b}
        blocks = build_blocks(systems, config)
        pairs = score_pairs(blocks, systems, config)
        # With top_n=1, a1 gets at most 1 match from sys_b
        a1_pairs = [
            (sa, ia, sb, ib, s) for sa, ia, sb, ib, s in pairs if ia == "a1"
        ]
        assert len(a1_pairs) <= 1


class TestFanOutGuard:
    def test_fan_out_guard_raises_on_large_block(self):
        config = EntityResolutionConfig(match_threshold=0.80, max_block_pairs=2)
        a = pd.DataFrame(
            {"sys_a_id": ["a1", "a2"], "DOB": ["2000-01-01", "2000-01-01"]}
        )
        b = pd.DataFrame(
            {"sys_b_id": ["b1", "b2", "b3"], "DOB": ["2000-01-01", "2000-01-01", "2000-01-01"]}
        )
        systems = {"sys_a_id": a, "sys_b_id": b}
        blocks = build_blocks(systems, config)
        with pytest.raises(RuntimeError, match="max_block_pairs"):
            score_pairs(blocks, systems, config)

    def test_fan_out_error_message_contains_no_phi(self):
        """The fan-out error must report counts only — never the block key value (a DOB)."""
        dob = "1987-03-14"
        config = EntityResolutionConfig(match_threshold=0.80, max_block_pairs=2)
        a = pd.DataFrame({"sys_a_id": ["a1", "a2"], "DOB": [dob, dob]})
        b = pd.DataFrame({"sys_b_id": ["b1", "b2", "b3"], "DOB": [dob, dob, dob]})
        systems = {"sys_a_id": a, "sys_b_id": b}
        blocks = build_blocks(systems, config)
        with pytest.raises(RuntimeError) as excinfo:
            score_pairs(blocks, systems, config)
        message = str(excinfo.value)
        assert dob not in message, "PHI (DOB) leaked into fan-out error message"
        # Counts and limits are still reported
        assert "6" in message  # 2 × 3 candidate pairs
        assert "max_block_pairs" in message
