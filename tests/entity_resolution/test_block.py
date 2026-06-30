"""Tests for _block.build_blocks()."""

from __future__ import annotations

import pandas as pd
import pytest

from analytics_toolbox.entity_resolution._block import build_blocks
from analytics_toolbox.entity_resolution._config import EntityResolutionConfig


@pytest.fixture
def config() -> EntityResolutionConfig:
    return EntityResolutionConfig(match_threshold=0.80)


def make_system(id_col: str, ids, dobs, last_names=None, postal_codes=None) -> pd.DataFrame:
    """Build a minimal system DataFrame where the ID column name matches the systems dict key."""
    data = {id_col: ids, "DOB": dobs, "Last_Name": last_names or ["Smith"] * len(ids)}
    if postal_codes is not None:
        data["Postal_Code"] = postal_codes
    return pd.DataFrame(data)


class TestPrimaryBlocking:
    def test_records_with_same_dob_land_in_same_block(self, config):
        a = make_system("sys_a_id", ["a1"], ["2000-01-01"])
        b = make_system("sys_b_id", ["b1"], ["2000-01-01"])
        systems = {"sys_a_id": a, "sys_b_id": b}
        blocks = build_blocks(systems, config)
        assert ("2000-01-01",) in blocks

    def test_different_dobs_produce_different_blocks(self, config):
        a = make_system("sys_a_id", ["a1", "a2"], ["2000-01-01", "1990-06-15"])
        b = make_system("sys_b_id", ["b1"], ["2000-01-01"])
        systems = {"sys_a_id": a, "sys_b_id": b}
        blocks = build_blocks(systems, config)
        assert ("2000-01-01",) in blocks
        assert ("1990-06-15",) in blocks

    def test_block_contains_correct_system_subsets(self, config):
        a = make_system("sys_a_id", ["a1", "a2"], ["2000-01-01", "1990-06-15"])
        b = make_system("sys_b_id", ["b1"], ["2000-01-01"])
        systems = {"sys_a_id": a, "sys_b_id": b}
        blocks = build_blocks(systems, config)
        block = blocks[("2000-01-01",)]
        assert set(block["sys_a_id"]["sys_a_id"].tolist()) == {"a1"}
        assert set(block["sys_b_id"]["sys_b_id"].tolist()) == {"b1"}

    def test_single_sided_block_included(self, config):
        """Block with records from only one system is included; matching yields no pairs."""
        a = make_system("sys_a_id", ["a1"], ["1990-06-15"])
        b = make_system("sys_b_id", ["b1"], ["2000-01-01"])
        systems = {"sys_a_id": a, "sys_b_id": b}
        blocks = build_blocks(systems, config)
        # Both DOBs should produce blocks, even if one system is absent from a block
        assert ("1990-06-15",) in blocks
        assert ("2000-01-01",) in blocks


class TestNullFallback:
    def test_null_dob_falls_back_to_secondary_block(self, config):
        a = pd.DataFrame(
            {
                "sys_a_id": ["a1"],
                "DOB": [None],
                "Last_Name": ["SMITH"],
                "Postal_Code": ["50131"],
            }
        )
        b = pd.DataFrame(
            {
                "sys_b_id": ["b1"],
                "DOB": [None],
                "Last_Name": ["SMITH"],
                "Postal_Code": ["50131"],
            }
        )
        systems = {"sys_a_id": a, "sys_b_id": b}
        blocks = build_blocks(systems, config)
        # Should appear in a secondary block, not the null primary block
        secondary_key = ("SMITH", "50131")
        assert secondary_key in blocks

    def test_record_all_keys_null_excluded(self, config):
        a = pd.DataFrame(
            {"sys_a_id": ["a1"], "DOB": [None], "Last_Name": [None], "Postal_Code": [None]}
        )
        b = make_system("sys_b_id", ["b1"], ["2000-01-01"])
        systems = {"sys_a_id": a, "sys_b_id": b}
        blocks = build_blocks(systems, config)
        # a1 should not appear in any block since all block keys are null
        for block_df_map in blocks.values():
            if "sys_a_id" in block_df_map:
                assert "a1" not in block_df_map["sys_a_id"]["sys_a_id"].tolist()

    def test_mixed_null_and_valid_dob(self, config):
        a = pd.DataFrame(
            {
                "sys_a_id": ["a1", "a2"],
                "DOB": ["2000-01-01", None],
                "Last_Name": ["SMITH", "JONES"],
                "Postal_Code": ["50131", "50132"],
            }
        )
        b = make_system("sys_b_id", ["b1"], ["2000-01-01"])
        systems = {"sys_a_id": a, "sys_b_id": b}
        blocks = build_blocks(systems, config)
        assert ("2000-01-01",) in blocks
        # a2 with null DOB should appear in secondary block
        assert ("JONES", "50132") in blocks


class TestEmptyStringBlockKey:
    def test_empty_string_dob_does_not_form_primary_block(self, config):
        """An empty-string DOB must not be treated as a valid primary block key.

        Otherwise every blank-DOB record shares a meaningless ("",) block and is
        credited sim=1.0 against the others. Empty string should fall through to
        secondary blocking, exactly like a null DOB.
        """
        a = pd.DataFrame(
            {
                "sys_a_id": ["a1"],
                "DOB": [""],
                "Last_Name": ["SMITH"],
                "Postal_Code": ["50131"],
            }
        )
        b = pd.DataFrame(
            {
                "sys_b_id": ["b1"],
                "DOB": [""],
                "Last_Name": ["SMITH"],
                "Postal_Code": ["50131"],
            }
        )
        systems = {"sys_a_id": a, "sys_b_id": b}
        blocks = build_blocks(systems, config)
        assert ("",) not in blocks
        assert ("SMITH", "50131") in blocks

    def test_whitespace_only_dob_does_not_form_primary_block(self, config):
        a = pd.DataFrame(
            {"sys_a_id": ["a1"], "DOB": ["   "], "Last_Name": ["SMITH"], "Postal_Code": ["50131"]}
        )
        b = pd.DataFrame(
            {"sys_b_id": ["b1"], "DOB": ["   "], "Last_Name": ["SMITH"], "Postal_Code": ["50131"]}
        )
        systems = {"sys_a_id": a, "sys_b_id": b}
        blocks = build_blocks(systems, config)
        assert ("   ",) not in blocks
        assert ("SMITH", "50131") in blocks
